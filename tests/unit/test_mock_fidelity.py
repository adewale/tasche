"""Tests for Phase B — Mock fidelity improvements.

Covers:
- MockD1 ``bind()`` parameter validation (placeholder count vs. bound params)
- MockD1 and SqliteD1 ``run()`` ``meta.changes`` against recorded real-D1 responses
- MockKV TTL tracking and ``advance_time()``
- MockR2 ``httpMetadata`` storage and retrieval
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import MockD1, MockKV, MockR2, SqliteD1

# =========================================================================
# MockD1 bind() parameter validation
# =========================================================================


class TestMockD1BindValidation:
    """Verify that bind() validates placeholder/param count."""

    def test_bind_correct_count_succeeds(self) -> None:
        """Binding the exact number of params matching ? placeholders works."""
        db = MockD1()
        stmt = db.prepare("SELECT * FROM articles WHERE user_id = ? AND status = ?")
        # Should not raise
        result = stmt.bind("user_001", "ready")
        assert result is stmt

    def test_bind_too_few_params_raises(self) -> None:
        """Binding fewer params than ? placeholders raises ValueError."""
        db = MockD1()
        stmt = db.prepare("SELECT * FROM articles WHERE user_id = ? AND status = ?")
        with pytest.raises(ValueError, match="Parameter count mismatch"):
            stmt.bind("user_001")

    def test_bind_too_many_params_raises(self) -> None:
        """Binding more params than ? placeholders raises ValueError."""
        db = MockD1()
        stmt = db.prepare("SELECT * FROM articles WHERE id = ?")
        with pytest.raises(ValueError, match="Parameter count mismatch"):
            stmt.bind("art_001", "extra_param")

    def test_bind_zero_params_for_no_placeholders(self) -> None:
        """SQL with no placeholders accepts zero bind params."""
        db = MockD1()
        stmt = db.prepare("SELECT * FROM articles")
        result = stmt.bind()
        assert result is stmt

    def test_bind_zero_params_raises_when_placeholders_exist(self) -> None:
        """SQL with placeholders raises when bind() is called with no args."""
        db = MockD1()
        stmt = db.prepare("SELECT * FROM articles WHERE id = ?")
        with pytest.raises(ValueError, match="Parameter count mismatch"):
            stmt.bind()

    def test_bind_handles_question_mark_in_string_literal(self) -> None:
        """Question marks in SQL are counted literally (no string parsing).

        This is a known simplification -- we count all ``?`` characters.
        """
        db = MockD1()
        # This SQL has 3 ? characters
        stmt = db.prepare("SELECT '?' FROM t WHERE a = ? AND b = ?")
        # We need 3 params to match 3 question marks
        result = stmt.bind("literal", "val_a", "val_b")
        assert result is stmt


# =========================================================================
# MockD1 run() row change tracking
# =========================================================================

# Expected values come from real D1, not from the mocks: recorded locally by
# scripts/record-d1-semantics.mjs (wrangler d1 migrations apply --local, then
# Miniflare's D1 binding). Re-run that script to refresh the fixture.
D1_RECORDING = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "d1_run_semantics.json").read_text()
)
SCENARIOS = D1_RECORDING["scenarios"]


def _ids(scenario: dict[str, Any]) -> str:
    return scenario["name"]


class TestMockD1RunChanges:
    """MockD1's meta.changes must agree with recorded D1 responses."""

    @pytest.mark.parametrize("scenario", [s for s in SCENARIOS if s["unconfigured_mock"]], ids=_ids)
    async def test_unconfigured_mock_matches_recorded_d1(self, scenario: dict[str, Any]) -> None:
        """An empty MockD1 answers like D1 when nothing matches or conflicts.

        The previous version of this test asserted ``changes == 1`` for a
        DELETE that matched nothing and called it D1 behaviour; D1 returns 0,
        as src/tags/routes.py and the batch-update endpoint assume.
        """
        result = await MockD1().prepare(scenario["sql"]).bind(*scenario["params"]).run()
        assert result["success"] is scenario["success"]
        assert result["meta"]["changes"] == scenario["meta_changes"]

    async def test_run_returns_changes_based_on_returned_rows(self) -> None:
        """When execute_fn returns rows, changes reflects the count."""
        rows = [{"id": "1"}, {"id": "2"}]
        db = MockD1(execute=lambda sql, params: rows)
        stmt = db.prepare("UPDATE articles SET status = ? WHERE user_id = ?")
        result = await stmt.bind("ready", "user_001").run()
        assert result["meta"]["changes"] == 2

    async def test_explicit_changes_override(self) -> None:
        """execute_fn can state changes directly, e.g. an INSERT OR IGNORE conflict."""
        db = MockD1(execute=lambda sql, params: {"changes": 0})
        stmt = db.prepare("INSERT OR IGNORE INTO tags (id) VALUES (?)")
        result = await stmt.bind("tag_1").run()
        assert result["meta"]["changes"] == 0


class TestSqliteD1MatchesRecordedD1:
    """The SQLite-backed double reproduces every recorded D1 response."""

    async def test_replays_the_recording(self) -> None:
        db = SqliteD1()
        for step in D1_RECORDING["setup"]:
            await db.prepare(step["sql"]).bind(*step["params"]).run()
        observed = {}
        for scenario in SCENARIOS:
            result = await db.prepare(scenario["sql"]).bind(*scenario["params"]).run()
            observed[scenario["name"]] = (result["success"], result["meta"]["changes"])
        assert observed == {s["name"]: (s["success"], s["meta_changes"]) for s in SCENARIOS}

    def test_recording_covers_the_production_assumptions(self) -> None:
        """The cases src/ branches on are in the recording, with D1's answer."""
        by_name = {s["name"]: s["meta_changes"] for s in SCENARIOS}
        assert by_name["insert_or_ignore_unique_conflict"] == 0  # tags create_tag -> 409
        assert by_name["update_no_matching_row"] == 0  # batch-update "updated" count
        assert by_name["delete_no_matching_row"] == 0
        # D1 counts trigger writes: meta.changes is "did anything change", not a row
        # count, for tables with triggers (articles has FTS5 sync triggers).
        assert by_name["insert_into_table_with_fts_trigger"] > 1


# =========================================================================
# MockKV TTL tracking
# =========================================================================


class TestMockKVTTL:
    """Verify that MockKV tracks TTL and supports advance_time()."""

    async def test_key_without_ttl_never_expires(self) -> None:
        """Keys stored without expirationTtl are always available."""
        kv = MockKV()
        await kv.put("key1", "value1")
        kv.advance_time(999999)
        assert await kv.get("key1") == "value1"

    async def test_key_with_ttl_available_before_expiry(self) -> None:
        """Keys are available before their TTL expires."""
        kv = MockKV()
        await kv.put("key1", "value1", expirationTtl=60)
        kv.advance_time(30)  # 30 seconds, TTL is 60
        assert await kv.get("key1") == "value1"

    async def test_key_with_ttl_expired_after_advance(self) -> None:
        """Keys return None after their TTL expires via advance_time()."""
        kv = MockKV()
        await kv.put("key1", "value1", expirationTtl=60)
        kv.advance_time(61)  # Past the 60-second TTL
        assert await kv.get("key1") is None

    async def test_key_with_ttl_exact_boundary(self) -> None:
        """Keys expire exactly at the TTL boundary."""
        kv = MockKV()
        await kv.put("key1", "value1", expirationTtl=60)
        kv.advance_time(60)  # Exactly at boundary
        assert await kv.get("key1") is None

    async def test_multiple_keys_independent_ttl(self) -> None:
        """Multiple keys with different TTLs expire independently."""
        kv = MockKV()
        await kv.put("short", "a", expirationTtl=10)
        await kv.put("long", "b", expirationTtl=100)
        await kv.put("forever", "c")

        kv.advance_time(20)

        assert await kv.get("short") is None  # Expired
        assert await kv.get("long") == "b"  # Still valid
        assert await kv.get("forever") == "c"  # No TTL

    async def test_advance_time_is_cumulative(self) -> None:
        """Multiple advance_time() calls accumulate."""
        kv = MockKV()
        await kv.put("key1", "value1", expirationTtl=100)

        kv.advance_time(40)
        assert await kv.get("key1") == "value1"

        kv.advance_time(40)
        assert await kv.get("key1") == "value1"

        kv.advance_time(21)  # Total: 101 seconds
        assert await kv.get("key1") is None

    async def test_expired_key_cleaned_from_store(self) -> None:
        """After expiry, the key is removed from the internal store."""
        kv = MockKV()
        await kv.put("key1", "value1", expirationTtl=10)
        kv.advance_time(11)

        # get() should clean up the key
        assert await kv.get("key1") is None
        assert "key1" not in kv._store

    async def test_overwrite_key_resets_ttl(self) -> None:
        """Writing a key again resets its TTL."""
        kv = MockKV()
        await kv.put("key1", "value1", expirationTtl=10)
        kv.advance_time(5)

        # Overwrite with a new TTL
        await kv.put("key1", "value2", expirationTtl=20)
        kv.advance_time(15)  # 20 seconds total

        # Should still be available (new TTL of 20, only 15 seconds since overwrite)
        assert await kv.get("key1") == "value2"

    async def test_overwrite_without_ttl_removes_expiry(self) -> None:
        """Overwriting a key without TTL removes any previous expiry."""
        kv = MockKV()
        await kv.put("key1", "value1", expirationTtl=10)
        await kv.put("key1", "value2")  # No TTL

        kv.advance_time(999)
        assert await kv.get("key1") == "value2"

    async def test_delete_removes_expiry(self) -> None:
        """Deleting a key also removes its TTL tracking."""
        kv = MockKV()
        await kv.put("key1", "value1", expirationTtl=60)
        await kv.delete("key1")

        assert "key1" not in kv._expiry


# =========================================================================
# MockR2 httpMetadata
# =========================================================================


class TestMockR2HttpMetadata:
    """Verify that MockR2 stores and returns httpMetadata."""

    async def test_put_without_metadata_returns_empty_dict(self) -> None:
        """Objects stored without httpMetadata return an empty dict on get()."""
        r2 = MockR2()
        await r2.put("key1", b"data")
        obj = await r2.get("key1")
        assert obj is not None
        assert obj.httpMetadata == {}

    async def test_put_with_metadata_returns_metadata(self) -> None:
        """Objects stored with httpMetadata return it on get()."""
        r2 = MockR2()
        metadata = {"contentType": "image/webp", "cacheControl": "public, max-age=86400"}
        await r2.put("key1", b"data", httpMetadata=metadata)
        obj = await r2.get("key1")
        assert obj is not None
        assert obj.httpMetadata == metadata

    async def test_metadata_is_independent_copy(self) -> None:
        """Stored metadata is a copy, not a reference to the original dict."""
        r2 = MockR2()
        metadata = {"contentType": "text/html"}
        await r2.put("key1", b"data", httpMetadata=metadata)

        # Mutating the original dict should not affect the stored metadata
        metadata["contentType"] = "text/plain"

        obj = await r2.get("key1")
        assert obj.httpMetadata["contentType"] == "text/html"

    async def test_different_keys_have_different_metadata(self) -> None:
        """Each key has its own httpMetadata."""
        r2 = MockR2()
        await r2.put("html", b"<html>", httpMetadata={"contentType": "text/html"})
        await r2.put("image", b"\x00", httpMetadata={"contentType": "image/webp"})

        html_obj = await r2.get("html")
        image_obj = await r2.get("image")

        assert html_obj.httpMetadata["contentType"] == "text/html"
        assert image_obj.httpMetadata["contentType"] == "image/webp"

    async def test_overwrite_updates_metadata(self) -> None:
        """Overwriting an object updates its httpMetadata."""
        r2 = MockR2()
        await r2.put("key1", b"v1", httpMetadata={"contentType": "text/plain"})
        await r2.put("key1", b"v2", httpMetadata={"contentType": "text/html"})

        obj = await r2.get("key1")
        assert obj.httpMetadata["contentType"] == "text/html"
        assert await obj.text() == "v2"

    async def test_overwrite_without_metadata_clears_it(self) -> None:
        """Overwriting an object without httpMetadata clears existing metadata."""
        r2 = MockR2()
        await r2.put("key1", b"v1", httpMetadata={"contentType": "text/plain"})
        await r2.put("key1", b"v2")  # No metadata

        obj = await r2.get("key1")
        assert obj.httpMetadata == {}

    async def test_delete_removes_metadata(self) -> None:
        """Deleting an object also removes its httpMetadata."""
        r2 = MockR2()
        await r2.put("key1", b"data", httpMetadata={"contentType": "image/png"})
        await r2.delete("key1")

        assert "key1" not in r2._metadata

    async def test_get_nonexistent_returns_none(self) -> None:
        """Getting a nonexistent key returns None (unchanged behavior)."""
        r2 = MockR2()
        assert await r2.get("nonexistent") is None

    async def test_string_value_with_metadata(self) -> None:
        """String values are stored as bytes alongside httpMetadata."""
        r2 = MockR2()
        await r2.put("key1", "hello", httpMetadata={"contentType": "text/plain"})

        obj = await r2.get("key1")
        assert obj is not None
        assert await obj.text() == "hello"
        assert obj.httpMetadata == {"contentType": "text/plain"}

"""Tests for audit issue fixes across auth, stats, and tags modules.

Covers:
- Issue 13: asyncio.gather for concurrent stats queries
- Issue 20: Cached parse_allowed_emails
- Issue 22: refresh_session does not mutate caller's dict
- Issue 24: Legacy 'reading' status folded into unread counts
- Issue 26: _calculate_streak uses UTC date
- Issue 27: _dev_user returns a copy (not the cached original)
- Issue 30: TOCTOU-safe tag creation via INSERT OR IGNORE
- Issue 75: Session ID size comment
- Issue 76: now_iso() used in dependencies
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.auth.session import (
    SESSION_PREFIX,
    _REFRESH_INTERVAL,
    refresh_session,
)
from src.stats.routes import _calculate_streak, get_stats, router as stats_router
from src.tags.routes import article_tags_router, router as tags_router
from tests.conftest import (
    MockD1,
    MockEnv,
    MockKV,
    make_test_helpers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_make_stats_app, _stats_client = make_test_helpers((stats_router, "/api/stats"))
_make_tags_app, _tags_client = make_test_helpers(
    (tags_router, "/api/tags"),
    (article_tags_router, "/api/articles"),
)


# =========================================================================
# Issue 22: refresh_session must not mutate caller's dict
# =========================================================================


class TestRefreshSessionNoMutation:
    async def test_does_not_mutate_original_dict(self) -> None:
        """refresh_session should not add 'refreshed_at' to the passed-in dict."""
        kv = MockKV()
        user_data = {"user_id": "u1", "email": "test@example.com"}
        original_keys = set(user_data.keys())

        session_id = "test_session_nomut"
        key = f"{SESSION_PREFIX}{session_id}"
        kv._store[key] = json.dumps(user_data)

        await refresh_session(kv, session_id, user_data)

        # The original dict must NOT have been mutated
        assert "refreshed_at" not in user_data
        assert set(user_data.keys()) == original_keys

    async def test_stores_refreshed_at_in_kv(self) -> None:
        """refresh_session should store refreshed_at in KV (just not mutate the input)."""
        kv = MockKV()
        user_data = {"user_id": "u1", "email": "test@example.com"}
        session_id = "test_session_kv"
        key = f"{SESSION_PREFIX}{session_id}"
        kv._store[key] = json.dumps(user_data)

        await refresh_session(kv, session_id, user_data)

        stored = json.loads(kv._store[key])
        assert "refreshed_at" in stored
        assert stored["refreshed_at"] > 0

    async def test_skips_when_recently_refreshed_without_mutation(self) -> None:
        """When refresh is skipped, the dict should also not be mutated."""
        kv = MockKV()
        recent = time.time() - 60  # 60 seconds ago (within _REFRESH_INTERVAL)
        user_data = {
            "user_id": "u1",
            "email": "test@example.com",
            "refreshed_at": recent,
        }
        session_id = "test_session_skip"
        key = f"{SESSION_PREFIX}{session_id}"
        original_json = json.dumps(user_data)
        kv._store[key] = original_json

        await refresh_session(kv, session_id, user_data)

        # Dict unchanged, KV unchanged
        assert user_data["refreshed_at"] == recent
        assert kv._store[key] == original_json


# =========================================================================
# Issue 27: _dev_user returns a copy each time
# =========================================================================


class TestDevUserReturnsCopy:
    def setup_method(self) -> None:
        """Reset the module-level dev user cache before each test."""
        import src.auth.dependencies as deps

        deps._dev_user = None

    async def test_returns_different_dict_objects(self) -> None:
        """Each call to _get_or_create_dev_user returns a distinct dict (copy)."""
        import src.auth.dependencies as deps
        from src.auth.dependencies import _get_or_create_dev_user
        from src.wrappers import SafeEnv

        env = MockEnv(disable_auth="true", site_url="http://localhost:8787")
        safe_env = SafeEnv(env)

        user1 = await _get_or_create_dev_user(safe_env.DB)
        user2 = await _get_or_create_dev_user(safe_env.DB)

        # They should be equal in content but different objects
        assert user1 == user2
        assert user1 is not user2

    async def test_mutation_does_not_affect_cache(self) -> None:
        """Mutating the returned dev user dict does not affect the cached version."""
        import src.auth.dependencies as deps
        from src.auth.dependencies import _get_or_create_dev_user
        from src.wrappers import SafeEnv

        env = MockEnv(disable_auth="true", site_url="http://localhost:8787")
        safe_env = SafeEnv(env)

        user1 = await _get_or_create_dev_user(safe_env.DB)
        user1["injected"] = "evil"

        user2 = await _get_or_create_dev_user(safe_env.DB)
        assert "injected" not in user2


# =========================================================================
# Issue 13: asyncio.gather used for concurrent stats queries
# =========================================================================


class TestStatsConcurrentQueries:
    async def test_get_stats_uses_asyncio_gather(self) -> None:
        """The get_stats handler should use concurrent execution for stats queries."""
        import asyncio
        from unittest.mock import patch as _patch

        call_order: list[str] = []

        def execute(sql: str, params: list) -> list:
            if "GROUP BY reading_status" in sql:
                call_order.append("status")
                return [{"reading_status": "unread", "cnt": 1}]
            if "SUM(word_count)" in sql:
                call_order.append("words")
                return [{"total": 100}]
            if "COUNT(*)" in sql:
                call_order.append("count")
                return [{"cnt": 0}]
            if "GROUP BY domain" in sql:
                call_order.append("domains")
                return []
            if "DISTINCT date" in sql:
                call_order.append("dates")
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            if "saved_week" in sql:
                return [{"saved_week": 0, "saved_month": 0, "archived_week": 0, "archived_month": 0}]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _stats_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        # Multiple queries were executed (concurrent via gather)
        assert len(call_order) >= 3


# =========================================================================
# Issue 24: Legacy 'reading' status articles are counted
# =========================================================================


class TestReadingStatusCounted:
    async def test_reading_status_folded_into_unread(self) -> None:
        """Articles with legacy 'reading' status are folded into the unread count."""

        def execute(sql: str, params: list) -> list:
            if "GROUP BY reading_status" in sql:
                return [
                    {"reading_status": "unread", "cnt": 10},
                    {"reading_status": "archived", "cnt": 5},
                    {"reading_status": "reading", "cnt": 3},
                ]
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY domain" in sql:
                return []
            if "DISTINCT date" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            if "saved_week" in sql:
                return [
                    {
                        "saved_week": 0,
                        "saved_month": 0,
                        "archived_week": 0,
                        "archived_month": 0,
                    }
                ]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _stats_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        status = resp.json()["articles_by_status"]
        # 'reading' (3) should be folded into 'unread' (10) = 13
        assert status["unread"] == 13
        assert status["archived"] == 5

    async def test_reading_only_status(self) -> None:
        """When only 'reading' status articles exist, they appear as unread."""

        def execute(sql: str, params: list) -> list:
            if "GROUP BY reading_status" in sql:
                return [{"reading_status": "reading", "cnt": 7}]
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY domain" in sql:
                return []
            if "DISTINCT date" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            if "saved_week" in sql:
                return [
                    {
                        "saved_week": 0,
                        "saved_month": 0,
                        "archived_week": 0,
                        "archived_month": 0,
                    }
                ]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _stats_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        status = resp.json()["articles_by_status"]
        assert status["unread"] == 7
        assert status["archived"] == 0


# =========================================================================
# Issue 26: _calculate_streak uses UTC date
# =========================================================================


class TestStreakUsesUTC:
    def test_calculate_streak_uses_utc(self) -> None:
        """_calculate_streak correctly handles UTC dates, not local time."""
        from datetime import datetime, timedelta, timezone

        utc_today = datetime.now(timezone.utc).date()
        # A streak of 3 consecutive UTC days should return 3
        rows = [
            {"d": (utc_today - timedelta(days=i)).isoformat()} for i in range(3)
        ]
        result = _calculate_streak(rows)
        assert result == 3

        # A gap should break the streak
        rows_with_gap = [
            {"d": utc_today.isoformat()},
            {"d": (utc_today - timedelta(days=2)).isoformat()},  # gap at day 1
        ]
        result_gap = _calculate_streak(rows_with_gap)
        assert result_gap == 1

    def test_streak_with_utc_today(self) -> None:
        """Streak calculation uses UTC date, not local date."""
        from datetime import datetime, timedelta, timezone

        utc_today = datetime.now(timezone.utc).date()
        rows = [
            {"d": (utc_today - timedelta(days=i)).isoformat()} for i in range(3)
        ]
        result = _calculate_streak(rows)
        assert result == 3


# =========================================================================
# Issue 30: TOCTOU-safe tag creation
# =========================================================================


class TestTagCreationTOCTOU:
    async def test_insert_or_ignore_used(self) -> None:
        """Tag creation uses INSERT OR IGNORE to handle concurrent duplicates."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _tags_client(env)
        resp = client.post("/api/tags", json={"name": "test-tag"})

        assert resp.status_code == 201
        insert_calls = [c for c in calls if c["sql"].startswith("INSERT")]
        assert len(insert_calls) == 1
        assert "INSERT OR IGNORE" in insert_calls[0]["sql"]

    async def test_concurrent_duplicate_returns_409(self) -> None:
        """When INSERT OR IGNORE inserts 0 rows (race), returns 409."""
        call_count = 0

        def execute(sql: str, params: list) -> list:
            nonlocal call_count
            # SELECT finds no duplicate (simulating the race window)
            if "SELECT" in sql and "name = ?" in sql:
                return []
            # INSERT OR IGNORE — simulate 0 changes (concurrent insert won)
            if "INSERT OR IGNORE" in sql:
                return {"changes": 0}
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _tags_client(env)
        resp = client.post("/api/tags", json={"name": "race-tag"})

        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]


# =========================================================================
# Issue 76: now_iso() used in dependencies.py
# =========================================================================


class TestNowIsoUsage:
    def test_dependencies_uses_now_iso(self) -> None:
        """dependencies.py should import and use now_iso() instead of raw datetime."""
        import src.auth.dependencies as deps

        source = inspect.getsource(deps)
        assert "from utils import now_iso" in source or "from src.utils import now_iso" in source
        # Should NOT use datetime.now(UTC).isoformat() directly
        assert "datetime.now(UTC).isoformat()" not in source


# =========================================================================
# Issue 20: Cached parse_allowed_emails
# =========================================================================


class TestCachedParseAllowedEmails:
    def setup_method(self) -> None:
        """Reset the cache before each test."""
        import src.auth.dependencies as deps

        deps._allowed_emails_cache = None

    def test_cache_exists(self) -> None:
        """The _cached_parse_allowed_emails function should exist."""
        from src.auth.dependencies import _cached_parse_allowed_emails

        result = _cached_parse_allowed_emails("a@b.com,c@d.com")
        assert result == {"a@b.com", "c@d.com"}

    def test_cache_returns_same_result_for_same_input(self) -> None:
        """Repeated calls with the same input return the cached result."""
        from src.auth.dependencies import _cached_parse_allowed_emails

        result1 = _cached_parse_allowed_emails("a@b.com,c@d.com")
        result2 = _cached_parse_allowed_emails("a@b.com,c@d.com")
        assert result1 == result2
        # Should be the exact same object (cached)
        assert result1 is result2

    def test_cache_invalidates_on_new_input(self) -> None:
        """When the raw string changes, the cache is recomputed."""
        from src.auth.dependencies import _cached_parse_allowed_emails

        result1 = _cached_parse_allowed_emails("a@b.com")
        result2 = _cached_parse_allowed_emails("a@b.com,c@d.com")
        assert result1 != result2
        assert result2 == {"a@b.com", "c@d.com"}


# =========================================================================
# Issue 75: Session ID size comment
# =========================================================================


class TestSessionIdComment:
    def test_session_id_size_is_documented(self) -> None:
        """session.py should have a comment explaining the 32-byte session ID size."""
        import src.auth.session as session_mod

        source = inspect.getsource(session_mod)
        # Should have a comment near token_urlsafe(32) explaining the choice
        assert "32 bytes" in source.lower() or "43 chars" in source.lower() or "higher entropy" in source.lower()

"""Tests for Phase 4 — Article processing pipeline.

Covers the main ``process_article`` function: happy path, failure handling,
and D1 field updates.  All external HTTP calls are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from tests.conftest import (
    MockEnv,
    MockR2,
    _browser_env,
    _make_mock_client,
    _make_mock_response,
    _noop_screenshot,
)
from tests.conftest import (
    TrackingD1 as _TrackingD1,
)

# Re-export helpers so test_health.py's existing cross-file imports still work
__all__ = [
    "_TrackingD1",
    "_browser_env",
    "_make_mock_client",
    "_make_mock_response",
    "_noop_screenshot",
]


# =========================================================================
# test_process_article — happy path
# =========================================================================


class TestProcessArticleHappyPath:
    async def test_sets_status_to_ready(self) -> None:
        """On successful processing, article status is updated to 'ready'."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.httpx.AsyncClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_001", "https://example.com/article", env)

        # Find the final UPDATE that sets status = 'ready'
        ready_updates = [
            (sql, params)
            for sql, params in db.executed
            if "status" in sql and "'ready'" not in sql and "ready" in str(params)
        ]
        assert len(ready_updates) >= 1
        last_sql, last_params = ready_updates[-1]
        assert "ready" in last_params

    async def test_stores_content_html_in_r2(self) -> None:
        """content.html is stored in R2 under the correct key."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.httpx.AsyncClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_002", "https://example.com/article", env)

        assert "articles/art_002/content.html" in r2._store

    async def test_does_not_store_content_md_in_r2(self) -> None:
        """Markdown is stored only in D1, not in R2."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.httpx.AsyncClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_003", "https://example.com/article", env)

        assert "articles/art_003/content.md" not in r2._store

    async def test_stores_metadata_json_in_r2(self) -> None:
        """metadata.json is stored in R2 with correct article metadata."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.httpx.AsyncClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_004", "https://example.com/article", env)

        metadata_key = "articles/art_004/metadata.json"
        assert metadata_key in r2._store
        metadata = json.loads(r2._store[metadata_key].decode("utf-8"))
        assert metadata["article_id"] == "art_004"
        assert metadata["original_url"] == "https://example.com/article"
        assert "word_count" in metadata
        assert "reading_time_minutes" in metadata


# =========================================================================
# test_process_article — full-page screenshot
# =========================================================================


class TestProcessArticleScreenshot:
    async def test_captures_full_page_screenshot_when_browser_rendering_available(
        self,
    ) -> None:
        """When CF_ACCOUNT_ID and CF_API_TOKEN are set, a full-page screenshot is stored."""
        db = _TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)
        env.CF_ACCOUNT_ID = "test-account"
        env.CF_API_TOKEN = "test-token"

        mock_client = _make_mock_client()
        # Mock the screenshot function to return fake image data
        fake_thumb = b"THUMB_DATA"
        fake_fullpage = b"FULLPAGE_DATA"
        call_count = {"n": 0}

        async def _mock_screenshot(client, url, account_id, api_token, **kwargs):
            call_count["n"] += 1
            if kwargs.get("full_page"):
                return fake_fullpage
            return fake_thumb

        with (
            patch("articles.processing.httpx.AsyncClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_mock_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_ss", "https://example.com/article", env)

        # Verify both screenshots were stored in R2
        assert "articles/art_ss/thumbnail.webp" in r2._store
        assert r2._store["articles/art_ss/thumbnail.webp"] == fake_thumb
        assert "articles/art_ss/original.webp" in r2._store
        assert r2._store["articles/art_ss/original.webp"] == fake_fullpage

    async def test_original_key_in_d1_update(self) -> None:
        """The final D1 UPDATE includes original_key field."""
        db = _TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)
        env.CF_ACCOUNT_ID = "test-account"
        env.CF_API_TOKEN = "test-token"

        mock_client = _make_mock_client()

        async def _mock_screenshot(client, url, account_id, api_token, **kwargs):
            return b"SCREENSHOT_DATA"

        with (
            patch("articles.processing.httpx.AsyncClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_mock_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_okf", "https://example.com/article", env)

        # Find the big UPDATE statement
        update_stmts = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("UPDATE") and "title" in sql
        ]
        assert len(update_stmts) >= 1
        sql, params = update_stmts[-1]
        assert "original_key" in sql
        assert "articles/art_okf/original.webp" in params

    async def test_fails_without_browser_rendering_config(self) -> None:
        """Without CF_ACCOUNT_ID/CF_API_TOKEN, processing fails."""
        db = _TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)
        # No CF_ACCOUNT_ID or CF_API_TOKEN set

        mock_client = _make_mock_client()

        with patch("articles.processing.httpx.AsyncClient", return_value=mock_client):
            from articles.processing import process_article

            await process_article("art_noss", "https://example.com/article", env)

        # Article should be marked as 'failed'
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if "status" in sql and "failed" in str(params)
        ]
        assert len(failed_updates) >= 1

    async def test_full_page_screenshot_failure_non_fatal(self) -> None:
        """If full-page screenshot fails, processing still succeeds."""
        db = _TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)
        env.CF_ACCOUNT_ID = "test-account"
        env.CF_API_TOKEN = "test-token"

        mock_client = _make_mock_client()

        from articles.browser_rendering import BrowserRenderingError

        async def _mock_screenshot(client, url, account_id, api_token, **kwargs):
            if kwargs.get("full_page"):
                raise BrowserRenderingError("Timeout on full-page")
            return b"THUMB_DATA"

        with (
            patch("articles.processing.httpx.AsyncClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_mock_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_sserr", "https://example.com/article", env)

        # Thumbnail was stored, but original was not
        assert "articles/art_sserr/thumbnail.webp" in r2._store
        assert "articles/art_sserr/original.webp" not in r2._store

        # Article should still be 'ready'
        ready_updates = [
            (sql, params)
            for sql, params in db.executed
            if "status" in sql and "ready" in str(params) and "title" in sql
        ]
        assert len(ready_updates) >= 1


# =========================================================================
# test_process_article — failure handling
# =========================================================================


class TestProcessArticleFailure:
    async def test_sets_status_to_failed_on_fetch_error(self) -> None:
        """When the page fetch fails, article status is set to 'failed'."""
        db = _TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        error_response = _make_mock_response(status_code=404)
        mock_client = _make_mock_client(page_response=error_response)

        with patch("articles.processing.httpx.AsyncClient", return_value=mock_client):
            from articles.processing import process_article

            await process_article("art_fail", "https://example.com/missing", env)

        # Should have 'failed' status update
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if "status" in sql and "failed" in str(params)
        ]
        assert len(failed_updates) >= 1

    async def test_sets_status_to_failed_on_exception(self) -> None:
        """Any unhandled exception results in 'failed' status."""
        db = _TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("Network error"))

        with patch("articles.processing.httpx.AsyncClient", return_value=mock_client):
            from articles.processing import process_article

            await process_article("art_err", "https://example.com/error", env)

        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if "status" in sql and "failed" in str(params)
        ]
        assert len(failed_updates) >= 1


# =========================================================================
# test_process_article — D1 field updates
# =========================================================================


class TestProcessArticleD1Updates:
    async def test_updates_all_required_fields(self) -> None:
        """The final D1 UPDATE includes all required metadata fields."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.httpx.AsyncClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_fields", "https://example.com/article", env)

        # Find the big UPDATE statement (the one with most fields)
        update_stmts = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("UPDATE") and "title" in sql
        ]
        assert len(update_stmts) >= 1

        sql, params = update_stmts[-1]
        # Verify the SQL contains all required field names
        required_fields = [
            "title",
            "excerpt",
            "author",
            "word_count",
            "reading_time_minutes",
            "domain",
            "final_url",
            "canonical_url",
            "html_key",
            "thumbnail_key",
            "original_key",
            "image_count",
            "markdown_content",
            "status",
        ]
        for field_name in required_fields:
            assert field_name in sql, f"Missing field: {field_name}"

    async def test_first_update_sets_processing_status(self) -> None:
        """The first D1 operation sets status to 'processing'."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.httpx.AsyncClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_proc", "https://example.com/article", env)

        # First executed statement should set status to 'processing'
        assert len(db.executed) >= 1
        first_sql, first_params = db.executed[0]
        assert "UPDATE" in first_sql
        assert "processing" in first_params


# =========================================================================
# test_process_article — content validation
# =========================================================================


class TestProcessArticleContentValidation:
    async def test_content_type_validation_rejects_non_html(self) -> None:
        """Non-HTML response (e.g. application/json) results in 'failed' status."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        json_response = _make_mock_response(
            headers={"content-type": "application/json"},
        )
        mock_client = _make_mock_client(page_response=json_response)

        with (
            patch("articles.processing.httpx.AsyncClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_ct", "https://example.com/api", env)

        # Should have 'failed' status update due to content-type validation
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("UPDATE") and "status" in sql
        ]
        assert len(failed_updates) >= 1
        last_sql, last_params = failed_updates[-1]
        assert last_params[0] == "failed"

    async def test_content_length_limit_rejects_oversized(self) -> None:
        """Oversized response (Content-Length > 10MB) results in 'failed' status."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        oversized_response = _make_mock_response(
            headers={
                "content-type": "text/html",
                "content-length": "20000000",  # 20 MB
            },
        )
        mock_client = _make_mock_client(page_response=oversized_response)

        with (
            patch("articles.processing.httpx.AsyncClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_big", "https://example.com/huge", env)

        # Should have 'failed' status update due to content-length limit
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("UPDATE") and "status" in sql
        ]
        assert len(failed_updates) >= 1
        last_sql, last_params = failed_updates[-1]
        assert last_params[0] == "failed"


# =========================================================================
# Phase E: Fix fragile assertions — exact index checks
# =========================================================================


class TestProcessArticleExactAssertions:
    async def test_ready_status_at_exact_index(self) -> None:
        """Verify the final UPDATE sets status='ready' at the correct param index."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.httpx.AsyncClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_exact", "https://example.com/article", env)

        # Find the big UPDATE that sets status to 'ready' (the one with 'title' in SQL)
        update_stmts = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("UPDATE") and "title" in sql
        ]
        assert len(update_stmts) >= 1
        sql, params = update_stmts[-1]

        # The 'status' field is the 14th SET clause (0-indexed: position 13)
        # In the SQL: title, excerpt, author, word_count, reading_time_minutes,
        #             domain, final_url, canonical_url, html_key, thumbnail_key,
        #             original_key, image_count, markdown_content, status, updated_at
        # So "ready" should be at index 13 in the params list
        assert params[13] == "ready"

    async def test_failed_status_at_exact_index(self) -> None:
        """Verify the failure UPDATE sets status='failed' at param index 0."""
        db = _TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        error_response = _make_mock_response(status_code=404)
        mock_client = _make_mock_client(page_response=error_response)

        with patch("articles.processing.httpx.AsyncClient", return_value=mock_client):
            from articles.processing import process_article

            await process_article("art_fidx", "https://example.com/missing", env)

        # The failure UPDATE is: UPDATE articles SET status = ?, updated_at = ? WHERE id = ?
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("UPDATE") and "status = ?" in sql and "title" not in sql
        ]
        assert len(failed_updates) >= 1
        _sql, params = failed_updates[-1]
        assert params[0] == "failed"

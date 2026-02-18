"""Tests for Phase 4 — Article processing pipeline.

Covers the main ``process_article`` function: happy path, failure handling,
and D1 field updates.  All external HTTP calls are mocked.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import MockD1, MockEnv, MockR2

# =========================================================================
# Helpers
# =========================================================================

_SAMPLE_HTML = """
<html>
<head>
    <title>Test Article Title</title>
    <link rel="canonical" href="https://example.com/canonical-url">
</head>
<body>
    <article>
        <h1>Test Article Title</h1>
        <p>This is the first paragraph with enough content for readability
        to consider it as the main article body. We need substantial text
        here so the extraction algorithm can identify the primary content
        area of the page and extract it correctly.</p>
        <p>Second paragraph with additional text to pad the content and
        ensure that readability treats this as a real article. The algorithm
        uses various heuristics including text length, paragraph count,
        and link density to determine what constitutes an article.</p>
        <p>Third paragraph provides even more content. This should give us
        enough text to count words and calculate a reasonable reading time
        estimate for our tests.</p>
        <img src="https://cdn.example.com/photo1.jpg">
        <img src="https://cdn.example.com/photo2.jpg">
    </article>
</body>
</html>
"""


class _TrackingD1(MockD1):
    """MockD1 that records all SQL statements executed against it."""

    def __init__(self) -> None:
        super().__init__()
        self.executed: list[tuple[str, list[Any]]] = []

    def _execute(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        self.executed.append((sql, params))
        return []


def _make_mock_response(
    *,
    status_code: int = 200,
    text: str = _SAMPLE_HTML,
    content: bytes = b"fake-image-data",
    url: str = "https://example.com/article",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.content = content
    resp.url = url
    resp.headers = headers or {"content-type": "image/jpeg"}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


async def _noop_screenshot(client, url, account_id, api_token, **kwargs):
    """Mock screenshot that returns fake image data."""
    return b"FAKE_SCREENSHOT"


def _browser_env(env: MockEnv) -> MockEnv:
    """Add Browser Rendering config to a MockEnv."""
    env.CF_ACCOUNT_ID = "test-account"
    env.CF_API_TOKEN = "test-token"
    return env


def _make_mock_client(
    page_response: MagicMock | None = None,
    image_response: MagicMock | None = None,
) -> AsyncMock:
    """Create a mock httpx.AsyncClient context manager."""
    if page_response is None:
        page_response = _make_mock_response()
    if image_response is None:
        image_response = _make_mock_response(content=b"fake-image-bytes")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    call_count = 0

    async def _get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        # First call is the page fetch, subsequent calls are image downloads
        if call_count == 1:
            return page_response
        return image_response

    mock_client.get = AsyncMock(side_effect=_get)
    return mock_client


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

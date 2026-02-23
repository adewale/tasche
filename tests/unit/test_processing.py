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
    MockReadability,
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
            patch("articles.processing.HttpClient", return_value=mock_client),
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
            patch("articles.processing.HttpClient", return_value=mock_client),
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
            patch("articles.processing.HttpClient", return_value=mock_client),
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
            patch("articles.processing.HttpClient", return_value=mock_client),
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
            patch("articles.processing.HttpClient", return_value=mock_client),
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
            patch("articles.processing.HttpClient", return_value=mock_client),
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

        with patch("articles.processing.HttpClient", return_value=mock_client):
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
            patch("articles.processing.HttpClient", return_value=mock_client),
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

        with patch("articles.processing.HttpClient", return_value=mock_client):
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

        with patch("articles.processing.HttpClient", return_value=mock_client):
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
            patch("articles.processing.HttpClient", return_value=mock_client),
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
            patch("articles.processing.HttpClient", return_value=mock_client),
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
            patch("articles.processing.HttpClient", return_value=mock_client),
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
            patch("articles.processing.HttpClient", return_value=mock_client),
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


# =========================================================================
# test_process_article — image path rewriting
# =========================================================================


class TestProcessArticleImageRewriting:
    async def test_image_paths_rewritten_to_api_urls(self) -> None:
        """Image paths in stored HTML should be /api/articles/{id}/images/... not bare R2 keys."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_img_rewrite", "https://example.com/article", env)

        # Check the stored HTML in R2 for rewritten image paths
        html_key = "articles/art_img_rewrite/content.html"
        assert html_key in r2._store, "content.html should be stored in R2"

        stored_html = r2._store[html_key]
        if isinstance(stored_html, bytes):
            stored_html = stored_html.decode("utf-8")

        # The SAMPLE_HTML has images from https://cdn.example.com/photo1.jpg and photo2.jpg
        # After rewriting, those should become /api/articles/art_img_rewrite/images/{hash}.ext
        # They should NOT contain the original external URLs anymore
        assert "cdn.example.com/photo1.jpg" not in stored_html, (
            "Original image URL should be rewritten"
        )
        assert "/api/articles/art_img_rewrite/images/" in stored_html, (
            "Image paths should be rewritten to /api/articles/{id}/images/..."
        )

    async def test_canonical_url_stored_in_d1(self) -> None:
        """After processing, canonical_url from HTML is stored in the D1 UPDATE."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_canon", "https://example.com/article", env)

        # The SAMPLE_HTML has <link rel="canonical" href="https://example.com/canonical-url">
        # Find the big UPDATE statement
        update_stmts = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("UPDATE") and "canonical_url" in sql
        ]
        assert len(update_stmts) >= 1, "D1 UPDATE should include canonical_url"
        sql, params = update_stmts[-1]
        # canonical_url is the 8th parameter (0-indexed: 7)
        # SQL: title, excerpt, author, word_count, reading_time_minutes,
        #      domain, final_url, canonical_url, ...
        assert "https://example.com/canonical-url" in params, (
            f"canonical_url should be extracted from HTML. Params: {params}"
        )


    async def test_subsequent_duplicate_check_finds_canonical_url(self) -> None:
        """After processing stores canonical_url, check_duplicate should find it."""
        # First, simulate processing that stored canonical_url
        stored_article = {
            "id": "art_round",
            "created_at": "2025-01-01T00:00:00",
            "status": "ready",
            "original_url": "https://example.com/article",
            "final_url": "https://example.com/article",
            "canonical_url": "https://example.com/canonical-url",
        }

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                # This is check_duplicate: params = [user_id, url, url, url]
                submitted_url = params[1]
                if (
                    submitted_url == stored_article["original_url"]
                    or submitted_url == stored_article["final_url"]
                    or submitted_url == stored_article["canonical_url"]
                ):
                    return [stored_article]
            return []

        from src.articles.urls import check_duplicate
        from tests.conftest import MockD1

        db = MockD1(execute=execute)
        result = await check_duplicate(db, "user_001", "https://example.com/canonical-url")
        assert result is not None, "Duplicate check should find article via canonical_url"
        assert result["id"] == "art_round"


    async def test_image_paths_match_serving_endpoint_format(self) -> None:
        """Image src attributes should match GET /api/articles/{id}/images/{filename}."""
        import re

        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_imgfmt", "https://example.com/article", env)

        html_key = "articles/art_imgfmt/content.html"
        stored_html = r2._store[html_key]
        if isinstance(stored_html, bytes):
            stored_html = stored_html.decode("utf-8")

        # Find all image src attributes in the stored HTML
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(stored_html, "html.parser")
        img_srcs = [img.get("src", "") for img in soup.find_all("img")]

        # Each image src should match /api/articles/{article_id}/images/{filename}
        for src in img_srcs:
            if src:
                assert re.match(
                    r"^/api/articles/art_imgfmt/images/[a-f0-9]+\.\w+$", src
                ), (
                    f"Image src '{src}' does not match expected "
                    f"/api/articles/{{id}}/images/{{filename}} format"
                )


    async def test_process_article_with_empty_readability_output(self) -> None:
        """Processing should handle pages where readability extracts minimal content."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        # Page with very little article content but enough body text to pass JS-heavy check
        minimal_html = """
        <html>
        <head><title>Empty Article</title></head>
        <body>
            <nav>Lots of navigation here to make it past the JS heavy check.
            We need to pad this with enough text content so that the is_js_heavy
            heuristic does not trigger Browser Rendering. This is just navigation
            and boilerplate text that fills up the page but has no actual article
            content. More text here to reach 500 characters easily. And some more
            text to really pad this out enough. Almost there with the padding text.
            Just a few more words should be enough now.</nav>
            <p>Short article.</p>
        </body>
        </html>
        """
        page_response = _make_mock_response(text=minimal_html)
        mock_client = _make_mock_client(page_response=page_response)

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_empty", "https://example.com/empty", env)

        # The article should still complete (status = 'ready') even with minimal content
        ready_updates = [
            (sql, params)
            for sql, params in db.executed
            if "title" in sql and "ready" in str(params)
        ]
        assert len(ready_updates) >= 1, (
            "Article should still be marked ready even with minimal content. "
            f"SQL executed: {[sql for sql, _ in db.executed]}"
        )


class TestProcessArticleUserTitle:
    async def test_preserves_user_supplied_title(self) -> None:
        """When user provided a title at creation, processing should keep it."""
        user_title = "My Custom Title"

        def result_fn(sql, params):
            # The processing pipeline queries: SELECT title FROM articles WHERE id = ?
            if "SELECT title FROM articles" in sql:
                return [{"title": user_title}]
            return []

        db = _TrackingD1(result_fn=result_fn)
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_title", "https://example.com/article", env)

        # Find the big UPDATE statement
        update_stmts = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("UPDATE") and "title = ?" in sql and "canonical_url" in sql
        ]
        assert len(update_stmts) >= 1
        sql, params = update_stmts[-1]
        # title is the first param in the big UPDATE
        assert params[0] == user_title, (
            f"User-supplied title should be preserved. Got: {params[0]!r}"
        )


class TestProcessArticleWithNoCanonical:
    async def test_falls_back_to_final_url_when_no_canonical(self) -> None:
        """When the HTML has no canonical URL, canonical_url should equal final_url."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        html_no_canonical = """
        <html>
        <head><title>No Canonical</title></head>
        <body>
            <article>
                <h1>No Canonical URL</h1>
                <p>This page has no canonical link tag. The processing pipeline
                should fall back to using the final URL as the canonical URL.
                We need enough text here for readability to pick it up properly.</p>
                <p>Second paragraph to pad the content so the extraction works
                as expected by the readability algorithm.</p>
                <p>Third paragraph for good measure. More text to ensure that
                the content is substantial enough.</p>
            </article>
        </body>
        </html>
        """
        page_response = _make_mock_response(
            text=html_no_canonical,
            url="https://example.com/final-destination",
        )
        mock_client = _make_mock_client(page_response=page_response)

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_nocanon", "https://example.com/original", env)

        # Find the big UPDATE statement
        update_stmts = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("UPDATE") and "canonical_url" in sql and "title" in sql
        ]
        assert len(update_stmts) >= 1
        sql, params = update_stmts[-1]

        # The fields in order: title, excerpt, author, word_count, reading_time,
        #                       domain, final_url, canonical_url, ...
        # canonical_url is at index 7
        canonical_in_params = params[7]
        final_in_params = params[6]
        assert canonical_in_params == "https://example.com/final-destination", (
            f"canonical_url should fall back to final_url. Got: {canonical_in_params}"
        )
        assert final_in_params == "https://example.com/final-destination", (
            f"final_url should be the final destination URL. Got: {final_in_params}"
        )


class TestProcessArticleRelativeImages:
    async def test_relative_image_urls_are_silently_skipped(self) -> None:
        """Images with relative URLs should not crash processing."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        html_with_relative_imgs = """
        <html>
        <head><title>Relative Images</title></head>
        <body>
            <article>
                <h1>Article with Relative Images</h1>
                <p>This article has images with relative URLs that cannot be
                downloaded because there is no base URL context available during
                processing. The pipeline should handle this gracefully.</p>
                <p>More text to ensure readability picks up the content properly
                and treats this as a valid article for extraction.</p>
                <p>Third paragraph with additional content padding for the
                readability algorithm to work correctly.</p>
                <img src="/images/photo.jpg" />
                <img src="relative/path/image.png" />
            </article>
        </body>
        </html>
        """
        page_response = _make_mock_response(text=html_with_relative_imgs)

        # Mock client that fails on relative URLs but succeeds on the page fetch
        from unittest.mock import AsyncMock

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        async def _get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return page_response
            # Image URLs: simulate failure for relative URLs
            if url.startswith("/") or not url.startswith("http"):
                raise Exception(f"Cannot fetch relative URL: {url}")
            return _make_mock_response(
                content=b"fake-image-bytes",
                headers={"content-type": "image/jpeg"},
            )

        mock_client.get = AsyncMock(side_effect=_get)

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_relimg", "https://example.com/article", env)

        # Processing should still succeed (status = 'ready')
        ready_updates = [
            (sql, params)
            for sql, params in db.executed
            if "title" in sql and "ready" in str(params)
        ]
        assert len(ready_updates) >= 1, (
            "Article should be marked ready even when relative image downloads fail. "
            f"SQL executed: {[(sql[:60], params) for sql, params in db.executed]}"
        )


class TestProcessArticleSQLParamCounts:
    async def test_all_sql_statements_have_matching_param_counts(self) -> None:
        """Every SQL statement executed during processing has matching placeholder/param counts."""
        import re

        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_params", "https://example.com/article", env)

        # Every SQL statement should have matching placeholder count and param count
        for sql, params in db.executed:
            expected = len(re.findall(r"\?", sql))
            actual = len(params)
            assert expected == actual, (
                f"SQL placeholder/param mismatch: {expected} placeholders but {actual} params.\n"
                f"SQL: {sql!r}\n"
                f"Params: {params!r}"
            )


class TestProcessArticleSSRF:
    async def test_redirect_to_private_ip_is_blocked(self) -> None:
        """Processing should fail if the page redirects to a private IP."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        # Page response that claims to redirect to 127.0.0.1
        redirect_response = _make_mock_response(
            url="http://127.0.0.1:8080/secret",
        )
        mock_client = _make_mock_client(page_response=redirect_response)

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_ssrf", "https://example.com/redirect", env)

        # Should be marked as 'failed' due to SSRF check
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if "status" in sql and "failed" in str(params)
        ]
        assert len(failed_updates) >= 1, (
            "Article should be marked failed when redirect targets private IP"
        )


class TestProcessArticleExactAssertions:
    async def test_ready_status_at_exact_index(self) -> None:
        """Verify the final UPDATE sets status='ready' at the correct param index."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
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

        with patch("articles.processing.HttpClient", return_value=mock_client):
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


# =========================================================================
# test_process_article — Readability Service Binding integration
# =========================================================================


class TestProcessArticleReadability:
    async def test_uses_readability_when_available(self) -> None:
        """When env.READABILITY is present, it is used instead of BS4."""
        db = _TrackingD1()
        r2 = MockR2()
        readability = MockReadability(response={
            "title": "Readability Title",
            "html": "<p>Content from Readability.</p>",
            "excerpt": "Content from Readability.",
            "byline": "Readability Author",
        })
        env = _browser_env(MockEnv(db=db, content=r2, readability=readability))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_read", "https://example.com/article", env)

        # Verify Readability was called
        assert len(readability.calls) == 1
        assert readability.calls[0]["url"] == "https://example.com/article"

        # Verify metadata records readability as extraction method
        metadata_key = "articles/art_read/metadata.json"
        assert metadata_key in r2._store
        metadata = json.loads(r2._store[metadata_key].decode("utf-8"))
        assert metadata["extraction_method"] == "readability"

    async def test_falls_back_to_bs4_when_no_binding(self) -> None:
        """When env.READABILITY is None, BS4 extractor is used."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))  # No readability

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_bs4", "https://example.com/article", env)

        # Verify metadata records bs4 as extraction method
        metadata_key = "articles/art_bs4/metadata.json"
        assert metadata_key in r2._store
        metadata = json.loads(r2._store[metadata_key].decode("utf-8"))
        assert metadata["extraction_method"] == "bs4"

    async def test_falls_back_to_bs4_on_readability_error(self) -> None:
        """When Readability raises, fall back to BS4 instead of failing."""
        db = _TrackingD1()
        r2 = MockR2()
        readability = MockReadability()
        readability.parse = AsyncMock(side_effect=RuntimeError("Service unavailable"))
        env = _browser_env(MockEnv(db=db, content=r2, readability=readability))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_fallback", "https://example.com/article", env)

        # Should still succeed (status = ready) using BS4 fallback
        ready_updates = [
            (sql, params)
            for sql, params in db.executed
            if "status" in sql and "ready" in str(params)
        ]
        assert len(ready_updates) >= 1

        # Verify metadata records bs4 as extraction method
        metadata_key = "articles/art_fallback/metadata.json"
        assert metadata_key in r2._store
        metadata = json.loads(r2._store[metadata_key].decode("utf-8"))
        assert metadata["extraction_method"] == "bs4"

    async def test_falls_back_to_bs4_on_empty_readability_result(self) -> None:
        """When Readability returns empty html, fall back to BS4."""
        db = _TrackingD1()
        r2 = MockR2()
        readability = MockReadability(response={
            "title": "",
            "html": "",
            "excerpt": "",
            "byline": None,
        })
        env = _browser_env(MockEnv(db=db, content=r2, readability=readability))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_empty", "https://example.com/article", env)

        # Readability was called but returned empty, so BS4 should be used
        assert len(readability.calls) == 1

        metadata_key = "articles/art_empty/metadata.json"
        assert metadata_key in r2._store
        metadata = json.loads(r2._store[metadata_key].decode("utf-8"))
        assert metadata["extraction_method"] == "bs4"

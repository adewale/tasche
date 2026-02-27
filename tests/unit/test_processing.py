"""Tests for Phase 4 — Article processing pipeline.

Covers the main ``process_article`` function: happy path, failure handling,
and D1 field updates.  All external HTTP calls are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from tests.conftest import (
    MockEnv,
    MockQueue,
    MockR2,
    MockReadability,
    TrackingD1,
    _browser_env,
    _make_mock_http_fetch,
    _make_mock_response,
    _noop_screenshot,
    parse_update_params,
)

# =========================================================================
# test_process_article — happy path
# =========================================================================


class TestProcessArticleHappyPath:
    async def test_sets_status_to_ready(self) -> None:
        """On successful processing, article status is updated to 'ready'."""
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_002", "https://example.com/article", env)

        assert "articles/art_002/content.html" in r2._store

    async def test_does_not_store_content_md_in_r2(self) -> None:
        """Markdown is stored only in D1, not in R2."""
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_003", "https://example.com/article", env)

        assert "articles/art_003/content.md" not in r2._store

    async def test_stores_metadata_json_in_r2(self) -> None:
        """metadata.json is stored in R2 with correct article metadata."""
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)
        env.CF_ACCOUNT_ID = "test-account"
        env.CF_API_TOKEN = "test-token"

        mock_client = _make_mock_http_fetch()
        # Mock the screenshot function to return fake image data
        fake_thumb = b"THUMB_DATA"
        fake_fullpage = b"FULLPAGE_DATA"
        call_count = {"n": 0}

        async def _mock_screenshot(url, account_id, api_token, **kwargs):
            call_count["n"] += 1
            if kwargs.get("full_page"):
                return fake_fullpage
            return fake_thumb

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)
        env.CF_ACCOUNT_ID = "test-account"
        env.CF_API_TOKEN = "test-token"

        mock_client = _make_mock_http_fetch()

        async def _mock_screenshot(url, account_id, api_token, **kwargs):
            return b"SCREENSHOT_DATA"

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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

    async def test_succeeds_without_browser_rendering_config(self) -> None:
        """Without CF_ACCOUNT_ID/CF_API_TOKEN, processing still succeeds
        (Browser Rendering is optional — screenshots are skipped)."""
        db = TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)
        # No CF_ACCOUNT_ID or CF_API_TOKEN set

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
        ):
            from articles.processing import process_article

            await process_article("art_noss", "https://example.com/article", env)

        # Article should be marked as 'ready' (not failed)
        ready_updates = [
            (sql, params)
            for sql, params in db.executed
            if "status" in sql and "ready" in str(params)
        ]
        assert len(ready_updates) >= 1

    async def test_full_page_screenshot_failure_non_fatal(self) -> None:
        """If full-page screenshot fails, processing still succeeds."""
        db = TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)
        env.CF_ACCOUNT_ID = "test-account"
        env.CF_API_TOKEN = "test-token"

        mock_client = _make_mock_http_fetch()

        from articles.browser_rendering import BrowserRenderingError

        async def _mock_screenshot(url, account_id, api_token, **kwargs):
            if kwargs.get("full_page"):
                raise BrowserRenderingError("Timeout on full-page")
            return b"THUMB_DATA"

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        error_response = _make_mock_response(status_code=404)
        mock_client = _make_mock_http_fetch(page_response=error_response)

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
        ):
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
        db = TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        mock_fetch = AsyncMock(side_effect=Exception("Network error"))

        with (
            patch("articles.processing.http_fetch", mock_fetch),
            patch("articles.images.http_fetch", mock_fetch),
        ):
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
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        json_response = _make_mock_response(
            headers={"content-type": "application/json"},
        )
        mock_client = _make_mock_http_fetch(page_response=json_response)

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        oversized_response = _make_mock_response(
            headers={
                "content-type": "text/html",
                "content-length": "20000000",  # 20 MB
            },
        )
        mock_client = _make_mock_http_fetch(page_response=oversized_response)

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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

        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
                assert re.match(r"^/api/articles/art_imgfmt/images/[a-f0-9]+\.\w+$", src), (
                    f"Image src '{src}' does not match expected "
                    f"/api/articles/{{id}}/images/{{filename}} format"
                )

    async def test_process_article_with_empty_readability_output(self) -> None:
        """Processing should handle pages where readability extracts minimal content."""
        db = TrackingD1()
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
        mock_client = _make_mock_http_fetch(page_response=page_response)

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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

        db = TrackingD1(result_fn=result_fn)
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        fields = parse_update_params(sql, params)
        assert fields["title"] == user_title, (
            f"User-supplied title should be preserved. Got: {fields.get('title')!r}"
        )


class TestProcessArticleWithNoCanonical:
    async def test_falls_back_to_final_url_when_no_canonical(self) -> None:
        """When the HTML has no canonical URL, canonical_url should equal final_url."""
        db = TrackingD1()
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
        mock_client = _make_mock_http_fetch(page_response=page_response)

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        fields = parse_update_params(sql, params)

        assert fields["canonical_url"] == "https://example.com/final-destination", (
            f"canonical_url should fall back to final_url. Got: {fields.get('canonical_url')}"
        )
        assert fields["final_url"] == "https://example.com/final-destination", (
            f"final_url should be the final destination URL. Got: {fields.get('final_url')}"
        )


class TestProcessArticleRelativeImages:
    async def test_relative_image_urls_are_silently_skipped(self) -> None:
        """Images with relative URLs should not crash processing."""
        db = TrackingD1()
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

        # Mock http_fetch that fails on relative URLs but succeeds on the page fetch
        call_count = 0

        async def _mock_fetch(url, **kwargs):
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

        mock_fetch = AsyncMock(side_effect=_mock_fetch)

        with (
            patch("articles.processing.http_fetch", mock_fetch),
            patch("articles.images.http_fetch", mock_fetch),
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

        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        # Page response that claims to redirect to 127.0.0.1
        redirect_response = _make_mock_response(
            url="http://127.0.0.1:8080/secret",
        )
        mock_client = _make_mock_http_fetch(page_response=redirect_response)

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        fields = parse_update_params(sql, params)

        assert fields["status"] == "ready"

    async def test_failed_status_at_exact_index(self) -> None:
        """Verify the failure UPDATE sets status='failed' at param index 0."""
        db = TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        error_response = _make_mock_response(status_code=404)
        mock_client = _make_mock_http_fetch(page_response=error_response)

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
        ):
            from articles.processing import process_article

            await process_article("art_fidx", "https://example.com/missing", env)

        # The failure UPDATE is: UPDATE articles SET status = ?, updated_at = ? WHERE id = ?
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("UPDATE") and "status = ?" in sql and "title" not in sql
        ]
        assert len(failed_updates) >= 1
        sql, params = failed_updates[-1]
        fields = parse_update_params(sql, params)
        assert fields["status"] == "failed"


# =========================================================================
# test_process_article — Readability Service Binding integration
# =========================================================================


class TestProcessArticleReadability:
    async def test_uses_readability_when_available(self) -> None:
        """When env.READABILITY is present, it is used instead of BS4."""
        db = TrackingD1()
        r2 = MockR2()
        readability = MockReadability(
            response={
                "title": "Readability Title",
                "html": "<p>Content from Readability.</p>",
                "excerpt": "Content from Readability.",
                "byline": "Readability Author",
            }
        )
        env = _browser_env(MockEnv(db=db, content=r2, readability=readability))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))  # No readability

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        readability = MockReadability()
        readability.parse = AsyncMock(side_effect=RuntimeError("Service unavailable"))
        env = _browser_env(MockEnv(db=db, content=r2, readability=readability))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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
        db = TrackingD1()
        r2 = MockR2()
        readability = MockReadability(
            response={
                "title": "",
                "html": "",
                "excerpt": "",
                "byline": None,
            }
        )
        env = _browser_env(MockEnv(db=db, content=r2, readability=readability))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
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


# =========================================================================
# test_process_article — auto-TTS enqueue after processing
# =========================================================================


class TestProcessArticleAutoTTS:
    async def test_enqueues_tts_when_audio_status_pending(self) -> None:
        """After processing, TTS is auto-enqueued if audio_status is 'pending'."""

        def result_fn(sql, params):
            if "SELECT audio_status, user_id FROM articles" in sql:
                return [{"audio_status": "pending", "user_id": "user_001"}]
            return []

        db = TrackingD1(result_fn=result_fn)
        r2 = MockR2()
        queue = MockQueue()
        env = _browser_env(MockEnv(db=db, content=r2, article_queue=queue))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_tts", "https://example.com/article", env)

        # Verify TTS generation message was sent to the queue
        tts_messages = [m for m in queue.messages if m.get("type") == "tts_generation"]
        assert len(tts_messages) == 1
        assert tts_messages[0]["article_id"] == "art_tts"
        assert tts_messages[0]["user_id"] == "user_001"

    async def test_does_not_enqueue_tts_when_audio_status_not_pending(self) -> None:
        """After processing, TTS is NOT enqueued if audio_status is not 'pending'."""

        def result_fn(sql, params):
            if "SELECT audio_status, user_id FROM articles" in sql:
                return [{"audio_status": None, "user_id": "user_001"}]
            return []

        db = TrackingD1(result_fn=result_fn)
        r2 = MockR2()
        queue = MockQueue()
        env = _browser_env(MockEnv(db=db, content=r2, article_queue=queue))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_notts", "https://example.com/article", env)

        # No TTS messages should be in the queue
        tts_messages = [m for m in queue.messages if m.get("type") == "tts_generation"]
        assert len(tts_messages) == 0


# =========================================================================
# test_process_article — pre-supplied content (bookmarklet capture)
# =========================================================================


class TestProcessArticlePreSuppliedContent:
    async def test_uses_pre_supplied_html_from_r2(self) -> None:
        """When raw.html exists in R2, processing skips the HTTP fetch."""
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        # Pre-store raw HTML in R2 (as the bookmarklet would)
        pre_supplied_html = """
        <html>
        <head><title>Paywalled Article</title></head>
        <body>
            <article>
                <h1>Paywalled Article</h1>
                <p>This is the secret content behind the paywall. It was
                captured by the bookmarklet from the user's browser where they
                were already authenticated. The processing pipeline should use
                this pre-supplied HTML instead of fetching the page.</p>
                <p>Second paragraph with more detail about the paywalled topic.
                This paragraph provides additional context and analysis that
                is only available to subscribers.</p>
                <p>Third paragraph to ensure enough content for readability
                extraction to identify the main article body.</p>
            </article>
        </body>
        </html>
        """
        await r2.put("articles/art_presupplied/raw.html", pre_supplied_html)

        # Create a mock http_fetch that should NOT be called for the page fetch
        # (only for image downloads since content is pre-supplied)
        fetch_calls = []

        async def _mock_fetch(url, **kwargs):
            fetch_calls.append(url)
            return _make_mock_response(
                content=b"fake-image-bytes",
                headers={"content-type": "image/jpeg"},
            )

        mock_fetch = AsyncMock(side_effect=_mock_fetch)

        with (
            patch("articles.processing.http_fetch", mock_fetch),
            patch("articles.images.http_fetch", mock_fetch),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article(
                "art_presupplied",
                "https://example.com/paywalled",
                env,
            )

        # The page URL should NOT have been fetched (only image URLs, if any)
        page_fetches = [u for u in fetch_calls if u == "https://example.com/paywalled"]
        assert len(page_fetches) == 0, (
            "Processing should skip HTTP fetch when raw.html is pre-supplied"
        )

        # Article should still be marked as 'ready'
        ready_updates = [
            (sql, params)
            for sql, params in db.executed
            if "title" in sql and "ready" in str(params)
        ]
        assert len(ready_updates) >= 1

    async def test_pre_supplied_content_still_extracts_article(self) -> None:
        """Pre-supplied HTML is processed through the extraction pipeline."""
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        pre_supplied_html = """
        <html>
        <head>
            <title>Premium Article</title>
            <link rel="canonical" href="https://example.com/premium-canonical">
        </head>
        <body>
            <article>
                <h1>Premium Article</h1>
                <p>Premium exclusive content that was captured by the bookmarklet.
                This content is behind a paywall and cannot be fetched by the
                server directly. The extraction pipeline should still process
                it normally and extract the title, excerpt, and other metadata.</p>
                <p>Second paragraph with analysis and supporting details for the
                premium article content. More text to pad the content.</p>
                <p>Final paragraph wrapping up the exclusive premium content
                that subscribers get access to.</p>
            </article>
        </body>
        </html>
        """
        await r2.put("articles/art_premium/raw.html", pre_supplied_html)

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article(
                "art_premium",
                "https://example.com/premium",
                env,
            )

        # Verify content.html was stored in R2
        assert "articles/art_premium/content.html" in r2._store

        # Verify metadata was stored
        metadata_key = "articles/art_premium/metadata.json"
        assert metadata_key in r2._store
        metadata = json.loads(r2._store[metadata_key].decode("utf-8"))
        assert metadata["article_id"] == "art_premium"
        assert metadata["word_count"] > 0

    async def test_falls_back_to_fetch_when_no_raw_html(self) -> None:
        """When no raw.html exists in R2, the normal HTTP fetch path is used."""
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        # No raw.html pre-stored in R2
        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_nopre", "https://example.com/article", env)

        # Should have used the normal fetch path and stored content
        assert "articles/art_nopre/content.html" in r2._store

        # Article should be ready
        ready_updates = [
            (sql, params)
            for sql, params in db.executed
            if "title" in sql and "ready" in str(params)
        ]
        assert len(ready_updates) >= 1


# =========================================================================
# test_process_article — content parity between Save and Save Audio paths
# =========================================================================


class TestProcessArticleContentParity:
    """Verify process_article() produces identical content outputs regardless
    of whether audio_status is 'pending' (Save Audio) or None (plain Save).

    Both paths go through the same processing pipeline. These tests confirm
    the content extraction (HTML, markdown, metadata) happens identically.
    """

    async def test_processing_stores_html_regardless_of_audio_status(self) -> None:
        """content.html is stored in R2 for both audio_status=None and audio_status='pending'."""
        for audio_status in [None, "pending"]:

            def make_result_fn(status):
                def result_fn(sql, params):
                    if "SELECT audio_status" in sql:
                        return [{"audio_status": status, "user_id": "user_001"}]
                    return []

                return result_fn

            db = TrackingD1(result_fn=make_result_fn(audio_status))
            r2 = MockR2()
            queue = MockQueue()
            env = _browser_env(MockEnv(db=db, content=r2, article_queue=queue))

            mock_client = _make_mock_http_fetch()

            with (
                patch("articles.processing.http_fetch", mock_client),
                patch("articles.images.http_fetch", mock_client),
                patch("articles.processing.screenshot", side_effect=_noop_screenshot),
            ):
                from articles.processing import process_article

                art_id = f"art_html_{audio_status}"
                await process_article(art_id, "https://example.com/article", env)

            assert f"articles/{art_id}/content.html" in r2._store, (
                f"content.html missing in R2 when audio_status={audio_status!r}"
            )

    async def test_processing_stores_markdown_in_d1_regardless_of_audio_status(
        self,
    ) -> None:
        """The final UPDATE includes markdown_content for both Save and Save Audio paths."""
        for audio_status in [None, "pending"]:

            def make_result_fn(status):
                def result_fn(sql, params):
                    if "SELECT audio_status" in sql:
                        return [{"audio_status": status, "user_id": "user_001"}]
                    return []

                return result_fn

            db = TrackingD1(result_fn=make_result_fn(audio_status))
            r2 = MockR2()
            queue = MockQueue()
            env = _browser_env(MockEnv(db=db, content=r2, article_queue=queue))

            mock_client = _make_mock_http_fetch()

            with (
                patch("articles.processing.http_fetch", mock_client),
                patch("articles.images.http_fetch", mock_client),
                patch("articles.processing.screenshot", side_effect=_noop_screenshot),
            ):
                from articles.processing import process_article

                await process_article(
                    f"art_md_{audio_status}", "https://example.com/article", env
                )

            # The final UPDATE should include markdown_content
            md_updates = [
                (sql, params)
                for sql, params in db.executed
                if "markdown_content" in sql and "UPDATE" in sql
            ]
            assert len(md_updates) >= 1, (
                f"No UPDATE with markdown_content when audio_status={audio_status!r}"
            )

    async def test_processing_stores_metadata_json_regardless_of_audio_status(
        self,
    ) -> None:
        """metadata.json is stored in R2 for both Save and Save Audio paths."""
        for audio_status in [None, "pending"]:

            def make_result_fn(status):
                def result_fn(sql, params):
                    if "SELECT audio_status" in sql:
                        return [{"audio_status": status, "user_id": "user_001"}]
                    return []

                return result_fn

            db = TrackingD1(result_fn=make_result_fn(audio_status))
            r2 = MockR2()
            queue = MockQueue()
            env = _browser_env(MockEnv(db=db, content=r2, article_queue=queue))

            mock_client = _make_mock_http_fetch()

            with (
                patch("articles.processing.http_fetch", mock_client),
                patch("articles.images.http_fetch", mock_client),
                patch("articles.processing.screenshot", side_effect=_noop_screenshot),
            ):
                from articles.processing import process_article

                art_id = f"art_meta_{audio_status}"
                await process_article(art_id, "https://example.com/article", env)

            assert f"articles/{art_id}/metadata.json" in r2._store, (
                f"metadata.json missing in R2 when audio_status={audio_status!r}"
            )

    async def test_processing_with_audio_pending_stores_content_and_enqueues_tts(
        self,
    ) -> None:
        """When audio_status='pending', both content storage AND TTS enqueue happen."""

        def result_fn(sql, params):
            if "SELECT audio_status" in sql:
                return [{"audio_status": "pending", "user_id": "user_001"}]
            return []

        db = TrackingD1(result_fn=result_fn)
        r2 = MockR2()
        queue = MockQueue()
        env = _browser_env(MockEnv(db=db, content=r2, article_queue=queue))

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_both", "https://example.com/article", env)

        # Content stored in R2
        assert "articles/art_both/content.html" in r2._store
        assert "articles/art_both/metadata.json" in r2._store

        # Markdown stored in D1
        md_updates = [
            (sql, params)
            for sql, params in db.executed
            if "markdown_content" in sql and "UPDATE" in sql
        ]
        assert len(md_updates) >= 1

        # TTS enqueued
        tts_msgs = [m for m in queue.messages if m.get("type") == "tts_generation"]
        assert len(tts_msgs) == 1
        assert tts_msgs[0]["article_id"] == "art_both"

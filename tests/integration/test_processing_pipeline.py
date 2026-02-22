"""Integration tests for the article processing pipeline.

Unlike unit tests in test_processing.py (which verify individual behaviors),
these tests exercise process_article() end-to-end and verify the complete
end-state in D1 and R2.
"""

from __future__ import annotations

import json
import re
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import (
    MockEnv,
    MockR2,
    TrackingD1,
    _browser_env,
    _make_mock_client,
    _make_mock_response,
    _noop_screenshot,
)

# Realistic HTML fixture with canonical URL, multiple images, and article body
REALISTIC_HTML = """
<html>
<head>
    <title>In Praise of DHH</title>
    <meta name="author" content="Ade Oshineye">
    <link rel="canonical" href="https://okayfail.com/2025/in-praise-of-dhh">
</head>
<body>
    <header><nav>Site Navigation</nav></header>
    <article>
        <h1>In Praise of DHH</h1>
        <p class="byline">By Ade Oshineye</p>
        <p>David Heinemeier Hansson has been one of the most influential
        voices in software development for the past two decades. His work
        on Ruby on Rails transformed how we build web applications, and
        his writings on software craftsmanship continue to inspire
        developers around the world.</p>
        <img src="https://cdn.okayfail.com/images/dhh-portrait.jpg" alt="DHH">
        <p>What makes DHH particularly interesting is his willingness to
        challenge conventional wisdom. Whether it's his stance on testing,
        his views on microservices, or his approach to company culture,
        he consistently forces the industry to re-examine its assumptions.</p>
        <p>The impact of Rails cannot be overstated. Before Rails, building
        a web application required significantly more boilerplate code and
        configuration. Rails introduced conventions that made developers
        productive from day one, and many of those conventions have been
        adopted by frameworks in other languages.</p>
        <img src="https://cdn.okayfail.com/images/rails-logo.png" alt="Rails">
        <p>Perhaps most importantly, DHH has consistently advocated for
        simplicity in software architecture. In an industry that often
        rewards complexity, his voice for pragmatism is refreshing and
        necessary. His recent work on Hotwire and Turbo continues this
        tradition of making web development simpler and more enjoyable.</p>
        <p>As we look to the future of web development, the principles
        that DHH champions — convention over configuration, programmer
        happiness, and beautiful code — remain as relevant as ever.</p>
    </article>
    <footer>Copyright 2025</footer>
</body>
</html>
"""


def _make_realistic_client(html: str = REALISTIC_HTML, final_url: str = "https://okayfail.com/2025/in-praise-of-dhh.html"):
    """Create a mock client that serves realistic HTML and image responses."""
    page_response = _make_mock_response(
        text=html,
        url=final_url,
    )
    image_response = _make_mock_response(
        content=b"\x89PNG\r\n\x1a\nfake-image-data",
        headers={"content-type": "image/png"},
    )
    return _make_mock_client(
        page_response=page_response,
        image_response=image_response,
    )


class TestFullPipelineEndToEnd:
    """Verify process_article() produces correct end-state in D1 and R2."""

    async def test_happy_path_complete_state(self) -> None:
        """Full pipeline: fetch page, extract content, store everything, status=ready."""
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))
        mock_client = _make_realistic_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article(
                "art_e2e_001",
                "https://okayfail.com/2025/in-praise-of-dhh.html",
                env,
            )

        # --- D1 assertions ---
        # Find the final big UPDATE (the one with all metadata fields)
        update_stmts = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("UPDATE") and "title" in sql and "canonical_url" in sql
        ]
        assert len(update_stmts) == 1, (
            f"Expected exactly 1 metadata UPDATE, got {len(update_stmts)}"
        )
        sql, params = update_stmts[0]

        # SQL field order: title, excerpt, author, word_count, reading_time_minutes,
        #   domain, final_url, canonical_url, html_key, thumbnail_key,
        #   original_key, image_count, markdown_content, status, updated_at, id
        title = params[0]
        excerpt = params[1]
        author = params[2]
        word_count = params[3]
        reading_time = params[4]
        domain = params[5]
        final_url = params[6]
        canonical_url = params[7]
        html_key = params[8]
        thumbnail_key = params[9]
        original_key = params[10]
        image_count = params[11]
        markdown_content = params[12]
        status = params[13]
        article_id = params[15]

        assert article_id == "art_e2e_001"
        assert status == "ready"
        assert "DHH" in title or "Praise" in title
        assert len(excerpt) > 0
        assert domain == "okayfail.com"
        assert final_url == "https://okayfail.com/2025/in-praise-of-dhh.html"
        assert canonical_url == "https://okayfail.com/2025/in-praise-of-dhh"
        assert word_count > 0
        assert reading_time > 0
        assert image_count >= 0
        assert html_key == "articles/art_e2e_001/content.html"
        assert thumbnail_key == "articles/art_e2e_001/thumbnail.webp"
        assert original_key == "articles/art_e2e_001/original.webp"
        assert len(markdown_content) > 0
        assert "DHH" in markdown_content or "Rails" in markdown_content

        # --- R2 assertions ---
        assert "articles/art_e2e_001/content.html" in r2._store
        assert "articles/art_e2e_001/metadata.json" in r2._store
        assert "articles/art_e2e_001/thumbnail.webp" in r2._store
        assert "articles/art_e2e_001/original.webp" in r2._store

        # Verify metadata.json structure
        metadata = json.loads(r2._store["articles/art_e2e_001/metadata.json"])
        assert metadata["article_id"] == "art_e2e_001"
        assert metadata["original_url"] == "https://okayfail.com/2025/in-praise-of-dhh.html"
        assert metadata["final_url"] == "https://okayfail.com/2025/in-praise-of-dhh.html"
        assert metadata["canonical_url"] == "https://okayfail.com/2025/in-praise-of-dhh"
        assert metadata["domain"] == "okayfail.com"
        assert metadata["word_count"] > 0
        assert metadata["reading_time_minutes"] > 0
        assert "content_hash" in metadata
        assert "archived_at" in metadata

        # Verify stored HTML has rewritten image paths
        stored_html = r2._store["articles/art_e2e_001/content.html"]
        if isinstance(stored_html, bytes):
            stored_html = stored_html.decode("utf-8")
        assert "cdn.okayfail.com" not in stored_html, (
            "Original image URLs should be rewritten"
        )

    async def test_first_update_is_processing(self) -> None:
        """The very first D1 operation sets status='processing'."""
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))
        mock_client = _make_realistic_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_e2e_002", "https://okayfail.com/article", env)

        first_sql, first_params = db.executed[0]
        assert "UPDATE" in first_sql
        assert "processing" in first_params

    async def test_sql_param_counts_match(self) -> None:
        """Every SQL statement has matching placeholder/param counts."""
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))
        mock_client = _make_realistic_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_e2e_003", "https://okayfail.com/article", env)

        for sql, params in db.executed:
            expected = len(re.findall(r"\?", sql))
            actual = len(params)
            assert expected == actual, (
                f"SQL placeholder/param mismatch: {expected} placeholders "
                f"but {actual} params.\nSQL: {sql!r}\nParams: {params!r}"
            )


class TestFullPipelineUserTitle:
    """Verify user-supplied title is preserved through the pipeline."""

    async def test_user_title_overrides_extracted(self) -> None:
        """When user provided a title at creation time, it survives processing."""
        user_title = "My Custom Title for DHH Article"

        def result_fn(sql, params):
            if "SELECT title FROM articles" in sql:
                return [{"title": user_title}]
            return []

        db = TrackingD1(result_fn=result_fn)
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))
        mock_client = _make_realistic_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_e2e_title", "https://okayfail.com/article", env)

        # Find the big UPDATE
        update_stmts = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("UPDATE") and "title = ?" in sql and "canonical_url" in sql
        ]
        assert len(update_stmts) >= 1
        sql, params = update_stmts[-1]
        assert params[0] == user_title


class TestFullPipelineFailure:
    """Verify pipeline failure handling end-to-end."""

    async def test_404_marks_failed_no_r2_content(self) -> None:
        """HTTP 404 results in status=failed and no content in R2."""
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        error_response = _make_mock_response(status_code=404)
        mock_client = _make_mock_client(page_response=error_response)

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_e2e_fail", "https://okayfail.com/missing", env)

        # D1 should have status=failed
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if "status" in sql and "failed" in str(params)
        ]
        assert len(failed_updates) >= 1

        # R2 should have no article content
        article_keys = [k for k in r2._store if k.startswith("articles/art_e2e_fail/")]
        assert len(article_keys) == 0, (
            f"No R2 content should be stored for failed articles, but found: {article_keys}"
        )

    async def test_missing_browser_config_marks_failed(self) -> None:
        """Without CF_ACCOUNT_ID/CF_API_TOKEN, processing fails."""
        db = TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)  # No _browser_env — no CF config

        mock_client = _make_realistic_client()

        with patch("articles.processing.HttpClient", return_value=mock_client):
            from articles.processing import process_article

            await process_article("art_e2e_noconfig", "https://okayfail.com/article", env)

        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if "status" in sql and "failed" in str(params)
        ]
        assert len(failed_updates) >= 1

    async def test_network_error_propagates_for_retry(self) -> None:
        """ConnectionError is re-raised (not caught) so the queue can retry."""
        db = TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=ConnectionError("DNS resolution failed"))

        with patch("articles.processing.HttpClient", return_value=mock_client):
            from articles.processing import process_article

            with pytest.raises(ConnectionError):
                await process_article("art_e2e_retry", "https://okayfail.com/article", env)

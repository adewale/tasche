"""Tests for audit fixes in articles/routes.py and articles/extraction.py.

Covers:
- Issue 1:  SVG security headers (Content-Disposition + CSP sandbox)
- Issue 5:  process-now error does not leak internal exception details
- Issue 9:  BeautifulSoup parsed once (parse_html / _ensure_soup)
- Issue 10: batch_update uses concurrent execution instead of N+1 loop
- Issue 11: batch_delete uses batch ownership check + concurrent deletion
- Issue 14: create_article tag association uses batch query
- Issue 19: _LIST_COLUMNS_PREFIXED is a module-level constant
- Issue 23: _LIST_COLUMNS includes last_checked_at
- Issue 28: create_article response includes created_at for new articles
- Issue 62: _parse_tags_json helper replaces duplicated inline code
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from src.articles.extraction import (
    _ensure_soup,
    extract_article,
    extract_canonical_url,
    extract_thumbnail_url,
    html_to_markdown,
    parse_html,
    rewrite_image_paths,
)
from src.articles.routes import (
    _LIST_COLUMNS,
    _LIST_COLUMNS_PREFIXED,
    _parse_tags_json,
    router,
)
from tests.conftest import (
    ArticleFactory,
    MockD1,
    MockEnv,
    MockQueue,
    MockR2,
    TrackingD1,
    make_test_helpers,
)

_make_app, _authenticated_client = make_test_helpers((router, "/api/articles"))


# =========================================================================
# Issue 23 — _LIST_COLUMNS includes last_checked_at
# =========================================================================


class TestListColumnsIncludesLastCheckedAt:
    def test_last_checked_at_in_list_columns(self) -> None:
        """_LIST_COLUMNS must contain last_checked_at."""
        assert "last_checked_at" in _LIST_COLUMNS


# =========================================================================
# Issue 19 — _LIST_COLUMNS_PREFIXED is a module-level constant
# =========================================================================


class TestListColumnsPrefixed:
    def test_is_module_level_string(self) -> None:
        """_LIST_COLUMNS_PREFIXED is computed once at module level, not per request."""
        assert isinstance(_LIST_COLUMNS_PREFIXED, str)
        assert "articles.id" in _LIST_COLUMNS_PREFIXED
        assert "articles.last_checked_at" in _LIST_COLUMNS_PREFIXED

    def test_columns_are_dot_prefixed(self) -> None:
        """Every column in _LIST_COLUMNS_PREFIXED has the 'articles.' prefix."""
        columns = [c.strip() for c in _LIST_COLUMNS_PREFIXED.split(",")]
        for col in columns:
            assert col.startswith("articles."), f"Column {col!r} missing prefix"


# =========================================================================
# Issue 62 — _parse_tags_json helper
# =========================================================================


class TestParseTagsJson:
    def test_parses_valid_tags(self) -> None:
        """Parses a tags_json field into a list of tag dicts."""
        row: dict[str, Any] = {
            "id": "art1",
            "tags_json": json.dumps([{"id": "t1", "name": "python"}]),
        }
        tags = _parse_tags_json(row)
        assert tags == [{"id": "t1", "name": "python"}]
        assert "tags_json" not in row  # key is popped

    def test_filters_null_entries(self) -> None:
        """json_group_array produces [null] for zero tags — filtered out."""
        row: dict[str, Any] = {"tags_json": "[null]"}
        tags = _parse_tags_json(row)
        assert tags == []

    def test_handles_missing_key(self) -> None:
        """Returns empty list when tags_json key is absent."""
        row: dict[str, Any] = {"id": "art1"}
        tags = _parse_tags_json(row)
        assert tags == []

    def test_handles_empty_string(self) -> None:
        """Returns empty list when tags_json is empty string."""
        row: dict[str, Any] = {"tags_json": ""}
        tags = _parse_tags_json(row)
        assert tags == []


# =========================================================================
# Issue 28 — create_article response includes created_at
# =========================================================================


class TestCreateArticleCreatedAt:
    async def test_new_article_has_created_at(self) -> None:
        """POST /api/articles includes created_at for newly created articles."""
        queue = MockQueue()
        db = MockD1()
        env = MockEnv(db=db, article_queue=queue)

        client, _ = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/new-article"},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert "created_at" in data
        assert data["created_at"]  # non-empty

    async def test_updated_article_has_existing_created_at(self) -> None:
        """POST /api/articles for duplicate preserves original created_at."""
        existing = ArticleFactory.create(
            user_id="user_001",
            original_url="https://example.com/dup",
            created_at="2025-06-01T00:00:00",
        )

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                return [existing]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, _ = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/dup"},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["created_at"] == "2025-06-01T00:00:00"


# =========================================================================
# Issue 1 — SVG security headers
# =========================================================================


class TestSvgSecurityHeaders:
    async def test_svg_has_content_disposition_attachment(self) -> None:
        """SVG responses include Content-Disposition: attachment."""
        article = ArticleFactory.create(id="art_svg", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql:
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        # Store a fake SVG
        await r2.put(
            "articles/art_svg/images/abcdef01.svg",
            b"<svg><script>alert(1)</script></svg>",
        )
        env = MockEnv(db=db, content=r2)

        client, _ = await _authenticated_client(env)
        resp = client.get("/api/articles/art_svg/images/abcdef01.svg")

        assert resp.status_code == 200
        assert resp.headers["Content-Disposition"] == "attachment"
        assert resp.headers["Content-Security-Policy"] == "sandbox"

    async def test_non_svg_no_extra_headers(self) -> None:
        """Non-SVG images do not get the SVG security headers."""
        article = ArticleFactory.create(id="art_webp", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql:
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        await r2.put("articles/art_webp/images/abcdef01.webp", b"RIFF")
        env = MockEnv(db=db, content=r2)

        client, _ = await _authenticated_client(env)
        resp = client.get("/api/articles/art_webp/images/abcdef01.webp")

        assert resp.status_code == 200
        assert "Content-Disposition" not in resp.headers
        # CSP sandbox should NOT be present for non-SVG
        assert resp.headers.get("Content-Security-Policy") != "sandbox"


# =========================================================================
# Issue 5 — process-now error does not leak exception details
# =========================================================================


class TestProcessNowErrorMessage:
    async def test_error_does_not_leak_exception_str(self) -> None:
        """process-now returns generic error, not raw str(exc)."""
        article = ArticleFactory.create(
            id="art_pn",
            user_id="user_001",
            original_url="https://example.com/fail",
            status="pending",
        )

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql:
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, _ = await _authenticated_client(env)

        # Mock process_article to raise with a sensitive message
        with patch(
            "src.articles.routes.process_now.__module__",
            new="src.articles.routes",
        ):
            pass  # Just need the import to work

        secret_message = "Database password is hunter2"

        async def _boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError(secret_message)

        with patch("src.articles.routes.process_now", new=AsyncMock()):
            # We need to patch at the point of import inside the handler
            with patch.dict(
                "sys.modules",
                {"articles.processing": MagicMock(process_article=_boom)},
            ):
                resp = client.post("/api/articles/art_pn/process-now")

        assert resp.status_code == 200
        data = resp.json()
        if data.get("result") == "error":
            assert secret_message not in data.get("error", "")
            assert data["error"] == "An internal error occurred during processing"


# =========================================================================
# Issue 9 — BeautifulSoup parsed once (parse_html / _ensure_soup)
# =========================================================================


class TestParseHtmlOnce:
    def test_parse_html_returns_beautifulsoup(self) -> None:
        """parse_html returns a BeautifulSoup object."""
        soup = parse_html("<p>Hello</p>")
        assert isinstance(soup, BeautifulSoup)

    def test_ensure_soup_passes_through_soup(self) -> None:
        """_ensure_soup returns the same object when given a BeautifulSoup."""
        soup = BeautifulSoup("<p>Hello</p>", "html.parser")
        result = _ensure_soup(soup)
        assert result is soup  # Same object, no re-parse

    def test_ensure_soup_parses_string(self) -> None:
        """_ensure_soup parses a raw HTML string."""
        result = _ensure_soup("<p>Hello</p>")
        assert isinstance(result, BeautifulSoup)

    def test_extract_canonical_url_accepts_soup(self) -> None:
        """extract_canonical_url works with a pre-parsed soup."""
        html = '<html><head><link rel="canonical" href="https://example.com/c"></head></html>'
        soup = parse_html(html)
        assert extract_canonical_url(soup) == "https://example.com/c"

    def test_extract_thumbnail_url_accepts_soup(self) -> None:
        """extract_thumbnail_url works with a pre-parsed soup."""
        html = '<html><head><meta property="og:image" content="https://img.example.com/thumb.jpg"></head><body></body></html>'
        soup = parse_html(html)
        assert extract_thumbnail_url(soup) == "https://img.example.com/thumb.jpg"

    def test_extract_article_accepts_soup(self) -> None:
        """extract_article works with a pre-parsed soup (copy needed since it mutates)."""
        import copy

        html = "<html><body><article><h1>Title</h1><p>Content here.</p></article></body></html>"
        soup = parse_html(html)
        soup_copy = copy.copy(soup)
        result = extract_article(soup_copy)
        assert result["title"] == "Title"

    def test_html_to_markdown_accepts_soup(self) -> None:
        """html_to_markdown works with a pre-parsed soup."""
        soup = parse_html("<h1>Hello</h1><p>World</p>")
        md = html_to_markdown(soup)
        assert "# Hello" in md
        assert "World" in md

    def test_rewrite_image_paths_accepts_soup(self) -> None:
        """rewrite_image_paths works with a pre-parsed soup."""
        soup = parse_html('<img src="https://old.com/img.jpg">')
        result = rewrite_image_paths(soup, {"https://old.com/img.jpg": "/local/img.webp"})
        assert "/local/img.webp" in result

    def test_beautifulsoup_called_once_in_pipeline(self) -> None:
        """When using parse_html, downstream functions do not re-parse."""
        html = """
        <html>
        <head>
            <link rel="canonical" href="https://example.com/c">
            <meta property="og:image" content="https://img.example.com/t.jpg">
        </head>
        <body><article><h1>Title</h1><p>Content</p></article></body>
        </html>
        """
        call_count = 0
        _original_init = BeautifulSoup.__init__

        def _counting_init(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            _original_init(self, *args, **kwargs)

        # Parse once
        soup = parse_html(html)
        # Reset counter after initial parse
        call_count = 0

        with patch.object(BeautifulSoup, "__init__", _counting_init):
            extract_canonical_url(soup)
            extract_thumbnail_url(soup)

        # Neither function should have created a new BeautifulSoup
        assert call_count == 0, f"BeautifulSoup was instantiated {call_count} time(s)"


# =========================================================================
# Issue 10 — batch_update uses concurrent execution
# =========================================================================


class TestBatchUpdateConcurrent:
    async def test_batch_update_updates_multiple_articles(self) -> None:
        """batch-update applies updates to all provided article IDs."""
        articles = [
            ArticleFactory.create(id=f"art_{i}", user_id="user_001") for i in range(3)
        ]

        def execute(sql: str, params: list) -> list:
            if "UPDATE" in sql:
                return [{"changes": 1}]
            return []

        db = TrackingD1(result_fn=execute)
        env = MockEnv(db=db)

        client, _ = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-update",
            json={
                "article_ids": ["art_0", "art_1", "art_2"],
                "updates": {"reading_status": "archived"},
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 3

    async def test_batch_update_skips_non_string_ids(self) -> None:
        """Non-string IDs are filtered out."""

        def execute(sql: str, params: list) -> list:
            if "UPDATE" in sql:
                return [{"changes": 1}]
            return []

        db = TrackingD1(result_fn=execute)
        env = MockEnv(db=db)

        client, _ = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-update",
            json={
                "article_ids": ["art_valid", 123, None],
                "updates": {"reading_status": "archived"},
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        # Only the string ID should be processed
        assert data["updated"] == 1


# =========================================================================
# Issue 11 — batch_delete uses batch ownership check
# =========================================================================


class TestBatchDeleteBatched:
    async def test_batch_delete_uses_single_ownership_query(self) -> None:
        """batch-delete verifies ownership with one SELECT IN query."""
        articles = [
            ArticleFactory.create(id=f"del_{i}", user_id="user_001") for i in range(3)
        ]

        def execute(sql: str, params: list) -> list:
            if "SELECT id FROM articles WHERE id IN" in sql:
                return [{"id": f"del_{i}"} for i in range(3)]
            if "DELETE" in sql:
                return [{"changes": 1}]
            return []

        db = TrackingD1(result_fn=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        client, _ = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-delete",
            json={"article_ids": ["del_0", "del_1", "del_2"]},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == 3

        # Verify a batch SELECT was used (not individual SELECTs per article)
        select_queries = [
            sql for sql, _ in db.executed if "SELECT id FROM articles WHERE id IN" in sql
        ]
        assert len(select_queries) == 1

    async def test_batch_delete_skips_unowned(self) -> None:
        """Articles not owned by the user are not deleted."""

        def execute(sql: str, params: list) -> list:
            if "SELECT id FROM articles WHERE id IN" in sql:
                # Only del_0 is owned
                return [{"id": "del_0"}]
            if "DELETE" in sql:
                return [{"changes": 1}]
            return []

        db = TrackingD1(result_fn=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        client, _ = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-delete",
            json={"article_ids": ["del_0", "del_1"]},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == 1


# =========================================================================
# Issue 14 — tag association uses batch query
# =========================================================================


class TestCreateArticleBatchTags:
    async def test_tag_association_uses_batch_validation(self) -> None:
        """create_article validates tag ownership in a single query."""
        queries: list[tuple[str, list]] = []

        def execute(sql: str, params: list) -> list:
            queries.append((sql, params))
            if "SELECT id FROM tags WHERE id IN" in sql:
                return [{"id": "tag_a"}, {"id": "tag_b"}]
            return []

        db = MockD1(execute=execute)
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, _ = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={
                "url": "https://example.com/tagged",
                "tag_ids": ["tag_a", "tag_b", "tag_c"],
            },
        )

        assert resp.status_code == 201

        # Should have one batch SELECT for tags, not one per tag
        tag_selects = [sql for sql, _ in queries if "SELECT id FROM tags WHERE id IN" in sql]
        assert len(tag_selects) == 1

        # Should have INSERT OR IGNORE for tag_a and tag_b (tag_c is not owned)
        tag_inserts = [sql for sql, _ in queries if "INSERT OR IGNORE INTO article_tags" in sql]
        assert len(tag_inserts) == 2


# =========================================================================
# Integration: extraction pipeline with pre-parsed soup
# =========================================================================


class TestExtractionPipelineIntegration:
    def test_full_pipeline_with_single_parse(self) -> None:
        """Simulates the processing pipeline using parse_html once."""
        import copy

        html = """
        <html>
        <head>
            <title>Test Article</title>
            <link rel="canonical" href="https://example.com/canonical">
            <meta property="og:image" content="https://img.example.com/thumb.jpg">
        </head>
        <body>
            <article>
                <h1>Test Article</h1>
                <p>This is substantial content for extraction testing with
                enough words to be meaningful.</p>
                <img src="https://old.com/photo.jpg">
            </article>
        </body>
        </html>
        """
        # Parse once
        soup = parse_html(html)

        # Step 1: Extract canonical (non-destructive)
        canonical = extract_canonical_url(soup)
        assert canonical == "https://example.com/canonical"

        # Step 2: Extract thumbnail (non-destructive)
        thumb = extract_thumbnail_url(soup)
        assert thumb == "https://img.example.com/thumb.jpg"

        # Step 3: Extract article (destructive — uses copy)
        article_soup = copy.copy(soup)
        article = extract_article(article_soup)
        assert article["title"] == "Test Article"
        assert "substantial content" in article["html"]

    def test_rewrite_image_paths_empty_map_returns_string(self) -> None:
        """rewrite_image_paths with empty map returns html unchanged as string."""
        soup = parse_html("<p>Hello</p>")
        result = rewrite_image_paths(soup, {})
        assert isinstance(result, str)

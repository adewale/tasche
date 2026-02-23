"""Tests for data export endpoints (src/articles/export.py).

Covers JSON and HTML bookmark format exports, including tag association,
empty exports, and authentication enforcement.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.articles.export import router
from src.auth.session import COOKIE_NAME
from tests.conftest import (
    ArticleFactory,
    MockD1,
    MockEnv,
    _make_test_app,
)
from tests.conftest import (
    _authenticated_client as _authenticated_client_base,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROUTERS = ((router, "/api/export"),)


def _make_app(env):
    return _make_test_app(env, *_ROUTERS)


async def _authenticated_client(env: MockEnv) -> tuple[TestClient, str]:
    return await _authenticated_client_base(env, *_ROUTERS)


# ---------------------------------------------------------------------------
# GET /api/export/json — JSON export
# ---------------------------------------------------------------------------


class TestExportJson:
    async def test_exports_articles_as_json(self) -> None:
        """GET /api/export/json returns a JSON array of articles with tags."""
        articles = [
            ArticleFactory.create(
                id="art_1",
                user_id="user_001",
                title="First Article",
                original_url="https://example.com/first",
            ),
            ArticleFactory.create(
                id="art_2",
                user_id="user_001",
                title="Second Article",
                original_url="https://example.com/second",
            ),
        ]

        tag_rows = [
            {"article_id": "art_1", "name": "python"},
            {"article_id": "art_1", "name": "tech"},
            {"article_id": "art_2", "name": "news"},
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM articles WHERE user_id" in sql:
                return articles
            if "FROM article_tags" in sql:
                return tag_rows
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/export/json",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]
        assert "attachment" in resp.headers["content-disposition"]
        assert "tasche-export-" in resp.headers["content-disposition"]
        assert ".json" in resp.headers["content-disposition"]

        data = resp.json()
        assert len(data) == 2
        assert data[0]["id"] == "art_1"
        assert data[0]["title"] == "First Article"
        assert data[0]["tags"] == ["python", "tech"]
        assert data[1]["tags"] == ["news"]

    async def test_exports_empty_when_no_articles(self) -> None:
        """GET /api/export/json returns an empty array when user has no articles."""

        def execute(sql: str, params: list) -> list:
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/export/json",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data == []

    async def test_articles_without_tags_have_empty_tags_list(self) -> None:
        """GET /api/export/json gives articles without tags an empty tags list."""
        articles = [
            ArticleFactory.create(
                id="art_notag",
                user_id="user_001",
                title="No Tags Article",
            ),
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM articles WHERE user_id" in sql:
                return articles
            if "FROM article_tags" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/export/json",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["tags"] == []

    def test_requires_auth(self) -> None:
        """GET /api/export/json returns 401 without auth."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/export/json")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/export/html — Netscape bookmark HTML export
# ---------------------------------------------------------------------------


class TestExportHtml:
    async def test_exports_articles_as_netscape_bookmarks(self) -> None:
        """GET /api/export/html returns Netscape bookmark format HTML."""
        articles = [
            ArticleFactory.create(
                id="art_bm1",
                user_id="user_001",
                title="Bookmark Article",
                original_url="https://example.com/bookmark",
                excerpt="This is an excerpt.",
                created_at="2025-06-15T12:00:00",
            ),
        ]

        tag_rows = [
            {"article_id": "art_bm1", "name": "saved"},
            {"article_id": "art_bm1", "name": "tech"},
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM articles WHERE user_id" in sql:
                return articles
            if "FROM article_tags" in sql:
                return tag_rows
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/export/html",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "attachment" in resp.headers["content-disposition"]
        assert "tasche-export-" in resp.headers["content-disposition"]
        assert ".html" in resp.headers["content-disposition"]

        body = resp.text
        assert "<!DOCTYPE NETSCAPE-Bookmark-file-1>" in body
        assert "<TITLE>Tasche Export</TITLE>" in body
        assert "<H1>Tasche Export</H1>" in body
        assert "https://example.com/bookmark" in body
        assert "Bookmark Article" in body
        assert "This is an excerpt." in body
        assert 'TAGS="saved,tech"' in body
        assert "ADD_DATE=" in body

    async def test_exports_empty_bookmark_file(self) -> None:
        """GET /api/export/html returns valid bookmark HTML with no entries."""

        def execute(sql: str, params: list) -> list:
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/export/html",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        body = resp.text
        assert "<!DOCTYPE NETSCAPE-Bookmark-file-1>" in body
        assert "<DL><p>" in body
        assert "</DL><p>" in body

    async def test_escapes_html_in_title_and_url(self) -> None:
        """GET /api/export/html escapes HTML special characters."""
        articles = [
            ArticleFactory.create(
                id="art_esc",
                user_id="user_001",
                title='Title with <script> & "quotes"',
                original_url="https://example.com/page?a=1&b=2",
                excerpt="Excerpt with <b>bold</b>",
            ),
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM articles WHERE user_id" in sql:
                return articles
            if "FROM article_tags" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/export/html",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        body = resp.text
        assert "&lt;script&gt;" in body
        assert "&amp;" in body
        assert "&quot;quotes&quot;" in body
        assert "&lt;b&gt;bold&lt;/b&gt;" in body

    async def test_omits_tags_attr_when_no_tags(self) -> None:
        """GET /api/export/html omits TAGS attribute when article has no tags."""
        articles = [
            ArticleFactory.create(
                id="art_notag_bm",
                user_id="user_001",
                title="No Tags",
                original_url="https://example.com/notag",
            ),
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM articles WHERE user_id" in sql:
                return articles
            if "FROM article_tags" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/export/html",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        body = resp.text
        assert "TAGS=" not in body

    async def test_uses_url_as_title_fallback(self) -> None:
        """GET /api/export/html uses the URL as title when title is None."""
        articles = [
            ArticleFactory.create(
                id="art_notitle",
                user_id="user_001",
                title=None,
                original_url="https://example.com/no-title",
            ),
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM articles WHERE user_id" in sql:
                return articles
            if "FROM article_tags" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/export/html",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        body = resp.text
        assert "https://example.com/no-title</A>" in body

    def test_requires_auth(self) -> None:
        """GET /api/export/html returns 401 without auth."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/export/html")
        assert resp.status_code == 401

"""Tests for Phase 5 — FTS5 Search API (src/search/routes.py).

Covers full-text search across articles, authentication enforcement,
empty query handling, and pagination.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from src.auth.session import COOKIE_NAME
from src.search.routes import router
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

_ROUTERS = ((router, "/api/search"),)


def _make_app(env):
    return _make_test_app(env, *_ROUTERS)


async def _authenticated_client(env: MockEnv) -> tuple[TestClient, str]:
    return await _authenticated_client_base(env, *_ROUTERS)


# ---------------------------------------------------------------------------
# GET /api/search?q=... — Full-text search
# ---------------------------------------------------------------------------


class TestSearchArticles:
    async def test_returns_matching_articles(self) -> None:
        """GET /api/search?q=python returns articles matching the query."""
        articles = [
            ArticleFactory.create(
                user_id="user_001",
                title="Learn Python",
                excerpt="A guide to Python programming",
            ),
            ArticleFactory.create(
                user_id="user_001",
                title="Python Tips",
                excerpt="Advanced Python techniques",
            ),
        ]

        def execute(sql: str, params: list) -> list:
            if "articles_fts MATCH ?" in sql:
                return articles
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/search?q=python",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["title"] == "Learn Python"
        assert data[1]["title"] == "Python Tips"

    async def test_filters_by_user_id(self) -> None:
        """GET /api/search?q=test ensures user_id is in the SQL query."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/search?q=test",
            cookies={COOKIE_NAME: session_id},
        )

        # Verify the query includes user_id filter
        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        assert "user_id = ?" in select_calls[0]["sql"]
        assert "user_001" in select_calls[0]["params"]

    async def test_uses_fts5_match(self) -> None:
        """GET /api/search?q=cloudflare uses FTS5 MATCH in the SQL query."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/search?q=cloudflare",
            cookies={COOKIE_NAME: session_id},
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        sql = select_calls[0]["sql"]
        assert "articles_fts MATCH ?" in sql
        assert "INNER JOIN articles_fts" in sql
        # After FTS5 sanitization, query is wrapped in quotes: "cloudflare"
        params = select_calls[0]["params"]
        assert any("cloudflare" in str(p) for p in params)

    async def test_rejects_empty_query(self) -> None:
        """GET /api/search?q= returns 422 for an empty search query."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.get(
            "/api/search?q=",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422
        assert "required" in resp.json()["detail"].lower()

    async def test_rejects_missing_query(self) -> None:
        """GET /api/search (no q param) returns 422."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.get(
            "/api/search",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422

    async def test_returns_empty_list_for_no_matches(self) -> None:
        """GET /api/search?q=nonexistent returns an empty list."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/search?q=nonexistent",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_respects_limit_and_offset(self) -> None:
        """GET /api/search?q=test&limit=5&offset=10 passes pagination params."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/search?q=test&limit=5&offset=10",
            cookies={COOKIE_NAME: session_id},
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        params = select_calls[0]["params"]
        # params should be [query, user_id, limit, offset]
        assert 5 in params
        assert 10 in params


# ---------------------------------------------------------------------------
# Authentication enforcement
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# FTS5 query sanitization
# ---------------------------------------------------------------------------


class TestFts5Sanitization:
    async def test_wraps_words_in_quotes(self) -> None:
        """Multi-word query becomes quoted tokens: "hello" "world"."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/search?q=hello+world",
            cookies={COOKIE_NAME: session_id},
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        query_param = select_calls[0]["params"][0]
        assert query_param == '"hello" "world"'

    async def test_strips_fts5_operators(self) -> None:
        """FTS5 operator characters are stripped from search queries."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/search?q=test*+OR+evil",
            cookies={COOKIE_NAME: session_id},
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        query_param = select_calls[0]["params"][0]
        # * and OR are stripped/quoted as literals
        assert '"test"' in query_param
        assert '"OR"' in query_param
        assert '"evil"' in query_param
        assert "*" not in query_param

    async def test_rejects_query_with_only_operators(self) -> None:
        """Query that becomes empty after sanitization returns 422."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.get(
            "/api/search?q=***",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422


class TestFts5SpecialCharsSanitized:
    async def test_quotes_do_not_crash(self) -> None:
        """Query with double quotes does not crash FTS5."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            '/api/search?q="hello"',
            cookies={COOKIE_NAME: session_id},
        )

        # Should either return 200 (empty results) or 422 (sanitized away)
        assert resp.status_code in (200, 422)

    async def test_parentheses_do_not_crash(self) -> None:
        """Query with parentheses does not crash FTS5."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/search?q=(test)+AND+(other)",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code in (200, 422)

    async def test_asterisks_and_special_chars_sanitized(self) -> None:
        """Query with *, (, ), " mixed with words is sanitized properly."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/search?q=test*+hello(world)",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        # Verify the query was sanitized (no raw special chars in the MATCH param)
        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        if select_calls:
            query_param = select_calls[0]["params"][0]
            assert "*" not in query_param
            assert "(" not in query_param
            assert ")" not in query_param


class TestSearchAuthRequired:
    def test_returns_401_without_auth(self) -> None:
        """GET /api/search?q=test returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/search?q=test")
        assert resp.status_code == 401

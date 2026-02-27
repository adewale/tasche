"""Tests for Phase 5 — FTS5 Search API (src/search/routes.py).

Covers full-text search across articles, authentication enforcement,
empty query handling, pagination, FTS5 sanitization, and result shape.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from src.search.routes import _sanitize_fts5_query, router
from tests.conftest import (
    ArticleFactory,
    MockD1,
    MockEnv,
    make_test_helpers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_make_app, _authenticated_client = make_test_helpers((router, "/api/search"))


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
        )

        assert resp.status_code == 422
        assert "required" in resp.json()["detail"].lower()

    async def test_rejects_missing_query(self) -> None:
        """GET /api/search (no q param) returns 422."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.get(
            "/api/search",
        )

        assert resp.status_code == 422

    async def test_returns_empty_list_for_no_matches(self) -> None:
        """GET /api/search?q=nonexistent returns an empty list."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/search?q=nonexistent",
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
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        sql = select_calls[0]["sql"]
        params = select_calls[0]["params"]
        # SQL should have LIMIT and OFFSET clauses
        assert "LIMIT ?" in sql
        assert "OFFSET ?" in sql
        # params are [query, user_id, limit, offset] — verify by position
        assert params[-2] == 5, f"limit should be 5, got {params[-2]}"
        assert params[-1] == 10, f"offset should be 10, got {params[-1]}"


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
        )

        assert resp.status_code == 200
        # Verify the query was sanitized (no raw special chars in the MATCH param)
        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        if select_calls:
            query_param = select_calls[0]["params"][0]
            assert "*" not in query_param
            assert "(" not in query_param
            assert ")" not in query_param


class TestSearchResultShape:
    async def test_result_includes_expected_fields(self) -> None:
        """Search results include all fields needed by the frontend."""
        article = ArticleFactory.create(
            user_id="user_001",
            title="Result Article",
            domain="example.com",
            excerpt="A matching excerpt",
        )

        db = MockD1(execute=lambda sql, params: [article] if "MATCH" in sql else [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/search?q=result",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        result = data[0]
        # Frontend Search.jsx uses: a.id, a.title, a.original_url, a.domain,
        # a.created_at, a.excerpt
        assert "id" in result
        assert "title" in result
        assert "original_url" in result
        assert "domain" in result
        assert "created_at" in result
        assert "excerpt" in result

    async def test_returns_list_not_wrapped_object(self) -> None:
        """Search results are a plain array, not wrapped in {articles: [...]}."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/search?q=anything",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


class TestSearchSingleWord:
    async def test_single_word_query_is_quoted(self) -> None:
        """A single-word query is wrapped in double quotes."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/search?q=python",
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        query_param = select_calls[0]["params"][0]
        assert query_param == '"python"'


class TestSearchUnicode:
    async def test_unicode_query_is_accepted(self) -> None:
        """Search with unicode characters works correctly."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/search?q=caf%C3%A9",
        )

        assert resp.status_code == 200
        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        query_param = select_calls[0]["params"][0]
        assert '"caf' in query_param


class TestSearchWhitespace:
    async def test_whitespace_only_query_returns_422(self) -> None:
        """GET /api/search?q=   (whitespace only) returns 422."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.get(
            "/api/search?q=%20%20%20",
        )

        assert resp.status_code == 422


class TestSearchDefaultPagination:
    async def test_defaults_to_limit_20_offset_0(self) -> None:
        """GET /api/search?q=test uses limit=20, offset=0 by default."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/search?q=test",
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        params = select_calls[0]["params"]
        # params: [query, user_id, limit, offset]
        assert params[-2] == 20
        assert params[-1] == 0


class TestSearchSqlStructure:
    async def test_uses_inner_join_with_fts5(self) -> None:
        """Search query uses INNER JOIN with articles_fts, not subquery."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/search?q=test",
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        sql = select_calls[0]["sql"]
        assert "INNER JOIN articles_fts ON articles.rowid = articles_fts.rowid" in sql
        assert "ORDER BY articles_fts.rank" in sql

    async def test_prefixes_columns_with_articles_table(self) -> None:
        """Search query prefixes all columns with 'articles.' to avoid ambiguity."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/search?q=test",
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        sql = select_calls[0]["sql"]
        # All selected columns should be prefixed with "articles."
        assert "articles.id" in sql
        assert "articles.title" in sql
        assert "articles.domain" in sql
        assert "articles.excerpt" in sql


class TestSearchAuthRequired:
    def test_returns_401_without_auth(self) -> None:
        """GET /api/search?q=test returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/search?q=test")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Direct unit tests for _sanitize_fts5_query
# ---------------------------------------------------------------------------


class TestSanitizeFts5Query:
    def test_simple_word(self) -> None:
        """Single word becomes quoted."""
        assert _sanitize_fts5_query("hello") == '"hello"'

    def test_multiple_words(self) -> None:
        """Multiple words each become quoted."""
        assert _sanitize_fts5_query("hello world") == '"hello" "world"'

    def test_strips_asterisk(self) -> None:
        """Asterisks are removed from tokens."""
        assert _sanitize_fts5_query("test*") == '"test"'

    def test_strips_plus(self) -> None:
        """Plus signs are removed from tokens."""
        assert _sanitize_fts5_query("+test") == '"test"'

    def test_strips_minus(self) -> None:
        """Minus signs are removed from tokens."""
        assert _sanitize_fts5_query("-test") == '"test"'

    def test_strips_caret(self) -> None:
        """Caret is removed from tokens."""
        assert _sanitize_fts5_query("^test") == '"test"'

    def test_strips_parentheses(self) -> None:
        """Parentheses are removed from tokens."""
        assert _sanitize_fts5_query("(test)") == '"test"'

    def test_strips_curly_braces(self) -> None:
        """Curly braces are removed from tokens."""
        assert _sanitize_fts5_query("{test}") == '"test"'

    def test_strips_square_brackets(self) -> None:
        """Square brackets are removed from tokens."""
        assert _sanitize_fts5_query("[test]") == '"test"'

    def test_strips_pipe(self) -> None:
        """Pipe is removed from tokens."""
        assert _sanitize_fts5_query("test|other") == '"testother"'

    def test_strips_double_quotes(self) -> None:
        """Double quotes are removed from tokens."""
        result = _sanitize_fts5_query('"test"')
        assert result == '"test"'

    def test_strips_backslash(self) -> None:
        """Backslash is removed from tokens."""
        assert _sanitize_fts5_query("test\\n") == '"testn"'

    def test_strips_colon(self) -> None:
        """Colon is removed from tokens."""
        assert _sanitize_fts5_query("title:test") == '"titletest"'

    def test_empty_after_stripping(self) -> None:
        """Token that becomes empty after stripping is excluded."""
        assert _sanitize_fts5_query("***") == ""

    def test_mixed_clean_and_dirty(self) -> None:
        """Mix of clean words and operator-only tokens."""
        result = _sanitize_fts5_query("hello *** world")
        assert result == '"hello" "world"'

    def test_preserves_numbers(self) -> None:
        """Numbers are preserved in tokens."""
        assert _sanitize_fts5_query("python3") == '"python3"'

    def test_preserves_unicode(self) -> None:
        """Unicode characters are preserved in tokens."""
        result = _sanitize_fts5_query("cafe\u0301")
        assert "cafe" in result

    def test_fts5_operators_quoted_as_literals(self) -> None:
        """FTS5 operator words (OR, AND, NOT) are quoted as literals."""
        result = _sanitize_fts5_query("OR AND NOT")
        assert result == '"OR" "AND" "NOT"'

    def test_empty_string(self) -> None:
        """Empty string produces empty result."""
        assert _sanitize_fts5_query("") == ""

    def test_whitespace_only(self) -> None:
        """Whitespace-only string produces empty result."""
        assert _sanitize_fts5_query("   ") == ""

    def test_near_operator_quoted(self) -> None:
        """NEAR operator is quoted as a literal."""
        result = _sanitize_fts5_query("NEAR test")
        assert '"NEAR"' in result
        assert '"test"' in result

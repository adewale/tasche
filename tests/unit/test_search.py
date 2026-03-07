"""Tests for Phase 5 — FTS5 Search API (src/search/routes.py).

Covers full-text search across articles, authentication enforcement,
empty query handling, pagination, FTS5 sanitization, and result shape.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from articles.routes import _LIST_COLUMNS
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
        assert "ORDER BY bm25(articles_fts, 10.0, 5.0, 1.0)" in sql

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


class TestBm25ColumnWeights:
    """Verify bm25() column weights boost title > excerpt > content."""

    async def _get_search_sql(self) -> str:
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get("/api/search?q=test")

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        return select_calls[0]["sql"]

    async def test_uses_bm25_not_default_rank(self) -> None:
        """Search uses bm25() function, not the default articles_fts.rank."""
        sql = await self._get_search_sql()
        assert "bm25(" in sql
        assert "articles_fts.rank" not in sql

    async def test_title_weight_exceeds_excerpt_weight(self) -> None:
        """Title weight (10.0) is higher than excerpt weight (5.0)."""
        import re

        sql = await self._get_search_sql()
        match = re.search(r"bm25\(articles_fts,\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)\)", sql)
        assert match, f"bm25() call not found in SQL: {sql}"
        title_w, excerpt_w, content_w = (float(match.group(i)) for i in (1, 2, 3))
        assert title_w > excerpt_w, f"title weight {title_w} should exceed excerpt {excerpt_w}"

    async def test_excerpt_weight_exceeds_content_weight(self) -> None:
        """Excerpt weight (5.0) is higher than content weight (1.0)."""
        import re

        sql = await self._get_search_sql()
        match = re.search(r"bm25\(articles_fts,\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)\)", sql)
        assert match, f"bm25() call not found in SQL: {sql}"
        title_w, excerpt_w, content_w = (float(match.group(i)) for i in (1, 2, 3))
        assert excerpt_w > content_w, (
            f"excerpt weight {excerpt_w} should exceed content weight {content_w}"
        )

    async def test_three_weights_match_fts5_columns(self) -> None:
        """bm25() has exactly 3 weights matching FTS5 columns."""
        import re

        sql = await self._get_search_sql()
        match = re.search(r"bm25\(articles_fts,\s*([\d.,\s]+)\)", sql)
        assert match, f"bm25() call not found in SQL: {sql}"
        weights = [w.strip() for w in match.group(1).split(",")]
        assert len(weights) == 3, f"Expected 3 weights for 3 FTS5 columns, got {len(weights)}"


# ---------------------------------------------------------------------------
# SQLite-backed integration tests — run the actual search SQL against a real
# FTS5 index to verify bm25() validity and ranking behavior.
# ---------------------------------------------------------------------------


def _create_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with articles + FTS5 tables and triggers."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE articles (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            original_url TEXT NOT NULL,
            final_url TEXT,
            canonical_url TEXT,
            domain TEXT,
            title TEXT,
            excerpt TEXT,
            author TEXT,
            word_count INTEGER,
            reading_time_minutes INTEGER,
            image_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ready',
            reading_status TEXT DEFAULT 'unread',
            is_favorite INTEGER DEFAULT 0,
            audio_key TEXT,
            audio_duration_seconds INTEGER,
            audio_status TEXT DEFAULT NULL,
            html_key TEXT,
            thumbnail_key TEXT,
            original_key TEXT,
            markdown_content TEXT,
            original_status TEXT DEFAULT 'unknown',
            last_checked_at TEXT DEFAULT NULL,
            scroll_position REAL DEFAULT 0,
            reading_progress REAL DEFAULT 0,
            created_at TEXT DEFAULT '2026-01-01T00:00:00.000+00:00',
            updated_at TEXT DEFAULT '2026-01-01T00:00:00.000+00:00'
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE articles_fts USING fts5(
            title, excerpt, markdown_content,
            content=articles, content_rowid=rowid
        )
    """)
    # Content-sync triggers (same as 0001_initial.sql)
    conn.execute("""
        CREATE TRIGGER articles_fts_ai AFTER INSERT ON articles BEGIN
            INSERT INTO articles_fts(rowid, title, excerpt, markdown_content)
            VALUES (new.rowid, new.title, new.excerpt, new.markdown_content);
        END
    """)
    conn.execute("""
        CREATE TRIGGER articles_fts_au AFTER UPDATE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, title, excerpt, markdown_content)
            VALUES ('delete', old.rowid, old.title, old.excerpt, old.markdown_content);
            INSERT INTO articles_fts(rowid, title, excerpt, markdown_content)
            VALUES (new.rowid, new.title, new.excerpt, new.markdown_content);
        END
    """)
    return conn


def _insert_article(
    conn: sqlite3.Connection,
    *,
    id: str,
    title: str = "",
    excerpt: str = "",
    markdown_content: str = "",
    user_id: str = "user_001",
) -> None:
    conn.execute(
        """INSERT INTO articles (id, user_id, original_url, title, excerpt, markdown_content)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (id, user_id, f"https://example.com/{id}", title, excerpt, markdown_content),
    )


def _search(conn: sqlite3.Connection, query: str, user_id: str = "user_001") -> list[dict]:
    """Run the same SQL the search endpoint generates against real SQLite."""
    prefixed = ", ".join(f"articles.{c.strip()}" for c in _LIST_COLUMNS.split(","))
    sql = (
        f"SELECT {prefixed} FROM articles "
        "INNER JOIN articles_fts ON articles.rowid = articles_fts.rowid "
        "WHERE articles_fts MATCH ? AND articles.user_id = ? "
        "ORDER BY bm25(articles_fts, 10.0, 5.0, 1.0) "
        "LIMIT ? OFFSET ?"
    )
    rows = conn.execute(sql, (f'"{query}"', user_id, 20, 0)).fetchall()
    return [dict(r) for r in rows]


class TestBm25SqliteIntegration:
    """Run the search SQL against a real SQLite FTS5 index."""

    def test_bm25_query_is_valid_sql(self) -> None:
        """The bm25() ORDER BY clause is valid SQLite syntax."""
        conn = _create_test_db()
        _insert_article(conn, id="a1", title="Test article")
        # Should not raise — proves the SQL is valid
        results = _search(conn, "test")
        assert len(results) == 1
        assert results[0]["id"] == "a1"

    def test_title_match_ranks_above_content_match(self) -> None:
        """An article with the query in its title ranks above one with it only in content."""
        conn = _create_test_db()
        _insert_article(
            conn,
            id="content_only",
            title="Unrelated Topic",
            excerpt="Nothing here",
            markdown_content="This article discusses python programming at length",
        )
        _insert_article(
            conn,
            id="title_match",
            title="Python Programming Guide",
            excerpt="Not relevant",
            markdown_content="Some other content entirely",
        )
        results = _search(conn, "python")
        assert len(results) == 2
        assert results[0]["id"] == "title_match", (
            f"Title match should rank first, got: {[r['id'] for r in results]}"
        )

    def test_excerpt_match_ranks_above_content_match(self) -> None:
        """An article with the query in its excerpt ranks above one with it only in content."""
        conn = _create_test_db()
        _insert_article(
            conn,
            id="content_only",
            title="Unrelated",
            excerpt="Nothing relevant",
            markdown_content="This covers rust language features in detail",
        )
        _insert_article(
            conn,
            id="excerpt_match",
            title="Unrelated",
            excerpt="A comprehensive guide to rust programming",
            markdown_content="Other content",
        )
        results = _search(conn, "rust")
        assert len(results) == 2
        assert results[0]["id"] == "excerpt_match", (
            f"Excerpt match should rank first, got: {[r['id'] for r in results]}"
        )

    def test_title_match_ranks_above_excerpt_match(self) -> None:
        """Title match (10x weight) outranks excerpt match (5x weight)."""
        conn = _create_test_db()
        _insert_article(
            conn,
            id="excerpt_only",
            title="Unrelated Topic",
            excerpt="Learn everything about kubernetes orchestration",
            markdown_content="Other content",
        )
        _insert_article(
            conn,
            id="title_match",
            title="Kubernetes Deep Dive",
            excerpt="Not relevant at all",
            markdown_content="Other content",
        )
        results = _search(conn, "kubernetes")
        assert len(results) == 2
        assert results[0]["id"] == "title_match", (
            f"Title match should rank above excerpt match, got: {[r['id'] for r in results]}"
        )

    def test_filters_by_user_id(self) -> None:
        """Search results only include articles for the queried user."""
        conn = _create_test_db()
        _insert_article(conn, id="mine", title="My Python Article", user_id="user_001")
        _insert_article(conn, id="theirs", title="Their Python Article", user_id="user_002")
        results = _search(conn, "python", user_id="user_001")
        assert len(results) == 1
        assert results[0]["id"] == "mine"

    def test_no_matches_returns_empty(self) -> None:
        """Search for a term that doesn't exist returns an empty list."""
        conn = _create_test_db()
        _insert_article(conn, id="a1", title="Something else")
        results = _search(conn, "nonexistent")
        assert results == []

    def test_result_contains_expected_columns(self) -> None:
        """Search results include all columns from _LIST_COLUMNS."""
        conn = _create_test_db()
        _insert_article(conn, id="a1", title="Column Check")
        conn.execute("UPDATE articles SET domain = 'example.com' WHERE id = 'a1'")
        results = _search(conn, "column")
        assert len(results) == 1
        row = results[0]
        for col in _LIST_COLUMNS.split(","):
            col = col.strip()
            assert col in row, f"Missing column {col} in search result"

    @pytest.mark.parametrize(
        "query",
        ["python", "hello world", "café", "test123"],
        ids=["simple", "multi_word", "unicode", "alphanumeric"],
    )
    def test_various_queries_execute_without_error(self, query: str) -> None:
        """Various query patterns don't cause SQLite errors."""
        conn = _create_test_db()
        _insert_article(conn, id="a1", title="Python hello world café test123")
        safe_q = _sanitize_fts5_query(query)
        if safe_q:
            _search_raw(conn, safe_q)


def _search_raw(conn: sqlite3.Connection, fts_query: str, user_id: str = "user_001") -> list:
    """Run search with a pre-sanitized FTS5 query string."""
    prefixed = ", ".join(f"articles.{c.strip()}" for c in _LIST_COLUMNS.split(","))
    sql = (
        f"SELECT {prefixed} FROM articles "
        "INNER JOIN articles_fts ON articles.rowid = articles_fts.rowid "
        "WHERE articles_fts MATCH ? AND articles.user_id = ? "
        "ORDER BY bm25(articles_fts, 10.0, 5.0, 1.0) "
        "LIMIT ? OFFSET ?"
    )
    return conn.execute(sql, (fts_query, user_id, 20, 0)).fetchall()


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

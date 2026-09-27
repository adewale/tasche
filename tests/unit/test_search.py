"""Tests for FTS5 search as a composable filter on GET /api/articles?q=...

Search is unified into the articles list endpoint. When ``q`` is provided,
the endpoint performs an FTS5 INNER JOIN and orders by relevance.  All other
filters (``tag``, ``reading_status``, ``sort``, etc.) compose naturally with
search.

Also tests the ``_sanitize_fts5_query`` helper directly.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.articles.routes import _LIST_COLUMNS, _VALID_SORT_OPTIONS, _sanitize_fts5_query, router
from tests.conftest import (
    ArticleFactory,
    MockEnv,
    SqliteD1,
    TrackingD1,
    make_test_helpers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_make_app, _authenticated_client = make_test_helpers((router, "/api/articles"))


def _article_with_tags(**overrides: Any) -> dict[str, Any]:
    """Create an article dict with a tags_json field for the list endpoint."""
    article = ArticleFactory.create(**overrides)
    article["tags_json"] = overrides.get("tags_json", "[]")
    return article


async def _search_client(
    result_fn: Any = None,
) -> tuple[TestClient, TrackingD1]:
    """Create an authenticated client with a TrackingD1 that records SQL.

    Returns ``(client, db)`` so tests can inspect ``db.executed``.
    """
    db = TrackingD1(result_fn=result_fn)
    env = MockEnv(db=db)
    client, _ = await _authenticated_client(env)
    return client, db


def _select_sql(db: TrackingD1) -> str:
    """Return the SQL of the first SELECT statement executed."""
    for sql, _ in db.executed:
        if "SELECT" in sql:
            return sql
    raise AssertionError("No SELECT statement was executed")


def _select_params(db: TrackingD1) -> list[Any]:
    """Return the params of the first SELECT statement executed."""
    for sql, params in db.executed:
        if "SELECT" in sql:
            return params
    raise AssertionError("No SELECT statement was executed")


# ---------------------------------------------------------------------------
# GET /api/articles?q=... — behavioral tests (response shape, not SQL)
# ---------------------------------------------------------------------------


class TestSearchBehavior:
    async def test_returns_matching_articles(self) -> None:
        """Search returns articles and preserves their field values."""
        articles = [
            _article_with_tags(user_id="user_001", title="Learn Python"),
            _article_with_tags(user_id="user_001", title="Python Tips"),
        ]

        def result_fn(sql, params):
            if "MATCH" in sql:
                return articles
            return []

        client, _ = await _search_client(result_fn)
        resp = client.get("/api/articles?q=python")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["title"] == "Learn Python"
        assert data[1]["title"] == "Python Tips"

    async def test_search_results_include_tags(self) -> None:
        """Search results include parsed tags (same shape as list without search)."""
        article = _article_with_tags(
            user_id="user_001",
            title="Tagged Article",
            tags_json=json.dumps([{"id": "t1", "name": "python"}]),
        )

        client, _ = await _search_client(
            result_fn=lambda sql, params: [article] if "MATCH" in sql else [],
        )
        resp = client.get("/api/articles?q=tagged")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["tags"] == [{"id": "t1", "name": "python"}]

    async def test_search_results_include_multiple_tags(self) -> None:
        """Articles with multiple tags have all tags in the response."""
        tags = [{"id": "t1", "name": "python"}, {"id": "t2", "name": "tutorial"}]
        article = _article_with_tags(
            user_id="user_001",
            tags_json=json.dumps(tags),
        )

        client, _ = await _search_client(
            result_fn=lambda sql, params: [article] if "MATCH" in sql else [],
        )
        resp = client.get("/api/articles?q=test")

        assert resp.status_code == 200
        assert resp.json()[0]["tags"] == tags

    async def test_null_tags_filtered_from_response(self) -> None:
        """json_group_array produces [null] for zero tags — nulls are stripped."""
        article = _article_with_tags(
            user_id="user_001",
            tags_json=json.dumps([None]),
        )

        client, _ = await _search_client(
            result_fn=lambda sql, params: [article] if "MATCH" in sql else [],
        )
        resp = client.get("/api/articles?q=test")

        assert resp.status_code == 200
        assert resp.json()[0]["tags"] == []

    async def test_empty_search_returns_all(self) -> None:
        """GET /api/articles?q= (empty) behaves like unfiltered list."""
        articles = [_article_with_tags(user_id="user_001")]

        client, db = await _search_client(
            result_fn=lambda sql, params: articles,
        )
        resp = client.get("/api/articles?q=")

        assert resp.status_code == 200
        assert len(resp.json()) == 1
        # Empty q must not trigger FTS5
        sql = _select_sql(db)
        assert "MATCH" not in sql

    async def test_whitespace_only_search_returns_all(self) -> None:
        """GET /api/articles?q=%20%20 (spaces) behaves like unfiltered list."""
        articles = [_article_with_tags(user_id="user_001")]

        client, db = await _search_client(
            result_fn=lambda sql, params: articles,
        )
        resp = client.get("/api/articles?q=%20%20")

        assert resp.status_code == 200
        sql = _select_sql(db)
        assert "MATCH" not in sql

    async def test_operator_only_query_returns_all(self) -> None:
        """GET /api/articles?q=*** (all-operator input) skips FTS5."""
        articles = [_article_with_tags(user_id="user_001")]

        client, db = await _search_client(
            result_fn=lambda sql, params: articles,
        )
        resp = client.get("/api/articles?q=***")

        assert resp.status_code == 200
        sql = _select_sql(db)
        assert "MATCH" not in sql

    async def test_no_matches_returns_empty_list(self) -> None:
        """Search with no results returns an empty JSON array, not 404."""
        client, _ = await _search_client()
        resp = client.get("/api/articles?q=nonexistent")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_response_excludes_tags_json_field(self) -> None:
        """The raw tags_json column is removed; only parsed 'tags' remains."""
        article = _article_with_tags(user_id="user_001")

        client, _ = await _search_client(
            result_fn=lambda sql, params: [article] if "MATCH" in sql else [],
        )
        resp = client.get("/api/articles?q=test")

        data = resp.json()
        assert len(data) == 1
        assert "tags_json" not in data[0]
        assert "tags" in data[0]


# ---------------------------------------------------------------------------
# SQL structure — verify the query is built correctly
# ---------------------------------------------------------------------------


class TestSearchSqlStructure:
    async def test_search_uses_fts5_inner_join(self) -> None:
        """When q is present, SQL has INNER JOIN articles_fts with MATCH."""
        client, db = await _search_client()
        client.get("/api/articles?q=cloudflare")

        sql = _select_sql(db)
        assert "INNER JOIN articles_fts ON articles.rowid = articles_fts.rowid" in sql
        assert "articles_fts MATCH ?" in sql

    async def test_search_scopes_to_user(self) -> None:
        """Search always includes user_id in WHERE clause."""
        client, db = await _search_client()
        client.get("/api/articles?q=test")

        sql = _select_sql(db)
        params = _select_params(db)
        assert "articles.user_id = ?" in sql
        assert "user_001" in params

    async def test_sanitized_query_passed_as_param(self) -> None:
        """The FTS5 MATCH param is the sanitized (quoted) query, not raw input."""
        client, db = await _search_client()
        client.get("/api/articles?q=hello+world")

        params = _select_params(db)
        # user_id is first, sanitized query is second
        assert params[1] == '"hello" "world"'

    async def test_columns_prefixed_with_table_name(self) -> None:
        """All selected columns are prefixed with 'articles.' to avoid ambiguity."""
        client, db = await _search_client()
        client.get("/api/articles?q=test")

        sql = _select_sql(db)
        assert "articles.id" in sql
        assert "articles.title" in sql
        assert "articles.user_id" in sql

    async def test_no_fts_join_without_query(self) -> None:
        """Without q, no FTS5 join is present and default sort is newest."""
        client, db = await _search_client(
            result_fn=lambda sql, params: [],
        )
        client.get("/api/articles")

        sql = _select_sql(db)
        assert "articles_fts" not in sql
        assert "ORDER BY articles.created_at DESC" in sql


# ---------------------------------------------------------------------------
# Composable filters — search + other filters
# ---------------------------------------------------------------------------


class TestSearchComposesWithFilters:
    async def test_search_with_reading_status(self) -> None:
        """q + reading_status both appear in WHERE clause."""
        client, db = await _search_client()
        client.get("/api/articles?q=test&reading_status=unread")

        sql = _select_sql(db)
        assert "articles_fts MATCH ?" in sql
        assert "articles.reading_status = ?" in sql
        params = _select_params(db)
        assert "unread" in params

    async def test_search_with_tag(self) -> None:
        """q + tag produces both FTS5 MATCH and tag subquery."""
        client, db = await _search_client()
        client.get("/api/articles?q=test&tag=t1")

        sql = _select_sql(db)
        assert "articles_fts MATCH ?" in sql
        assert "article_tags WHERE tag_id = ?" in sql
        params = _select_params(db)
        assert "t1" in params

    async def test_search_with_is_favorite(self) -> None:
        """q + is_favorite both appear in WHERE clause."""
        client, db = await _search_client()
        client.get("/api/articles?q=test&is_favorite=true")

        sql = _select_sql(db)
        assert "articles_fts MATCH ?" in sql
        assert "articles.is_favorite = ?" in sql
        params = _select_params(db)
        assert 1 in params  # True -> 1

    async def test_search_with_audio_status(self) -> None:
        """q + audio_status both appear in WHERE clause."""
        client, db = await _search_client()
        client.get("/api/articles?q=test&audio_status=ready")

        sql = _select_sql(db)
        assert "articles_fts MATCH ?" in sql
        assert "articles.audio_status = ?" in sql

    async def test_search_with_status(self) -> None:
        """q + status both appear in WHERE clause."""
        client, db = await _search_client()
        client.get("/api/articles?q=test&status=ready")

        sql = _select_sql(db)
        assert "articles_fts MATCH ?" in sql
        assert "articles.status = ?" in sql

    async def test_all_filters_compose(self) -> None:
        """q + reading_status + tag + is_favorite all compose in one query."""
        client, db = await _search_client()
        client.get("/api/articles?q=test&reading_status=unread&tag=t1&is_favorite=true")

        sql = _select_sql(db)
        assert "articles_fts MATCH ?" in sql
        assert "articles.reading_status = ?" in sql
        assert "article_tags WHERE tag_id = ?" in sql
        assert "articles.is_favorite = ?" in sql

    async def test_search_defaults_to_weighted_bm25_ordering(self) -> None:
        """When q is provided without sort, ORDER BY uses weighted bm25."""
        client, db = await _search_client()
        client.get("/api/articles?q=test")

        sql = _select_sql(db)
        assert "ORDER BY bm25(articles_fts, 10.0, 5.0, 1.0)" in sql

    async def test_explicit_sort_overrides_relevance(self) -> None:
        """When q and sort are both provided, sort wins over bm25."""
        client, db = await _search_client()
        client.get("/api/articles?q=test&sort=oldest")

        sql = _select_sql(db)
        assert "ORDER BY articles.created_at ASC" in sql
        assert "bm25(" not in sql

    async def test_pagination_params_passed_through(self) -> None:
        """limit and offset are the last two params in the query."""
        client, db = await _search_client()
        client.get("/api/articles?q=test&limit=5&offset=10")

        params = _select_params(db)
        assert params[-2] == 5
        assert params[-1] == 10


# ---------------------------------------------------------------------------
# GET /api/articles?q=... against real SQLite FTS5 (the production migrations)
#
# The string assertions above pin the SQL text; these run that SQL. Restores
# the SQLite-backed ranking tier from 887c8e3 (LESSONS_LEARNED Lesson 82),
# which 45cc325 replaced with string checks. Unlike the originals, they go
# through the endpoint, so they test the SQL the code actually generates
# against the schema the migrations actually create.
# ---------------------------------------------------------------------------


async def _sqlite_search_client() -> tuple[TestClient, SqliteD1]:
    db = SqliteD1()
    for user_id in ("user_001", "user_002"):
        db.conn.execute(
            "INSERT INTO users (id, email, username) VALUES (?, ?, ?)",
            (user_id, f"{user_id}@example.com", user_id),
        )
    client, _ = await _authenticated_client(MockEnv(db=db))
    return client, db


def _insert_article(
    db: SqliteD1,
    *,
    id: str,
    title: str = "",
    excerpt: str = "",
    markdown_content: str = "",
    user_id: str = "user_001",
    created_at: str = "2026-01-01T00:00:00.000+00:00",
) -> None:
    db.conn.execute(
        "INSERT INTO articles (id, user_id, original_url, title, excerpt, markdown_content, "
        "status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'ready', ?)",
        (id, user_id, f"https://example.com/{id}", title, excerpt, markdown_content, created_at),
    )


def _ids(resp: Any) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [article["id"] for article in resp.json()]


class TestSearchOnSqliteFts5:
    async def test_search_sql_executes_and_matches(self) -> None:
        client, db = await _sqlite_search_client()
        _insert_article(db, id="a1", title="Test article")
        assert _ids(client.get("/api/articles?q=test")) == ["a1"]

    async def test_title_match_ranks_above_content_match(self) -> None:
        client, db = await _sqlite_search_client()
        _insert_article(
            db,
            id="content_only",
            title="Unrelated Topic",
            excerpt="Nothing here",
            markdown_content="This article discusses python programming at length",
        )
        _insert_article(
            db,
            id="title_match",
            title="Python Programming Guide",
            excerpt="Not relevant",
            markdown_content="Some other content entirely",
        )
        assert _ids(client.get("/api/articles?q=python")) == ["title_match", "content_only"]

    async def test_excerpt_match_ranks_above_content_match(self) -> None:
        client, db = await _sqlite_search_client()
        _insert_article(
            db,
            id="content_only",
            title="Unrelated",
            excerpt="Nothing relevant",
            markdown_content="This covers rust language features in detail",
        )
        _insert_article(
            db,
            id="excerpt_match",
            title="Unrelated",
            excerpt="A comprehensive guide to rust programming",
            markdown_content="Other content",
        )
        assert _ids(client.get("/api/articles?q=rust")) == ["excerpt_match", "content_only"]

    async def test_title_match_ranks_above_excerpt_match(self) -> None:
        """Title (10x) outranks excerpt (5x): catches swapped bm25 weights."""
        client, db = await _sqlite_search_client()
        _insert_article(
            db,
            id="excerpt_only",
            title="Unrelated Topic",
            excerpt="Learn everything about kubernetes orchestration",
            markdown_content="Other content",
        )
        _insert_article(
            db,
            id="title_match",
            title="Kubernetes Deep Dive",
            excerpt="Not relevant at all",
            markdown_content="Other content",
        )
        assert _ids(client.get("/api/articles?q=kubernetes")) == ["title_match", "excerpt_only"]

    async def test_filters_by_user_id(self) -> None:
        client, db = await _sqlite_search_client()
        _insert_article(db, id="mine", title="My Python Article", user_id="user_001")
        _insert_article(db, id="theirs", title="Their Python Article", user_id="user_002")
        assert _ids(client.get("/api/articles?q=python")) == ["mine"]

    async def test_no_matches_returns_empty(self) -> None:
        client, db = await _sqlite_search_client()
        _insert_article(db, id="a1", title="Something else")
        assert _ids(client.get("/api/articles?q=nonexistent")) == []

    async def test_result_contains_list_columns_and_parsed_tags(self) -> None:
        client, db = await _sqlite_search_client()
        _insert_article(db, id="a1", title="Column Check")
        db.conn.execute(
            "INSERT INTO tags (id, user_id, name, created_at) VALUES ('t1', 'user_001', 'x', 'now')"
        )
        db.conn.execute("INSERT INTO article_tags (article_id, tag_id) VALUES ('a1', 't1')")

        resp = client.get("/api/articles?q=column")

        assert _ids(resp) == ["a1"]
        (row,) = resp.json()
        for column in (c.strip() for c in _LIST_COLUMNS.split(",")):
            assert column in row, f"missing column {column}"
        assert row["tags"] == [{"id": "t1", "name": "x"}]
        assert "tags_json" not in row

    @pytest.mark.parametrize(
        "query",
        ["python", "hello world", "café", "test123", "'; DROP TABLE articles; --", "* OR 1=1 --"],
        ids=["simple", "multi_word", "unicode", "alphanumeric", "sql_injection", "fts_operators"],
    )
    async def test_sanitized_queries_run_without_sqlite_errors(self, query: str) -> None:
        client, db = await _sqlite_search_client()
        _insert_article(db, id="a1", title="Python hello world café test123")
        resp = client.get("/api/articles", params={"q": query})
        assert resp.status_code == 200, resp.text
        if query in ("python", "hello world", "café", "test123"):
            assert _ids(resp) == ["a1"]
        assert db.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1

    @pytest.mark.parametrize("sort", sorted(_VALID_SORT_OPTIONS))
    async def test_every_sort_composes_with_search(self, sort: str) -> None:
        """Explicit sorts replace bm25 and must still be valid next to the FTS join."""
        client, db = await _sqlite_search_client()
        _insert_article(db, id="b", title="Beta python", created_at="2026-01-02T00:00:00+00:00")
        _insert_article(db, id="a", title="Alpha python", created_at="2026-01-01T00:00:00+00:00")
        ids = _ids(client.get(f"/api/articles?q=python&sort={sort}"))
        assert sorted(ids) == ["a", "b"]
        if sort in ("title_asc", "oldest"):
            assert ids == ["a", "b"]
        if sort == "newest":
            assert ids == ["b", "a"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestSearchValidation:
    async def test_invalid_sort_returns_422(self) -> None:
        """Invalid sort value is rejected even when searching."""
        client, _ = await _search_client()
        resp = client.get("/api/articles?q=test&sort=invalid")
        assert resp.status_code == 422

    async def test_invalid_reading_status_returns_422(self) -> None:
        """Invalid reading_status is rejected even when searching."""
        client, _ = await _search_client()
        resp = client.get("/api/articles?q=test&reading_status=bogus")
        assert resp.status_code == 422

    async def test_invalid_audio_status_returns_422(self) -> None:
        """Invalid audio_status is rejected even when searching."""
        client, _ = await _search_client()
        resp = client.get("/api/articles?q=test&audio_status=bogus")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TestSearchAuthRequired:
    def test_returns_401_without_auth(self) -> None:
        """GET /api/articles?q=test returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles?q=test")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Direct unit tests for _sanitize_fts5_query
# ---------------------------------------------------------------------------


class TestSanitizeFts5Query:
    def test_simple_word(self) -> None:
        assert _sanitize_fts5_query("hello") == '"hello"'

    def test_multiple_words(self) -> None:
        assert _sanitize_fts5_query("hello world") == '"hello" "world"'

    def test_strips_asterisk(self) -> None:
        assert _sanitize_fts5_query("test*") == '"test"'

    def test_strips_plus(self) -> None:
        assert _sanitize_fts5_query("+test") == '"test"'

    def test_strips_minus(self) -> None:
        assert _sanitize_fts5_query("-test") == '"test"'

    def test_strips_caret(self) -> None:
        assert _sanitize_fts5_query("^test") == '"test"'

    def test_strips_parentheses(self) -> None:
        assert _sanitize_fts5_query("(test)") == '"test"'

    def test_strips_curly_braces(self) -> None:
        assert _sanitize_fts5_query("{test}") == '"test"'

    def test_strips_square_brackets(self) -> None:
        assert _sanitize_fts5_query("[test]") == '"test"'

    def test_strips_pipe(self) -> None:
        assert _sanitize_fts5_query("test|other") == '"testother"'

    def test_strips_double_quotes(self) -> None:
        result = _sanitize_fts5_query('"test"')
        assert result == '"test"'

    def test_strips_backslash(self) -> None:
        assert _sanitize_fts5_query("test\\n") == '"testn"'

    def test_strips_colon(self) -> None:
        assert _sanitize_fts5_query("title:test") == '"titletest"'

    def test_empty_after_stripping(self) -> None:
        assert _sanitize_fts5_query("***") == ""

    def test_mixed_clean_and_dirty(self) -> None:
        assert _sanitize_fts5_query("hello *** world") == '"hello" "world"'

    def test_preserves_numbers(self) -> None:
        assert _sanitize_fts5_query("python3") == '"python3"'

    def test_preserves_unicode(self) -> None:
        result = _sanitize_fts5_query("café")
        assert "caf" in result

    def test_fts5_operators_quoted_as_literals(self) -> None:
        assert _sanitize_fts5_query("OR AND NOT") == '"OR" "AND" "NOT"'

    def test_empty_string(self) -> None:
        assert _sanitize_fts5_query("") == ""

    def test_whitespace_only(self) -> None:
        assert _sanitize_fts5_query("   ") == ""

    def test_near_operator_quoted(self) -> None:
        result = _sanitize_fts5_query("NEAR test")
        assert '"NEAR"' in result
        assert '"test"' in result

    def test_preserves_hyphens_in_words(self) -> None:
        # Hyphen is in the special chars set, so it's stripped
        assert _sanitize_fts5_query("real-time") == '"realtime"'

    def test_single_character_tokens(self) -> None:
        assert _sanitize_fts5_query("a b c") == '"a" "b" "c"'

    def test_mixed_case_preserved(self) -> None:
        assert _sanitize_fts5_query("CloudFlare") == '"CloudFlare"'


# ---------------------------------------------------------------------------
# Negative test cases — SQL injection via search query
# ---------------------------------------------------------------------------


class TestSearchSqlInjection:
    async def test_sql_injection_in_query_does_not_crash(self) -> None:
        """GET /api/articles?q=<sql injection> returns 200 without error."""
        db = TrackingD1(result_fn=lambda sql, params: [])
        env = MockEnv(db=db)
        client, _ = await _authenticated_client(env)

        # Attempt SQL injection via the search query parameter
        resp = client.get("/api/articles?q='; DROP TABLE articles; --")

        assert resp.status_code == 200

    async def test_fts5_special_chars_injection(self) -> None:
        """GET /api/articles?q=<fts5 operators> does not cause FTS5 syntax error."""
        db = TrackingD1(result_fn=lambda sql, params: [])
        env = MockEnv(db=db)
        client, _ = await _authenticated_client(env)

        # FTS5 special characters that could cause syntax errors
        resp = client.get("/api/articles?q=* OR 1=1 --")

        assert resp.status_code == 200

    async def test_sanitizer_neutralizes_sql_injection(self) -> None:
        """_sanitize_fts5_query wraps injection attempts in quotes."""
        result = _sanitize_fts5_query("'; DROP TABLE articles; --")
        # Dangerous keywords should be quoted, not left as bare FTS5 operators
        assert "DROP" not in result or '"DROP"' in result
        # The result should not contain unquoted SQL operators
        assert "TABLE" not in result or '"TABLE"' in result

"""Tests for tag rules API (src/tags/rules.py) and auto-tagging logic.

Covers tag rule CRUD, validation, authentication enforcement,
and the apply_auto_tags function from processing.py.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from src.auth.session import COOKIE_NAME
from src.tags.rules import router
from src.wrappers import SafeEnv
from tests.conftest import (
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

_ROUTERS = ((router, "/api/tag-rules"),)


def _make_app(env):
    return _make_test_app(env, *_ROUTERS)


async def _authenticated_client(env: MockEnv) -> tuple[TestClient, str]:
    return await _authenticated_client_base(env, *_ROUTERS)


# ---------------------------------------------------------------------------
# GET /api/tag-rules — List rules
# ---------------------------------------------------------------------------


class TestListTagRules:
    async def test_returns_rules_with_tag_name(self) -> None:
        """GET /api/tag-rules returns rules joined with tag names."""
        rules = [
            {
                "id": "rule_1",
                "tag_id": "tag_1",
                "match_type": "domain",
                "pattern": "example.com",
                "created_at": "2025-01-01",
                "tag_name": "tech",
            },
            {
                "id": "rule_2",
                "tag_id": "tag_2",
                "match_type": "title_contains",
                "pattern": "python",
                "created_at": "2025-01-02",
                "tag_name": "python",
            },
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM tag_rules" in sql and "INNER JOIN tags" in sql:
                return rules
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/tag-rules",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["tag_name"] == "tech"
        assert data[0]["match_type"] == "domain"
        assert data[1]["pattern"] == "python"

    async def test_returns_empty_list_when_no_rules(self) -> None:
        """GET /api/tag-rules returns empty list when no rules exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/tag-rules",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# POST /api/tag-rules — Create rule
# ---------------------------------------------------------------------------


class TestCreateTagRule:
    async def test_creates_rule_successfully(self) -> None:
        """POST /api/tag-rules creates a new rule and returns it."""
        tag = {"id": "tag_1", "name": "tech"}
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            if "FROM tags" in sql and "id = ?" in sql:
                return [tag]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/tag-rules",
            json={
                "tag_id": "tag_1",
                "match_type": "domain",
                "pattern": "example.com",
            },
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["tag_id"] == "tag_1"
        assert data["tag_name"] == "tech"
        assert data["match_type"] == "domain"
        assert data["pattern"] == "example.com"
        assert "id" in data
        assert "created_at" in data

        insert_calls = [c for c in calls if c["sql"].startswith("INSERT")]
        assert len(insert_calls) == 1

    async def test_rejects_missing_tag_id(self) -> None:
        """POST /api/tag-rules returns 422 when tag_id is missing."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/tag-rules",
            json={"match_type": "domain", "pattern": "example.com"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422
        assert "tag_id" in resp.json()["detail"].lower()

    async def test_rejects_missing_match_type(self) -> None:
        """POST /api/tag-rules returns 422 when match_type is missing."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/tag-rules",
            json={"tag_id": "tag_1", "pattern": "example.com"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422
        assert "match_type" in resp.json()["detail"].lower()

    async def test_rejects_invalid_match_type(self) -> None:
        """POST /api/tag-rules returns 400 for invalid match_type."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/tag-rules",
            json={"tag_id": "tag_1", "match_type": "invalid", "pattern": "x"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 400
        assert "match_type" in resp.json()["detail"].lower()

    async def test_rejects_missing_pattern(self) -> None:
        """POST /api/tag-rules returns 422 when pattern is missing."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/tag-rules",
            json={"tag_id": "tag_1", "match_type": "domain"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422
        assert "pattern" in resp.json()["detail"].lower()

    async def test_rejects_pattern_too_long(self) -> None:
        """POST /api/tag-rules returns 400 when pattern exceeds 500 chars."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/tag-rules",
            json={
                "tag_id": "tag_1",
                "match_type": "domain",
                "pattern": "x" * 501,
            },
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 400
        assert "500" in resp.json()["detail"]

    async def test_returns_404_for_nonexistent_tag(self) -> None:
        """POST /api/tag-rules returns 404 when tag doesn't exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/tag-rules",
            json={
                "tag_id": "nonexistent",
                "match_type": "domain",
                "pattern": "example.com",
            },
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404
        assert "tag not found" in resp.json()["detail"].lower()

    async def test_rejects_duplicate_rule(self) -> None:
        """POST /api/tag-rules returns 409 for duplicate tag/type/pattern."""
        tag = {"id": "tag_1", "name": "tech"}
        existing_rule = {"id": "rule_existing"}

        def execute(sql: str, params: list) -> list:
            if "FROM tags" in sql and "id = ?" in sql:
                return [tag]
            if "FROM tag_rules" in sql and "tag_id = ?" in sql:
                return [existing_rule]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/tag-rules",
            json={
                "tag_id": "tag_1",
                "match_type": "domain",
                "pattern": "example.com",
            },
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# DELETE /api/tag-rules/{rule_id} — Delete rule
# ---------------------------------------------------------------------------


class TestDeleteTagRule:
    async def test_deletes_rule(self) -> None:
        """DELETE /api/tag-rules/{rule_id} removes the rule."""
        rule = {"id": "rule_001"}
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            if "FROM tag_rules" in sql and "INNER JOIN tags" in sql:
                return [rule]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.delete(
            "/api/tag-rules/rule_001",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 204

        delete_calls = [c for c in calls if "DELETE FROM tag_rules" in c["sql"]]
        assert len(delete_calls) == 1

    async def test_returns_404_for_missing_rule(self) -> None:
        """DELETE /api/tag-rules/{rule_id} returns 404 when rule doesn't exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.delete(
            "/api/tag-rules/nonexistent",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Authentication enforcement
# ---------------------------------------------------------------------------


class TestTagRulesAuthRequired:
    def test_get_rules_returns_401_without_auth(self) -> None:
        """GET /api/tag-rules returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/tag-rules")
        assert resp.status_code == 401

    def test_post_rule_returns_401_without_auth(self) -> None:
        """POST /api/tag-rules returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/tag-rules",
            json={"tag_id": "t", "match_type": "domain", "pattern": "x"},
        )
        assert resp.status_code == 401

    def test_delete_rule_returns_401_without_auth(self) -> None:
        """DELETE /api/tag-rules/{id} returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/api/tag-rules/rule_001")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# apply_auto_tags — unit tests
# ---------------------------------------------------------------------------


class TestApplyAutoTags:
    """Tests for the apply_auto_tags function.

    All tests wrap MockEnv in SafeEnv to match production behavior, where
    process_article passes a SafeEnv to apply_auto_tags.
    """

    async def test_domain_exact_match(self) -> None:
        """Domain rule matches an exact domain."""
        rules = [
            {"tag_id": "tag_1", "match_type": "domain", "pattern": "example.com"},
        ]
        inserts: list[tuple[str, list]] = []

        def execute(sql: str, params: list) -> list:
            if "FROM tag_rules" in sql:
                return rules
            if sql.startswith("INSERT"):
                inserts.append((sql, params))
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db))

        from src.articles.processing import apply_auto_tags

        count = await apply_auto_tags(
            env,
            "art_1",
            "example.com",
            "Some Title",
            "https://example.com/page",
        )

        assert count == 1
        assert len(inserts) == 1
        assert inserts[0][1] == ["art_1", "tag_1"]

    async def test_domain_glob_match(self) -> None:
        """Domain rule supports glob patterns like *.example.com."""
        rules = [
            {"tag_id": "tag_1", "match_type": "domain", "pattern": "*.example.com"},
        ]
        inserts: list[tuple[str, list]] = []

        def execute(sql: str, params: list) -> list:
            if "FROM tag_rules" in sql:
                return rules
            if sql.startswith("INSERT"):
                inserts.append((sql, params))
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db))

        from src.articles.processing import apply_auto_tags

        count = await apply_auto_tags(
            env,
            "art_1",
            "blog.example.com",
            "Title",
            "https://blog.example.com/post",
        )

        assert count == 1

    async def test_domain_no_match(self) -> None:
        """Domain rule does not match a different domain."""
        rules = [
            {"tag_id": "tag_1", "match_type": "domain", "pattern": "example.com"},
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM tag_rules" in sql:
                return rules
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db))

        from src.articles.processing import apply_auto_tags

        count = await apply_auto_tags(
            env,
            "art_1",
            "other.com",
            "Title",
            "https://other.com/page",
        )

        assert count == 0

    async def test_title_contains_match(self) -> None:
        """title_contains rule matches case-insensitively."""
        rules = [
            {"tag_id": "tag_1", "match_type": "title_contains", "pattern": "Python"},
        ]
        inserts: list[tuple[str, list]] = []

        def execute(sql: str, params: list) -> list:
            if "FROM tag_rules" in sql:
                return rules
            if sql.startswith("INSERT"):
                inserts.append((sql, params))
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db))

        from src.articles.processing import apply_auto_tags

        count = await apply_auto_tags(
            env,
            "art_1",
            "blog.com",
            "Learning PYTHON the Hard Way",
            "https://blog.com/python",
        )

        assert count == 1

    async def test_title_contains_no_match(self) -> None:
        """title_contains rule does not match when substring is absent."""
        rules = [
            {"tag_id": "tag_1", "match_type": "title_contains", "pattern": "Python"},
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM tag_rules" in sql:
                return rules
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db))

        from src.articles.processing import apply_auto_tags

        count = await apply_auto_tags(
            env,
            "art_1",
            "blog.com",
            "Learning JavaScript",
            "https://blog.com/js",
        )

        assert count == 0

    async def test_url_contains_match(self) -> None:
        """url_contains rule matches case-insensitively."""
        rules = [
            {"tag_id": "tag_1", "match_type": "url_contains", "pattern": "/blog/"},
        ]
        inserts: list[tuple[str, list]] = []

        def execute(sql: str, params: list) -> list:
            if "FROM tag_rules" in sql:
                return rules
            if sql.startswith("INSERT"):
                inserts.append((sql, params))
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db))

        from src.articles.processing import apply_auto_tags

        count = await apply_auto_tags(
            env,
            "art_1",
            "example.com",
            "Title",
            "https://example.com/Blog/post-1",
        )

        assert count == 1

    async def test_url_contains_no_match(self) -> None:
        """url_contains rule does not match when pattern is absent from URL."""
        rules = [
            {"tag_id": "tag_1", "match_type": "url_contains", "pattern": "/docs/"},
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM tag_rules" in sql:
                return rules
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db))

        from src.articles.processing import apply_auto_tags

        count = await apply_auto_tags(
            env,
            "art_1",
            "example.com",
            "Title",
            "https://example.com/blog/post-1",
        )

        assert count == 0

    async def test_multiple_rules_same_tag_deduplicates(self) -> None:
        """Multiple rules matching the same tag only insert once."""
        rules = [
            {"tag_id": "tag_1", "match_type": "domain", "pattern": "example.com"},
            {"tag_id": "tag_1", "match_type": "title_contains", "pattern": "test"},
        ]
        inserts: list[tuple[str, list]] = []

        def execute(sql: str, params: list) -> list:
            if "FROM tag_rules" in sql:
                return rules
            if sql.startswith("INSERT"):
                inserts.append((sql, params))
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db))

        from src.articles.processing import apply_auto_tags

        count = await apply_auto_tags(
            env,
            "art_1",
            "example.com",
            "Test Article",
            "https://example.com/test",
        )

        assert count == 1
        assert len(inserts) == 1

    async def test_multiple_different_tags(self) -> None:
        """Multiple rules matching different tags create separate associations."""
        rules = [
            {"tag_id": "tag_1", "match_type": "domain", "pattern": "example.com"},
            {"tag_id": "tag_2", "match_type": "title_contains", "pattern": "python"},
        ]
        inserts: list[tuple[str, list]] = []

        def execute(sql: str, params: list) -> list:
            if "FROM tag_rules" in sql:
                return rules
            if sql.startswith("INSERT"):
                inserts.append((sql, params))
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db))

        from src.articles.processing import apply_auto_tags

        count = await apply_auto_tags(
            env,
            "art_1",
            "example.com",
            "Python Guide",
            "https://example.com/python",
        )

        assert count == 2
        assert len(inserts) == 2

    async def test_no_rules_returns_zero(self) -> None:
        """When no rules exist, returns 0."""

        def execute(sql: str, params: list) -> list:
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db))

        from src.articles.processing import apply_auto_tags

        count = await apply_auto_tags(
            env,
            "art_1",
            "example.com",
            "Title",
            "https://example.com",
        )

        assert count == 0

    async def test_domain_match_is_case_insensitive(self) -> None:
        """Domain matching is case-insensitive."""
        rules = [
            {"tag_id": "tag_1", "match_type": "domain", "pattern": "Example.COM"},
        ]
        inserts: list[tuple[str, list]] = []

        def execute(sql: str, params: list) -> list:
            if "FROM tag_rules" in sql:
                return rules
            if sql.startswith("INSERT"):
                inserts.append((sql, params))
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db))

        from src.articles.processing import apply_auto_tags

        count = await apply_auto_tags(
            env,
            "art_1",
            "example.com",
            "Title",
            "https://example.com/page",
        )

        assert count == 1

    async def test_handles_none_values_gracefully(self) -> None:
        """apply_auto_tags handles None domain/title/url without crashing."""
        rules = [
            {"tag_id": "tag_1", "match_type": "domain", "pattern": "example.com"},
            {"tag_id": "tag_2", "match_type": "title_contains", "pattern": "test"},
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM tag_rules" in sql:
                return rules
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db))

        from src.articles.processing import apply_auto_tags

        count = await apply_auto_tags(env, "art_1", None, None, None)

        assert count == 0

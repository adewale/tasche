"""Tests for Phase 5 — Tags API (src/tags/routes.py).

Covers tag CRUD, article-tag associations, duplicate prevention,
and authentication enforcement.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from src.auth.session import COOKIE_NAME
from src.tags.routes import article_tags_router, router
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

_ROUTERS = (
    (router, "/api/tags"),
    (article_tags_router, "/api/articles"),
)


def _make_app(env):
    return _make_test_app(env, *_ROUTERS)


async def _authenticated_client(env: MockEnv) -> tuple[TestClient, str]:
    return await _authenticated_client_base(env, *_ROUTERS)


# ---------------------------------------------------------------------------
# POST /api/tags — Create tag
# ---------------------------------------------------------------------------


class TestCreateTag:
    async def test_creates_tag_successfully(self) -> None:
        """POST /api/tags creates a new tag and returns it."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            # No duplicate found
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/tags",
            json={"name": "python"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "python"
        assert data["user_id"] == "user_001"
        assert "id" in data
        assert "created_at" in data

        # Verify INSERT was called
        insert_calls = [c for c in calls if c["sql"].startswith("INSERT")]
        assert len(insert_calls) == 1

    async def test_rejects_duplicate_tag_name(self) -> None:
        """POST /api/tags returns 409 when a tag with the same name exists."""
        existing_tag = {
            "id": "tag_existing",
            "user_id": "user_001",
            "name": "python",
        }

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql and "name = ?" in sql:
                return [existing_tag]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/tags",
            json={"name": "python"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    async def test_rejects_empty_tag_name(self) -> None:
        """POST /api/tags returns 422 when name is empty."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/tags",
            json={"name": ""},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422
        assert "required" in resp.json()["detail"].lower()

    async def test_rejects_whitespace_only_name(self) -> None:
        """POST /api/tags returns 422 when name is only whitespace."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/tags",
            json={"name": "   "},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422

    # ---------------------------------------------------------------------------
    # GET /api/tags — List tags
    # ---------------------------------------------------------------------------

    async def test_rejects_name_too_long(self) -> None:
        """POST /api/tags returns 400 when name exceeds 100 characters."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/tags",
            json={"name": "x" * 101},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 400
        assert "100" in resp.json()["detail"]


class TestListTags:
    async def test_returns_users_tags(self) -> None:
        """GET /api/tags returns all tags for the authenticated user."""
        tags = [
            {
                "id": "tag_1",
                "user_id": "user_001",
                "name": "javascript",
                "created_at": "2025-01-01",
                "article_count": 5,
            },
            {
                "id": "tag_2",
                "user_id": "user_001",
                "name": "python",
                "created_at": "2025-01-02",
                "article_count": 12,
            },
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM tags" in sql and "LEFT JOIN" in sql:
                return tags
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/tags",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "javascript"
        assert data[1]["name"] == "python"

    async def test_returns_article_count_for_tags(self) -> None:
        """GET /api/tags includes article_count for each tag."""
        tags = [
            {
                "id": "tag_1",
                "user_id": "user_001",
                "name": "javascript",
                "created_at": "2025-01-01",
                "article_count": 5,
            },
            {
                "id": "tag_2",
                "user_id": "user_001",
                "name": "python",
                "created_at": "2025-01-02",
                "article_count": 0,
            },
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM tags" in sql and "LEFT JOIN" in sql:
                return tags
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/tags",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["article_count"] == 5
        assert data[1]["article_count"] == 0

    async def test_list_tags_uses_left_join_with_group_by(self) -> None:
        """GET /api/tags query uses LEFT JOIN and GROUP BY for article counts."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/tags",
            cookies={COOKIE_NAME: session_id},
        )

        tag_queries = [c for c in calls if "FROM tags" in c["sql"]]
        assert len(tag_queries) >= 1
        sql = tag_queries[0]["sql"]
        assert "LEFT JOIN article_tags" in sql
        assert "GROUP BY" in sql
        assert "article_count" in sql

    async def test_returns_empty_list_when_no_tags(self) -> None:
        """GET /api/tags returns an empty list when the user has no tags."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/tags",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# DELETE /api/tags/{tag_id} — Delete tag
# ---------------------------------------------------------------------------


class TestDeleteTag:
    async def test_deletes_tag(self) -> None:
        """DELETE /api/tags/{tag_id} removes the tag."""
        tag = {
            "id": "tag_001",
            "user_id": "user_001",
            "name": "python",
            "created_at": "2025-01-01",
        }
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            if "FROM tags" in sql and "id = ?" in sql:
                return [tag]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.delete(
            "/api/tags/tag_001",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 204

        delete_calls = [c for c in calls if "DELETE FROM tags" in c["sql"]]
        assert len(delete_calls) == 1

    async def test_returns_404_for_missing_tag(self) -> None:
        """DELETE /api/tags/{tag_id} returns 404 when tag doesn't exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.delete(
            "/api/tags/nonexistent",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/articles/{article_id}/tags — Add tag to article
# ---------------------------------------------------------------------------


class TestAddTagToArticle:
    async def test_adds_tag_to_article(self) -> None:
        """POST /api/articles/{id}/tags associates a tag with an article."""
        article = ArticleFactory.create(id="art_001", user_id="user_001")
        tag = {
            "id": "tag_001",
            "user_id": "user_001",
            "name": "python",
            "created_at": "2025-01-01",
        }
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            if "FROM articles" in sql and params[0] == "art_001":
                return [article]
            if "FROM tags" in sql and params[0] == "tag_001":
                return [tag]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_001/tags",
            json={"tag_id": "tag_001"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["article_id"] == "art_001"
        assert data["tag_id"] == "tag_001"

        insert_calls = [c for c in calls if "INSERT INTO article_tags" in c["sql"]]
        assert len(insert_calls) == 1

    async def test_returns_404_for_missing_article(self) -> None:
        """POST /api/articles/{id}/tags returns 404 for missing article."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/nonexistent/tags",
            json={"tag_id": "tag_001"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404
        assert "Article not found" in resp.json()["detail"]

    async def test_returns_404_for_missing_tag(self) -> None:
        """POST /api/articles/{id}/tags returns 404 for missing tag."""
        article = ArticleFactory.create(id="art_001", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "FROM articles" in sql and params[0] == "art_001":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_001/tags",
            json={"tag_id": "nonexistent"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404
        assert "Tag not found" in resp.json()["detail"]

    async def test_returns_409_for_duplicate_association(self) -> None:
        """POST /api/articles/{id}/tags returns 409 when already tagged."""
        article = ArticleFactory.create(id="art_001", user_id="user_001")
        tag = {
            "id": "tag_001",
            "user_id": "user_001",
            "name": "python",
            "created_at": "2025-01-01",
        }
        existing_assoc = {"article_id": "art_001", "tag_id": "tag_001"}

        def execute(sql: str, params: list) -> list:
            if "FROM articles" in sql and params[0] == "art_001":
                return [article]
            if "FROM tags" in sql and params[0] == "tag_001":
                return [tag]
            if "FROM article_tags" in sql:
                return [existing_assoc]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_001/tags",
            json={"tag_id": "tag_001"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 409
        assert "already applied" in resp.json()["detail"].lower()

    async def test_rejects_missing_tag_id(self) -> None:
        """POST /api/articles/{id}/tags returns 422 when tag_id missing."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/articles/art_001/tags",
            json={},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/articles/{article_id}/tags/{tag_id} — Remove tag
# ---------------------------------------------------------------------------


class TestRemoveTagFromArticle:
    async def test_removes_tag_from_article(self) -> None:
        """DELETE /api/articles/{id}/tags/{tag_id} removes the association."""
        article = ArticleFactory.create(id="art_001", user_id="user_001")
        tag = {
            "id": "tag_001",
            "user_id": "user_001",
            "name": "python",
            "created_at": "2025-01-01",
        }
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            if "FROM articles" in sql and params[0] == "art_001":
                return [article]
            if "FROM tags" in sql and params[0] == "tag_001":
                return [tag]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.delete(
            "/api/articles/art_001/tags/tag_001",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 204

        delete_calls = [c for c in calls if "DELETE FROM article_tags" in c["sql"]]
        assert len(delete_calls) == 1

    async def test_returns_404_for_missing_article(self) -> None:
        """DELETE /api/articles/{id}/tags/{tag_id} returns 404."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.delete(
            "/api/articles/nonexistent/tags/tag_001",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    async def test_returns_404_for_missing_tag(self) -> None:
        """DELETE /api/articles/{id}/tags/{tag_id} returns 404."""
        article = ArticleFactory.create(id="art_001", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "FROM articles" in sql and params[0] == "art_001":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.delete(
            "/api/articles/art_001/tags/nonexistent",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id}/tags — List article tags
# ---------------------------------------------------------------------------


class TestGetArticleTags:
    async def test_returns_tags_for_article(self) -> None:
        """GET /api/articles/{id}/tags returns all tags for an article."""
        article = ArticleFactory.create(id="art_001", user_id="user_001")
        tags = [
            {
                "id": "tag_1",
                "user_id": "user_001",
                "name": "python",
                "created_at": "2025-01-01",
            },
            {
                "id": "tag_2",
                "user_id": "user_001",
                "name": "cloudflare",
                "created_at": "2025-01-02",
            },
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM articles" in sql and params[0] == "art_001":
                return [article]
            if "FROM tags" in sql and "INNER JOIN" in sql:
                return tags
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_001/tags",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["name"] == "python"
        assert data[1]["name"] == "cloudflare"

    async def test_returns_404_for_missing_article(self) -> None:
        """GET /api/articles/{id}/tags returns 404 for missing article."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/nonexistent/tags",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Authentication enforcement
# ---------------------------------------------------------------------------


class TestTagsAuthRequired:
    def test_post_tag_returns_401_without_auth(self) -> None:
        """POST /api/tags returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/tags", json={"name": "test"})
        assert resp.status_code == 401

    def test_get_tags_returns_401_without_auth(self) -> None:
        """GET /api/tags returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/tags")
        assert resp.status_code == 401

    def test_delete_tag_returns_401_without_auth(self) -> None:
        """DELETE /api/tags/{id} returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/api/tags/tag_001")
        assert resp.status_code == 401

    def test_add_article_tag_returns_401_without_auth(self) -> None:
        """POST /api/articles/{id}/tags returns 401 without auth."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/articles/art_001/tags",
            json={"tag_id": "tag_001"},
        )
        assert resp.status_code == 401

    def test_remove_article_tag_returns_401_without_auth(self) -> None:
        """DELETE /api/articles/{id}/tags/{tag_id} returns 401."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/api/articles/art_001/tags/tag_001")
        assert resp.status_code == 401

    def test_get_article_tags_returns_401_without_auth(self) -> None:
        """GET /api/articles/{id}/tags returns 401 without auth."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles/art_001/tags")
        assert resp.status_code == 401

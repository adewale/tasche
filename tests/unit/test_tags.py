"""Tests for Phase 5 — Tags API (src/tags/routes.py).

Covers tag CRUD, article-tag associations, duplicate prevention,
and authentication enforcement.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from src.articles.routes import router as articles_router
from src.tags.routes import article_tags_router, router
from tests.conftest import (
    ArticleFactory,
    MockD1,
    MockEnv,
    TrackingD1,
    make_test_helpers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_make_app, _authenticated_client = make_test_helpers(
    (router, "/api/tags"),
    (article_tags_router, "/api/articles"),
)


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
        resp = client.get("/api/tags")

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
        resp = client.get("/api/tags")

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
        client.get("/api/tags")

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
        resp = client.get("/api/tags")

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# PATCH /api/tags/{tag_id} — Rename tag
# ---------------------------------------------------------------------------


class TestRenameTag:
    async def test_renames_tag_successfully(self) -> None:
        """PATCH /api/tags/{tag_id} updates the tag name."""
        tag = {
            "id": "tag_001",
            "user_id": "user_001",
            "name": "python",
            "created_at": "2025-01-01",
        }
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            # _get_user_tag lookup (does NOT have "name = ?")
            if "FROM tags" in sql and "id = ?" in sql and "name = ?" not in sql:
                return [tag]
            # Duplicate name check — no duplicate found
            if "FROM tags" in sql and "name = ?" in sql and "id != ?" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/tags/tag_001",
            json={"name": "python3"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "tag_001"
        assert data["name"] == "python3"
        assert data["user_id"] == "user_001"

        update_calls = [c for c in calls if "UPDATE tags" in c["sql"]]
        assert len(update_calls) == 1

    async def test_returns_404_for_missing_tag(self) -> None:
        """PATCH /api/tags/{tag_id} returns 404 when tag doesn't exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/tags/nonexistent",
            json={"name": "new-name"},
        )

        assert resp.status_code == 404

    async def test_returns_409_for_duplicate_name(self) -> None:
        """PATCH /api/tags/{tag_id} returns 409 when another tag has the name."""
        tag = {
            "id": "tag_001",
            "user_id": "user_001",
            "name": "python",
            "created_at": "2025-01-01",
        }
        other_tag = {
            "id": "tag_002",
            "user_id": "user_001",
            "name": "javascript",
        }

        def execute(sql: str, params: list) -> list:
            # _get_user_tag lookup
            if "FROM tags" in sql and "id = ?" in sql and "name = ?" not in sql:
                return [tag]
            # Duplicate name check
            if "FROM tags" in sql and "name = ?" in sql and "id != ?" in sql:
                return [other_tag]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/tags/tag_001",
            json={"name": "javascript"},
        )

        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    async def test_rejects_empty_name(self) -> None:
        """PATCH /api/tags/{tag_id} returns 422 when name is empty."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.patch(
            "/api/tags/tag_001",
            json={"name": ""},
        )

        assert resp.status_code == 422
        assert "required" in resp.json()["detail"].lower()

    async def test_trims_whitespace(self) -> None:
        """PATCH /api/tags/{tag_id} trims whitespace from the new name."""
        tag = {
            "id": "tag_001",
            "user_id": "user_001",
            "name": "python",
            "created_at": "2025-01-01",
        }
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            if "FROM tags" in sql and "id = ?" in sql and "name = ?" not in sql:
                return [tag]
            if "FROM tags" in sql and "name = ?" in sql and "id != ?" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/tags/tag_001",
            json={"name": "  python3  "},
        )

        assert resp.status_code == 200
        assert resp.json()["name"] == "python3"


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
        resp = client.delete("/api/tags/tag_001")

        assert resp.status_code == 204

        delete_calls = [c for c in calls if "DELETE FROM tags" in c["sql"]]
        assert len(delete_calls) == 1

    async def test_returns_404_for_missing_tag(self) -> None:
        """DELETE /api/tags/{tag_id} returns 404 when tag doesn't exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.delete("/api/tags/nonexistent")

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
        resp = client.delete("/api/articles/art_001/tags/tag_001")

        assert resp.status_code == 204

        delete_calls = [c for c in calls if "DELETE FROM article_tags" in c["sql"]]
        assert len(delete_calls) == 1

    async def test_returns_404_for_missing_article(self) -> None:
        """DELETE /api/articles/{id}/tags/{tag_id} returns 404."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.delete("/api/articles/nonexistent/tags/tag_001")

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
        resp = client.delete("/api/articles/art_001/tags/nonexistent")

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
        resp = client.get("/api/articles/art_001/tags")

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
        resp = client.get("/api/articles/nonexistent/tags")

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

    def test_patch_tag_returns_401_without_auth(self) -> None:
        """PATCH /api/tags/{id} returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.patch("/api/tags/tag_001", json={"name": "new"})
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


# ---------------------------------------------------------------------------
# Tag creation edge cases
# ---------------------------------------------------------------------------


class TestTagCreationEdgeCases:
    async def test_tag_name_is_trimmed(self) -> None:
        """POST /api/tags trims whitespace from the tag name."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/tags",
            json={"name": "  my tag  "},
        )

        assert resp.status_code == 201
        assert resp.json()["name"] == "my tag"

    async def test_tag_name_with_special_characters(self) -> None:
        """POST /api/tags accepts tags with special characters."""

        def execute(sql: str, params: list) -> list:
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/tags",
            json={"name": "C++/Rust"},
        )

        assert resp.status_code == 201
        assert resp.json()["name"] == "C++/Rust"


# ---------------------------------------------------------------------------
# Tag list response shape
# ---------------------------------------------------------------------------


class TestTagListShape:
    async def test_tag_includes_id_name_article_count(self) -> None:
        """GET /api/tags returns tags with id, name, and article_count."""
        tags = [
            {
                "id": "tag_1",
                "user_id": "user_001",
                "name": "python",
                "created_at": "2025-01-01",
                "article_count": 3,
            },
        ]

        def execute(sql: str, params: list) -> list:
            if "FROM tags" in sql and "LEFT JOIN" in sql:
                return tags
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get("/api/tags")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        tag = data[0]
        assert "id" in tag
        assert "name" in tag
        assert "article_count" in tag
        assert tag["article_count"] == 3

    async def test_tags_ordered_by_name(self) -> None:
        """GET /api/tags returns tags ordered alphabetically by name."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get("/api/tags")

        tag_queries = [c for c in calls if "FROM tags" in c["sql"]]
        assert len(tag_queries) >= 1
        assert "ORDER BY t.name" in tag_queries[0]["sql"]


# ---------------------------------------------------------------------------
# Rename tag edge cases
# ---------------------------------------------------------------------------


class TestRenameTagEdgeCases:
    async def test_rejects_name_too_long(self) -> None:
        """PATCH /api/tags/{tag_id} returns 400 when name exceeds 100 chars."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.patch(
            "/api/tags/tag_001",
            json={"name": "x" * 101},
        )

        assert resp.status_code == 400
        assert "100" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Article-tag association edge cases
# ---------------------------------------------------------------------------


class TestArticleTagEdgeCases:
    async def test_get_article_tags_returns_empty_when_no_tags(self) -> None:
        """GET /api/articles/{id}/tags returns empty list for article with no tags."""
        article = ArticleFactory.create(id="art_001", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "FROM articles" in sql and params[0] == "art_001":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get("/api/articles/art_001/tags")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_get_article_tags_uses_inner_join(self) -> None:
        """GET /api/articles/{id}/tags uses INNER JOIN for efficient querying."""
        article = ArticleFactory.create(id="art_001", user_id="user_001")
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            if "FROM articles" in sql and params[0] == "art_001":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get("/api/articles/art_001/tags")

        tag_queries = [c for c in calls if "FROM tags" in c["sql"]]
        assert len(tag_queries) >= 1
        assert "INNER JOIN article_tags" in tag_queries[0]["sql"]


# ---------------------------------------------------------------------------
# Multi-tag intersection filtering (GET /api/articles?tag=...&tag=...)
# ---------------------------------------------------------------------------

_make_articles_app, _articles_client = make_test_helpers(
    (articles_router, "/api/articles"),
)


class TestMultiTagFiltering:
    async def test_single_tag_filter_uses_subquery(self) -> None:
        """GET /api/articles?tag=t1 uses IN (SELECT ...) for single tag."""
        db = TrackingD1(result_fn=lambda sql, params: [])
        env = MockEnv(db=db)

        client, _ = await _articles_client(env)
        resp = client.get("/api/articles?tag=tag_1")

        assert resp.status_code == 200
        # Find the main SELECT query
        select_sqls = [sql for sql, _ in db.executed if "FROM articles" in sql and "LIMIT" in sql]
        assert len(select_sqls) >= 1
        assert "tag_id = ?" in select_sqls[0]
        # Should NOT use HAVING for single tag
        assert "HAVING" not in select_sqls[0]

    async def test_two_tag_filter_uses_having_count(self) -> None:
        """GET /api/articles?tag=t1&tag=t2 uses HAVING COUNT for intersection."""
        db = TrackingD1(result_fn=lambda sql, params: [])
        env = MockEnv(db=db)

        client, _ = await _articles_client(env)
        resp = client.get("/api/articles?tag=tag_1&tag=tag_2")

        assert resp.status_code == 200
        select_sqls = [sql for sql, _ in db.executed if "FROM articles" in sql and "LIMIT" in sql]
        assert len(select_sqls) >= 1
        sql = select_sqls[0]
        assert "tag_id IN" in sql
        assert "HAVING COUNT(DISTINCT tag_id)" in sql

    async def test_two_tag_filter_binds_correct_params(self) -> None:
        """Multi-tag filter binds all tag IDs plus the count."""
        db = TrackingD1(result_fn=lambda sql, params: [])
        env = MockEnv(db=db)

        client, _ = await _articles_client(env)
        client.get("/api/articles?tag=aaa&tag=bbb")

        select_queries = [
            (sql, params)
            for sql, params in db.executed
            if "FROM articles" in sql and "LIMIT" in sql
        ]
        assert len(select_queries) >= 1
        _, params = select_queries[0]
        # params should contain: user_id, aaa, bbb, 2 (count), limit, offset
        assert "aaa" in params
        assert "bbb" in params
        assert 2 in params

    async def test_four_tags_accepted(self) -> None:
        """GET /api/articles with 4 tag params succeeds (max allowed)."""
        db = TrackingD1(result_fn=lambda sql, params: [])
        env = MockEnv(db=db)

        client, _ = await _articles_client(env)
        resp = client.get("/api/articles?tag=a&tag=b&tag=c&tag=d")

        assert resp.status_code == 200

    async def test_five_tags_rejected(self) -> None:
        """GET /api/articles with 5+ tag params returns 400."""
        db = TrackingD1(result_fn=lambda sql, params: [])
        env = MockEnv(db=db)

        client, _ = await _articles_client(env)
        resp = client.get("/api/articles?tag=a&tag=b&tag=c&tag=d&tag=e")

        assert resp.status_code == 400
        assert "4" in resp.json()["detail"]

    async def test_three_tag_filter(self) -> None:
        """GET /api/articles?tag=a&tag=b&tag=c uses HAVING COUNT = 3."""
        db = TrackingD1(result_fn=lambda sql, params: [])
        env = MockEnv(db=db)

        client, _ = await _articles_client(env)
        client.get("/api/articles?tag=x&tag=y&tag=z")

        select_queries = [
            (sql, params)
            for sql, params in db.executed
            if "FROM articles" in sql and "LIMIT" in sql
        ]
        assert len(select_queries) >= 1
        _, params = select_queries[0]
        assert 3 in params

    async def test_tag_filter_combines_with_reading_status(self) -> None:
        """Tag filter + reading_status both appear in the query."""
        db = TrackingD1(result_fn=lambda sql, params: [])
        env = MockEnv(db=db)

        client, _ = await _articles_client(env)
        resp = client.get("/api/articles?tag=t1&reading_status=unread")

        assert resp.status_code == 200
        select_sqls = [sql for sql, _ in db.executed if "FROM articles" in sql and "LIMIT" in sql]
        assert len(select_sqls) >= 1
        sql = select_sqls[0]
        assert "reading_status = ?" in sql
        assert "tag_id = ?" in sql


# ---------------------------------------------------------------------------
# Property-based tests for tag operations (Hypothesis)
# ---------------------------------------------------------------------------

# Strategy for valid tag names: 1-100 chars, no leading/trailing whitespace
_tag_name_strategy = st.text(
    min_size=1,
    max_size=100,
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S"),
        whitelist_characters=" -_/",
    ),
).filter(lambda s: len(s.strip()) > 0)


class TestTagPropertyBased:
    """Property-based tests for tag CRUD state machine.

    These tests verify invariants that should hold for *any* valid tag name,
    not just specific examples.
    """

    @given(name=_tag_name_strategy)
    @settings(max_examples=30)
    async def test_create_tag_preserves_trimmed_name(self, name: str) -> None:
        """Creating a tag always returns the trimmed version of the name."""

        def execute(sql: str, params: list) -> list:
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, _ = await _authenticated_client(env)
        resp = client.post("/api/tags", json={"name": name})

        if resp.status_code == 201:
            assert resp.json()["name"] == name.strip()

    @given(name=_tag_name_strategy)
    @settings(max_examples=30)
    async def test_create_then_rename_roundtrip(self, name: str) -> None:
        """A tag can be created and then renamed to any valid name."""
        trimmed = name.strip()
        assume(len(trimmed) > 0)

        created_tag = {
            "id": "tag_roundtrip",
            "user_id": "user_001",
            "name": "original",
            "created_at": "2025-01-01",
        }

        def execute(sql: str, params: list) -> list:
            # For create: no duplicate
            if "SELECT" in sql and "name = ?" in sql and "id != ?" not in sql:
                return []
            # For rename: tag exists
            if "FROM tags" in sql and "id = ?" in sql and "name = ?" not in sql:
                return [created_tag]
            # For rename: no duplicate with new name
            if "FROM tags" in sql and "name = ?" in sql and "id != ?" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, _ = await _authenticated_client(env)
        resp = client.patch(
            "/api/tags/tag_roundtrip",
            json={"name": name},
        )

        assert resp.status_code == 200
        assert resp.json()["name"] == trimmed

    @given(name=st.text(min_size=101, max_size=200))
    @settings(max_examples=10)
    async def test_long_names_always_rejected(self, name: str) -> None:
        """Tag names over 100 characters are always rejected."""
        env = MockEnv()
        client, _ = await _authenticated_client(env)

        resp = client.post("/api/tags", json={"name": name})
        assert resp.status_code == 400

    @given(data=st.data())
    @settings(max_examples=20)
    async def test_duplicate_detection_is_idempotent(self, data: st.DataObject) -> None:
        """Creating the same tag name twice always returns 409 on the second attempt."""
        name = data.draw(_tag_name_strategy)
        trimmed = name.strip()

        existing = {"id": "tag_dup", "user_id": "user_001", "name": trimmed}

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql and "name = ?" in sql:
                return [existing]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, _ = await _authenticated_client(env)
        resp = client.post("/api/tags", json={"name": name})

        assert resp.status_code == 409

    @given(
        tag_ids=st.lists(
            st.text(
                min_size=5,
                max_size=20,
                alphabet=st.characters(whitelist_categories=("L", "N")),
            ),
            min_size=1,
            max_size=6,
            unique=True,
        )
    )
    @settings(max_examples=20)
    async def test_tag_count_limit_invariant(self, tag_ids: list[str]) -> None:
        """Filtering with >4 tags always fails; <=4 always succeeds."""
        db = TrackingD1(result_fn=lambda sql, params: [])
        env = MockEnv(db=db)

        client, _ = await _articles_client(env)
        qs = "&".join(f"tag={t}" for t in tag_ids)
        resp = client.get(f"/api/articles?{qs}")

        if len(tag_ids) <= 4:
            assert resp.status_code == 200
        else:
            assert resp.status_code == 400

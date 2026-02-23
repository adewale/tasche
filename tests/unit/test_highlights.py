"""Tests for Highlights CRUD API (src/highlights/routes.py).

Covers creating, listing, updating, and deleting highlights on articles,
as well as the random highlight endpoint for spaced repetition review.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from src.auth.session import COOKIE_NAME
from src.highlights.routes import article_highlights_router, router
from tests.conftest import (
    ArticleFactory,
    MockD1,
    MockEnv,
    _make_test_app,
    make_highlight,
)
from tests.conftest import (
    _authenticated_client as _authenticated_client_base,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROUTERS = (
    (router, "/api/highlights"),
    (article_highlights_router, "/api/articles"),
)


def _make_app(env):
    return _make_test_app(env, *_ROUTERS)


async def _authenticated_client(env: MockEnv) -> tuple[TestClient, str]:
    return await _authenticated_client_base(env, *_ROUTERS)


# ---------------------------------------------------------------------------
# POST /api/articles/{article_id}/highlights — Create highlight
# ---------------------------------------------------------------------------


class TestCreateHighlight:
    async def test_creates_highlight(self) -> None:
        """POST creates a highlight and returns it."""
        article = ArticleFactory.create(user_id="user_001")
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            if "SELECT id, title FROM articles" in sql:
                return [{"id": article["id"], "title": article["title"]}]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            f"/api/articles/{article['id']}/highlights",
            json={
                "text": "important passage",
                "color": "green",
                "prefix": "the ",
                "suffix": " was",
            },
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["text"] == "important passage"
        assert data["color"] == "green"
        assert data["prefix"] == "the "
        assert data["suffix"] == " was"
        assert "id" in data
        assert data["article_id"] == article["id"]

        insert_calls = [c for c in calls if c["sql"].startswith("INSERT INTO highlights")]
        assert len(insert_calls) == 1

    async def test_requires_text(self) -> None:
        """POST returns 422 when text is empty."""
        article = ArticleFactory.create(user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "SELECT id, title FROM articles" in sql:
                return [{"id": article["id"], "title": article["title"]}]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            f"/api/articles/{article['id']}/highlights",
            json={"text": ""},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422

    async def test_invalid_color_rejected(self) -> None:
        """POST returns 422 when color is not in the valid set."""
        article = ArticleFactory.create(user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "SELECT id, title FROM articles" in sql:
                return [{"id": article["id"], "title": article["title"]}]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            f"/api/articles/{article['id']}/highlights",
            json={"text": "some text", "color": "red"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422

    async def test_article_not_found(self) -> None:
        """POST returns 404 when article does not belong to user."""
        db = MockD1()  # returns [] for all queries
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/nonexistent/highlights",
            json={"text": "some text"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    async def test_requires_auth(self) -> None:
        """POST returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/api/articles/art_001/highlights",
            json={"text": "some text"},
        )

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id}/highlights — List article highlights
# ---------------------------------------------------------------------------


class TestListArticleHighlights:
    async def test_lists_highlights_for_article(self) -> None:
        """GET returns highlights for a specific article."""
        article = ArticleFactory.create(user_id="user_001")
        hl1 = make_highlight(id="hl_001", article_id=article["id"], text="first")
        hl2 = make_highlight(id="hl_002", article_id=article["id"], text="second")

        def execute(sql: str, params: list) -> list:
            if "SELECT id, title FROM articles" in sql:
                return [{"id": article["id"], "title": article["title"]}]
            if "SELECT * FROM highlights" in sql:
                return [hl1, hl2]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            f"/api/articles/{article['id']}/highlights",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    async def test_returns_empty_for_no_highlights(self) -> None:
        """GET returns empty list when article has no highlights."""
        article = ArticleFactory.create(user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "SELECT id, title FROM articles" in sql:
                return [{"id": article["id"], "title": article["title"]}]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            f"/api/articles/{article['id']}/highlights",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/highlights — List all highlights
# ---------------------------------------------------------------------------


class TestListAllHighlights:
    async def test_lists_all_highlights(self) -> None:
        """GET /api/highlights returns highlights with article titles."""
        hl1 = {**make_highlight(id="hl_001"), "article_title": "Article One"}
        hl2 = {**make_highlight(id="hl_002", article_id="art_002"), "article_title": "Article Two"}

        def execute(sql: str, params: list) -> list:
            if (
                "SELECT h.*, a.title AS article_title" in sql
                and "ORDER BY h.created_at DESC" in sql
            ):
                return [hl1, hl2]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/highlights",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["article_title"] == "Article One"

    async def test_pagination(self) -> None:
        """GET /api/highlights respects limit and offset."""
        calls = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/highlights?limit=10&offset=20",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        # Check that limit and offset were passed
        select_calls = [c for c in calls if "LIMIT" in c["sql"]]
        assert len(select_calls) == 1
        assert select_calls[0]["params"][-2] == 10  # limit
        assert select_calls[0]["params"][-1] == 20  # offset


# ---------------------------------------------------------------------------
# GET /api/highlights/random — Random highlight
# ---------------------------------------------------------------------------


class TestRandomHighlight:
    async def test_returns_random_highlight(self) -> None:
        """GET /api/highlights/random returns a highlight with article title."""
        hl = {**make_highlight(), "article_title": "Test Article"}

        def execute(sql: str, params: list) -> list:
            if "ORDER BY RANDOM()" in sql:
                return [hl]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/highlights/random",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "highlighted text"
        assert data["article_title"] == "Test Article"

    async def test_returns_404_when_no_highlights(self) -> None:
        """GET /api/highlights/random returns 404 when there are no highlights."""
        db = MockD1()  # returns [] for all queries
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/highlights/random",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/highlights/{highlight_id} — Update highlight
# ---------------------------------------------------------------------------


class TestUpdateHighlight:
    async def test_updates_note(self) -> None:
        """PATCH updates the note field."""
        hl = make_highlight()
        updated_hl = {**hl, "note": "my note", "article_title": "Test"}

        def execute(sql: str, params: list) -> list:
            if "SELECT h.* FROM highlights h" in sql and "WHERE h.id = ?" in sql:
                return [hl]
            if "UPDATE highlights" in sql:
                return []
            if "SELECT h.*, a.title AS article_title" in sql and "WHERE h.id = ?" in sql:
                return [updated_hl]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/highlights/hl_001",
            json={"note": "my note"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["note"] == "my note"

    async def test_updates_color(self) -> None:
        """PATCH updates the color field."""
        hl = make_highlight()
        updated_hl = {**hl, "color": "blue", "article_title": "Test"}

        def execute(sql: str, params: list) -> list:
            if "SELECT h.* FROM highlights h" in sql and "WHERE h.id = ?" in sql:
                return [hl]
            if "UPDATE highlights" in sql:
                return []
            if "SELECT h.*, a.title AS article_title" in sql and "WHERE h.id = ?" in sql:
                return [updated_hl]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/highlights/hl_001",
            json={"color": "blue"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["color"] == "blue"

    async def test_rejects_invalid_color(self) -> None:
        """PATCH returns 422 for invalid color."""
        hl = make_highlight()

        def execute(sql: str, params: list) -> list:
            if "SELECT h.* FROM highlights h" in sql:
                return [hl]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/highlights/hl_001",
            json={"color": "purple"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422

    async def test_no_updatable_fields(self) -> None:
        """PATCH returns 422 when no recognized fields are provided."""
        hl = make_highlight()

        def execute(sql: str, params: list) -> list:
            if "SELECT h.* FROM highlights h" in sql:
                return [hl]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/highlights/hl_001",
            json={"bogus": "field"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422

    async def test_highlight_not_found(self) -> None:
        """PATCH returns 404 when highlight doesn't exist."""
        db = MockD1()
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/highlights/nonexistent",
            json={"note": "test"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /api/highlights/{highlight_id} — Delete highlight
# ---------------------------------------------------------------------------


class TestDeleteHighlight:
    async def test_deletes_highlight(self) -> None:
        """DELETE removes a highlight."""
        hl = make_highlight()
        calls = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            if "SELECT h.* FROM highlights h" in sql:
                return [hl]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.delete(
            "/api/highlights/hl_001",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 204
        delete_calls = [c for c in calls if c["sql"].startswith("DELETE FROM highlights")]
        assert len(delete_calls) == 1

    async def test_highlight_not_found(self) -> None:
        """DELETE returns 404 when highlight doesn't exist."""
        db = MockD1()
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.delete(
            "/api/highlights/nonexistent",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    async def test_requires_auth(self) -> None:
        """DELETE returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.delete("/api/highlights/hl_001")

        assert resp.status_code == 401

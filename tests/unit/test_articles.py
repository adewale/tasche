"""Tests for Phase 3 — Article CRUD API (src/articles/routes.py).

Covers creating, listing, retrieving, updating, and deleting articles,
as well as authentication enforcement on all endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.articles.routes import router
from src.auth.session import COOKIE_NAME, create_session
from tests.conftest import ArticleFactory, MockD1, MockEnv, MockQueue, MockR2

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_DATA: dict[str, Any] = {
    "user_id": "user_001",
    "email": "test@example.com",
    "username": "tester",
    "avatar_url": "https://github.com/avatar.png",
    "created_at": "2025-01-01T00:00:00",
}


def _make_app(env: Any) -> FastAPI:
    """Create a FastAPI app with the articles router and env injection."""
    test_app = FastAPI()

    @test_app.middleware("http")
    async def inject_env(request, call_next):
        request.scope["env"] = env
        return await call_next(request)

    test_app.include_router(router, prefix="/api/articles")
    return test_app


async def _authenticated_client(env: MockEnv) -> tuple[TestClient, str]:
    """Create a test client with a valid session cookie."""
    session_id = await create_session(env.SESSIONS, _USER_DATA)
    app = _make_app(env)
    client = TestClient(app, raise_server_exceptions=False)
    return client, session_id


# ---------------------------------------------------------------------------
# POST /api/articles — Create article
# ---------------------------------------------------------------------------


class TestCreateArticle:
    async def test_creates_article_and_enqueues_job(self) -> None:
        """POST /api/articles inserts into D1 and sends to ARTICLE_QUEUE."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/article", "title": "My Article"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["status"] == "pending"

        # Verify D1 insert was called
        insert_calls = [c for c in calls if c["sql"].startswith("INSERT")]
        assert len(insert_calls) == 1

        # Verify queue message was sent
        assert len(queue.messages) == 1
        msg = queue.messages[0]
        assert msg["type"] == "article_processing"
        assert msg["url"] == "https://example.com/article"

    async def test_rejects_duplicate_url(self) -> None:
        """POST /api/articles returns 409 when URL already exists."""
        existing = ArticleFactory.create(
            user_id="user_001",
            original_url="https://example.com/existing",
        )

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                return [existing]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/existing"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    async def test_rejects_invalid_url(self) -> None:
        """POST /api/articles returns 422 for an invalid URL."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/articles",
            json={"url": "ftp://not-allowed.com/file"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422

    async def test_rejects_empty_url(self) -> None:
        """POST /api/articles returns 422 when url is empty."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/articles",
            json={"url": ""},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/articles — List articles
# ---------------------------------------------------------------------------


class TestListArticles:
    async def test_returns_users_articles(self) -> None:
        """GET /api/articles returns a list of the user's articles."""
        articles = [
            ArticleFactory.create(user_id="user_001", title="First"),
            ArticleFactory.create(user_id="user_001", title="Second"),
        ]

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "FROM articles" in sql and "LIMIT" in sql:
                return articles
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["title"] == "First"
        assert data[1]["title"] == "Second"

    async def test_filters_by_reading_status(self) -> None:
        """GET /api/articles?reading_status=unread filters correctly."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/articles?reading_status=unread",
            cookies={COOKIE_NAME: session_id},
        )

        # Verify the query includes reading_status filter
        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        assert "reading_status = ?" in select_calls[0]["sql"]


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id} — Get single article
# ---------------------------------------------------------------------------


class TestGetArticle:
    async def test_returns_single_article(self) -> None:
        """GET /api/articles/{id} returns the article metadata."""
        article = ArticleFactory.create(id="art_123", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_123":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_123",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "art_123"

    async def test_returns_404_for_missing_article(self) -> None:
        """GET /api/articles/{id} returns 404 when article doesn't exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/nonexistent",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/articles/{article_id} — Update article
# ---------------------------------------------------------------------------


class TestUpdateArticle:
    async def test_updates_reading_status(self) -> None:
        """PATCH /api/articles/{id} updates reading_status and returns updated article."""
        article = ArticleFactory.create(id="art_456", user_id="user_001")
        updated_article = {**article, "reading_status": "reading"}

        call_count = 0

        def execute(sql: str, params: list) -> list:
            nonlocal call_count
            call_count += 1
            if sql.startswith("SELECT") and "id = ?" in sql and params[0] == "art_456":
                # First SELECT returns existing, subsequent returns updated
                if call_count <= 1:
                    return [article]
                return [updated_article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_456",
            json={"reading_status": "reading"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["reading_status"] == "reading"

    async def test_returns_404_for_missing_article(self) -> None:
        """PATCH /api/articles/{id} returns 404 when article doesn't exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/nonexistent",
            json={"reading_status": "reading"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    async def test_rejects_empty_update(self) -> None:
        """PATCH /api/articles/{id} returns 422 when no updatable fields are provided."""
        article = ArticleFactory.create(id="art_789", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_789":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_789",
            json={"unknown_field": "value"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/articles/{article_id} — Delete article
# ---------------------------------------------------------------------------


class TestDeleteArticle:
    async def test_deletes_article(self) -> None:
        """DELETE /api/articles/{id} removes article from D1 and R2."""
        article = ArticleFactory.create(id="art_del", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and params[0] == "art_del":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        # Pre-populate R2
        await r2.put("articles/art_del/content.html", "<p>html</p>")
        await r2.put("articles/art_del/content.md", "# markdown")

        client, session_id = await _authenticated_client(env)
        resp = client.delete(
            "/api/articles/art_del",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 204

    async def test_returns_404_for_missing_article(self) -> None:
        """DELETE /api/articles/{id} returns 404 when article doesn't exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.delete(
            "/api/articles/nonexistent",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Authentication enforcement
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Input validation (field length limits)
# ---------------------------------------------------------------------------


class TestInputValidation:
    async def test_rejects_url_too_long(self) -> None:
        """POST /api/articles returns 400 when URL exceeds 2048 chars."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        long_url = "https://example.com/" + "a" * 2100
        resp = client.post(
            "/api/articles",
            json={"url": long_url},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 400
        assert "2048" in resp.json()["detail"]

    async def test_rejects_title_too_long(self) -> None:
        """POST /api/articles returns 400 when title exceeds 500 chars."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com", "title": "x" * 501},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 400
        assert "500" in resp.json()["detail"]

    async def test_rejects_title_too_long_on_update(self) -> None:
        """PATCH /api/articles/{id} returns 400 when title exceeds 500 chars."""
        article = ArticleFactory.create(id="art_valid", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_valid":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_valid",
            json={"title": "x" * 501},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 400
        assert "500" in resp.json()["detail"]


class TestAuthRequired:
    def test_post_returns_401_without_auth(self) -> None:
        """POST /api/articles returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/articles", json={"url": "https://example.com"})
        assert resp.status_code == 401

    def test_get_list_returns_401_without_auth(self) -> None:
        """GET /api/articles returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles")
        assert resp.status_code == 401

    def test_get_single_returns_401_without_auth(self) -> None:
        """GET /api/articles/{id} returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles/some_id")
        assert resp.status_code == 401

    def test_patch_returns_401_without_auth(self) -> None:
        """PATCH /api/articles/{id} returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.patch("/api/articles/some_id", json={"title": "new"})
        assert resp.status_code == 401

    def test_delete_returns_401_without_auth(self) -> None:
        """DELETE /api/articles/{id} returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/api/articles/some_id")
        assert resp.status_code == 401

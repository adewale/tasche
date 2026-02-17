"""Integration tests for Tasche API flows.

Tests the full API using FastAPI's TestClient with the app from entry.py,
injecting mock Cloudflare bindings via middleware.  These tests verify
end-to-end flows rather than individual route behavior.

Security audit notes (Phase 14):
- All SQL queries use parameterized statements (? placeholders) -- no string interpolation
- All endpoints except /api/health and /api/auth/* require authentication
- URL validation includes SSRF protection (private network blocklist) at 3 points:
  URL submission, after redirect resolution, and image downloads
- FTS5 queries are sanitized (operators stripped, words quoted as literals)
- Input validation enforces field length limits on all user-supplied text
- Session cookies are HttpOnly, Secure, SameSite=Lax
- CSRF state token validated in OAuth callback flow
- Session revocation on ALLOWED_EMAILS change re-checked on every request
- Queue error categorization: transient errors retry, permanent errors fail
- TTS idempotency: checks audio_status before enqueuing (409 if pending/generating)
- Cross-store deletion: R2 content deleted before D1 reference
- Fixed: markdown renderer re-encodes " as &quot; in decoded URLs before placing
  into href/src attributes, preventing attribute breakout XSS
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.auth.session import COOKIE_NAME
from tests.conftest import (
    ArticleFactory,
    MockD1,
    MockEnv,
    MockKV,
    MockQueue,
    MockR2,
)

# ---------------------------------------------------------------------------
# Stateful mock D1 — handles basic SQL routing for integration tests
# ---------------------------------------------------------------------------


class StatefulMockD1(MockD1):
    """A MockD1 that routes queries to return appropriate mock data.

    Maintains in-memory state for articles and tags so that create-then-read
    flows work end-to-end.
    """

    def __init__(self) -> None:
        self.articles: dict[str, dict[str, Any]] = {}
        self.tags: dict[str, dict[str, Any]] = {}
        self.article_tags: list[dict[str, str]] = []
        self.users: dict[str, dict[str, Any]] = {}
        super().__init__(execute=self._route_query)

    def _route_query(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        sql_upper = sql.strip().upper()

        # --- DELETE (must be checked before SELECT to avoid "FROM X" substring matches) ---
        if sql_upper.startswith("DELETE FROM ARTICLE_TAGS"):
            self.article_tags = [
                at for at in self.article_tags
                if not (at["article_id"] == params[0] and at["tag_id"] == params[1])
            ]
            return []

        if sql_upper.startswith("DELETE FROM TAGS"):
            tag_id = params[0]
            self.tags.pop(tag_id, None)
            self.article_tags = [
                at for at in self.article_tags if at["tag_id"] != tag_id
            ]
            return []

        if sql_upper.startswith("DELETE FROM ARTICLES"):
            article_id = params[0]
            self.articles.pop(article_id, None)
            self.article_tags = [
                at for at in self.article_tags if at["article_id"] != article_id
            ]
            return []

        # --- INSERT ---
        if sql_upper.startswith("INSERT INTO ARTICLES"):
            self._handle_insert_article(sql, params)
            return []

        if sql_upper.startswith("INSERT INTO TAGS"):
            self._handle_insert_tag(sql, params)
            return []

        if sql_upper.startswith("INSERT INTO ARTICLE_TAGS"):
            self.article_tags.append({
                "article_id": params[0],
                "tag_id": params[1],
            })
            return []

        # --- UPDATE ---
        if sql_upper.startswith("UPDATE ARTICLES"):
            return self._handle_update_article(sql, params)

        # --- SELECT (articles with FTS) ---
        if "FROM ARTICLES" in sql_upper and "ARTICLES_FTS" in sql_upper:
            return self._handle_search(sql, params)

        # --- SELECT (articles) ---
        if "FROM ARTICLES" in sql_upper:
            return self._handle_select_articles(sql, params)

        # --- SELECT (tags with join) ---
        if "FROM TAGS" in sql_upper and "ARTICLE_TAGS" in sql_upper:
            return self._handle_select_article_tags(sql, params)

        # --- SELECT (tags) ---
        if "FROM TAGS" in sql_upper:
            return self._handle_select_tags(sql, params)

        # --- SELECT (article_tags) ---
        if "FROM ARTICLE_TAGS" in sql_upper:
            return self._handle_select_article_tag_assoc(sql, params)

        return []

    def _handle_insert_article(self, sql: str, params: list[Any]) -> dict[str, Any]:
        article_id = params[0]
        article = ArticleFactory.create(
            id=article_id,
            user_id=params[1],
            original_url=params[2],
            domain=params[3],
            title=params[4] or f"Article {article_id[:8]}",
            status="pending",
            reading_status="unread",
        )
        self.articles[article_id] = article
        return article

    def _handle_insert_tag(self, sql: str, params: list[Any]) -> dict[str, Any]:
        tag = {
            "id": params[0],
            "user_id": params[1],
            "name": params[2],
            "created_at": params[3],
        }
        self.tags[params[0]] = tag
        return tag

    def _handle_select_articles(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        sql_upper = sql.upper()

        # Duplicate check (original_url OR final_url OR canonical_url)
        # Must be checked BEFORE single-article-by-ID because both contain "ID = ?"
        if "ORIGINAL_URL = ?" in sql_upper and "FINAL_URL = ?" in sql_upper:
            user_id = params[0]
            url = params[1]
            for a in self.articles.values():
                if a["user_id"] == user_id and (
                    a["original_url"] == url
                    or a.get("final_url") == url
                    or a.get("canonical_url") == url
                ):
                    return [a]
            return []

        # Single article by ID (WHERE id = ? AND user_id = ?)
        if "WHERE" in sql_upper and "LIMIT" not in sql_upper:
            article_id = params[0]
            user_id = params[1]
            article = self.articles.get(article_id)
            if article and article["user_id"] == user_id:
                return [article]
            return []

        # List articles (with filters)
        if "LIMIT" in sql_upper:
            user_id = params[0]
            results = [
                a for a in self.articles.values() if a["user_id"] == user_id
            ]
            # Sort by created_at descending
            results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return results

        return []

    def _handle_select_tags(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        sql_upper = sql.upper()

        # Tag by name (duplicate check) — "WHERE user_id = ? AND name = ?"
        if "NAME = ?" in sql_upper:
            user_id = params[0]
            name = params[1]
            for t in self.tags.values():
                if t["user_id"] == user_id and t["name"] == name:
                    return [t]
            return []

        # Single tag by ID — "WHERE id = ? AND user_id = ?"
        # Only matches when there are exactly 2 params and no ORDER BY
        if len(params) == 2 and "ORDER BY" not in sql_upper:
            tag_id = params[0]
            user_id = params[1]
            tag = self.tags.get(tag_id)
            if tag and tag["user_id"] == user_id:
                return [tag]
            return []

        # List all tags — "WHERE user_id = ? ORDER BY name"
        if len(params) == 1:
            user_id = params[0]
            results = [t for t in self.tags.values() if t["user_id"] == user_id]
            results.sort(key=lambda x: x.get("name", ""))
            return results

        return []

    def _handle_select_article_tags(
        self, sql: str, params: list[Any],
    ) -> list[dict[str, Any]]:
        article_id = params[0]
        user_id = params[1]
        tag_ids = {at["tag_id"] for at in self.article_tags if at["article_id"] == article_id}
        return [
            t for t in self.tags.values()
            if t["id"] in tag_ids and t["user_id"] == user_id
        ]

    def _handle_select_article_tag_assoc(
        self, sql: str, params: list[Any],
    ) -> list[dict[str, Any]]:
        article_id = params[0]
        tag_id = params[1]
        for at in self.article_tags:
            if at["article_id"] == article_id and at["tag_id"] == tag_id:
                return [at]
        return []

    def _handle_update_article(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        sql_upper = sql.upper()
        # The last two params are always article_id and user_id in WHERE clause
        article_id = params[-2]
        user_id = params[-1]
        article = self.articles.get(article_id)
        if article and article["user_id"] == user_id:
            # Parse SET clause fields from sql
            if "READING_STATUS = ?" in sql_upper:
                idx = 0
                for i, token in enumerate(sql.split("?")):
                    if "reading_status" in token.lower():
                        article["reading_status"] = params[idx]
                        break
                    idx += 1
            if "IS_FAVORITE = ?" in sql_upper:
                idx = 0
                for i, token in enumerate(sql.split("?")):
                    if "is_favorite" in token.lower():
                        article["is_favorite"] = params[idx]
                        break
                    idx += 1
            if "LISTEN_LATER = 1" in sql_upper:
                article["listen_later"] = 1
            if "AUDIO_STATUS" in sql_upper:
                if "AUDIO_STATUS = 'PENDING'" in sql_upper:
                    article["audio_status"] = "pending"
                elif "audio_status = ?" in sql:
                    # Find the audio_status param
                    idx = 0
                    for i, token in enumerate(sql.split("?")):
                        if "audio_status" in token.lower():
                            article["audio_status"] = params[idx]
                            break
                        idx += 1
            if "UPDATED_AT = ?" in sql_upper:
                # updated_at is always set
                pass
        return []

    def _handle_search(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        query = params[0].lower().replace('"', "")
        user_id = params[1]
        results = []
        for a in self.articles.values():
            if a["user_id"] != user_id:
                continue
            title = (a.get("title") or "").lower()
            excerpt = (a.get("excerpt") or "").lower()
            md = (a.get("markdown_content") or "").lower()
            if query in title or query in excerpt or query in md:
                results.append(a)
        return results


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


_USER_DATA: dict[str, Any] = {
    "user_id": "user_001",
    "email": "test@example.com",
    "username": "tester",
    "avatar_url": "https://github.com/avatar.png",
    "created_at": "2025-01-01T00:00:00",
}


@pytest.fixture
def env() -> MockEnv:
    """Create a MockEnv with a stateful D1 mock."""
    return MockEnv(
        db=StatefulMockD1(),
        content=MockR2(),
        sessions=MockKV(),
        article_queue=MockQueue(),
    )


@pytest.fixture
def test_app(env: MockEnv) -> TestClient:
    """Create a TestClient that injects mock env into every request."""
    from fastapi import FastAPI
    from starlette.requests import Request as StarletteRequest

    test_application = FastAPI()

    @test_application.middleware("http")
    async def inject_env(request: StarletteRequest, call_next):
        request.scope["env"] = env
        return await call_next(request)

    # Mount all the same routers as entry.py
    from src.articles.routes import router as articles_router  # noqa: E402
    from src.auth.routes import router as auth_router  # noqa: E402
    from src.search.routes import router as search_router  # noqa: E402
    from src.tags.routes import article_tags_router  # noqa: E402
    from src.tags.routes import router as tags_router  # noqa: E402
    from src.tts.routes import router as tts_router  # noqa: E402

    @test_application.get("/api/health")
    async def health():
        return {"status": "ok"}

    test_application.include_router(auth_router, prefix="/api/auth")
    test_application.include_router(articles_router, prefix="/api/articles")
    test_application.include_router(tags_router, prefix="/api/tags")
    test_application.include_router(article_tags_router, prefix="/api/articles")
    test_application.include_router(search_router, prefix="/api/search")
    test_application.include_router(tts_router, prefix="/api/articles")

    return TestClient(test_application, raise_server_exceptions=False)


@pytest.fixture
async def auth_cookie(env: MockEnv) -> str:
    """Create a session in MockKV and return the session cookie value."""
    from src.auth.session import create_session

    session_id = await create_session(env.SESSIONS, _USER_DATA)
    return session_id


# ---------------------------------------------------------------------------
# 1. Health check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    def test_health_returns_ok(self, test_app: TestClient) -> None:
        """GET /api/health returns 200 with status ok."""
        resp = test_app.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# 2. Auth flow
# ---------------------------------------------------------------------------


class TestAuthFlow:
    def test_session_without_cookie_returns_401(self, test_app: TestClient) -> None:
        """GET /api/auth/session without a cookie returns 401."""
        resp = test_app.get("/api/auth/session")
        assert resp.status_code == 401

    def test_session_with_invalid_cookie_returns_401(self, test_app: TestClient) -> None:
        """GET /api/auth/session with an invalid cookie returns 401."""
        resp = test_app.get(
            "/api/auth/session",
            cookies={COOKIE_NAME: "invalid_session_id"},
        )
        assert resp.status_code == 401

    async def test_session_with_valid_cookie_returns_user(
        self, test_app: TestClient, auth_cookie: str,
    ) -> None:
        """GET /api/auth/session with a valid session returns user data."""
        resp = test_app.get(
            "/api/auth/session",
            cookies={COOKIE_NAME: auth_cookie},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "user_001"
        assert data["email"] == "test@example.com"

    def test_login_endpoint_exists(self, test_app: TestClient) -> None:
        """GET /api/auth/login returns a redirect (302)."""
        resp = test_app.get("/api/auth/login", follow_redirects=False)
        # Should redirect to GitHub OAuth
        assert resp.status_code == 302
        assert "github.com" in resp.headers.get("location", "")

    def test_logout_clears_session(self, test_app: TestClient) -> None:
        """POST /api/auth/logout returns 200."""
        resp = test_app.post("/api/auth/logout")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. Article CRUD flow
# ---------------------------------------------------------------------------


class TestArticleCrudFlow:
    async def test_full_article_lifecycle(
        self, test_app: TestClient, auth_cookie: str, env: MockEnv,
    ) -> None:
        """Create, list, get, update, and delete an article."""
        cookies = {COOKIE_NAME: auth_cookie}

        # --- Create ---
        resp = test_app.post(
            "/api/articles",
            json={"url": "https://example.com/test-article", "title": "Test Article"},
            cookies=cookies,
        )
        assert resp.status_code == 201
        data = resp.json()
        article_id = data["id"]
        assert data["status"] == "pending"

        # Verify queue message was sent
        assert len(env.ARTICLE_QUEUE.messages) == 1
        msg = env.ARTICLE_QUEUE.messages[0]
        assert msg["type"] == "article_processing"

        # --- List ---
        resp = test_app.get("/api/articles", cookies=cookies)
        assert resp.status_code == 200
        articles = resp.json()
        assert len(articles) >= 1
        assert any(a["id"] == article_id for a in articles)

        # --- Get ---
        resp = test_app.get(f"/api/articles/{article_id}", cookies=cookies)
        assert resp.status_code == 200
        article = resp.json()
        assert article["id"] == article_id
        assert article["original_url"] == "https://example.com/test-article"

        # --- Update reading status ---
        resp = test_app.patch(
            f"/api/articles/{article_id}",
            json={"reading_status": "reading"},
            cookies=cookies,
        )
        assert resp.status_code == 200

        # --- Verify update ---
        resp = test_app.get(f"/api/articles/{article_id}", cookies=cookies)
        assert resp.status_code == 200
        assert resp.json()["reading_status"] == "reading"

        # --- Delete ---
        resp = test_app.delete(f"/api/articles/{article_id}", cookies=cookies)
        assert resp.status_code == 204

        # --- Verify deleted ---
        resp = test_app.get(f"/api/articles/{article_id}", cookies=cookies)
        assert resp.status_code == 404

    async def test_create_rejects_unauthenticated(self, test_app: TestClient) -> None:
        """POST /api/articles without auth returns 401."""
        resp = test_app.post(
            "/api/articles",
            json={"url": "https://example.com/test"},
        )
        assert resp.status_code == 401

    async def test_create_rejects_duplicate_url(
        self, test_app: TestClient, auth_cookie: str,
    ) -> None:
        """POST /api/articles returns 409 for a duplicate URL."""
        cookies = {COOKIE_NAME: auth_cookie}

        # Create first
        resp = test_app.post(
            "/api/articles",
            json={"url": "https://example.com/duplicate"},
            cookies=cookies,
        )
        assert resp.status_code == 201

        # Try to create again
        resp = test_app.post(
            "/api/articles",
            json={"url": "https://example.com/duplicate"},
            cookies=cookies,
        )
        assert resp.status_code == 409

    async def test_create_rejects_private_url(
        self, test_app: TestClient, auth_cookie: str,
    ) -> None:
        """POST /api/articles rejects URLs pointing to private networks (SSRF)."""
        cookies = {COOKIE_NAME: auth_cookie}

        for url in [
            "http://127.0.0.1/secret",
            "http://10.0.0.1/admin",
            "http://169.254.169.254/latest/meta-data/",
            "http://localhost:8080/internal",
        ]:
            resp = test_app.post(
                "/api/articles",
                json={"url": url},
                cookies=cookies,
            )
            assert resp.status_code == 422, f"Expected 422 for {url}, got {resp.status_code}"

    async def test_create_validates_url_length(
        self, test_app: TestClient, auth_cookie: str,
    ) -> None:
        """POST /api/articles rejects URLs exceeding 2048 characters."""
        cookies = {COOKIE_NAME: auth_cookie}

        long_url = "https://example.com/" + "a" * 2050
        resp = test_app.post(
            "/api/articles",
            json={"url": long_url},
            cookies=cookies,
        )
        assert resp.status_code == 400

    async def test_get_nonexistent_article_returns_404(
        self, test_app: TestClient, auth_cookie: str,
    ) -> None:
        """GET /api/articles/{id} returns 404 for nonexistent article."""
        cookies = {COOKIE_NAME: auth_cookie}
        resp = test_app.get("/api/articles/nonexistent_id", cookies=cookies)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. Tags flow
# ---------------------------------------------------------------------------


class TestTagsFlow:
    async def test_full_tag_lifecycle(
        self, test_app: TestClient, auth_cookie: str, env: MockEnv,
    ) -> None:
        """Create tag, list tags, associate with article, list article tags,
        remove association, delete tag."""
        cookies = {COOKIE_NAME: auth_cookie}

        # --- Create a tag ---
        resp = test_app.post(
            "/api/tags",
            json={"name": "python"},
            cookies=cookies,
        )
        assert resp.status_code == 201
        tag = resp.json()
        tag_id = tag["id"]
        assert tag["name"] == "python"

        # --- List tags ---
        resp = test_app.get("/api/tags", cookies=cookies)
        assert resp.status_code == 200
        tags = resp.json()
        assert len(tags) >= 1
        assert any(t["id"] == tag_id for t in tags)

        # --- Create an article to tag ---
        resp = test_app.post(
            "/api/articles",
            json={"url": "https://example.com/tagged-article"},
            cookies=cookies,
        )
        assert resp.status_code == 201
        article_id = resp.json()["id"]

        # --- Associate tag with article ---
        resp = test_app.post(
            f"/api/articles/{article_id}/tags",
            json={"tag_id": tag_id},
            cookies=cookies,
        )
        assert resp.status_code == 201
        assoc = resp.json()
        assert assoc["article_id"] == article_id
        assert assoc["tag_id"] == tag_id

        # --- List article tags ---
        resp = test_app.get(f"/api/articles/{article_id}/tags", cookies=cookies)
        assert resp.status_code == 200
        article_tags = resp.json()
        assert len(article_tags) == 1
        assert article_tags[0]["id"] == tag_id

        # --- Remove tag from article ---
        resp = test_app.delete(
            f"/api/articles/{article_id}/tags/{tag_id}",
            cookies=cookies,
        )
        assert resp.status_code == 204

        # --- Verify removed ---
        resp = test_app.get(f"/api/articles/{article_id}/tags", cookies=cookies)
        assert resp.status_code == 200
        assert len(resp.json()) == 0

        # --- Delete tag ---
        resp = test_app.delete(f"/api/tags/{tag_id}", cookies=cookies)
        assert resp.status_code == 204

        # --- Verify tag deleted ---
        resp = test_app.get("/api/tags", cookies=cookies)
        assert resp.status_code == 200
        assert not any(t["id"] == tag_id for t in resp.json())

    async def test_create_tag_rejects_empty_name(
        self, test_app: TestClient, auth_cookie: str,
    ) -> None:
        """POST /api/tags rejects empty tag names."""
        cookies = {COOKIE_NAME: auth_cookie}
        resp = test_app.post("/api/tags", json={"name": ""}, cookies=cookies)
        assert resp.status_code == 422

    async def test_create_tag_rejects_long_name(
        self, test_app: TestClient, auth_cookie: str,
    ) -> None:
        """POST /api/tags rejects names exceeding 100 characters."""
        cookies = {COOKIE_NAME: auth_cookie}
        resp = test_app.post(
            "/api/tags",
            json={"name": "a" * 101},
            cookies=cookies,
        )
        assert resp.status_code == 400

    async def test_duplicate_tag_name_returns_409(
        self, test_app: TestClient, auth_cookie: str,
    ) -> None:
        """POST /api/tags returns 409 for duplicate tag names."""
        cookies = {COOKIE_NAME: auth_cookie}

        test_app.post("/api/tags", json={"name": "duplicate"}, cookies=cookies)
        resp = test_app.post("/api/tags", json={"name": "duplicate"}, cookies=cookies)
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 5. Search flow
# ---------------------------------------------------------------------------


class TestSearchFlow:
    async def test_search_returns_matching_articles(
        self, test_app: TestClient, auth_cookie: str, env: MockEnv,
    ) -> None:
        """GET /api/search?q=test returns matching articles."""
        cookies = {COOKIE_NAME: auth_cookie}

        # Create an article with searchable content
        resp = test_app.post(
            "/api/articles",
            json={
                "url": "https://example.com/searchable",
                "title": "Machine Learning Guide",
            },
            cookies=cookies,
        )
        assert resp.status_code == 201

        # Search for it
        resp = test_app.get("/api/search?q=machine", cookies=cookies)
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) >= 1

    async def test_search_requires_query(
        self, test_app: TestClient, auth_cookie: str,
    ) -> None:
        """GET /api/search without q returns 422."""
        cookies = {COOKIE_NAME: auth_cookie}
        resp = test_app.get("/api/search", cookies=cookies)
        assert resp.status_code == 422

    async def test_search_sanitizes_fts5_operators(
        self, test_app: TestClient, auth_cookie: str,
    ) -> None:
        """GET /api/search with FTS5 operators does not cause errors."""
        cookies = {COOKIE_NAME: auth_cookie}

        # These queries contain FTS5 special characters that should be sanitized
        for query in ["OR AND NOT", "test*", "title:secret", 'hello "world"']:
            resp = test_app.get(f"/api/search?q={query}", cookies=cookies)
            assert resp.status_code in (200, 422), (
                f"Unexpected status {resp.status_code} for query '{query}'"
            )

    async def test_search_requires_auth(self, test_app: TestClient) -> None:
        """GET /api/search without auth returns 401."""
        resp = test_app.get("/api/search?q=test")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 6. Listen Later flow
# ---------------------------------------------------------------------------


class TestListenLaterFlow:
    async def test_listen_later_enqueues_tts(
        self, test_app: TestClient, auth_cookie: str, env: MockEnv,
    ) -> None:
        """POST /api/articles/{id}/listen-later enqueues a TTS job."""
        cookies = {COOKIE_NAME: auth_cookie}

        # Create an article first
        resp = test_app.post(
            "/api/articles",
            json={"url": "https://example.com/listen-article"},
            cookies=cookies,
        )
        assert resp.status_code == 201
        article_id = resp.json()["id"]

        # Clear queue from article creation
        env.ARTICLE_QUEUE.messages.clear()

        # Request TTS generation
        resp = test_app.post(
            f"/api/articles/{article_id}/listen-later",
            cookies=cookies,
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["audio_status"] == "pending"

        # Verify TTS queue message
        assert len(env.ARTICLE_QUEUE.messages) == 1
        msg = env.ARTICLE_QUEUE.messages[0]
        assert msg["type"] == "tts_generation"
        assert msg["article_id"] == article_id

    async def test_listen_later_idempotent_when_pending(
        self, test_app: TestClient, auth_cookie: str, env: MockEnv,
    ) -> None:
        """POST /api/articles/{id}/listen-later returns 409 if already pending."""
        cookies = {COOKIE_NAME: auth_cookie}

        # Create an article
        resp = test_app.post(
            "/api/articles",
            json={"url": "https://example.com/idempotent-listen"},
            cookies=cookies,
        )
        article_id = resp.json()["id"]

        # First listen-later request
        resp = test_app.post(
            f"/api/articles/{article_id}/listen-later",
            cookies=cookies,
        )
        assert resp.status_code == 202

        # Second request should return 409 (already pending)
        resp = test_app.post(
            f"/api/articles/{article_id}/listen-later",
            cookies=cookies,
        )
        assert resp.status_code == 409

    async def test_listen_later_requires_auth(self, test_app: TestClient) -> None:
        """POST /api/articles/{id}/listen-later without auth returns 401."""
        resp = test_app.post("/api/articles/some-id/listen-later")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 7. Content serving
# ---------------------------------------------------------------------------


class TestContentServing:
    async def test_get_article_content_from_r2(
        self, test_app: TestClient, auth_cookie: str, env: MockEnv,
    ) -> None:
        """GET /api/articles/{id}/content serves HTML from R2."""
        cookies = {COOKIE_NAME: auth_cookie}

        # Create an article
        resp = test_app.post(
            "/api/articles",
            json={"url": "https://example.com/content-test"},
            cookies=cookies,
        )
        article_id = resp.json()["id"]

        # Store HTML content in R2
        html_key = f"articles/{article_id}/content.html"
        await env.CONTENT.put(html_key, "<h1>Test Content</h1><p>Hello world</p>")

        # Update the article to have an html_key
        db: StatefulMockD1 = env.DB  # type: ignore[assignment]
        if article_id in db.articles:
            db.articles[article_id]["html_key"] = html_key

        # Fetch content
        resp = test_app.get(f"/api/articles/{article_id}/content", cookies=cookies)
        assert resp.status_code == 200
        assert "Test Content" in resp.text

    async def test_get_content_returns_404_when_missing(
        self, test_app: TestClient, auth_cookie: str, env: MockEnv,
    ) -> None:
        """GET /api/articles/{id}/content returns 404 when no HTML in R2."""
        cookies = {COOKIE_NAME: auth_cookie}

        # Create article without R2 content
        resp = test_app.post(
            "/api/articles",
            json={"url": "https://example.com/no-content"},
            cookies=cookies,
        )
        article_id = resp.json()["id"]

        # Wipe html_key
        db: StatefulMockD1 = env.DB  # type: ignore[assignment]
        if article_id in db.articles:
            db.articles[article_id]["html_key"] = None

        resp = test_app.get(f"/api/articles/{article_id}/content", cookies=cookies)
        assert resp.status_code == 404

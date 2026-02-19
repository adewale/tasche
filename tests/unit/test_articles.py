"""Tests for Phase 3 — Article CRUD API (src/articles/routes.py).

Covers creating, listing, retrieving, updating, and deleting articles,
as well as authentication enforcement on all endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from src.articles.routes import router
from src.auth.session import COOKIE_NAME
from tests.conftest import (
    ArticleFactory,
    MockD1,
    MockEnv,
    MockQueue,
    MockR2,
    _make_test_app,
)
from tests.conftest import (
    _authenticated_client as _authenticated_client_base,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROUTERS = ((router, "/api/articles"),)


def _make_app(env):
    return _make_test_app(env, *_ROUTERS)


async def _authenticated_client(env: MockEnv) -> tuple[TestClient, str]:
    return await _authenticated_client_base(env, *_ROUTERS)


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
# GET /api/articles/{article_id}/thumbnail — Serve thumbnail
# ---------------------------------------------------------------------------


class TestGetArticleThumbnail:
    async def test_returns_thumbnail_image(self) -> None:
        """GET /api/articles/{id}/thumbnail returns WebP image from R2."""
        article = ArticleFactory.create(
            id="art_thumb",
            user_id="user_001",
            thumbnail_key="articles/art_thumb/thumbnail.webp",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_thumb":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        # Put a fake WebP image in R2
        await r2.put("articles/art_thumb/thumbnail.webp", b"\x00WEBP_IMAGE_DATA")

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_thumb/thumbnail",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"
        assert resp.headers["cache-control"] == "public, max-age=86400"
        assert resp.content == b"\x00WEBP_IMAGE_DATA"

    async def test_returns_404_when_no_thumbnail_key(self) -> None:
        """GET /api/articles/{id}/thumbnail returns 404 when thumbnail_key is null."""
        article = ArticleFactory.create(
            id="art_nothumb",
            user_id="user_001",
            thumbnail_key=None,
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_nothumb":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_nothumb/thumbnail",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    async def test_returns_404_when_r2_object_missing(self) -> None:
        """GET /api/articles/{id}/thumbnail returns 404 when R2 object is gone."""
        article = ArticleFactory.create(
            id="art_gone",
            user_id="user_001",
            thumbnail_key="articles/art_gone/thumbnail.webp",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_gone":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()  # Empty R2 — no object stored
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_gone/thumbnail",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id}/screenshot — Serve full-page screenshot
# ---------------------------------------------------------------------------


class TestGetArticleScreenshot:
    async def test_returns_screenshot_image(self) -> None:
        """GET /api/articles/{id}/screenshot returns WebP image from R2."""
        article = ArticleFactory.create(
            id="art_ss",
            user_id="user_001",
            original_key="articles/art_ss/original.webp",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_ss":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        # Put a fake WebP image in R2
        await r2.put("articles/art_ss/original.webp", b"\x00FULLPAGE_SCREENSHOT")

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_ss/screenshot",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"
        assert resp.headers["cache-control"] == "public, max-age=86400"
        assert resp.content == b"\x00FULLPAGE_SCREENSHOT"

    async def test_returns_404_when_no_original_key(self) -> None:
        """GET /api/articles/{id}/screenshot returns 404 when original_key is null."""
        article = ArticleFactory.create(
            id="art_noss",
            user_id="user_001",
            original_key=None,
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_noss":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_noss/screenshot",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    async def test_returns_404_when_r2_object_missing(self) -> None:
        """GET /api/articles/{id}/screenshot returns 404 when R2 object is gone."""
        article = ArticleFactory.create(
            id="art_ss_gone",
            user_id="user_001",
            original_key="articles/art_ss_gone/original.webp",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_ss_gone":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()  # Empty R2 — no object stored
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_ss_gone/screenshot",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    async def test_requires_auth(self) -> None:
        """GET /api/articles/{id}/screenshot returns 401 without auth."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles/some-id/screenshot")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/articles?audio_status=... — Filter by audio_status
# ---------------------------------------------------------------------------


class TestFilterByAudioStatus:
    async def test_filters_by_audio_status(self) -> None:
        """GET /api/articles?audio_status=ready includes audio_status filter in query."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/articles?audio_status=ready",
            cookies={COOKIE_NAME: session_id},
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        assert "audio_status = ?" in select_calls[0]["sql"]
        assert "ready" in select_calls[0]["params"]


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


class TestEnqueueFailure:
    async def test_enqueue_failure_marks_article_failed(self) -> None:
        """POST /api/articles marks article as 'failed' when queue.send() raises."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        class FailingQueue:
            messages: list = []

            async def send(self, message: Any, **kwargs: Any) -> None:
                raise RuntimeError("Queue unavailable")

        queue = FailingQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/queue-fail"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 503
        assert "enqueue" in resp.json()["detail"].lower()

        # Verify D1 UPDATE set status='failed'
        update_calls = [c for c in calls if c["sql"].startswith("UPDATE")]
        assert len(update_calls) >= 1
        assert "failed" in update_calls[0]["params"]


class TestRejectsInvalidReadingStatus:
    async def test_rejects_invalid_reading_status(self) -> None:
        """PATCH /api/articles/{id} returns 422 for invalid reading_status enum."""
        article = ArticleFactory.create(id="art_inv_rs", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_inv_rs":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_inv_rs",
            json={"reading_status": "invalid_status"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422
        assert "reading_status" in resp.json()["detail"]


class TestRejectsInvalidReadingProgressBounds:
    async def test_rejects_reading_progress_above_one(self) -> None:
        """PATCH /api/articles/{id} returns 422 when reading_progress > 1.0."""
        article = ArticleFactory.create(id="art_rp_hi", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_rp_hi":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_rp_hi",
            json={"reading_progress": 1.5},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422
        assert "reading_progress" in resp.json()["detail"]

    async def test_rejects_reading_progress_below_zero(self) -> None:
        """PATCH /api/articles/{id} returns 422 when reading_progress < 0."""
        article = ArticleFactory.create(id="art_rp_lo", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_rp_lo":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_rp_lo",
            json={"reading_progress": -0.1},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422
        assert "reading_progress" in resp.json()["detail"]


class TestRejectsInvalidScrollPosition:
    async def test_rejects_negative_scroll_position(self) -> None:
        """PATCH /api/articles/{id} returns 422 for negative scroll_position."""
        article = ArticleFactory.create(id="art_sp_neg", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_sp_neg":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_sp_neg",
            json={"scroll_position": -1},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422
        assert "scroll_position" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id}/content — Serve article HTML
# ---------------------------------------------------------------------------


class TestGetArticleContent:
    async def test_content_endpoint_returns_html(self) -> None:
        """GET /api/articles/{id}/content returns HTML from R2."""
        article = ArticleFactory.create(
            id="art_html",
            user_id="user_001",
            html_key="articles/art_html/content.html",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_html":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        await r2.put("articles/art_html/content.html", "<p>Article content</p>")

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_html/content",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "<p>Article content</p>" in resp.text

    async def test_content_endpoint_not_found(self) -> None:
        """GET /api/articles/{id}/content returns 404 when no HTML in R2."""
        article = ArticleFactory.create(
            id="art_nohtml",
            user_id="user_001",
            html_key="articles/art_nohtml/content.html",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_nohtml":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()  # Empty R2
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_nohtml/content",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id}/metadata — Serve article metadata
# ---------------------------------------------------------------------------


class TestGetArticleMetadata:
    async def test_metadata_endpoint(self) -> None:
        """GET /api/articles/{id}/metadata returns metadata JSON from R2."""
        article = ArticleFactory.create(id="art_meta", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_meta":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        import json as _json

        metadata = {"article_id": "art_meta", "word_count": 500}
        await r2.put("articles/art_meta/metadata.json", _json.dumps(metadata))

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_meta/metadata",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["article_id"] == "art_meta"
        assert data["word_count"] == 500

    async def test_metadata_not_found(self) -> None:
        """GET /api/articles/{id}/metadata returns 404 when no metadata in R2."""
        article = ArticleFactory.create(id="art_nometa", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_nometa":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()  # Empty R2
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_nometa/metadata",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/articles?status=... — Filter by status
# ---------------------------------------------------------------------------


class TestFilterByStatus:
    async def test_filters_by_status(self) -> None:
        """GET /api/articles?status=ready includes status filter in query."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/articles?status=ready",
            cookies={COOKIE_NAME: session_id},
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        assert "status = ?" in select_calls[0]["sql"]
        assert "ready" in select_calls[0]["params"]

    async def test_rejects_invalid_status(self) -> None:
        """GET /api/articles?status=bogus returns 422."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.get(
            "/api/articles?status=bogus",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422
        assert "status" in resp.json()["detail"]

    async def test_rejects_invalid_audio_status_filter(self) -> None:
        """GET /api/articles?audio_status=bogus returns 422."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.get(
            "/api/articles?audio_status=bogus",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 422
        assert "audio_status" in resp.json()["detail"]


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

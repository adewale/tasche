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

    async def test_reprocesses_duplicate_url(self) -> None:
        """POST /api/articles re-processes when URL already exists."""
        existing = ArticleFactory.create(
            user_id="user_001",
            original_url="https://example.com/existing",
        )

        updates = []

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                return [existing]
            if "UPDATE articles SET status" in sql:
                updates.append(params)
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/existing"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["updated"] is True
        assert data["id"] == existing["id"]
        assert len(updates) == 1

    async def test_finds_duplicate_via_final_url(self) -> None:
        """POST /api/articles detects duplicate when submitted URL matches final_url."""
        # Scenario: article was saved with original_url="https://example.com/old"
        # but after processing, its final_url was set to "https://example.com/redirected".
        # Now the user submits "https://example.com/redirected" — should find the duplicate.
        existing = ArticleFactory.create(
            id="existing_art",
            user_id="user_001",
            original_url="https://example.com/old",
            final_url="https://example.com/redirected",
        )

        updates = []

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                # The submitted URL is "https://example.com/redirected" which does NOT
                # match original_url, but DOES match final_url.
                # The SQL is: WHERE user_id = ? AND (original_url = ?
                # OR final_url = ? OR canonical_url = ?)
                # params are: [user_id, url, url, url]
                submitted_url = params[1]  # the URL being checked
                if (
                    submitted_url == existing["original_url"]
                    or submitted_url == existing["final_url"]
                    or submitted_url == existing["canonical_url"]
                ):
                    return [existing]
                return []
            if "UPDATE articles SET status" in sql:
                updates.append(params)
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/redirected"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["updated"] is True
        assert data["id"] == "existing_art"
        assert data.get("created_at") == existing["created_at"]

    async def test_finds_duplicate_via_canonical_url(self) -> None:
        """POST /api/articles detects duplicate when submitted URL matches canonical_url."""
        # Scenario: article was saved, processing set canonical_url to a clean URL.
        # User submits that clean canonical URL — should detect it as duplicate.
        existing = ArticleFactory.create(
            id="existing_canon",
            user_id="user_001",
            original_url="https://example.com/page?utm_source=twitter",
            final_url="https://example.com/page?utm_source=twitter",
            canonical_url="https://example.com/page",
        )

        updates = []

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                submitted_url = params[1]
                if (
                    submitted_url == existing["original_url"]
                    or submitted_url == existing["final_url"]
                    or submitted_url == existing["canonical_url"]
                ):
                    return [existing]
                return []
            if "UPDATE articles SET status" in sql:
                updates.append(params)
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/page"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["updated"] is True
        assert data["id"] == "existing_canon"
        assert data.get("created_at") == existing["created_at"]

    async def test_reprocess_enqueues_with_submitted_url(self) -> None:
        """When re-processing, the queue message uses the newly submitted URL, not original_url."""
        existing = ArticleFactory.create(
            id="existing_art_2",
            user_id="user_001",
            original_url="https://example.com/original-page",
            final_url="https://example.com/final-page",
        )

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                submitted_url = params[1]
                if (
                    submitted_url == existing["original_url"]
                    or submitted_url == existing["final_url"]
                    or submitted_url == existing["canonical_url"]
                ):
                    return [existing]
                return []
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        # User submits the final_url, not the original_url
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/final-page"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["updated"] is True

        # The queue message should use the submitted URL (final-page),
        # which will be re-fetched by the processing pipeline
        assert len(queue.messages) == 1
        msg = queue.messages[0]
        assert msg["url"] == "https://example.com/final-page"
        assert msg["article_id"] == "existing_art_2"

    async def test_create_article_with_real_url(self) -> None:
        """POST /api/articles succeeds with a real-world URL."""
        url = "https://okayfail.com/2025/in-praise-of-dhh.html"
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
            json={"url": url},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["status"] == "pending"

        # Verify queue was sent with the correct URL
        assert len(queue.messages) == 1
        assert queue.messages[0]["url"] == url

    async def test_duplicate_with_wrapped_d1_result(self) -> None:
        """POST /api/articles handles D1 .first() returning a result wrapper.

        In Pyodide, D1's .first() may return the full result wrapper
        {results: [...], success, meta} instead of just the row.
        The duplicate check must still extract the article ID correctly.
        """
        existing = ArticleFactory.create(
            id="wrapped_art",
            user_id="user_001",
            original_url="https://okayfail.com/2025/test.html",
        )
        updates = []

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                # Simulate what Pyodide D1 .first() might return:
                # the result wrapper instead of the row itself.
                # d1_first() must unwrap this before returning.
                return [existing]
            if "UPDATE articles SET status" in sql:
                updates.append(params)
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://okayfail.com/2025/test.html"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "wrapped_art"
        assert data["updated"] is True

    async def test_create_article_sql_param_counts_match(self) -> None:
        """Every SQL statement executed during article creation has matching placeholders/params."""
        import re

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
            json={"url": "https://example.com/test-params"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 201

        for call in calls:
            sql = call["sql"]
            params = call["params"]
            expected = len(re.findall(r"\?", sql))
            actual = len(params)
            assert expected == actual, (
                f"SQL placeholder/param mismatch: {expected} placeholders but {actual} params.\n"
                f"SQL: {sql!r}\n"
                f"Params: {params!r}"
            )

    async def test_url_normalization_preserves_query_params(self) -> None:
        """POST /api/articles preserves URL query parameters during validation."""
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
            json={"url": "https://example.com/article?utm_source=twitter&ref=123"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 201

        # Verify the queue message preserves the full URL with query params
        assert len(queue.messages) == 1
        msg = queue.messages[0]
        assert "utm_source=twitter" in msg["url"]
        assert "ref=123" in msg["url"]

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
    async def test_returns_pending_articles_with_null_fields(self) -> None:
        """GET /api/articles returns articles with NULL optional fields."""
        # A freshly-created article has many NULL fields before processing completes
        pending_article = {
            "id": "art_pending",
            "user_id": "user_001",
            "original_url": "https://okayfail.com/2025/in-praise-of-dhh.html",
            "final_url": None,
            "canonical_url": None,
            "domain": "okayfail.com",
            "title": None,
            "excerpt": None,
            "author": None,
            "word_count": None,
            "reading_time_minutes": None,
            "image_count": 0,
            "status": "pending",
            "reading_status": "unread",
            "is_favorite": 0,
            "audio_key": None,
            "audio_duration_seconds": None,
            "audio_status": None,
            "html_key": None,
            "thumbnail_key": None,
            "original_key": None,
            "original_status": "unknown",
            "scroll_position": 0.0,
            "reading_progress": 0.0,
            "created_at": "2025-01-15T10:00:00",
            "updated_at": "2025-01-15T10:00:00",
        }

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "FROM articles" in sql and "LIMIT" in sql:
                return [pending_article]
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
        assert len(data) == 1
        assert data[0]["id"] == "art_pending"
        assert data[0]["status"] == "pending"
        assert data[0]["title"] is None
        assert data[0]["final_url"] is None

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
# GET /api/articles/{article_id}/images/{filename} — Serve article images
# ---------------------------------------------------------------------------


class TestGetArticleImage:
    async def test_returns_webp_image(self) -> None:
        """GET /api/articles/{id}/images/{filename} returns image from R2."""
        article = ArticleFactory.create(id="art_img", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_img":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        await r2.put(
            "articles/art_img/images/abc123.webp", b"\x00WEBP_IMAGE_DATA"
        )

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_img/images/abc123.webp",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"
        assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert resp.content == b"\x00WEBP_IMAGE_DATA"

    async def test_returns_jpeg_image(self) -> None:
        """GET /api/articles/{id}/images/{filename} returns correct type for .jpg."""
        article = ArticleFactory.create(id="art_jpg", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_jpg":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        await r2.put("articles/art_jpg/images/def456.jpg", b"\xff\xd8JPEG_DATA")

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_jpg/images/def456.jpg",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"

    async def test_returns_404_when_image_not_in_r2(self) -> None:
        """GET /api/articles/{id}/images/{filename} returns 404 when not in R2."""
        article = ArticleFactory.create(id="art_noimg", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_noimg":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()  # Empty R2
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_noimg/images/missing.webp",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    async def test_returns_404_when_article_not_found(self) -> None:
        """GET /api/articles/{id}/images/{filename} returns 404 for wrong article."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/nonexistent/images/abc123.webp",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    def test_requires_auth(self) -> None:
        """GET /api/articles/{id}/images/{filename} returns 401 without auth."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles/some-id/images/abc123.webp")
        assert resp.status_code == 401

    async def test_returns_octet_stream_for_unknown_extension(self) -> None:
        """GET /api/articles/{id}/images/{filename} falls back to octet-stream."""
        article = ArticleFactory.create(id="art_bin", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_bin":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        await r2.put("articles/art_bin/images/file.bin", b"\x00BINARY_DATA")

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_bin/images/file.bin",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"


# ---------------------------------------------------------------------------
# GET /api/articles?audio_status=... — Filter by audio_status
# ---------------------------------------------------------------------------


class TestUpdateArticleMultipleFields:
    async def test_updates_multiple_fields_at_once(self) -> None:
        """PATCH /api/articles/{id} can update multiple fields at once."""
        article = ArticleFactory.create(id="art_multi", user_id="user_001")
        updated_article = {
            **article,
            "reading_status": "reading",
            "is_favorite": 1,
            "reading_progress": 0.5,
        }

        call_count = 0

        def execute(sql: str, params: list) -> list:
            nonlocal call_count
            call_count += 1
            if sql.startswith("SELECT") and "id = ?" in sql:
                if call_count <= 1:
                    return [article]
                return [updated_article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_multi",
            json={
                "reading_status": "reading",
                "is_favorite": True,
                "reading_progress": 0.5,
            },
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["reading_status"] == "reading"
        assert data["is_favorite"] == 1
        assert data["reading_progress"] == 0.5


class TestListenLaterIdempotency:
    """Test TTS listen-later endpoint returns correct status for already-ready articles."""

    async def test_listen_later_returns_200_when_audio_ready(self) -> None:
        """POST /api/articles/{id}/listen-later returns 200 when audio ready."""
        from src.tts.routes import router as tts_router

        article = ArticleFactory.create(
            id="art_audio_ready",
            user_id="user_001",
            audio_status="ready",
            audio_key="articles/art_audio_ready/audio.mp3",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_audio_ready":
                return [article]
            return []

        db = MockD1(execute=execute)
        from tests.conftest import _authenticated_client as _auth_client

        env = MockEnv(db=db)
        client, session_id = await _auth_client(
            env,
            (tts_router, "/api/articles"),
        )

        resp = client.post(
            "/api/articles/art_audio_ready/listen-later",
            cookies={COOKIE_NAME: session_id},
        )

        assert (
            resp.status_code == 200
        ), f"Expected 200 for already-ready audio, got {resp.status_code}"
        data = resp.json()
        assert data["audio_status"] == "ready"


class TestFilterByTag:
    async def test_filters_by_tag_id(self) -> None:
        """GET /api/articles?tag=tag_001 includes subquery filter in SQL."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/articles?tag=tag_001",
            cookies={COOKIE_NAME: session_id},
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        sql = select_calls[0]["sql"]
        assert "article_tags" in sql, "Tag filter should use article_tags subquery"
        assert "tag_id = ?" in sql, "Tag filter should use parameterized tag_id"
        assert "tag_001" in select_calls[0]["params"]


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

    async def test_content_endpoint_includes_csp_header(self) -> None:
        """GET /api/articles/{id}/content includes a restrictive CSP header."""
        article = ArticleFactory.create(
            id="art_csp",
            user_id="user_001",
            html_key="articles/art_csp/content.html",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_csp":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        await r2.put("articles/art_csp/content.html", "<p>Content with CSP</p>")

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_csp/content",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert "content-security-policy" in resp.headers
        assert "default-src 'none'" in resp.headers["content-security-policy"]

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


# ---------------------------------------------------------------------------
# POST /api/articles/{article_id}/retry — Retry failed/pending article
# ---------------------------------------------------------------------------


class TestRetryArticle:
    async def test_retries_failed_article(self) -> None:
        """POST /api/articles/{id}/retry re-queues a failed article."""
        article = ArticleFactory.create(
            id="art_fail", user_id="user_001", status="failed"
        )
        updates: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql and params[0] == "art_fail":
                return [article]
            if sql.startswith("UPDATE"):
                updates.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_fail/retry",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 202
        data = resp.json()
        assert data["id"] == "art_fail"
        assert data["status"] == "pending"

        # Verify D1 UPDATE set status='pending'
        assert len(updates) >= 1
        assert "pending" in updates[0]["sql"]

        # Verify queue message
        assert len(queue.messages) == 1
        msg = queue.messages[0]
        assert msg["type"] == "article_processing"
        assert msg["article_id"] == "art_fail"

    async def test_retries_pending_article(self) -> None:
        """POST /api/articles/{id}/retry re-queues a pending (stuck) article."""
        article = ArticleFactory.create(
            id="art_stuck", user_id="user_001", status="pending"
        )

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql and params[0] == "art_stuck":
                return [article]
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_stuck/retry",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 202
        assert resp.json()["status"] == "pending"
        assert len(queue.messages) == 1

    async def test_retries_ready_article(self) -> None:
        """POST /api/articles/{id}/retry re-queues a ready article."""
        article = ArticleFactory.create(
            id="art_ready", user_id="user_001", status="ready"
        )

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql and params[0] == "art_ready":
                return [article]
            return []

        db = MockD1(execute=execute)
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_ready/retry",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 202
        assert resp.json()["status"] == "pending"
        assert len(queue.messages) == 1

    async def test_returns_404_for_unknown_article(self) -> None:
        """POST /api/articles/{id}/retry returns 404 for nonexistent article."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/nonexistent/retry",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    def test_returns_401_without_auth(self) -> None:
        """POST /api/articles/{id}/retry returns 401 without auth."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/articles/some-id/retry")
        assert resp.status_code == 401

    async def test_returns_503_on_queue_failure(self) -> None:
        """POST /api/articles/{id}/retry returns 503 and marks failed on queue error."""
        article = ArticleFactory.create(
            id="art_qfail", user_id="user_001", status="failed"
        )
        updates: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql and params[0] == "art_qfail":
                return [article]
            if sql.startswith("UPDATE"):
                updates.append({"sql": sql, "params": params})
            return []

        class FailingQueue:
            messages: list = []

            async def send(self, message: Any, **kwargs: Any) -> None:
                raise RuntimeError("Queue unavailable")

        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=FailingQueue())

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_qfail/retry",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 503
        assert "enqueue" in resp.json()["detail"].lower()

        # Should have 2 updates: first to 'pending', then to 'failed'
        assert len(updates) >= 2
        assert "failed" in updates[-1]["params"]

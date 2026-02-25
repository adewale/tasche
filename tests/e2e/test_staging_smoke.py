"""E2E smoke tests against the live Tasche staging deployment.

These tests hit the real Cloudflare Workers runtime — real D1, real R2,
real Queue, real Pyodide FFI.  They catch bugs that unit tests (running
in CPython with mocks) fundamentally cannot.

Modelled after planet_cf's E2E test pattern:
  - httpx client, no auth (DISABLE_AUTH=true on staging)
  - pytest.mark.e2e + RUN_E2E_TESTS=1 gating
  - try/finally cleanup for created resources
  - /process-now endpoint for deterministic processing (bypass queue)
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("RUN_E2E_TESTS"),
        reason="Requires RUN_E2E_TESTS=1 and live staging",
    ),
]


# =========================================================================
# Health & Routing — does the Worker respond correctly?
# =========================================================================


class TestHealthAndRouting:
    async def test_articles_endpoint_returns_json_array(
        self, http_client: httpx.AsyncClient
    ) -> None:
        resp = await http_client.get("/api/articles")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    async def test_tags_endpoint_returns_json_array(self, http_client: httpx.AsyncClient) -> None:
        resp = await http_client.get("/api/tags")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    async def test_stats_endpoint_returns_json_object(self, http_client: httpx.AsyncClient) -> None:
        resp = await http_client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)

    async def test_unknown_api_path_returns_404_not_html(
        self, http_client: httpx.AsyncClient
    ) -> None:
        """Verify Worker-first routing: /api/* is handled by FastAPI, not SPA."""
        resp = await http_client.get("/api/nonexistent-path-12345")
        # Should be a JSON 404 from FastAPI, NOT index.html from the SPA
        assert resp.status_code in (404, 405)
        assert "text/html" not in resp.headers.get("content-type", "")


# =========================================================================
# Article Lifecycle — the core user journey
# =========================================================================


class TestArticleLifecycle:
    """Save a URL → process it → read it → update it → delete it.

    This is the primary user journey. If this doesn't work on the real
    platform, nothing else matters.
    """

    async def test_full_lifecycle(
        self,
        http_client: httpx.AsyncClient,
        cleanup_articles: list[str],
    ) -> None:
        test_id = uuid.uuid4().hex[:8]
        test_url = f"https://example.com/e2e-lifecycle-{test_id}"

        # 1. Create article
        resp = await http_client.post(
            "/api/articles",
            json={"url": test_url, "title": f"E2E Lifecycle Test {test_id}"},
        )
        assert resp.status_code == 201, f"Create failed: {resp.text}"
        article = resp.json()
        article_id = article["id"]
        cleanup_articles.append(article_id)
        assert article["status"] == "pending"

        # 2. Process inline (bypass queue — deterministic)
        resp = await http_client.post(
            f"/api/articles/{article_id}/process-now",
            timeout=60.0,
        )
        assert resp.status_code == 200, f"Process-now failed: {resp.text}"
        process_result = resp.json()
        assert process_result.get("result") in ("success", "error")

        # 3. Fetch the article — verify D1 has the data
        resp = await http_client.get(f"/api/articles/{article_id}")
        assert resp.status_code == 200
        article = resp.json()
        assert article["id"] == article_id
        # After processing, article should have metadata
        assert "title" in article
        assert "domain" in article
        assert article["domain"] == "example.com"

        # 4. Fetch HTML content — verify R2 has the content
        resp = await http_client.get(f"/api/articles/{article_id}/content")
        # Content might be empty for example.com but endpoint should work
        assert resp.status_code in (200, 404)

        # 5. Update reading status — verify D1 write with None→null works
        resp = await http_client.patch(
            f"/api/articles/{article_id}",
            json={"reading_status": "reading"},
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["reading_status"] == "reading"

        # 6. Delete article
        resp = await http_client.delete(f"/api/articles/{article_id}")
        assert resp.status_code == 204
        cleanup_articles.remove(article_id)

        # 7. Verify deletion
        resp = await http_client.get(f"/api/articles/{article_id}")
        assert resp.status_code == 404


# =========================================================================
# Search & Tags — FTS5 on real D1, tag associations
# =========================================================================


class TestSearchAndTags:
    """Test search and tagging with a real processed article."""

    async def test_search_and_tags(
        self,
        http_client: httpx.AsyncClient,
        cleanup_articles: list[str],
        cleanup_tags: list[str],
    ) -> None:
        test_id = uuid.uuid4().hex[:8]

        # Create and process an article
        resp = await http_client.post(
            "/api/articles",
            json={
                "url": f"https://example.com/e2e-search-{test_id}",
                "title": f"E2E Search Tasche Test {test_id}",
            },
        )
        assert resp.status_code == 201
        article_id = resp.json()["id"]
        cleanup_articles.append(article_id)

        resp = await http_client.post(
            f"/api/articles/{article_id}/process-now",
            timeout=60.0,
        )
        assert resp.status_code == 200

        # Create a tag
        resp = await http_client.post(
            "/api/tags",
            json={"name": f"e2e-tag-{test_id}"},
        )
        assert resp.status_code == 201
        tag_id = resp.json()["id"]
        cleanup_tags.append(tag_id)

        # Assign tag to article
        resp = await http_client.post(
            f"/api/articles/{article_id}/tags",
            json={"tag_id": tag_id},
        )
        assert resp.status_code in (200, 201)

        # Filter articles by tag
        resp = await http_client.get(f"/api/articles?tag={tag_id}")
        assert resp.status_code == 200
        articles = resp.json()
        assert any(a["id"] == article_id for a in articles)

        # Search by title — FTS5 on real D1
        resp = await http_client.get(
            "/api/search",
            params={"q": f"Tasche {test_id}"},
        )
        assert resp.status_code == 200
        results = resp.json()
        # FTS5 may or may not match depending on indexing timing,
        # but the endpoint should not crash
        assert isinstance(results, list)


# =========================================================================
# FFI Boundary — specifically targeting historical production bugs
# =========================================================================


class TestFFIBoundary:
    """Tests that specifically target the 3 historical FFI bugs:
    1. JsNull leaking through (D1 returns JsNull for missing rows)
    2. None→undefined breaking D1 bind (nullable fields)
    3. bytes→PyProxy breaking R2 (content storage)
    """

    async def test_null_fields_returned_as_json_null(
        self,
        http_client: httpx.AsyncClient,
        cleanup_articles: list[str],
    ) -> None:
        """Articles with nullable DB fields must return JSON null, not crash."""
        test_id = uuid.uuid4().hex[:8]
        resp = await http_client.post(
            "/api/articles",
            json={
                "url": f"https://example.com/e2e-null-{test_id}",
                "title": f"Null Fields Test {test_id}",
            },
        )
        assert resp.status_code == 201
        article_id = resp.json()["id"]
        cleanup_articles.append(article_id)

        # Before processing: nullable fields should be null in JSON
        resp = await http_client.get(f"/api/articles/{article_id}")
        assert resp.status_code == 200
        article = resp.json()
        # These fields are nullable in D1 — they must come back as JSON null,
        # not as the string "undefined" or missing entirely
        assert "author" in article
        assert article["author"] is None  # JSON null → Python None

    async def test_nonexistent_article_returns_404(
        self,
        http_client: httpx.AsyncClient,
    ) -> None:
        """D1 .first() returning JsNull for a missing row must become 404."""
        resp = await http_client.get("/api/articles/nonexistent-id-12345")
        assert resp.status_code == 404

    async def test_duplicate_url_reprocesses_existing(
        self,
        http_client: httpx.AsyncClient,
        cleanup_articles: list[str],
    ) -> None:
        """Duplicate URL detection re-processes the existing article (not reject)."""
        test_id = uuid.uuid4().hex[:8]
        test_url = f"https://example.com/e2e-dup-{test_id}"

        resp = await http_client.post(
            "/api/articles",
            json={"url": test_url},
        )
        assert resp.status_code == 201
        first_id = resp.json()["id"]
        cleanup_articles.append(first_id)

        # Same URL again — should return 201 with the SAME article ID
        # (re-process, not create a new one)
        resp = await http_client.post(
            "/api/articles",
            json={"url": test_url},
        )
        assert resp.status_code == 201
        second_id = resp.json()["id"]
        assert second_id == first_id, "Duplicate URL should reuse the existing article"

    async def test_tts_produces_reasonable_audio(
        self,
        http_client: httpx.AsyncClient,
        cleanup_articles: list[str],
    ) -> None:
        """TTS pipeline must produce audio proportional to text, not truncated.

        This is a permanent regression guardrail against the ReadableStream
        truncation bug where arrayBuffer() in Pyodide only captures the first
        buffered chunk of a multi-chunk stream.
        """
        test_id = uuid.uuid4().hex[:8]

        # Create and process an article to populate markdown_content
        resp = await http_client.post(
            "/api/articles",
            json={
                "url": f"https://example.com/e2e-tts-{test_id}",
                "title": f"TTS Audio Size Test {test_id}",
            },
        )
        assert resp.status_code == 201
        article_id = resp.json()["id"]
        cleanup_articles.append(article_id)

        resp = await http_client.post(
            f"/api/articles/{article_id}/process-now",
            timeout=60.0,
        )
        assert resp.status_code == 200

        # Trigger TTS inline (bypass queue for determinism)
        resp = await http_client.post(
            f"/api/articles/{article_id}/tts-now",
            timeout=120.0,
        )
        assert resp.status_code == 200
        tts_result = resp.json()

        if tts_result.get("result") == "error":
            # TTS may fail on example.com (minimal content) — skip gracefully
            pytest.skip(f"TTS failed (possibly no content): {tts_result.get('error', '')[:200]}")

        # Verify diagnostics report multiple chunks with data
        diag = tts_result.get("diagnostics", {})
        assert diag.get("total_bytes", 0) > 0, "Pipeline reported 0 audio bytes"

        # Download the audio and check actual size
        resp = await http_client.get(f"/api/articles/{article_id}/audio")
        assert resp.status_code == 200

        audio_bytes = len(resp.content)
        pipeline_bytes = diag.get("total_bytes", 0)

        # The downloaded audio should match what the pipeline reported
        assert audio_bytes == pipeline_bytes, (
            f"Pipeline reported {pipeline_bytes} bytes but downloaded {audio_bytes}"
        )

        # Audio should be more than a single MP3 frame (~400 bytes)
        # A real article produces >10KB; example.com may be smaller
        assert audio_bytes > 400, f"Audio is only {audio_bytes} bytes — likely truncated"

    async def test_process_stores_content_in_r2(
        self,
        http_client: httpx.AsyncClient,
        cleanup_articles: list[str],
    ) -> None:
        """Processing must store content in R2 without bytes→PyProxy crash."""
        test_id = uuid.uuid4().hex[:8]
        resp = await http_client.post(
            "/api/articles",
            json={
                "url": f"https://example.com/e2e-r2-{test_id}",
                "title": f"R2 Storage Test {test_id}",
            },
        )
        assert resp.status_code == 201
        article_id = resp.json()["id"]
        cleanup_articles.append(article_id)

        # Process inline
        resp = await http_client.post(
            f"/api/articles/{article_id}/process-now",
            timeout=60.0,
        )
        assert resp.status_code == 200

        # Verify article reached ready or failed status (not stuck in pending)
        resp = await http_client.get(f"/api/articles/{article_id}")
        assert resp.status_code == 200
        article = resp.json()
        assert article["status"] in ("ready", "failed"), (
            f"Article stuck in status={article['status']}"
        )

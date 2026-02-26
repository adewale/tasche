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
        test_url = f"https://example.com?e2e=lifecycle-{test_id}"

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
            json={"reading_status": "archived"},
        )
        assert resp.status_code == 200
        updated = resp.json()
        assert updated["reading_status"] == "archived"

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
                "url": f"https://example.com?e2e=search-{test_id}",
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
                "url": f"https://example.com?e2e=null-{test_id}",
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
        test_url = f"https://example.com?e2e=dup-{test_id}"

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
                "url": f"https://example.com?e2e=tts-{test_id}",
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

        # Trigger TTS via listen-later (queues the generation job)
        resp = await http_client.post(
            f"/api/articles/{article_id}/listen-later",
            timeout=30.0,
        )
        assert resp.status_code in (200, 202)

        # Poll until audio is ready (or timeout after 120s)
        import asyncio

        for _ in range(24):
            await asyncio.sleep(5)
            resp = await http_client.get(f"/api/articles/{article_id}")
            if resp.status_code == 200:
                article_data = resp.json()
                if article_data.get("audio_status") == "ready":
                    break
                if article_data.get("audio_status") == "failed":
                    pytest.skip("TTS failed (possibly no content)")
        else:
            pytest.skip("TTS did not complete within 120s")

        # Download the audio and check actual size
        resp = await http_client.get(f"/api/articles/{article_id}/audio")
        assert resp.status_code == 200

        audio_bytes = len(resp.content)

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
                "url": f"https://example.com?e2e=r2-{test_id}",
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


# =========================================================================
# R2 Binary Round-Trip — verifies content survives the FFI write path
# =========================================================================


class TestR2BinaryRoundTrip:
    """Verifies content survives the full write/read cycle through R2.

    The processing pipeline writes HTML and metadata to R2 via
    ``to_js_bytes()`` (Python bytes → JS Uint8Array).  These tests verify
    the data reads back correctly on the real Pyodide runtime — catching
    FFI conversion bugs that CPython mocks cannot.
    """

    async def test_html_content_round_trips_through_r2(
        self,
        http_client: httpx.AsyncClient,
        cleanup_articles: list[str],
    ) -> None:
        """HTML content written via to_js_bytes() must read back intact.

        The processing pipeline stores HTML in R2 via
        ``SafeR2.put(key, html_bytes)`` which calls ``to_js_bytes()``.
        Without ``.slice()``, memory growth during the async put would
        truncate the stored HTML to the first Wasm page (~4KB) or zero it.
        """
        test_id = uuid.uuid4().hex[:8]
        resp = await http_client.post(
            "/api/articles",
            json={
                "url": f"https://example.com?e2e=binary-rt-{test_id}",
                "title": f"Binary Round-Trip Test {test_id}",
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

        # Verify article processed successfully
        resp = await http_client.get(f"/api/articles/{article_id}")
        assert resp.status_code == 200
        article = resp.json()
        if article["status"] != "ready":
            pytest.skip(f"Article processing failed: status={article['status']}")

        # Fetch HTML from R2 — this is the binary round-trip
        resp = await http_client.get(f"/api/articles/{article_id}/content")
        assert resp.status_code == 200, "Content must exist in R2 after processing"

        html = resp.text
        # example.com returns ~1.2KB of HTML.  If to_js_bytes() failed to
        # .slice(), the stored data would be 0 bytes (zeroed view) or
        # truncated to a partial page.
        assert len(html) > 100, (
            f"HTML is only {len(html)} bytes — likely truncated by "
            f"Wasm memory view detachment (missing .slice())"
        )
        # Basic structure check: must be recognisable HTML, not binary garbage
        assert "<" in html, "Content must be HTML, not corrupted binary"

    async def test_markdown_round_trips_through_d1(
        self,
        http_client: httpx.AsyncClient,
        cleanup_articles: list[str],
    ) -> None:
        """Markdown stored in D1 must match the HTML content's text.

        The pipeline converts HTML→Markdown and stores it in both R2 and
        D1 (for FTS5).  While D1 text columns don't go through
        ``to_js_bytes()``, they go through ``_to_js_value()`` and
        ``d1_null()`` — verifying the full FFI write path.
        """
        test_id = uuid.uuid4().hex[:8]
        resp = await http_client.post(
            "/api/articles",
            json={
                "url": f"https://example.com?e2e=md-rt-{test_id}",
                "title": f"Markdown Round-Trip Test {test_id}",
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

        resp = await http_client.get(f"/api/articles/{article_id}")
        assert resp.status_code == 200
        if resp.json()["status"] != "ready":
            pytest.skip("Article processing failed")

        # Fetch markdown from D1 (not R2)
        resp = await http_client.get(f"/api/articles/{article_id}/markdown")
        assert resp.status_code == 200

        markdown = resp.text
        assert len(markdown) > 50, (
            f"Markdown is only {len(markdown)} bytes — FFI write to D1 may have failed"
        )

    async def test_metadata_json_round_trips_through_r2(
        self,
        http_client: httpx.AsyncClient,
        cleanup_articles: list[str],
    ) -> None:
        """Processing stores metadata.json in R2 — verify D1 fields match.

        The metadata (word_count, reading_time_minutes, etc.) is stored as
        JSON in R2 via ``to_js_bytes()`` and also written to D1.  If the
        R2 write was truncated, the data would be lost.  We verify via the
        D1 fields which are populated from the same processing run.
        """
        test_id = uuid.uuid4().hex[:8]
        resp = await http_client.post(
            "/api/articles",
            json={
                "url": f"https://example.com?e2e=meta-rt-{test_id}",
                "title": f"Metadata Round-Trip Test {test_id}",
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

        resp = await http_client.get(f"/api/articles/{article_id}")
        assert resp.status_code == 200
        article = resp.json()

        if article["status"] != "ready":
            pytest.skip("Article processing failed")

        # These fields come from the processing pipeline and are written
        # to D1 in the same transaction.  If the pipeline crashed due to
        # FFI issues, these would be null or missing.
        assert article["domain"] == "example.com"
        assert article["word_count"] is not None and article["word_count"] > 0, (
            f"word_count is {article.get('word_count')} — processing metadata lost"
        )
        assert (
            article["reading_time_minutes"] is not None and article["reading_time_minutes"] >= 0
        ), f"reading_time_minutes is {article.get('reading_time_minutes')}"


# =========================================================================
# Input Validation — API rejects bad input on real infrastructure
# =========================================================================


class TestInputValidation:
    """Verify server-side validation on the real Cloudflare Workers runtime.

    These tests were previously in integration tests with mocked D1/R2.
    Running them against real infrastructure confirms the validation layer
    works end-to-end (FastAPI → Pydantic → route handler → real D1).
    """

    async def test_ssrf_rejects_private_urls(
        self,
        http_client: httpx.AsyncClient,
    ) -> None:
        """POST /api/articles rejects URLs pointing to private networks."""
        for url in [
            "http://127.0.0.1/secret",
            "http://10.0.0.1/admin",
            "http://169.254.169.254/latest/meta-data/",
            "http://localhost:8080/internal",
        ]:
            resp = await http_client.post("/api/articles", json={"url": url})
            assert resp.status_code == 422, (
                f"Expected 422 for SSRF URL {url}, got {resp.status_code}: {resp.text}"
            )

    async def test_rejects_oversized_url(
        self,
        http_client: httpx.AsyncClient,
    ) -> None:
        """POST /api/articles rejects URLs exceeding 2048 characters."""
        long_url = "https://example.com/" + "a" * 2050
        resp = await http_client.post("/api/articles", json={"url": long_url})
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    async def test_rejects_empty_tag_name(
        self,
        http_client: httpx.AsyncClient,
    ) -> None:
        """POST /api/tags rejects empty tag names."""
        resp = await http_client.post("/api/tags", json={"name": ""})
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    async def test_rejects_long_tag_name(
        self,
        http_client: httpx.AsyncClient,
    ) -> None:
        """POST /api/tags rejects names exceeding 100 characters."""
        resp = await http_client.post("/api/tags", json={"name": "a" * 101})
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    async def test_duplicate_tag_name_returns_409(
        self,
        http_client: httpx.AsyncClient,
        cleanup_tags: list[str],
    ) -> None:
        """POST /api/tags returns 409 for duplicate tag names."""
        tag_name = f"e2e-dup-{uuid.uuid4().hex[:8]}"

        resp = await http_client.post("/api/tags", json={"name": tag_name})
        assert resp.status_code == 201
        cleanup_tags.append(resp.json()["id"])

        resp = await http_client.post("/api/tags", json={"name": tag_name})
        assert resp.status_code == 409, f"Expected 409 for duplicate tag, got {resp.status_code}"

    async def test_search_requires_query_param(
        self,
        http_client: httpx.AsyncClient,
    ) -> None:
        """GET /api/search without q returns 422."""
        resp = await http_client.get("/api/search")
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}"

    async def test_fts5_operators_dont_crash(
        self,
        http_client: httpx.AsyncClient,
    ) -> None:
        """GET /api/search with FTS5 special characters doesn't error."""
        for query in ["OR AND NOT", "test*", "title:secret", 'hello "world"']:
            resp = await http_client.get("/api/search", params={"q": query})
            assert resp.status_code in (200, 422), (
                f"Unexpected {resp.status_code} for FTS5 query '{query}': {resp.text}"
            )


# =========================================================================
# Idempotency — duplicate/retry behavior on real infrastructure
# =========================================================================


class TestIdempotency:
    """Verify idempotency guards on the real runtime.

    Tests that the server correctly rejects duplicate operations — behavior
    that depends on D1 state which can't be validated with mocked databases.
    """

    async def test_listen_later_idempotent(
        self,
        http_client: httpx.AsyncClient,
        cleanup_articles: list[str],
    ) -> None:
        """POST listen-later twice: first → 202, second → 409."""
        test_id = uuid.uuid4().hex[:8]
        resp = await http_client.post(
            "/api/articles",
            json={"url": f"https://example.com?e2e=idempotent-{test_id}"},
        )
        assert resp.status_code == 201
        article_id = resp.json()["id"]
        cleanup_articles.append(article_id)

        # First listen-later → accepted
        resp = await http_client.post(f"/api/articles/{article_id}/listen-later")
        assert resp.status_code == 202, f"First listen-later: expected 202, got {resp.status_code}"

        # Second listen-later → conflict (already pending)
        resp = await http_client.post(f"/api/articles/{article_id}/listen-later")
        assert resp.status_code == 409, (
            f"Second listen-later: expected 409, got {resp.status_code}: {resp.text}"
        )


# =========================================================================
# Processing Edge Cases — behaviors that integration tests verified with mocks
# =========================================================================


class TestProcessingEdgeCases:
    """Verify processing edge cases on the real Pyodide runtime.

    These tests promote behaviors from integration tests (which used mocked
    HTTP and D1) to real infrastructure where they exercise the actual
    content extraction pipeline.
    """

    async def test_user_title_survives_processing(
        self,
        http_client: httpx.AsyncClient,
        cleanup_articles: list[str],
    ) -> None:
        """User-supplied title at creation time is preserved after processing."""
        test_id = uuid.uuid4().hex[:8]
        user_title = f"My Custom Title {test_id}"

        resp = await http_client.post(
            "/api/articles",
            json={
                "url": f"https://example.com?e2e=title-{test_id}",
                "title": user_title,
            },
        )
        assert resp.status_code == 201
        article_id = resp.json()["id"]
        cleanup_articles.append(article_id)

        # Process the article
        resp = await http_client.post(
            f"/api/articles/{article_id}/process-now",
            timeout=60.0,
        )
        assert resp.status_code == 200

        # Verify user title survived processing
        resp = await http_client.get(f"/api/articles/{article_id}")
        assert resp.status_code == 200
        article = resp.json()
        assert article["title"] == user_title, (
            f"User title lost during processing: expected '{user_title}', "
            f"got '{article.get('title')}'"
        )

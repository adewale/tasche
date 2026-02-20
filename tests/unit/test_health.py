"""Tests for Phase 12 — URL health checker and check-original endpoints.

Covers:
- ``check_original_url``: status mapping for HTTP responses and errors
- SSRF protection for health checks
- ``POST /{article_id}/check-original`` endpoint: updates D1, returns 404
- ``POST /batch-check-originals`` endpoint: batch checking
- Enhanced ``metadata.json`` with ``content_hash`` and ``extraction_method``
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from src.articles.routes import router
from src.auth.session import COOKIE_NAME
from tests.conftest import (
    ArticleFactory,
    MockD1,
    MockEnv,
    MockQueue,
    MockR2,
    _browser_env,
    _make_mock_client,
    _make_test_app,
    _noop_screenshot,
)
from tests.conftest import (
    TrackingD1 as _TrackingD1,
)
from tests.conftest import (
    _authenticated_client as _authenticated_client_base,
)

# =========================================================================
# Helpers
# =========================================================================

_ROUTERS = ((router, "/api/articles"),)


def _make_app(env):
    return _make_test_app(env, *_ROUTERS)


async def _authenticated_client(env: MockEnv) -> tuple[TestClient, str]:
    return await _authenticated_client_base(env, *_ROUTERS)


# =========================================================================
# check_original_url — status mapping
# =========================================================================


class TestCheckOriginalUrl:
    """Tests for the check_original_url function."""

    async def test_200_returns_available(self) -> None:
        """HTTP 200 maps to 'available'."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://example.com/article"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=mock_response)

        with patch("articles.health.HttpClient", return_value=mock_client):
            from articles.health import check_original_url

            result = await check_original_url("https://example.com/article")

        assert result == "available"

    async def test_301_with_200_returns_available(self) -> None:
        """HTTP 200 after redirect maps to 'available'."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://www.example.com/article"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=mock_response)

        with patch("articles.health.HttpClient", return_value=mock_client):
            from articles.health import check_original_url

            result = await check_original_url("https://example.com/article")

        assert result == "available"

    async def test_403_returns_paywalled(self) -> None:
        """HTTP 403 maps to 'paywalled'."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.url = "https://example.com/article"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=mock_response)

        with patch("articles.health.HttpClient", return_value=mock_client):
            from articles.health import check_original_url

            result = await check_original_url("https://example.com/article")

        assert result == "paywalled"

    async def test_401_returns_paywalled(self) -> None:
        """HTTP 401 maps to 'paywalled'."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.url = "https://example.com/article"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=mock_response)

        with patch("articles.health.HttpClient", return_value=mock_client):
            from articles.health import check_original_url

            result = await check_original_url("https://example.com/article")

        assert result == "paywalled"

    async def test_404_returns_gone(self) -> None:
        """HTTP 404 maps to 'gone'."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.url = "https://example.com/article"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=mock_response)

        with patch("articles.health.HttpClient", return_value=mock_client):
            from articles.health import check_original_url

            result = await check_original_url("https://example.com/article")

        assert result == "gone"

    async def test_410_returns_gone(self) -> None:
        """HTTP 410 maps to 'gone'."""
        mock_response = MagicMock()
        mock_response.status_code = 410
        mock_response.url = "https://example.com/article"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=mock_response)

        with patch("articles.health.HttpClient", return_value=mock_client):
            from articles.health import check_original_url

            result = await check_original_url("https://example.com/article")

        assert result == "gone"

    async def test_connect_error_returns_domain_dead(self) -> None:
        """Connection error (DNS failure etc.) maps to 'domain_dead'."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(side_effect=ConnectionError("DNS lookup failed"))

        with patch("articles.health.HttpClient", return_value=mock_client):
            from articles.health import check_original_url

            result = await check_original_url("https://dead-domain.example")

        assert result == "domain_dead"

    async def test_connect_timeout_returns_domain_dead(self) -> None:
        """Connection timeout maps to 'domain_dead'."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(side_effect=TimeoutError("Timed out"))

        with patch("articles.health.HttpClient", return_value=mock_client):
            from articles.health import check_original_url

            result = await check_original_url("https://slow-domain.example")

        assert result == "domain_dead"

    async def test_500_returns_unknown(self) -> None:
        """HTTP 500 maps to 'unknown'."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.url = "https://example.com/article"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=mock_response)

        with patch("articles.health.HttpClient", return_value=mock_client):
            from articles.health import check_original_url

            result = await check_original_url("https://example.com/article")

        assert result == "unknown"


# =========================================================================
# check_original_url — SSRF protection
# =========================================================================


class TestCheckOriginalUrlSsrf:
    """SSRF protection tests for check_original_url."""

    async def test_skips_localhost(self) -> None:
        """Private URL (localhost) returns 'unknown' without making a request."""
        from articles.health import check_original_url

        result = await check_original_url("https://localhost/admin")
        assert result == "unknown"

    async def test_skips_127_0_0_1(self) -> None:
        """Private URL (127.0.0.1) returns 'unknown'."""
        from articles.health import check_original_url

        result = await check_original_url("https://127.0.0.1/secret")
        assert result == "unknown"

    async def test_skips_10_x(self) -> None:
        """Private URL (10.x.x.x) returns 'unknown'."""
        from articles.health import check_original_url

        result = await check_original_url("https://10.0.0.1/internal")
        assert result == "unknown"

    async def test_skips_192_168_x(self) -> None:
        """Private URL (192.168.x.x) returns 'unknown'."""
        from articles.health import check_original_url

        result = await check_original_url("https://192.168.1.1/router")
        assert result == "unknown"

    async def test_skips_169_254_x(self) -> None:
        """Cloud metadata endpoint returns 'unknown'."""
        from articles.health import check_original_url

        result = await check_original_url("https://169.254.169.254/metadata")
        assert result == "unknown"

    async def test_skips_non_http_scheme(self) -> None:
        """Non-HTTP scheme returns 'unknown'."""
        from articles.health import check_original_url

        result = await check_original_url("ftp://files.example.com/doc.pdf")
        assert result == "unknown"

    async def test_skips_redirect_to_private(self) -> None:
        """A redirect to a private IP returns 'unknown'."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "http://127.0.0.1/internal"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(return_value=mock_response)

        with patch("articles.health.HttpClient", return_value=mock_client):
            from articles.health import check_original_url

            result = await check_original_url("https://redirect.example.com/to-local")

        assert result == "unknown"


# =========================================================================
# POST /api/articles/{id}/check-original — endpoint tests
# =========================================================================


class TestCheckOriginalEndpoint:
    """Tests for the POST /{article_id}/check-original endpoint."""

    async def test_updates_original_status_in_d1(self) -> None:
        """Endpoint updates original_status and returns the new status."""
        article = ArticleFactory.create(
            id="art_check_001",
            user_id="user_001",
            original_url="https://example.com/article",
            original_status="unknown",
        )

        calls: list[tuple[str, list[Any]]] = []

        def execute(sql: str, params: list) -> list:
            calls.append((sql, params))
            if "SELECT" in sql and "art_check_001" in params:
                return [article]
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)

        with patch("src.articles.routes.check_original_url", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = "available"
            resp = client.post(
                "/api/articles/art_check_001/check-original",
                cookies={COOKIE_NAME: session_id},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["article_id"] == "art_check_001"
        assert data["original_status"] == "available"
        assert "last_checked_at" in data

        # Verify D1 UPDATE was called
        update_calls = [
            (sql, params) for sql, params in calls if "UPDATE" in sql and "original_status" in sql
        ]
        assert len(update_calls) >= 1
        sql, params = update_calls[0]
        assert "available" in params

    async def test_returns_404_for_nonexistent_article(self) -> None:
        """Endpoint returns 404 when article does not exist."""
        db = MockD1(execute=lambda sql, params: [])
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/articles/nonexistent_id/check-original",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    async def test_requires_authentication(self) -> None:
        """Endpoint returns 401 without valid session."""
        db = MockD1()
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/api/articles/some_id/check-original")
        assert resp.status_code == 401


# =========================================================================
# POST /api/articles/batch-check-originals — endpoint tests
# =========================================================================


class TestBatchCheckOriginals:
    """Tests for the POST /batch-check-originals endpoint."""

    async def test_checks_unknown_articles(self) -> None:
        """Batch endpoint checks articles with unknown status."""
        calls: list[tuple[str, list[Any]]] = []

        def execute(sql: str, params: list) -> list:
            calls.append((sql, params))
            if "original_status = 'unknown'" in sql:
                return [
                    {"id": "batch_001", "original_url": "https://example.com/a1"},
                    {"id": "batch_002", "original_url": "https://example.com/a2"},
                ]
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)

        with patch("src.articles.routes.check_original_url", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = "available"
            resp = client.post(
                "/api/articles/batch-check-originals",
                cookies={COOKIE_NAME: session_id},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["checked"] == 2
        assert len(data["results"]) == 2
        assert data["results"][0]["original_status"] == "available"
        assert data["results"][1]["original_status"] == "available"

    async def test_returns_empty_when_nothing_to_check(self) -> None:
        """Batch endpoint returns empty results when no articles need checking."""
        db = MockD1(execute=lambda sql, params: [])
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)

        with patch("articles.routes.check_original_url", new_callable=AsyncMock):
            resp = client.post(
                "/api/articles/batch-check-originals",
                cookies={COOKIE_NAME: session_id},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["checked"] == 0
        assert data["results"] == []


# =========================================================================
# metadata.json — enhanced fields
# =========================================================================


class TestMetadataJsonEnhanced:
    """Tests for enhanced metadata.json with content_hash and extraction_method."""

    async def test_metadata_includes_content_hash(self) -> None:
        """metadata.json includes a sha256 content_hash field."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_hash", "https://example.com/article", env)

        metadata_key = "articles/art_hash/metadata.json"
        assert metadata_key in r2._store
        metadata = json.loads(r2._store[metadata_key].decode("utf-8"))
        assert "content_hash" in metadata
        # SHA-256 produces a 64-character hex string
        assert len(metadata["content_hash"]) == 64

    async def test_metadata_includes_extraction_method(self) -> None:
        """metadata.json includes extraction_method field set to 'readability'."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_method", "https://example.com/article", env)

        metadata_key = "articles/art_method/metadata.json"
        assert metadata_key in r2._store
        metadata = json.loads(r2._store[metadata_key].decode("utf-8"))
        assert metadata["extraction_method"] == "readability"

    async def test_metadata_includes_archived_at(self) -> None:
        """metadata.json includes an archived_at timestamp."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_ts", "https://example.com/article", env)

        metadata_key = "articles/art_ts/metadata.json"
        assert metadata_key in r2._store
        metadata = json.loads(r2._store[metadata_key].decode("utf-8"))
        assert "archived_at" in metadata
        # ISO 8601 timestamp should contain 'T'
        assert "T" in metadata["archived_at"]

    async def test_metadata_has_all_expected_fields(self) -> None:
        """metadata.json contains all required provenance fields."""
        db = _TrackingD1()
        r2 = MockR2()
        env = _browser_env(MockEnv(db=db, content=r2))

        mock_client = _make_mock_client()

        with (
            patch("articles.processing.HttpClient", return_value=mock_client),
            patch("articles.processing.screenshot", side_effect=_noop_screenshot),
        ):
            from articles.processing import process_article

            await process_article("art_all", "https://example.com/article", env)

        metadata_key = "articles/art_all/metadata.json"
        assert metadata_key in r2._store
        metadata = json.loads(r2._store[metadata_key].decode("utf-8"))

        expected_fields = [
            "article_id",
            "archived_at",
            "original_url",
            "final_url",
            "canonical_url",
            "domain",
            "title",
            "author",
            "word_count",
            "reading_time_minutes",
            "image_count",
            "extraction_method",
            "content_hash",
        ]
        for field_name in expected_fields:
            assert field_name in metadata, f"Missing field: {field_name}"


# =========================================================================
# check_original_url — OSError handling (Issue G8)
# =========================================================================


class TestCheckOriginalUrlOsError:
    """OSError maps to 'domain_dead' for socket-level failures."""

    async def test_oserror_returns_domain_dead(self) -> None:
        """OSError (socket-level failure) maps to 'domain_dead'."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head = AsyncMock(side_effect=OSError("Network is unreachable"))

        with patch("articles.health.HttpClient", return_value=mock_client):
            from articles.health import check_original_url

            result = await check_original_url("https://unreachable.example.com/page")

        assert result == "domain_dead"


# =========================================================================
# POST /api/articles/batch-check-originals — boundary & staleness (Issue G7)
# =========================================================================


class TestBatchCheckOriginalsEdgeCases:
    """Edge-case tests for the batch-check-originals endpoint."""

    async def test_batch_limits_to_10_articles(self) -> None:
        """Even if D1 returns more rows, the SQL includes LIMIT 10."""
        captured_sql: list[str] = []

        def execute(sql: str, params: list) -> list:
            captured_sql.append(sql)
            if "original_status" in sql and "SELECT" in sql:
                # Return only 2 articles (the LIMIT is enforced in SQL, not Python)
                return [
                    {"id": f"batch_{i:03d}", "original_url": f"https://example.com/a{i}"}
                    for i in range(2)
                ]
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)

        with patch("src.articles.routes.check_original_url", new_callable=AsyncMock) as mock_check:
            mock_check.return_value = "available"
            resp = client.post(
                "/api/articles/batch-check-originals",
                cookies={COOKIE_NAME: session_id},
            )

        assert resp.status_code == 200

        # Verify the SELECT query includes LIMIT 10
        select_queries = [s for s in captured_sql if "SELECT" in s and "original_status" in s]
        assert len(select_queries) >= 1
        assert "LIMIT 10" in select_queries[0]

    async def test_batch_includes_staleness_condition(self) -> None:
        """The batch query checks for last_checked_at older than 30 days."""
        captured_sql: list[str] = []

        def execute(sql: str, params: list) -> list:
            captured_sql.append(sql)
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)

        with patch("src.articles.routes.check_original_url", new_callable=AsyncMock):
            resp = client.post(
                "/api/articles/batch-check-originals",
                cookies={COOKIE_NAME: session_id},
            )

        assert resp.status_code == 200

        # Verify the query includes the staleness condition
        select_queries = [s for s in captured_sql if "SELECT" in s and "original_status" in s]
        assert len(select_queries) >= 1
        sql = select_queries[0]
        assert "last_checked_at" in sql
        assert "-30 days" in sql

    async def test_batch_continues_on_check_failure(self) -> None:
        """If check_original_url raises for one article, others still get processed."""
        call_count = 0

        def execute(sql: str, params: list) -> list:
            if "original_status" in sql and "SELECT" in sql:
                return [
                    {"id": "ok_001", "original_url": "https://example.com/ok1"},
                    {"id": "fail_002", "original_url": "https://example.com/fail"},
                    {"id": "ok_003", "original_url": "https://example.com/ok2"},
                ]
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)

        async def side_effect(url: str) -> str:
            nonlocal call_count
            call_count += 1
            if "fail" in url:
                raise RuntimeError("Simulated check failure")
            return "available"

        with patch("src.articles.routes.check_original_url", new_callable=AsyncMock) as mock_check:
            mock_check.side_effect = side_effect
            resp = client.post(
                "/api/articles/batch-check-originals",
                cookies={COOKIE_NAME: session_id},
            )

        assert resp.status_code == 200
        data = resp.json()
        # All 3 articles should be in results (the failed one falls back to 'unknown')
        assert data["checked"] == 3
        assert len(data["results"]) == 3

        # Verify the failed one got 'unknown' and the others got 'available'
        results_by_id = {r["article_id"]: r["original_status"] for r in data["results"]}
        assert results_by_id["ok_001"] == "available"
        assert results_by_id["fail_002"] == "unknown"
        assert results_by_id["ok_003"] == "available"

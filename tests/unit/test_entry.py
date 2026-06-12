"""Tests for the Worker entrypoint (src/entry.py).

Covers queue dispatch (unknown types, missing fields, handler exceptions,
body.to_py() conversion) and SPA fallback routing logic.
"""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import MockEnv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockMessage:
    """Simulates a single queue message with ack/retry controls."""

    def __init__(self, body: Any, attempts: int = 1) -> None:
        self.body = body
        self.attempts = attempts
        self.acked = False
        self.retried = False
        self.retry_options: Any = None

    def ack(self) -> None:
        self.acked = True

    def retry(self, options: Any = None) -> None:
        self.retried = True
        self.retry_options = options


class _MockBatch:
    """Simulates a Workers ``MessageBatch`` object."""

    def __init__(self, messages: list[_MockMessage]) -> None:
        self.messages = messages


# ---------------------------------------------------------------------------
# Queue dispatch — unknown message type
# ---------------------------------------------------------------------------


class TestQueueUnknownType:
    async def test_unknown_type_acks_and_logs(self, capsys: Any) -> None:
        """A message with an unrecognised type is acked and logged."""
        from entry import Default

        worker = Default()
        env = MockEnv()
        worker.env = env

        msg = _MockMessage({"type": "totally_unknown"})
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        assert msg.retried is False

        output = capsys.readouterr().out
        assert "unknown_type" in output
        assert "totally_unknown" in output

    async def test_missing_type_defaults_to_unknown(self, capsys: Any) -> None:
        """A message with no 'type' field is treated as unknown and acked."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg = _MockMessage({"some_field": "value"})
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        output = capsys.readouterr().out
        assert "unknown_type" in output


# ---------------------------------------------------------------------------
# Queue dispatch — missing required fields
# ---------------------------------------------------------------------------


class TestQueueMissingFields:
    async def test_article_processing_missing_article_id(self, capsys: Any) -> None:
        """article_processing without article_id is skipped and acked."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg = _MockMessage(
            {
                "type": "article_processing",
                "url": "https://example.com/page",
            }
        )
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        output = capsys.readouterr().out
        assert "skipped" in output

    async def test_article_processing_missing_url(self, capsys: Any) -> None:
        """article_processing without url is skipped and acked."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg = _MockMessage(
            {
                "type": "article_processing",
                "article_id": "art_123",
            }
        )
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        output = capsys.readouterr().out
        assert "skipped" in output

    async def test_tts_generation_missing_article_id(self, capsys: Any) -> None:
        """tts_generation without article_id is skipped and acked."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg = _MockMessage(
            {
                "type": "tts_generation",
                "user_id": "user_001",
            }
        )
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        output = capsys.readouterr().out
        assert "skipped" in output

    async def test_tts_generation_missing_user_id(self, capsys: Any) -> None:
        """tts_generation without user_id is skipped and acked."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg = _MockMessage(
            {
                "type": "tts_generation",
                "article_id": "art_456",
            }
        )
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        output = capsys.readouterr().out
        assert "skipped" in output


# ---------------------------------------------------------------------------
# Queue dispatch — handler exception triggers retry
# ---------------------------------------------------------------------------


class TestQueueHandlerException:
    async def test_handler_exception_calls_retry(self, capsys: Any) -> None:
        """When a handler raises, the message is retried, not acked."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg = _MockMessage(
            {
                "type": "article_processing",
                "article_id": "art_err",
                "url": "https://example.com/page",
            }
        )
        batch = _MockBatch([msg])

        with patch(
            "articles.processing.process_article",
            side_effect=RuntimeError("processing failed"),
        ):
            await worker.queue(batch)

        assert msg.retried is True
        assert msg.acked is False
        output = capsys.readouterr().out
        assert '"outcome": "error"' in output

    async def test_retry_uses_exponential_backoff_delay(self, capsys: Any) -> None:
        """Retries pass increasing delaySeconds based on the attempt number."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg1 = _MockMessage(
            {"type": "article_processing", "article_id": "a1", "url": "https://x.test/1"},
            attempts=1,
        )
        msg2 = _MockMessage(
            {"type": "article_processing", "article_id": "a2", "url": "https://x.test/2"},
            attempts=2,
        )

        with patch(
            "articles.processing.process_article",
            side_effect=RuntimeError("boom"),
        ):
            await worker.queue(_MockBatch([msg1, msg2]))

        assert msg1.retry_options == {"delaySeconds": 30}
        assert msg2.retry_options == {"delaySeconds": 60}

    async def test_terminal_failure_marks_article_failed_and_acks(self, capsys: Any) -> None:
        """On the final permitted delivery, the article is marked failed and acked."""
        from entry import _MAX_QUEUE_DELIVERIES, Default
        from tests.conftest import TrackingD1

        db = TrackingD1()
        worker = Default()
        worker.env = MockEnv(db=db)

        msg = _MockMessage(
            {
                "type": "article_processing",
                "article_id": "art_term",
                "url": "https://example.com/page",
                "user_id": "user_001",
            },
            attempts=_MAX_QUEUE_DELIVERIES,
        )

        with patch(
            "articles.processing.process_article",
            side_effect=RuntimeError("still failing"),
        ):
            await worker.queue(_MockBatch([msg]))

        assert msg.acked is True
        assert msg.retried is False

        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if "status = 'failed'" in sql and "audio_status" not in sql
        ]
        assert failed_updates, f"No terminal-failure UPDATE found in {db.executed}"
        sql, params = failed_updates[-1]
        assert "user_id = ?" in sql
        assert params[-2:] == ["art_term", "user_001"]

        output = capsys.readouterr().out
        assert '"queue.terminal_failure": true' in output

    async def test_terminal_tts_failure_marks_audio_status_failed(self, capsys: Any) -> None:
        """tts_generation terminal failures set audio_status='failed'."""
        from entry import _MAX_QUEUE_DELIVERIES, Default
        from tests.conftest import TrackingD1

        db = TrackingD1()
        worker = Default()
        worker.env = MockEnv(db=db)

        msg = _MockMessage(
            {
                "type": "tts_generation",
                "article_id": "art_tts_term",
                "user_id": "user_001",
            },
            attempts=_MAX_QUEUE_DELIVERIES + 1,
        )

        with patch(
            "tts.processing.process_tts",
            side_effect=RuntimeError("AI down"),
        ):
            await worker.queue(_MockBatch([msg]))

        assert msg.acked is True
        assert msg.retried is False
        audio_updates = [
            (sql, params) for sql, params in db.executed if "audio_status = 'failed'" in sql
        ]
        assert audio_updates, f"No audio_status terminal UPDATE found in {db.executed}"


# ---------------------------------------------------------------------------
# Queue dispatch — body.to_py() conversion path
# ---------------------------------------------------------------------------


class TestQueueBodyConversion:
    async def test_body_as_dict(self, capsys: Any) -> None:
        """When body is already a dict, _to_py_safe passes it through."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        body_dict = {"type": "totally_unknown_type", "data": "test"}
        msg = _MockMessage(body_dict)
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        output = capsys.readouterr().out
        assert "totally_unknown_type" in output

    async def test_body_as_json_string(self, capsys: Any) -> None:
        """When body is a JSON string, it is parsed via json.loads."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        body_str = json.dumps({"type": "unknown_str_type"})
        msg = _MockMessage(body_str)
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        output = capsys.readouterr().out
        assert "unknown_str_type" in output


# ---------------------------------------------------------------------------
# SPA fallback routing
# ---------------------------------------------------------------------------


class TestSPAFallbackRouting:
    async def test_api_routes_go_to_fastapi(self, monkeypatch) -> None:
        """Requests to /api/* are handled by the FastAPI ASGI app."""
        from entry import Default

        worker = Default()
        env = MockEnv()
        worker.env = env

        # Mock URL parsing and ASGI fetch
        mock_url = MagicMock()
        mock_url.pathname = "/api/articles"

        mock_request = MagicMock()
        mock_request.url = "https://tasche.test/api/articles"
        mock_request.js_object = mock_request

        mock_response = MagicMock()

        mock_URL_cls = MagicMock()
        mock_URL_cls.new = MagicMock(return_value=mock_url)
        mock_Request_cls = MagicMock()

        mock_js = MagicMock(URL=mock_URL_cls, Request=mock_Request_cls)

        monkeypatch.setitem(sys.modules, "js", mock_js)

        with patch("entry.asgi") as mock_asgi:
            mock_asgi.fetch = AsyncMock(return_value=mock_response)
            result = await worker.fetch(mock_request)

        mock_asgi.fetch.assert_called_once()
        assert result == mock_response

    async def test_non_api_routes_go_to_assets(self, monkeypatch) -> None:
        """Non-/api/ requests are served from the ASSETS binding with security headers."""
        from entry import Default

        worker = Default()
        env = MockEnv()
        # Add an ASSETS binding
        mock_assets = MagicMock()
        asset_response = MagicMock()
        asset_response.status = 200
        mock_assets.fetch = AsyncMock(return_value=asset_response)
        env.ASSETS = mock_assets
        worker.env = env

        mock_url = MagicMock()
        mock_url.pathname = "/about"

        mock_request = MagicMock()
        mock_request.url = "https://tasche.test/about"
        mock_request.js_object = mock_request

        mock_URL_cls = MagicMock()
        mock_URL_cls.new = MagicMock(return_value=mock_url)
        mock_Request_cls = MagicMock()

        mock_js = MagicMock(URL=mock_URL_cls, Request=mock_Request_cls)

        monkeypatch.setitem(sys.modules, "js", mock_js)

        result = await worker.fetch(mock_request)

        mock_assets.fetch.assert_called_once_with(mock_request.js_object)
        # The asset response is wrapped in a new Response carrying the
        # canonical security headers (assets bypass the ASGI middleware).
        mock_js.Response.new.assert_called_once_with(asset_response.body, asset_response)
        assert result == mock_js.Response.new.return_value
        set_headers = {call.args[0] for call in result.headers.set.call_args_list}
        assert "content-security-policy" in set_headers
        assert "x-frame-options" in set_headers
        # HTTPS request → HSTS applied to the asset response
        assert "strict-transport-security" in set_headers

    async def test_404_falls_back_to_index_document(self, monkeypatch) -> None:
        """When ASSETS returns 404, the SPA fallback serves the "/" document.

        Fetching "/" (not "/index.html") avoids the 307 redirect Workers
        Assets issues when canonicalising /index.html.
        """
        from entry import Default

        worker = Default()
        env = MockEnv()

        # First fetch returns 404, second (the "/" document) returns 200
        asset_404 = MagicMock()
        asset_404.status = 404
        index_response = MagicMock()
        index_response.status = 200

        mock_assets = MagicMock()
        mock_assets.fetch = AsyncMock(side_effect=[asset_404, index_response])
        env.ASSETS = mock_assets
        worker.env = env

        mock_url = MagicMock()
        mock_url.pathname = "/some/spa/route"

        mock_index_url = MagicMock()
        mock_js_request = MagicMock()

        mock_URL_cls = MagicMock()
        mock_URL_cls.new = MagicMock(side_effect=[mock_url, mock_index_url])

        mock_Request_cls = MagicMock()
        mock_Request_cls.new = MagicMock(return_value=mock_js_request)

        mock_request = MagicMock()
        mock_request.url = "https://tasche.test/some/spa/route"
        mock_request.js_object = mock_request

        mock_js = MagicMock(URL=mock_URL_cls, Request=mock_Request_cls)

        monkeypatch.setitem(sys.modules, "js", mock_js)

        result = await worker.fetch(mock_request)

        # Second call should be the SPA fallback, requesting "/"
        assert mock_assets.fetch.call_count == 2
        fallback_url_call = mock_URL_cls.new.call_args_list[1]
        assert fallback_url_call.args[0] == "/"
        # Fallback response is wrapped with security headers too
        mock_js.Response.new.assert_called_once_with(index_response.body, index_response)
        assert result == mock_js.Response.new.return_value


# ---------------------------------------------------------------------------
# GET /api/health/config — configuration check
# ---------------------------------------------------------------------------


class TestConfigCheck:
    """Tests for the configuration verification endpoint."""

    def _make_client(self, env: MockEnv):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient as TC

        from entry import app

        # Enable dev auth so the config endpoint is accessible — the bypass
        # requires an explicit non-production WORKER_ENV.
        env.DISABLE_AUTH = "true"
        env.WORKER_ENV = "development"

        # Clone the app's routes into a test app with env injection
        test_app = FastAPI()

        from src.wrappers import SafeEnv

        safe_env = SafeEnv(env)

        @test_app.middleware("http")
        async def inject_env(request, call_next):
            request.scope["env"] = safe_env
            return await call_next(request)

        for route in app.routes:
            test_app.routes.append(route)

        return TC(test_app, raise_server_exceptions=False)

    def test_all_configured_returns_ok(self) -> None:
        """When all required config is present, status is 'ok'."""
        env = MockEnv()
        env.SITE_URL = "https://tasche.example.com"
        env.ALLOWED_EMAILS = "user@example.com"
        env.GITHUB_CLIENT_ID = "test-id"
        env.GITHUB_CLIENT_SECRET = "test-secret"
        env.READABILITY = "mock-readability-binding"

        client = self._make_client(env)
        resp = client.get("/api/health/config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        # All items should be configured
        for item in data["checks"]:
            assert item["status"] == "ok", f"{item['name']} should be ok"

    def test_missing_optional_returns_degraded(self) -> None:
        """When optional bindings are missing, status is 'degraded'."""
        env = MockEnv()
        env.SITE_URL = "https://tasche.example.com"
        env.ALLOWED_EMAILS = "user@example.com"
        env.GITHUB_CLIENT_ID = "test-id"
        env.GITHUB_CLIENT_SECRET = "test-secret"
        # READABILITY not set — optional

        client = self._make_client(env)
        resp = client.get("/api/health/config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        missing = [c for c in data["checks"] if c["status"] == "missing"]
        missing_names = {c["name"] for c in missing}
        assert "READABILITY" in missing_names

    def test_missing_required_returns_error(self) -> None:
        """When required vars are missing, status is 'error'."""
        env = MockEnv()
        # MockEnv sets defaults — clear them to simulate missing config
        env.SITE_URL = ""
        env.GITHUB_CLIENT_ID = ""
        env.GITHUB_CLIENT_SECRET = ""

        client = self._make_client(env)
        resp = client.get("/api/health/config")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        missing = {c["name"] for c in data["checks"] if c["status"] == "missing"}
        assert "SITE_URL" in missing
        assert "GITHUB_CLIENT_ID" in missing

    def test_bindings_checked(self) -> None:
        """D1, R2, KV, Queue, and AI bindings are verified."""
        env = MockEnv()
        env.SITE_URL = "https://tasche.example.com"
        env.ALLOWED_EMAILS = "user@example.com"
        env.GITHUB_CLIENT_ID = "test-id"
        env.GITHUB_CLIENT_SECRET = "test-secret"

        client = self._make_client(env)
        resp = client.get("/api/health/config")

        data = resp.json()
        check_names = {c["name"] for c in data["checks"]}
        assert "DB" in check_names
        assert "CONTENT" in check_names
        assert "SESSIONS" in check_names
        assert "ARTICLE_QUEUE" in check_names
        assert "AI" in check_names


# ---------------------------------------------------------------------------
# DISABLE_AUTH guard
# ---------------------------------------------------------------------------


class TestDisableAuthGuard:
    """DISABLE_AUTH must be blocked in production (WORKER_ENV=production)."""

    def _make_client(self, env: MockEnv):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient as TC

        from entry import app
        from src.wrappers import SafeEnv

        safe_env = SafeEnv(env)
        test_app = FastAPI()

        @test_app.middleware("http")
        async def inject_env(request, call_next):
            request.scope["env"] = safe_env
            return await call_next(request)

        for route in app.routes:
            test_app.routes.append(route)

        return TC(test_app, raise_server_exceptions=False)

    def test_disable_auth_blocked_in_production(self) -> None:
        """DISABLE_AUTH + WORKER_ENV=production returns 500."""
        env = MockEnv(
            disable_auth="true",
            worker_env="production",
        )
        client = self._make_client(env)
        resp = client.get("/api/articles")
        assert resp.status_code == 500
        assert "DISABLE_AUTH" in resp.json()["detail"]

    def test_disable_auth_allowed_in_staging(self) -> None:
        """DISABLE_AUTH + WORKER_ENV=staging is allowed (E2E testing)."""
        env = MockEnv(
            disable_auth="true",
            worker_env="staging",
        )
        client = self._make_client(env)
        resp = client.get("/api/articles")
        assert resp.status_code == 200

    def test_disable_auth_allowed_in_development(self) -> None:
        """DISABLE_AUTH + WORKER_ENV=development is allowed (local dev)."""
        env = MockEnv(
            disable_auth="true",
            worker_env="development",
            site_url="http://localhost:8787",
        )
        client = self._make_client(env)
        resp = client.get("/api/articles")
        assert resp.status_code == 200

    def test_disable_auth_blocked_without_worker_env(self) -> None:
        """DISABLE_AUTH without an explicit WORKER_ENV is refused.

        The default deploy path (deploy-button / `wrangler deploy` without
        --env) sets no WORKER_ENV — it must never run with auth disabled.
        Local dev gets WORKER_ENV=development via .dev.vars.
        """
        env = MockEnv(
            disable_auth="true",
            site_url="http://localhost:8787",
        )
        client = self._make_client(env)
        resp = client.get("/api/articles")
        assert resp.status_code == 500
        assert "WORKER_ENV" in resp.json()["detail"]

"""Tests for the Worker entrypoint (src/entry.py).

Covers queue dispatch (unknown types, missing fields, handler exceptions,
body.to_py() conversion) and SPA fallback routing logic.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import MockEnv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockMessage:
    """Simulates a single queue message with ack/retry controls."""

    def __init__(self, body: Any) -> None:
        self.body = body
        self.acked = False
        self.retried = False

    def ack(self) -> None:
        self.acked = True

    def retry(self) -> None:
        self.retried = True


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

    async def test_body_as_plain_dict(self) -> None:
        """When body is already a dict, _to_py_safe returns it unchanged."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg = _MockMessage({"type": "some_other_unknown"})
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True


# ---------------------------------------------------------------------------
# SPA fallback routing
# ---------------------------------------------------------------------------


class TestSPAFallbackRouting:
    async def test_api_routes_go_to_fastapi(self) -> None:
        """Requests to /api/* are handled by the FastAPI ASGI app."""
        import sys

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

        # Temporarily inject the mock js module so `from js import URL` works
        old_js = sys.modules.get("js")
        sys.modules["js"] = mock_js
        try:
            with patch("entry.asgi") as mock_asgi:
                mock_asgi.fetch = AsyncMock(return_value=mock_response)
                result = await worker.fetch(mock_request)

            mock_asgi.fetch.assert_called_once()
            assert result == mock_response
        finally:
            if old_js is None:
                sys.modules.pop("js", None)
            else:
                sys.modules["js"] = old_js

    async def test_non_api_routes_go_to_assets(self) -> None:
        """Non-/api/ requests are served from the ASSETS binding."""
        import sys

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

        old_js = sys.modules.get("js")
        sys.modules["js"] = mock_js
        try:
            result = await worker.fetch(mock_request)
        finally:
            if old_js is None:
                sys.modules.pop("js", None)
            else:
                sys.modules["js"] = old_js

        mock_assets.fetch.assert_called_once_with(mock_request.js_object)
        assert result == asset_response

    async def test_404_falls_back_to_index_html(self) -> None:
        """When ASSETS returns 404, the SPA fallback serves /index.html."""
        import sys

        from entry import Default

        worker = Default()
        env = MockEnv()

        # First fetch returns 404, second (index.html) returns 200
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

        old_js = sys.modules.get("js")
        sys.modules["js"] = mock_js
        try:
            result = await worker.fetch(mock_request)
        finally:
            if old_js is None:
                sys.modules.pop("js", None)
            else:
                sys.modules["js"] = old_js

        # Second call should be the index.html fallback
        assert mock_assets.fetch.call_count == 2
        assert result == index_response


# ---------------------------------------------------------------------------
# GET /api/health/config — configuration check
# ---------------------------------------------------------------------------


class TestConfigCheck:
    """Tests for the configuration verification endpoint."""

    def _make_client(self, env: MockEnv):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient as TC

        from entry import app

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
    """DISABLE_AUTH must be blocked when SITE_URL is HTTPS (production)."""

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

    def test_disable_auth_blocked_with_https_site_url(self) -> None:
        """DISABLE_AUTH + HTTPS SITE_URL returns 500."""
        env = MockEnv(
            disable_auth="true",
            site_url="https://tasche.example.com",
        )
        client = self._make_client(env)
        resp = client.get("/api/articles")
        assert resp.status_code == 500
        assert "DISABLE_AUTH" in resp.json()["detail"]

    def test_disable_auth_allowed_with_http_site_url(self) -> None:
        """DISABLE_AUTH + HTTP SITE_URL is allowed (local dev)."""
        env = MockEnv(
            disable_auth="true",
            site_url="http://localhost:8787",
        )
        client = self._make_client(env)
        resp = client.get("/api/articles")
        assert resp.status_code == 200

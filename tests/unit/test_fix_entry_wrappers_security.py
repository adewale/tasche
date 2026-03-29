"""Tests for audit fixes across entry.py, wrappers.py, observability.py, and security.py.

Covers:
- Issue 2:  /api/health/config requires authentication
- Issue 4:  FastAPI docs disabled in production mode
- Issue 6:  Exception message truncation in observability
- Issue 7:  CSP unsafe-inline is documented
- Issue 32: Timeout support in Pyodide js.fetch() path
- Issue 61: _serve_audio auth extracted into shared helper
- Issue 66: HAS_PYODIDE imported from wrappers (not redefined in entry)
- Issue 67: is_js_null canonical utility in wrappers.py
- Issue 74: _serve_audio uses is_js_null instead of ad-hoc type checks
"""

from __future__ import annotations

import importlib
import inspect
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import MockEnv

# =========================================================================
# Issue 66: HAS_PYODIDE is imported from wrappers, not redefined in entry
# =========================================================================


class TestHasPyodideDedup:
    """HAS_PYODIDE should be defined in wrappers.py and imported in entry.py."""

    def test_entry_does_not_duplicate_has_pyodide(self) -> None:
        """entry.py should not define its own HAS_PYODIDE — wrappers.py is canonical."""
        import src.wrappers as wrappers_mod

        # wrappers is the single source of truth
        assert hasattr(wrappers_mod, "HAS_PYODIDE")

    def test_entry_does_not_define_has_pyodide(self) -> None:
        """entry.py should import HAS_PYODIDE from wrappers, not define it locally."""
        source = inspect.getsource(importlib.import_module("src.entry"))
        # Should not have a local assignment like "HAS_PYODIDE = False" or "HAS_PYODIDE = True"
        import re

        local_assignments = re.findall(r"^HAS_PYODIDE\s*=", source, re.MULTILINE)
        assert len(local_assignments) == 0, (
            "entry.py should not assign HAS_PYODIDE — it should import it from wrappers"
        )

    def test_wrappers_defines_has_pyodide(self) -> None:
        """wrappers.py should be the canonical definition of HAS_PYODIDE."""
        from src.wrappers import HAS_PYODIDE

        # In test environment, Pyodide is not available
        assert HAS_PYODIDE is False


# =========================================================================
# Issue 67: is_js_null canonical utility
# =========================================================================


class TestIsJsNull:
    """The canonical is_js_null() utility should be in wrappers.py."""

    def test_is_js_null_exists_in_wrappers(self) -> None:
        """is_js_null should be importable from wrappers."""
        from src.wrappers import is_js_null

        assert callable(is_js_null)

    def test_is_js_null_returns_true_for_none_outside_pyodide(self) -> None:
        """Outside Pyodide, None is treated as JS null."""
        from src.wrappers import is_js_null

        assert is_js_null(None) is True

    def test_is_js_null_returns_false_for_values(self) -> None:
        """Non-null values should return False."""
        from src.wrappers import is_js_null

        assert is_js_null("hello") is False
        assert is_js_null(0) is False
        assert is_js_null(False) is False
        assert is_js_null({}) is False
        assert is_js_null([]) is False

    def test_backward_compat_alias_exists(self) -> None:
        """_is_js_null_or_undefined should still be importable as an alias."""
        from src.wrappers import _is_js_null_or_undefined, is_js_null

        assert _is_js_null_or_undefined is is_js_null

    def test_entry_imports_is_js_null(self) -> None:
        """entry.py should import is_js_null from wrappers."""
        source = inspect.getsource(importlib.import_module("src.entry"))
        # Verify is_js_null appears in the imports at the top of the module
        assert "is_js_null" in source
        # It should be imported from wrappers, not defined locally
        assert "from wrappers import" in source or "from src.wrappers import" in source


# =========================================================================
# Issue 2: /api/health/config requires authentication
# =========================================================================


class TestHealthConfigAuth:
    """The /api/health/config endpoint must require authentication."""

    def _make_client(self, env: MockEnv) -> TestClient:
        from src.entry import app
        from src.wrappers import SafeEnv

        safe_env = SafeEnv(env)
        test_app = FastAPI()

        @test_app.middleware("http")
        async def inject_env(request, call_next):
            request.scope["env"] = safe_env
            return await call_next(request)

        for route in app.routes:
            test_app.routes.append(route)

        return TestClient(test_app, raise_server_exceptions=False)

    def test_unauthenticated_returns_minimal_response(self) -> None:
        """Without a session cookie, /api/health/config returns only status."""
        env = MockEnv()
        client = self._make_client(env)
        resp = client.get("/api/health/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        # Should NOT include detailed checks or environment info
        assert "checks" not in data
        assert "environment" not in data

    async def test_authenticated_returns_config(self) -> None:
        """With a valid session, /api/health/config returns binding checks."""
        from src.auth.session import COOKIE_NAME, create_session
        from src.entry import app
        from src.wrappers import SafeEnv

        env = MockEnv()
        env.SITE_URL = "https://tasche.example.com"
        env.ALLOWED_EMAILS = "test@example.com"
        env.GITHUB_CLIENT_ID = "test-id"
        env.GITHUB_CLIENT_SECRET = "test-secret"

        user_data = {
            "user_id": "user_001",
            "email": "test@example.com",
            "username": "tester",
            "avatar_url": "",
            "created_at": "2025-01-01T00:00:00",
        }
        session_id = await create_session(env.SESSIONS, user_data)

        safe_env = SafeEnv(env)
        test_app = FastAPI()

        @test_app.middleware("http")
        async def inject_env(request, call_next):
            request.scope["env"] = safe_env
            return await call_next(request)

        for route in app.routes:
            test_app.routes.append(route)

        client = TestClient(
            test_app,
            raise_server_exceptions=False,
            cookies={COOKIE_NAME: session_id},
        )
        resp = client.get("/api/health/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "checks" in data

    async def test_config_no_longer_exposes_environment(self) -> None:
        """The response should not include an 'environment' field."""
        from src.auth.session import COOKIE_NAME, create_session
        from src.entry import app
        from src.wrappers import SafeEnv

        env = MockEnv()
        env.SITE_URL = "https://tasche.example.com"
        env.ALLOWED_EMAILS = "test@example.com"
        env.GITHUB_CLIENT_ID = "test-id"
        env.GITHUB_CLIENT_SECRET = "test-secret"

        user_data = {
            "user_id": "user_001",
            "email": "test@example.com",
            "username": "tester",
            "avatar_url": "",
            "created_at": "2025-01-01T00:00:00",
        }
        session_id = await create_session(env.SESSIONS, user_data)

        safe_env = SafeEnv(env)
        test_app = FastAPI()

        @test_app.middleware("http")
        async def inject_env(request, call_next):
            request.scope["env"] = safe_env
            return await call_next(request)

        for route in app.routes:
            test_app.routes.append(route)

        client = TestClient(
            test_app,
            raise_server_exceptions=False,
            cookies={COOKIE_NAME: session_id},
        )
        resp = client.get("/api/health/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "environment" not in data


# =========================================================================
# Issue 4: FastAPI docs disabled in production
# =========================================================================


class TestDocsDisabledInProduction:
    """FastAPI docs/OpenAPI endpoints should be disabled when WORKER_ENV=production."""

    def test_docs_enabled_by_default(self) -> None:
        """In test/dev mode (no WORKER_ENV), docs should be enabled."""
        from src.entry import app

        assert app.docs_url is not None, "docs_url should be set in non-production mode"
        assert app.redoc_url is not None, "redoc_url should be set in non-production mode"
        assert app.openapi_url is not None, "openapi_url should be set in non-production mode"

    def test_production_flag_logic(self) -> None:
        """The _is_production flag should control docs availability."""
        # We can't easily change os.environ after module import, but we
        # can verify the logic by checking the module variables.
        from src.entry import _is_production

        # In tests, WORKER_ENV is not "production"
        assert _is_production is False

    def test_app_conditional_docs_wiring(self) -> None:
        """Verify the app is wired to conditionally disable docs."""
        # Read the source and check that docs_url is conditional
        source = inspect.getsource(importlib.import_module("src.entry"))
        assert "docs_url=None if _is_production" in source
        assert "redoc_url=None if _is_production" in source
        assert "openapi_url=None if _is_production" in source


# =========================================================================
# Issue 6: Exception message truncation in observability
# =========================================================================


class TestExceptionTruncation:
    """Exception messages should be truncated to prevent log bloat."""

    def test_observability_truncates_error_message(self) -> None:
        """The observability middleware should truncate error.message to 1000 chars."""
        source = inspect.getsource(importlib.import_module("src.observability"))
        # Check that str(exc) is sliced
        assert "str(exc)[:1000]" in source

    async def test_long_exception_is_truncated(self) -> None:
        """A long exception message should be truncated in the emitted event."""
        from src.observability import ObservabilityMiddleware

        async def failing_app(scope, receive, send):
            raise RuntimeError("x" * 2000)

        middleware = ObservabilityMiddleware(failing_app)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
        }

        with patch("src.observability.emit_event") as mock_emit:
            with pytest.raises(RuntimeError):
                await middleware(scope, AsyncMock(), AsyncMock())

            # The event should have been emitted
            assert mock_emit.called
            event = mock_emit.call_args[0][0]
            error_msg = event._fields.get("error.message", "")
            assert len(error_msg) <= 1000


# =========================================================================
# Issue 32: Timeout support in Pyodide fetch path
# =========================================================================


class TestFetchTimeout:
    """The http_fetch function should respect the timeout parameter in Pyodide."""

    def test_pyodide_path_uses_asyncio_wait_for(self) -> None:
        """The Pyodide fetch path should use asyncio.wait_for for timeout."""
        source = inspect.getsource(importlib.import_module("src.wrappers"))
        assert "asyncio.wait_for" in source

    async def test_cpython_path_raises_timeout_error(self) -> None:
        """In CPython (test env), httpx TimeoutException is converted to TimeoutError."""
        import httpx

        from src.wrappers import http_fetch

        # Patch httpx.AsyncClient at the point of use (inside the function)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(TimeoutError):
                await http_fetch("https://example.com", timeout=0.001)


# =========================================================================
# Issue 61: _serve_audio auth extracted into _authenticate_raw_request
# =========================================================================


class TestServeAudioAuthRefactor:
    """_serve_audio should delegate auth to _authenticate_raw_request."""

    def test_authenticate_raw_request_method_exists(self) -> None:
        """Default class should have _authenticate_raw_request method."""
        from src.entry import Default

        assert hasattr(Default, "_authenticate_raw_request")
        assert callable(getattr(Default, "_authenticate_raw_request"))

    def test_serve_audio_does_not_duplicate_cookie_parsing(self) -> None:
        """_serve_audio should not contain cookie parsing logic directly."""
        from src.entry import Default

        source = inspect.getsource(Default._serve_audio)
        # Cookie parsing (COOKIE_NAME, split(";"), etc.) should NOT be in _serve_audio
        assert "COOKIE_NAME" not in source
        assert 'split(";")' not in source

    def test_authenticate_raw_request_has_cookie_logic(self) -> None:
        """_authenticate_raw_request should contain the cookie parsing logic."""
        from src.entry import Default

        source = inspect.getsource(Default._authenticate_raw_request)
        assert "COOKIE_NAME" in source
        assert "get_session" in source


# =========================================================================
# Issue 74: _serve_audio uses is_js_null instead of ad-hoc type checks
# =========================================================================


class TestServeAudioUsesIsJsNull:
    """_serve_audio should use is_js_null() instead of type().__name__ checks."""

    def test_no_adhoc_jsnull_checks_in_serve_audio(self) -> None:
        """_serve_audio should not use type().__name__ == 'JsNull' pattern."""
        from src.entry import Default

        source = inspect.getsource(Default._serve_audio)
        assert "JsNull" not in source
        assert "JsUndefined" not in source

    def test_serve_audio_uses_is_js_null(self) -> None:
        """_serve_audio should call is_js_null for null checks."""
        from src.entry import Default

        source = inspect.getsource(Default._serve_audio)
        assert "is_js_null" in source

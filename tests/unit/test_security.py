"""Tests for security headers and CORS middleware.

Verifies that SecurityHeadersMiddleware injects the correct HTTP security
headers on all responses, including conditional HSTS for HTTPS origins.

Also verifies that CORS is restricted to localhost (for local dev) and
rejects external origins (production is same-origin via bookmarklet popup).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.security import SecurityHeadersMiddleware


def _make_app(site_url: str = "https://tasche.test") -> FastAPI:
    """Create a minimal FastAPI app with SecurityHeadersMiddleware."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.middleware("http")
    async def inject_env(request, call_next):
        request.scope["env"] = type("Env", (), {"SITE_URL": site_url})()
        return await call_next(request)

    @app.get("/test")
    async def test_endpoint():
        return {"ok": True}

    return app


def _make_cors_app() -> FastAPI:
    """Create a minimal FastAPI app with the production CORS config."""
    from src.entry import app

    return app


class TestSecurityHeaders:
    def test_x_content_type_options(self) -> None:
        """X-Content-Type-Options: nosniff is present."""
        client = TestClient(_make_app())
        resp = client.get("/test")
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_x_frame_options(self) -> None:
        """X-Frame-Options: DENY prevents clickjacking."""
        client = TestClient(_make_app())
        resp = client.get("/test")
        assert resp.headers["x-frame-options"] == "DENY"

    def test_referrer_policy(self) -> None:
        """Referrer-Policy is set to strict-origin-when-cross-origin."""
        client = TestClient(_make_app())
        resp = client.get("/test")
        assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"

    def test_permissions_policy(self) -> None:
        """Permissions-Policy disables camera, microphone, geolocation."""
        client = TestClient(_make_app())
        resp = client.get("/test")
        assert resp.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"

    def test_hsts_present_for_https(self) -> None:
        """HSTS header is added when SITE_URL starts with https://."""
        client = TestClient(_make_app(site_url="https://tasche.example.com"))
        resp = client.get("/test")
        assert "strict-transport-security" in resp.headers
        assert "max-age=" in resp.headers["strict-transport-security"]

    def test_hsts_absent_for_http(self) -> None:
        """HSTS header is NOT added for HTTP origins (local dev)."""
        client = TestClient(_make_app(site_url="http://localhost:6060"))
        resp = client.get("/test")
        assert "strict-transport-security" not in resp.headers

    def test_headers_on_error_responses(self) -> None:
        """Security headers are present even on 404 responses."""
        client = TestClient(_make_app())
        resp = client.get("/nonexistent")
        assert resp.status_code == 404
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"


# ---------------------------------------------------------------------------
# CORS — localhost only for local dev; production is same-origin
# ---------------------------------------------------------------------------


class TestCORS:
    """Verify CORS is restricted to localhost for local dev.

    Production requests are same-origin (bookmarklet uses a popup on
    Tasche's own origin), so no external CORS is needed.
    """

    def test_cors_allows_localhost(self) -> None:
        """Preflight from localhost gets CORS headers (local dev)."""
        client = TestClient(_make_cors_app())
        resp = client.options(
            "/api/articles",
            headers={
                "origin": "http://localhost:3000",
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
        assert "POST" in resp.headers["access-control-allow-methods"]
        assert resp.headers["access-control-allow-credentials"] == "true"

    def test_cors_allows_127_0_0_1(self) -> None:
        """Preflight from 127.0.0.1 gets CORS headers (local dev)."""
        client = TestClient(_make_cors_app())
        resp = client.options(
            "/api/articles",
            headers={
                "origin": "http://127.0.0.1:8080",
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["access-control-allow-origin"] == "http://127.0.0.1:8080"
        assert resp.headers["access-control-allow-credentials"] == "true"

    def test_cors_rejects_external_origin(self) -> None:
        """Preflight from an external site does NOT get CORS headers."""
        client = TestClient(_make_cors_app())
        resp = client.options(
            "/api/articles",
            headers={
                "origin": "https://stratechery.com",
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type",
            },
        )
        assert resp.headers.get("access-control-allow-origin") != "https://stratechery.com"

    def test_cors_rejects_chrome_extension(self) -> None:
        """Preflight from a chrome-extension:// origin does NOT get CORS headers."""
        client = TestClient(_make_cors_app())
        ext_origin = "chrome-extension://abcdefghijklmnop"
        resp = client.options(
            "/api/articles",
            headers={
                "origin": ext_origin,
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type",
            },
        )
        assert resp.headers.get("access-control-allow-origin") != ext_origin

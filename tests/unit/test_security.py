"""Tests for security headers middleware.

Verifies that SecurityHeadersMiddleware injects the correct HTTP security
headers on all responses, including conditional HSTS for HTTPS origins.
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

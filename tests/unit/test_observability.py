"""Tests for Phase 8 — Observability (wide events middleware).

Covers:
- Wide event includes all required fields
- Successful request has outcome="success"
- Error request has outcome="error" and error fields
- User ID is included when authenticated
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from src.auth.session import COOKIE_NAME, create_session
from src.observability import ObservabilityMiddleware
from tests.conftest import MockEnv

# ---------------------------------------------------------------------------
# Helper: build a test app with the observability middleware
# ---------------------------------------------------------------------------


def _make_app(env: Any | None = None) -> FastAPI:
    """Create a minimal FastAPI app with ObservabilityMiddleware and env injection."""
    test_app = FastAPI()

    if env is not None:
        from src.wrappers import SafeEnv

        safe_env = SafeEnv(env)

        @test_app.middleware("http")
        async def inject_env(request: Request, call_next):
            request.scope["env"] = safe_env
            return await call_next(request)

    test_app.add_middleware(ObservabilityMiddleware)

    @test_app.get("/ok")
    async def ok_route():
        return {"status": "ok"}

    @test_app.get("/not-found")
    async def not_found_route():
        raise HTTPException(status_code=404, detail="Not found")

    @test_app.get("/server-error")
    async def server_error_route():
        raise HTTPException(status_code=500, detail="Internal server error")

    @test_app.get("/unhandled-error")
    async def unhandled_error_route():
        msg = "something broke"
        raise RuntimeError(msg)

    @test_app.get("/slow")
    async def slow_route():
        return {"status": "slow"}

    return test_app


def _capture_events(capsys, client: TestClient, method: str, path: str, **kwargs) -> list[dict]:
    """Make a request and return all JSON events printed to stdout."""
    getattr(client, method)(path, **kwargs)
    captured = capsys.readouterr()
    events = []
    for line in captured.out.strip().split("\n"):
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                import warnings
                warnings.warn(f"Non-JSON output from middleware: {line!r}", stacklevel=2)
    return events


# =========================================================================
# Wide event required fields
# =========================================================================


class TestWideEventRequiredFields:
    """Verify the wide event contains all required fields."""

    def test_includes_all_required_fields(self, capsys) -> None:
        """A successful GET request produces an event with all required fields."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        events = _capture_events(capsys, client, "get", "/ok")

        assert len(events) >= 1
        event = events[-1]

        required_fields = [
            "timestamp",
            "request_id",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "outcome",
            "user.id",
        ]
        for field_name in required_fields:
            assert field_name in event, f"Missing required field: {field_name}"

    def test_timestamp_is_iso_format(self, capsys) -> None:
        """Timestamp should be in ISO 8601 format."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        events = _capture_events(capsys, client, "get", "/ok")

        event = events[-1]
        # Validate ISO 8601 format by parsing
        from datetime import datetime

        datetime.fromisoformat(event["timestamp"])

    def test_request_id_from_cf_ray_header(self, capsys) -> None:
        """request_id should come from cf-ray header when present."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        events = _capture_events(capsys, client, "get", "/ok", headers={"cf-ray": "abc123-IAD"})

        event = events[-1]
        assert event["request_id"] == "abc123-IAD"

    def test_request_id_is_uuid_when_no_cf_ray(self, capsys) -> None:
        """request_id should be a generated UUID when cf-ray header is missing."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        events = _capture_events(capsys, client, "get", "/ok")

        event = events[-1]
        # Should be a non-empty string (UUID format, typically 36 chars)
        assert isinstance(event["request_id"], str)
        assert len(event["request_id"]) >= 8

    def test_method_and_path(self, capsys) -> None:
        """method and path should reflect the actual request."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        events = _capture_events(capsys, client, "get", "/ok")

        event = events[-1]
        assert event["method"] == "GET"
        assert event["path"] == "/ok"

    def test_duration_ms_is_positive(self, capsys) -> None:
        """duration_ms should be a positive number."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        events = _capture_events(capsys, client, "get", "/ok")

        event = events[-1]
        assert event["duration_ms"] >= 0


# =========================================================================
# Outcome field
# =========================================================================


class TestOutcome:
    """Verify outcome field reflects request success/failure."""

    def test_success_outcome(self, capsys) -> None:
        """Successful request (200) has outcome='success'."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        events = _capture_events(capsys, client, "get", "/ok")

        event = events[-1]
        assert event["outcome"] == "success"
        assert event["status_code"] == 200

    def test_error_outcome_client_error(self, capsys) -> None:
        """Client error (404) has outcome='error'."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        events = _capture_events(capsys, client, "get", "/not-found")

        event = events[-1]
        assert event["outcome"] == "error"
        assert event["status_code"] == 404

    def test_error_outcome_server_error(self, capsys) -> None:
        """Server error (500) has outcome='error'."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        events = _capture_events(capsys, client, "get", "/server-error")

        event = events[-1]
        assert event["outcome"] == "error"
        assert event["status_code"] == 500

    def test_unhandled_exception_has_error_fields(self, capsys) -> None:
        """Unhandled exception populates error.type and error.message."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        events = _capture_events(capsys, client, "get", "/unhandled-error")

        event = events[-1]
        assert event["outcome"] == "error"
        assert event["status_code"] == 500
        assert event["error.type"] == "RuntimeError"
        assert event["error.message"] == "something broke"


# =========================================================================
# User ID extraction
# =========================================================================


class TestUserIdExtraction:
    """Verify user.id is included when the user is authenticated."""

    def test_user_id_null_when_unauthenticated(self, capsys) -> None:
        """user.id is None when there is no session cookie."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)
        events = _capture_events(capsys, client, "get", "/ok")

        event = events[-1]
        assert event["user.id"] is None

    async def test_user_id_present_when_authenticated(self, capsys) -> None:
        """user.id is populated from request.state when set by auth dependency."""
        env = MockEnv()
        user_data = {
            "user_id": "u42",
            "email": "test@example.com",
            "username": "tester",
            "avatar_url": "",
            "created_at": "2025-01-01T00:00:00",
        }
        session_id = await create_session(env.SESSIONS, user_data)

        # Build a custom app where the route sets request.state.user_id
        # (simulating what get_current_user does in real handlers).
        from src.wrappers import SafeEnv

        test_app = FastAPI()
        safe_env = SafeEnv(env)

        @test_app.middleware("http")
        async def inject_env(request: Request, call_next):
            request.scope["env"] = safe_env
            return await call_next(request)

        test_app.add_middleware(ObservabilityMiddleware)

        @test_app.get("/authenticated")
        async def authenticated_route(request: Request):
            from src.auth.session import get_session as _get_session

            sid = request.cookies.get(COOKIE_NAME)
            data = await _get_session(env.SESSIONS, sid)
            if data:
                request.state.user_id = data.get("user_id")
            return {"status": "ok"}

        client = TestClient(test_app, raise_server_exceptions=False)
        client.cookies.set(COOKIE_NAME, session_id)
        events = _capture_events(capsys, client, "get", "/authenticated")

        event = events[-1]
        assert event["user.id"] == "u42"

    def test_user_id_null_with_invalid_session(self, capsys) -> None:
        """user.id is None when the session cookie is invalid."""
        env = MockEnv()
        app = _make_app(env=env)
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set(COOKIE_NAME, "bogus_session_id")
        events = _capture_events(capsys, client, "get", "/ok")

        event = events[-1]
        assert event["user.id"] is None


# =========================================================================
# Integration: middleware emits events
# =========================================================================


class TestMiddlewareEmission:
    """Verify the middleware actually prints JSON to stdout."""

    def test_event_emitted_for_request(self, capsys) -> None:
        """Every request emits exactly one event."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)

        client.get("/ok")

        captured = capsys.readouterr()
        json_lines = [
            line
            for line in captured.out.strip().split("\n")
            if line.strip() and _is_json(line.strip())
        ]
        assert len(json_lines) == 1

        event = json.loads(json_lines[0])
        assert event["path"] == "/ok"
        assert event["method"] == "GET"


def _is_json(s: str) -> bool:
    """Check if a string is valid JSON."""
    try:
        json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return False
    return True

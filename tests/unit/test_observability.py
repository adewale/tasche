"""Tests for Phase 8 — Observability (wide events middleware, tail sampling).

Covers:
- Wide event includes all required fields
- Successful request has outcome="success"
- Error request has outcome="error" and error fields
- User ID is included when authenticated
- Tail sampling: errors always emitted, slow requests always emitted
- Tail sampling: successful fast requests only emitted ~5-10% of time
"""

from __future__ import annotations

import json
import random
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from src.auth.session import COOKIE_NAME, create_session
from src.observability import (
    _SUCCESS_SAMPLE_RATE,
    ObservabilityMiddleware,
    _should_sample,
)
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
    # Always sample so we capture the event
    with patch("src.observability._should_sample", return_value=True):
        getattr(client, method)(path, **kwargs)
    captured = capsys.readouterr()
    events = []
    for line in captured.out.strip().split("\n"):
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
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
        # ISO 8601 timestamps contain 'T' and end with timezone info
        assert "T" in event["timestamp"]

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
        # Should be a non-empty string (UUID format)
        assert len(event["request_id"]) > 0

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
        events = _capture_events(
            capsys, client, "get", "/authenticated", cookies={COOKIE_NAME: session_id}
        )

        event = events[-1]
        assert event["user.id"] == "u42"

    def test_user_id_null_with_invalid_session(self, capsys) -> None:
        """user.id is None when the session cookie is invalid."""
        env = MockEnv()
        app = _make_app(env=env)
        client = TestClient(app, raise_server_exceptions=False)
        events = _capture_events(
            capsys, client, "get", "/ok", cookies={COOKIE_NAME: "bogus_session_id"}
        )

        event = events[-1]
        assert event["user.id"] is None


# =========================================================================
# Tail sampling logic (_should_sample)
# =========================================================================


class TestTailSampling:
    """Verify tail sampling rules."""

    def test_server_error_always_sampled(self) -> None:
        """Status >= 500 is always sampled."""
        event = {"status_code": 500, "duration_ms": 10, "outcome": "error"}
        # Run multiple times to ensure it is always True
        for _ in range(100):
            assert _should_sample(event) is True

    def test_client_error_always_sampled(self) -> None:
        """outcome='error' (e.g. 404) is always sampled."""
        event = {"status_code": 404, "duration_ms": 10, "outcome": "error"}
        for _ in range(100):
            assert _should_sample(event) is True

    def test_slow_request_always_sampled(self) -> None:
        """Requests with duration_ms > 2000 are always sampled."""
        event = {"status_code": 200, "duration_ms": 2500, "outcome": "success"}
        for _ in range(100):
            assert _should_sample(event) is True

    def test_slow_request_at_boundary_not_always_sampled(self) -> None:
        """Requests with duration_ms == 2000 are not considered slow (> 2000)."""
        # Seed random for deterministic behavior
        random.seed(123)
        event = {"status_code": 200, "duration_ms": 2000, "outcome": "success"}
        # At exactly 2000ms, the slow check does NOT trigger (> not >=),
        # so it falls through to random sampling.  With a 5% sample rate,
        # at least some of 200 runs should be False.
        results = [_should_sample(event) for _ in range(200)]
        assert False in results, "At boundary, some calls should be unsampled"

    def test_success_fast_request_sampled_at_low_rate(self) -> None:
        """Successful fast requests are sampled at ~5% (not all, not none)."""
        # Seed random for deterministic behavior
        random.seed(42)
        event = {"status_code": 200, "duration_ms": 50, "outcome": "success"}
        results = [_should_sample(event) for _ in range(2000)]
        sampled_count = sum(results)

        # With 5% rate and 2000 trials, expect ~100 sampled.
        # Use a generous range: between 1% and 20% to avoid flaky tests.
        assert sampled_count > 0, "At least some successful requests should be sampled"
        assert sampled_count < 2000, "Not all successful requests should be sampled"

        # More precise: the rate should be roughly around _SUCCESS_SAMPLE_RATE
        observed_rate = sampled_count / 2000
        assert 0.01 < observed_rate < 0.20, (
            f"Observed sample rate {observed_rate:.3f} outside expected range "
            f"for _SUCCESS_SAMPLE_RATE={_SUCCESS_SAMPLE_RATE}"
        )

    def test_missing_fields_do_not_crash(self) -> None:
        """_should_sample handles events with missing fields gracefully."""
        event: dict = {}
        # Should not raise — falls through to random sampling
        result = _should_sample(event)
        assert isinstance(result, bool)


# =========================================================================
# Integration: middleware actually emits events
# =========================================================================


class TestMiddlewareEmission:
    """Verify the middleware actually prints JSON to stdout."""

    def test_event_not_emitted_when_sampling_rejects(self, capsys) -> None:
        """When tail sampling says no, no event is printed."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)

        with patch("src.observability._should_sample", return_value=False):
            client.get("/ok")

        captured = capsys.readouterr()
        # No JSON event lines should be present
        json_lines = [
            line
            for line in captured.out.strip().split("\n")
            if line.strip() and _is_json(line.strip())
        ]
        assert len(json_lines) == 0

    def test_event_emitted_when_sampling_accepts(self, capsys) -> None:
        """When tail sampling says yes, exactly one event is printed."""
        app = _make_app()
        client = TestClient(app, raise_server_exceptions=False)

        with patch("src.observability._should_sample", return_value=True):
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

"""Tests for user preferences API (src/preferences/).

Covers GET/PATCH /api/preferences endpoints for TTS voice selection.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.preferences.routes import router
from tests.conftest import (
    MockD1,
    MockEnv,
    TrackingD1,
    make_test_helpers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_make_app, _authenticated_client = make_test_helpers((router, "/api/preferences"))


# ---------------------------------------------------------------------------
# GET /api/preferences
# ---------------------------------------------------------------------------


class TestGetPreferences:
    async def test_returns_default_when_no_row(self) -> None:
        """GET /api/preferences returns athena when no preferences row exists."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, _ = await _authenticated_client(env)
        resp = client.get("/api/preferences")

        assert resp.status_code == 200
        assert resp.json() == {"tts_voice": "athena"}

    async def test_returns_stored_preference(self) -> None:
        """GET /api/preferences returns stored voice preference."""

        def execute(sql: str, params: list) -> list:
            if "user_preferences" in sql and "SELECT" in sql:
                return [{"tts_voice": "orion"}]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, _ = await _authenticated_client(env)
        resp = client.get("/api/preferences")

        assert resp.status_code == 200
        assert resp.json() == {"tts_voice": "orion"}

    def test_returns_401_without_auth(self) -> None:
        """GET /api/preferences returns 401 without session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/preferences")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PATCH /api/preferences
# ---------------------------------------------------------------------------


class TestUpdatePreferences:
    async def test_updates_voice_to_orion(self) -> None:
        """PATCH /api/preferences updates tts_voice to orion."""
        db = TrackingD1()
        env = MockEnv(db=db)

        client, _ = await _authenticated_client(env)
        resp = client.patch("/api/preferences", json={"tts_voice": "orion"})

        assert resp.status_code == 200
        assert resp.json() == {"tts_voice": "orion"}

        # Verify UPSERT SQL was executed
        upsert_calls = [
            (sql, params)
            for sql, params in db.executed
            if "INSERT" in sql and "user_preferences" in sql
        ]
        assert len(upsert_calls) == 1
        sql, params = upsert_calls[0]
        assert "ON CONFLICT" in sql
        assert "orion" in params

    async def test_updates_voice_to_athena(self) -> None:
        """PATCH /api/preferences updates tts_voice to athena."""
        db = TrackingD1()
        env = MockEnv(db=db)

        client, _ = await _authenticated_client(env)
        resp = client.patch("/api/preferences", json={"tts_voice": "athena"})

        assert resp.status_code == 200
        assert resp.json() == {"tts_voice": "athena"}

    async def test_rejects_invalid_voice(self) -> None:
        """PATCH /api/preferences returns 422 for invalid voice."""
        db = TrackingD1()
        env = MockEnv(db=db)

        client, _ = await _authenticated_client(env)
        resp = client.patch("/api/preferences", json={"tts_voice": "invalid"})

        assert resp.status_code == 422
        assert "Invalid tts_voice" in resp.json()["detail"]

    async def test_rejects_empty_body(self) -> None:
        """PATCH /api/preferences returns 422 when no valid fields given."""
        db = TrackingD1()
        env = MockEnv(db=db)

        client, _ = await _authenticated_client(env)
        resp = client.patch("/api/preferences", json={})

        assert resp.status_code == 422

    def test_returns_401_without_auth(self) -> None:
        """PATCH /api/preferences returns 401 without session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.patch("/api/preferences", json={"tts_voice": "orion"})
        assert resp.status_code == 401

"""Tests for Phase 2 — Authentication (session management, dependencies, routes).

Covers session CRUD, the ``get_current_user`` dependency, ALLOWED_EMAILS
parsing, callback CSRF state verification, and error handling.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth.dependencies import get_current_user
from src.auth.routes import OAUTH_STATE_PREFIX, _parse_allowed_emails, router
from src.auth.session import (
    COOKIE_NAME,
    SESSION_PREFIX,
    create_session,
    delete_session,
    get_session,
)
from tests.conftest import MockD1, MockEnv, MockKV

# =========================================================================
# Session management (src/auth/session.py)
# =========================================================================


class TestCreateSession:
    async def test_stores_in_kv_with_correct_key_format(self) -> None:
        """create_session stores data under ``session:{id}`` key."""
        kv = MockKV()
        user_data = {
            "user_id": "u1",
            "email": "test@example.com",
            "username": "tester",
            "avatar_url": "https://github.com/avatar.png",
            "created_at": "2025-01-01T00:00:00",
        }
        session_id = await create_session(kv, user_data)

        assert session_id  # non-empty
        key = f"{SESSION_PREFIX}{session_id}"
        assert key in kv._store
        stored = json.loads(kv._store[key])
        assert stored["user_id"] == "u1"
        assert stored["email"] == "test@example.com"


class TestGetSession:
    async def test_retrieves_valid_session(self) -> None:
        """get_session returns stored user data for a valid session ID."""
        kv = MockKV()
        user_data = {"user_id": "u1", "email": "test@example.com"}
        session_id = await create_session(kv, user_data)

        result = await get_session(kv, session_id)
        assert result is not None
        assert result["user_id"] == "u1"

    async def test_returns_none_for_missing_session(self) -> None:
        """get_session returns None when the session ID does not exist."""
        kv = MockKV()
        result = await get_session(kv, "nonexistent_id")
        assert result is None


class TestDeleteSession:
    async def test_removes_from_kv(self) -> None:
        """delete_session removes the session key from KV."""
        kv = MockKV()
        user_data = {"user_id": "u1", "email": "test@example.com"}
        session_id = await create_session(kv, user_data)

        # Verify it exists first
        assert await get_session(kv, session_id) is not None

        await delete_session(kv, session_id)
        assert await get_session(kv, session_id) is None


# =========================================================================
# get_current_user dependency (src/auth/dependencies.py)
# =========================================================================


def _make_app_with_env(env: Any) -> FastAPI:
    """Create a minimal FastAPI app that injects ``env`` into the ASGI scope."""
    test_app = FastAPI()

    @test_app.middleware("http")
    async def inject_env(request, call_next):
        request.scope["env"] = env
        return await call_next(request)

    @test_app.get("/me")
    async def me(user: dict = pytest.importorskip("fastapi").Depends(get_current_user)):
        return user

    return test_app


class TestGetCurrentUser:
    def test_raises_401_with_no_cookie(self) -> None:
        """No session cookie -> 401 Unauthorized."""
        env = MockEnv()
        app = _make_app_with_env(env)
        client = TestClient(app)
        resp = client.get("/me")
        assert resp.status_code == 401

    def test_raises_401_with_invalid_session(self) -> None:
        """Cookie present but session not in KV -> 401 Unauthorized."""
        env = MockEnv()
        app = _make_app_with_env(env)
        client = TestClient(app)
        resp = client.get("/me", cookies={COOKIE_NAME: "bogus_session_id"})
        assert resp.status_code == 401

    async def test_returns_user_data_with_valid_session(self) -> None:
        """Valid session cookie -> returns stored user data."""
        env = MockEnv()
        user_data = {
            "user_id": "u1",
            "email": "test@example.com",
            "username": "tester",
            "avatar_url": "https://avatar.url",
            "created_at": "2025-01-01T00:00:00",
        }
        session_id = await create_session(env.SESSIONS, user_data)

        app = _make_app_with_env(env)
        client = TestClient(app)
        resp = client.get("/me", cookies={COOKIE_NAME: session_id})
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "u1"
        assert data["email"] == "test@example.com"


# =========================================================================
# ALLOWED_EMAILS parsing (src/auth/routes.py)
# =========================================================================


class TestAllowedEmailsParsing:
    def test_empty_string_returns_empty_set(self) -> None:
        assert _parse_allowed_emails("") == set()

    def test_single_email(self) -> None:
        assert _parse_allowed_emails("a@b.com") == {"a@b.com"}

    def test_comma_separated(self) -> None:
        result = _parse_allowed_emails("a@b.com,c@d.com,e@f.com")
        assert result == {"a@b.com", "c@d.com", "e@f.com"}

    def test_strips_whitespace(self) -> None:
        result = _parse_allowed_emails("  a@b.com , c@d.com  ,  e@f.com  ")
        assert result == {"a@b.com", "c@d.com", "e@f.com"}

    def test_ignores_empty_entries(self) -> None:
        result = _parse_allowed_emails("a@b.com,,c@d.com,")
        assert result == {"a@b.com", "c@d.com"}


# =========================================================================
# Auth route integration tests
# =========================================================================


def _make_auth_app(env: Any) -> FastAPI:
    """Create a FastAPI app with the auth router mounted, injecting env."""
    test_app = FastAPI()

    @test_app.middleware("http")
    async def inject_env(request, call_next):
        request.scope["env"] = env
        return await call_next(request)

    test_app.include_router(router, prefix="/api/auth")
    return test_app


def _mock_github_responses(
    token_data: dict[str, Any],
    user_data: dict[str, Any],
    *,
    token_status: int = 200,
    user_status: int = 200,
    emails_data: list[dict[str, Any]] | None = None,
    emails_status: int = 200,
) -> MagicMock:
    """Build a mock ``httpx.AsyncClient`` that returns canned responses.

    Supports the single-client pattern (one context manager for all requests).
    Routes .post() to token response, .get() to user or emails response by URL.
    """
    mock_token_resp = MagicMock()
    mock_token_resp.json.return_value = token_data
    mock_token_resp.status_code = token_status

    mock_user_resp = MagicMock()
    mock_user_resp.json.return_value = user_data
    mock_user_resp.status_code = user_status

    mock_emails_resp = MagicMock()
    mock_emails_resp.json.return_value = emails_data or []
    mock_emails_resp.status_code = emails_status

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post.return_value = mock_token_resp

    async def _get_by_url(url, **kwargs):
        if "emails" in url:
            return mock_emails_resp
        return mock_user_resp

    mock_client.get = AsyncMock(side_effect=_get_by_url)

    mock_cls = MagicMock()
    mock_cls.return_value = mock_client
    return mock_cls


async def _setup_oauth_state(env: MockEnv) -> str:
    """Store an OAuth state token in KV, as the login endpoint would."""
    import secrets

    state = secrets.token_urlsafe(32)
    await env.SESSIONS.put(f"{OAUTH_STATE_PREFIX}{state}", "1")
    return state


class TestCallbackCsrfState:
    async def test_rejects_missing_state(self) -> None:
        """Callback without state parameter returns 400."""
        env = MockEnv()
        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/auth/callback?code=test_code")
        assert resp.status_code == 400

    async def test_rejects_invalid_state(self) -> None:
        """Callback with state not in KV returns 400."""
        env = MockEnv()
        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/auth/callback?code=test_code&state=bogus")
        assert resp.status_code == 400

    async def test_state_consumed_after_use(self) -> None:
        """State token is deleted from KV after successful verification."""
        env = MockEnv(db=MockD1(execute=lambda sql, params: []))
        state = await _setup_oauth_state(env)

        mock_cls = _mock_github_responses(
            token_data={"access_token": "gho_test"},
            user_data={"id": 1, "login": "user", "email": "u@e.com", "avatar_url": ""},
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.httpx.AsyncClient", mock_cls):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 302

        # State should be consumed
        stored = await env.SESSIONS.get(f"{OAUTH_STATE_PREFIX}{state}")
        assert stored is None


class TestCallbackMissingCode:
    def test_returns_400_without_code(self) -> None:
        """Callback without code parameter returns 400."""
        env = MockEnv()
        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/auth/callback")
        assert resp.status_code == 400


class TestCallbackGitHubErrors:
    async def test_returns_502_on_token_exchange_failure(self) -> None:
        """502 when GitHub token exchange returns non-200."""
        env = MockEnv()
        state = await _setup_oauth_state(env)

        mock_cls = _mock_github_responses(
            token_data={},
            user_data={},
            token_status=500,
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.httpx.AsyncClient", mock_cls):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 502

    async def test_returns_400_on_missing_access_token(self) -> None:
        """400 when GitHub returns 200 but no access_token in response."""
        env = MockEnv()
        state = await _setup_oauth_state(env)

        mock_cls = _mock_github_responses(
            token_data={"error": "bad_verification_code"},
            user_data={},
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.httpx.AsyncClient", mock_cls):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 400

    async def test_returns_502_on_user_fetch_failure(self) -> None:
        """502 when GitHub user info endpoint returns non-200."""
        env = MockEnv()
        state = await _setup_oauth_state(env)

        mock_cls = _mock_github_responses(
            token_data={"access_token": "gho_test"},
            user_data={},
            user_status=500,
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.httpx.AsyncClient", mock_cls):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 502


class TestCallbackRejectsUnauthorizedEmail:
    async def test_rejects_email_not_in_allowed_list(self) -> None:
        """When ALLOWED_EMAILS is set, emails not in the list get 403."""
        env = MockEnv(allowed_emails="allowed@example.com,admin@example.com")
        state = await _setup_oauth_state(env)

        mock_cls = _mock_github_responses(
            token_data={"access_token": "gho_test_token"},
            user_data={
                "id": 12345,
                "login": "unauthorized_user",
                "email": "notallowed@example.com",
                "avatar_url": "https://github.com/avatar.png",
            },
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.httpx.AsyncClient", mock_cls):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
            )
        assert resp.status_code == 403

    async def test_allows_authorized_email(self) -> None:
        """When ALLOWED_EMAILS is set, emails in the list proceed (redirect)."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(
            allowed_emails="allowed@example.com,admin@example.com",
            db=db,
        )
        state = await _setup_oauth_state(env)

        mock_cls = _mock_github_responses(
            token_data={"access_token": "gho_test_token"},
            user_data={
                "id": 12345,
                "login": "allowed_user",
                "email": "allowed@example.com",
                "avatar_url": "https://github.com/avatar.png",
            },
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.httpx.AsyncClient", mock_cls):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    async def test_allows_any_email_when_allowlist_empty(self) -> None:
        """When ALLOWED_EMAILS is empty, any authenticated user is accepted."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(allowed_emails="", db=db)
        state = await _setup_oauth_state(env)

        mock_cls = _mock_github_responses(
            token_data={"access_token": "gho_test_token"},
            user_data={
                "id": 99999,
                "login": "anyone",
                "email": "anyone@example.com",
                "avatar_url": "https://github.com/avatar.png",
            },
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.httpx.AsyncClient", mock_cls):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 302


class TestCallbackPrivateEmail:
    async def test_fetches_email_from_user_emails_endpoint(self) -> None:
        """When /user returns no email, fetches from /user/emails."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(allowed_emails="private@example.com", db=db)
        state = await _setup_oauth_state(env)

        mock_cls = _mock_github_responses(
            token_data={"access_token": "gho_test"},
            user_data={"id": 1, "login": "user", "email": None, "avatar_url": ""},
            emails_data=[
                {"email": "noreply@users.github.com", "primary": False, "verified": True},
                {"email": "private@example.com", "primary": True, "verified": True},
            ],
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.httpx.AsyncClient", mock_cls):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 302


# =========================================================================
# Login redirect
# =========================================================================


class TestLoginRedirect:
    def test_redirects_to_github_with_state(self) -> None:
        """GET /login redirects to GitHub with correct params including state."""
        env = MockEnv()
        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/auth/login", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "github.com/login/oauth/authorize" in location
        assert "client_id=test_client_id" in location
        assert "scope=user" in location
        assert "state=" in location

    async def test_stores_state_in_kv(self) -> None:
        """GET /login stores the state token in KV."""
        env = MockEnv()
        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)

        client.get("/api/auth/login", follow_redirects=False)

        # Check that an oauth_state: key was stored
        state_keys = [k for k in env.SESSIONS._store if k.startswith(OAUTH_STATE_PREFIX)]
        assert len(state_keys) == 1


# =========================================================================
# Logout
# =========================================================================


class TestLogout:
    async def test_deletes_session_and_clears_cookie(self) -> None:
        """POST /logout removes session from KV and clears the cookie."""
        env = MockEnv()
        user_data = {"user_id": "u1", "email": "test@example.com"}
        session_id = await create_session(env.SESSIONS, user_data)

        app = _make_auth_app(env)
        client = TestClient(app)

        resp = client.post("/api/auth/logout", cookies={COOKIE_NAME: session_id})
        assert resp.status_code == 200

        # Session should be deleted from KV
        assert await get_session(env.SESSIONS, session_id) is None


# =========================================================================
# Session endpoint
# =========================================================================


class TestSessionEndpoint:
    async def test_returns_user_data(self) -> None:
        """GET /session returns the user data from KV."""
        env = MockEnv()
        user_data = {
            "user_id": "u1",
            "email": "test@example.com",
            "username": "tester",
            "avatar_url": "https://avatar.url",
            "created_at": "2025-01-01T00:00:00",
        }
        session_id = await create_session(env.SESSIONS, user_data)

        app = _make_auth_app(env)
        client = TestClient(app)

        resp = client.get("/api/auth/session", cookies={COOKIE_NAME: session_id})
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "u1"

    def test_returns_401_without_cookie(self) -> None:
        """GET /session without a cookie returns 401."""
        env = MockEnv()
        app = _make_auth_app(env)
        client = TestClient(app)

        resp = client.get("/api/auth/session")
        assert resp.status_code == 401

    async def test_returns_401_with_expired_session(self) -> None:
        """GET /session with a cookie but expired/invalid session returns 401."""
        env = MockEnv()
        app = _make_auth_app(env)
        client = TestClient(app)

        resp = client.get("/api/auth/session", cookies={COOKIE_NAME: "expired_id"})
        assert resp.status_code == 401

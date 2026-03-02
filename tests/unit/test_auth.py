"""Tests for Phase 2 — Authentication (session management, dependencies, routes).

Covers session CRUD, the ``get_current_user`` dependency, ALLOWED_EMAILS
parsing, callback CSRF state verification, and error handling.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth.dependencies import get_current_user
from src.auth.routes import OAUTH_STATE_PREFIX, _get_site_url, router
from src.auth.session import (
    _REFRESH_INTERVAL,
    COOKIE_NAME,
    SESSION_PREFIX,
    create_session,
    delete_session,
    get_session,
    parse_allowed_emails,
    refresh_session,
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
        # Verify through the public get_session API, not mock internals
        result = await get_session(kv, session_id)
        assert result is not None
        assert result["user_id"] == "u1"
        assert result["email"] == "test@example.com"


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


class TestRefreshSession:
    async def test_refreshes_when_no_refreshed_at(self) -> None:
        """refresh_session writes to KV when user_data has no refreshed_at."""
        kv = MockKV()
        user_data = {"user_id": "u1", "email": "test@example.com"}
        session_id = "test_session_123"

        # Pre-populate KV so we can verify it gets updated
        key = f"{SESSION_PREFIX}{session_id}"
        kv._store[key] = json.dumps(user_data)

        await refresh_session(kv, session_id, user_data)

        # Should have written refreshed_at to KV
        stored = json.loads(kv._store[key])
        assert "refreshed_at" in stored
        assert stored["refreshed_at"] > 0

    async def test_refreshes_when_interval_exceeded(self) -> None:
        """refresh_session writes to KV when last refresh was over 1 hour ago."""
        import time

        kv = MockKV()
        old_time = time.time() - _REFRESH_INTERVAL - 100  # well past the threshold
        user_data = {
            "user_id": "u1",
            "email": "test@example.com",
            "refreshed_at": old_time,
        }
        session_id = "test_session_456"
        key = f"{SESSION_PREFIX}{session_id}"
        kv._store[key] = json.dumps(user_data)

        await refresh_session(kv, session_id, user_data)

        stored = json.loads(kv._store[key])
        assert stored["refreshed_at"] > old_time

    async def test_skips_when_recently_refreshed(self) -> None:
        """refresh_session does NOT write to KV when refreshed less than 1 hour ago."""
        import time

        kv = MockKV()
        recent_time = time.time() - 60  # only 60 seconds ago
        user_data = {
            "user_id": "u1",
            "email": "test@example.com",
            "refreshed_at": recent_time,
        }
        session_id = "test_session_789"
        key = f"{SESSION_PREFIX}{session_id}"
        original_json = json.dumps(user_data)
        kv._store[key] = original_json

        await refresh_session(kv, session_id, user_data)

        # KV should NOT have been updated
        assert kv._store[key] == original_json

    async def test_updates_user_data_in_place(self) -> None:
        """refresh_session mutates user_data dict with new refreshed_at."""
        import time

        kv = MockKV()
        user_data = {"user_id": "u1", "email": "test@example.com"}
        session_id = "test_session_mut"
        key = f"{SESSION_PREFIX}{session_id}"
        kv._store[key] = json.dumps(user_data)

        before = time.time()
        await refresh_session(kv, session_id, user_data)
        after = time.time()

        # user_data dict should have been mutated
        assert "refreshed_at" in user_data
        assert before <= user_data["refreshed_at"] <= after


# =========================================================================
# get_current_user dependency (src/auth/dependencies.py)
# =========================================================================


def _make_app_with_env(env: Any) -> FastAPI:
    """Create a minimal FastAPI app that injects ``env`` into the ASGI scope."""
    from src.wrappers import SafeEnv

    test_app = FastAPI()
    safe_env = SafeEnv(env)

    @test_app.middleware("http")
    async def inject_env(request, call_next):
        request.scope["env"] = safe_env
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
        client.cookies.set(COOKIE_NAME, "bogus_session_id")
        resp = client.get("/me")
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
        client.cookies.set(COOKIE_NAME, session_id)
        resp = client.get("/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "u1"
        assert data["email"] == "test@example.com"


# =========================================================================
# DISABLE_AUTH (dev mode)
# =========================================================================


class TestDisableAuth:
    def setup_method(self) -> None:
        """Reset the module-level dev user cache before each test."""
        import src.auth.dependencies as deps

        deps._dev_user = None

    def test_returns_dev_user_without_cookie(self) -> None:
        """DISABLE_AUTH=true returns a dev user with no session cookie."""
        env = MockEnv(disable_auth="true", site_url="http://localhost:8787")
        app = _make_app_with_env(env)
        client = TestClient(app)
        resp = client.get("/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == "dev"
        assert data["email"] == "dev@localhost"
        assert data["username"] == "dev"

    def test_does_not_bypass_when_flag_is_absent(self) -> None:
        """Without DISABLE_AUTH, normal auth is enforced."""
        env = MockEnv()
        app = _make_app_with_env(env)
        client = TestClient(app)
        resp = client.get("/me")
        assert resp.status_code == 401

    def test_does_not_bypass_when_flag_is_false(self) -> None:
        """DISABLE_AUTH=false does not bypass auth."""
        env = MockEnv(disable_auth="false")
        app = _make_app_with_env(env)
        client = TestClient(app)
        resp = client.get("/me")
        assert resp.status_code == 401

    def test_ignores_allowed_emails(self) -> None:
        """DISABLE_AUTH=true skips ALLOWED_EMAILS check."""
        env = MockEnv(disable_auth="true", allowed_emails="", site_url="http://localhost:8787")
        app = _make_app_with_env(env)
        client = TestClient(app)
        resp = client.get("/me")
        assert resp.status_code == 200

    def test_dev_user_is_cached(self) -> None:
        """Second request uses cached dev user (no extra D1 insert)."""
        import src.auth.dependencies as deps

        db = MockD1()
        env = MockEnv(disable_auth="true", db=db, site_url="http://localhost:8787")
        app = _make_app_with_env(env)
        client = TestClient(app)

        resp1 = client.get("/me")
        assert resp1.status_code == 200

        # Cache should now be populated
        assert deps._dev_user is not None
        assert deps._dev_user["user_id"] == "dev"

        resp2 = client.get("/me")
        assert resp2.status_code == 200
        assert resp2.json()["user_id"] == "dev"


# =========================================================================
# ALLOWED_EMAILS parsing (src/auth/routes.py)
# =========================================================================


class TestSessionRevocation:
    async def test_revokes_session_when_email_no_longer_allowed(self) -> None:
        """Access is denied if user email is removed from ALLOWED_EMAILS."""
        env = MockEnv(allowed_emails="other@example.com")
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
        client.cookies.set(COOKIE_NAME, session_id)
        resp = client.get("/me")
        assert resp.status_code == 401
        assert "revoked" in resp.json()["detail"].lower()

        # Session should be deleted from KV
        assert await get_session(env.SESSIONS, session_id) is None

    async def test_allows_session_when_email_still_allowed(self) -> None:
        """Access is allowed when user email is in ALLOWED_EMAILS."""
        env = MockEnv(allowed_emails="test@example.com")
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
        client.cookies.set(COOKIE_NAME, session_id)
        resp = client.get("/me")
        assert resp.status_code == 200

    async def test_rejects_session_when_allowlist_empty(self) -> None:
        """When ALLOWED_EMAILS is empty, access is denied (whitelist is required)."""
        env = MockEnv(allowed_emails="")
        user_data = {
            "user_id": "u1",
            "email": "anyone@example.com",
            "username": "anyone",
            "avatar_url": "",
            "created_at": "2025-01-01T00:00:00",
        }
        session_id = await create_session(env.SESSIONS, user_data)

        app = _make_app_with_env(env)
        client = TestClient(app)
        client.cookies.set(COOKIE_NAME, session_id)
        resp = client.get("/me")
        assert resp.status_code == 401


class TestSessionRevocationOnAllowlistChange:
    async def test_access_revoked_when_allowlist_changes(self) -> None:
        """Changing ALLOWED_EMAILS to exclude a user returns 401."""
        # Start with the user's email in the allowed list
        env = MockEnv(allowed_emails="test@example.com")
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
        client.cookies.set(COOKIE_NAME, session_id)

        # First request should succeed
        resp = client.get("/me")
        assert resp.status_code == 200

        # Change ALLOWED_EMAILS to exclude the user
        env.ALLOWED_EMAILS = "other@example.com"

        # Next request should return 401
        resp = client.get("/me")
        assert resp.status_code == 401

        # Session should be deleted from KV
        assert await get_session(env.SESSIONS, session_id) is None


class TestAllowedEmailsParsing:
    def test_empty_string_returns_empty_set(self) -> None:
        assert parse_allowed_emails("") == set()

    def test_single_email(self) -> None:
        assert parse_allowed_emails("a@b.com") == {"a@b.com"}

    def test_comma_separated(self) -> None:
        result = parse_allowed_emails("a@b.com,c@d.com,e@f.com")
        assert result == {"a@b.com", "c@d.com", "e@f.com"}

    def test_strips_whitespace(self) -> None:
        result = parse_allowed_emails("  a@b.com , c@d.com  ,  e@f.com  ")
        assert result == {"a@b.com", "c@d.com", "e@f.com"}

    def test_ignores_empty_entries(self) -> None:
        result = parse_allowed_emails("a@b.com,,c@d.com,")
        assert result == {"a@b.com", "c@d.com"}


# =========================================================================
# _get_site_url helper (src/auth/routes.py)
# =========================================================================


class _FakeURL:
    """Minimal stand-in for Starlette's URL with a scheme attribute."""

    def __init__(self, scheme: str = "https") -> None:
        self.scheme = scheme


class _FakeRequest:
    """Minimal request-like object for testing _get_site_url."""

    def __init__(self, headers: dict[str, str] | None = None, scheme: str = "https") -> None:
        self.headers = headers or {}
        self.url = _FakeURL(scheme)


class TestGetSiteUrl:
    def test_site_url_returns_configured_value(self) -> None:
        """When SITE_URL is set to a real URL, returns it unchanged."""
        env = MockEnv(site_url="https://tasche.example.com")
        from src.wrappers import SafeEnv

        safe_env = SafeEnv(env)
        request = _FakeRequest(headers={"host": "other.example.com"})
        result = _get_site_url(safe_env, request)
        assert result == "https://tasche.example.com"

    def test_site_url_auto_detects_from_host_header(self) -> None:
        """When SITE_URL is empty, returns https://{host} from request."""
        env = MockEnv(site_url="")
        from src.wrappers import SafeEnv

        safe_env = SafeEnv(env)
        request = _FakeRequest(
            headers={"host": "my-app.workers.dev", "x-forwarded-proto": "https"},
        )
        result = _get_site_url(safe_env, request)
        assert result == "https://my-app.workers.dev"

    def test_site_url_auto_detects_when_placeholder(self) -> None:
        """When SITE_URL contains <your-subdomain>, auto-detects from host."""
        env = MockEnv(site_url="https://<your-subdomain>.workers.dev")
        from src.wrappers import SafeEnv

        safe_env = SafeEnv(env)
        request = _FakeRequest(
            headers={"host": "tasche.adewale-883.workers.dev", "x-forwarded-proto": "https"},
        )
        result = _get_site_url(safe_env, request)
        assert result == "https://tasche.adewale-883.workers.dev"

    def test_site_url_strips_trailing_slash(self) -> None:
        """Ensures no trailing slash on configured or auto-detected URLs."""
        env = MockEnv(site_url="https://tasche.example.com/")
        from src.wrappers import SafeEnv

        safe_env = SafeEnv(env)
        request = _FakeRequest(headers={"host": "other.example.com"})
        result = _get_site_url(safe_env, request)
        assert result == "https://tasche.example.com"
        assert not result.endswith("/")

    def test_site_url_uses_http_when_no_https_indicators(self) -> None:
        """When neither x-forwarded-proto nor scheme is https, uses http."""
        env = MockEnv(site_url="")
        from src.wrappers import SafeEnv

        safe_env = SafeEnv(env)
        request = _FakeRequest(
            headers={"host": "localhost:8787"},
            scheme="http",
        )
        result = _get_site_url(safe_env, request)
        assert result == "http://localhost:8787"


# =========================================================================
# Auth route integration tests
# =========================================================================


def _make_auth_app(env: Any) -> FastAPI:
    """Create a FastAPI app with the auth router mounted, injecting env."""
    from src.wrappers import SafeEnv

    safe_env = SafeEnv(env)
    test_app = FastAPI()

    @test_app.middleware("http")
    async def inject_env(request, call_next):
        request.scope["env"] = safe_env
        return await call_next(request)

    test_app.include_router(router, prefix="/api/auth")
    return test_app


def _mock_http_fetch(
    token_data: dict[str, Any],
    user_data: dict[str, Any],
    *,
    token_status: int = 200,
    user_status: int = 200,
    emails_data: list[dict[str, Any]] | None = None,
    emails_status: int = 200,
) -> AsyncMock:
    """Build a mock ``http_fetch`` that returns canned ``HttpResponse`` objects.

    Routes by URL: token endpoint -> token response, /user/emails -> emails
    response, /user -> user response.
    """
    from src.wrappers import HttpResponse

    token_resp = HttpResponse(status_code=token_status, _body=json.dumps(token_data).encode())
    user_resp = HttpResponse(status_code=user_status, _body=json.dumps(user_data).encode())
    emails_resp = HttpResponse(
        status_code=emails_status, _body=json.dumps(emails_data or []).encode()
    )

    async def _fetch(url, *, method="GET", headers=None, body=None, form_data=None, timeout=10.0):
        if "access_token" in url:
            return token_resp
        if "emails" in url:
            return emails_resp
        if "api.github.com/user" in url:
            return user_resp
        return HttpResponse(status_code=404, _body='{"error": "not found"}')

    return AsyncMock(side_effect=_fetch)


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

        mock_fetch = _mock_http_fetch(
            token_data={"access_token": "gho_test"},
            user_data={"id": 1, "login": "user", "email": "test@example.com", "avatar_url": ""},
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.http_fetch", mock_fetch):
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

        mock_fetch = _mock_http_fetch(
            token_data={},
            user_data={},
            token_status=500,
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.http_fetch", mock_fetch):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 502

    async def test_returns_400_on_missing_access_token(self) -> None:
        """400 when GitHub returns 200 but no access_token in response."""
        env = MockEnv()
        state = await _setup_oauth_state(env)

        mock_fetch = _mock_http_fetch(
            token_data={"error": "bad_verification_code"},
            user_data={},
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.http_fetch", mock_fetch):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 400

    async def test_returns_502_on_user_fetch_failure(self) -> None:
        """502 when GitHub user info endpoint returns non-200."""
        env = MockEnv()
        state = await _setup_oauth_state(env)

        mock_fetch = _mock_http_fetch(
            token_data={"access_token": "gho_test"},
            user_data={},
            user_status=500,
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.http_fetch", mock_fetch):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 502


class TestCallbackRejectsUnauthorizedEmail:
    async def test_rejects_email_not_in_allowed_list(self) -> None:
        """Emails not in ALLOWED_EMAILS get a redirect to login error."""
        env = MockEnv(allowed_emails="allowed@example.com,admin@example.com")
        state = await _setup_oauth_state(env)

        mock_fetch = _mock_http_fetch(
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
        with patch("src.auth.routes.http_fetch", mock_fetch):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 302
        assert "error=not_owner" in resp.headers["location"]

    async def test_allows_authorized_email(self) -> None:
        """When ALLOWED_EMAILS is set, emails in the list proceed (redirect)."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(
            allowed_emails="allowed@example.com,admin@example.com",
            db=db,
        )
        state = await _setup_oauth_state(env)

        mock_fetch = _mock_http_fetch(
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
        with patch("src.auth.routes.http_fetch", mock_fetch):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/"

    async def test_rejects_when_allowlist_empty(self) -> None:
        """When ALLOWED_EMAILS is empty, auth is rejected (whitelist required)."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(allowed_emails="", db=db)
        state = await _setup_oauth_state(env)

        mock_fetch = _mock_http_fetch(
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
        with patch("src.auth.routes.http_fetch", mock_fetch):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 403


class TestCallbackCookieAttributes:
    async def test_callback_sets_secure_cookie_attributes(self) -> None:
        """OAuth callback Set-Cookie header includes httponly, samesite=lax, and path=/."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(
            allowed_emails="test@example.com",
            db=db,
        )
        state = await _setup_oauth_state(env)

        mock_fetch = _mock_http_fetch(
            token_data={"access_token": "gho_test_token"},
            user_data={
                "id": 12345,
                "login": "testuser",
                "email": "test@example.com",
                "avatar_url": "https://github.com/avatar.png",
            },
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.http_fetch", mock_fetch):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 302

        set_cookie = resp.headers.get("set-cookie", "")
        assert "httponly" in set_cookie.lower()
        assert "samesite=lax" in set_cookie.lower()
        assert "path=/" in set_cookie.lower()


class TestCallbackUserAgentHeader:
    """Verify User-Agent is sent on GitHub API requests.

    GitHub returns 403 if User-Agent is missing from requests to
    api.github.com. We use http_fetch (js.fetch in Workers) to
    ensure headers are reliably transmitted.
    """

    async def test_user_endpoint_receives_user_agent(self) -> None:
        """http_fetch call for /user includes User-Agent header."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(allowed_emails="test@example.com", db=db)
        state = await _setup_oauth_state(env)

        mock_fetch = _mock_http_fetch(
            token_data={"access_token": "gho_test"},
            user_data={"id": 1, "login": "user", "email": "test@example.com", "avatar_url": ""},
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.http_fetch", mock_fetch):
            client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )

        # Find the /user call (not /user/emails) and check headers
        user_calls = [
            c
            for c in mock_fetch.call_args_list
            if c.args and "api.github.com/user" in c.args[0] and "emails" not in c.args[0]
        ]
        assert len(user_calls) >= 1, "Expected at least one call to /user"
        headers = user_calls[0].kwargs.get("headers", {})
        assert "User-Agent" in headers, (
            "User-Agent missing from /user request — GitHub returns 403 without it"
        )

    async def test_user_agent_missing_causes_403(self) -> None:
        """Without User-Agent, GitHub returns 403 (simulated)."""
        env = MockEnv()
        state = await _setup_oauth_state(env)

        mock_fetch = _mock_http_fetch(
            token_data={"access_token": "gho_test"},
            user_data={},
            user_status=403,
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.http_fetch", mock_fetch):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 502


class TestCallbackPrivateEmail:
    async def test_fetches_email_from_user_emails_endpoint(self) -> None:
        """When /user returns no email, fetches from /user/emails."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(allowed_emails="private@example.com", db=db)
        state = await _setup_oauth_state(env)

        mock_fetch = _mock_http_fetch(
            token_data={"access_token": "gho_test"},
            user_data={"id": 1, "login": "user", "email": None, "avatar_url": ""},
            emails_data=[
                {"email": "noreply@users.github.com", "primary": False, "verified": True},
                {"email": "private@example.com", "primary": True, "verified": True},
            ],
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.http_fetch", mock_fetch):
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
        client.cookies.set(COOKIE_NAME, session_id)

        resp = client.post("/api/auth/logout")
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
        client.cookies.set(COOKIE_NAME, session_id)

        resp = client.get("/api/auth/session")
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

        client.cookies.set(COOKIE_NAME, "expired_id")
        resp = client.get("/api/auth/session")
        assert resp.status_code == 401


# =========================================================================
# Case-insensitive email matching
# =========================================================================


class TestCaseInsensitiveEmail:
    async def test_case_insensitive_email(self) -> None:
        """Email matching ignores case during callback."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(
            allowed_emails="Test@Example.Com",
            db=db,
        )
        state = await _setup_oauth_state(env)

        mock_fetch = _mock_http_fetch(
            token_data={"access_token": "gho_test_token"},
            user_data={
                "id": 12345,
                "login": "testuser",
                "email": "test@example.com",
                "avatar_url": "https://github.com/avatar.png",
            },
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.http_fetch", mock_fetch):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 302

    async def test_case_insensitive_email_session_check(self) -> None:
        """Session email check is case-insensitive (via parse_allowed_emails)."""
        env = MockEnv(allowed_emails="Test@Example.Com")
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
        client.cookies.set(COOKIE_NAME, session_id)
        resp = client.get("/me")
        assert resp.status_code == 200


# =========================================================================
# Empty email from GitHub
# =========================================================================


class TestEmptyEmailReturns400:
    async def test_empty_email_returns_400(self) -> None:
        """400 when GitHub returns no email (public or private)."""
        env = MockEnv(allowed_emails="test@example.com")
        state = await _setup_oauth_state(env)

        mock_fetch = _mock_http_fetch(
            token_data={"access_token": "gho_test"},
            user_data={
                "id": 1,
                "login": "user",
                "email": None,
                "avatar_url": "",
            },
            emails_data=[],
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.http_fetch", mock_fetch):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 400
        assert "email" in resp.json()["detail"].lower()


# =========================================================================
# GitHub error field in token response
# =========================================================================


class TestGitHubErrorFieldInTokenResponse:
    async def test_github_error_field_in_token_response(self) -> None:
        """200 with {"error": "bad_verification_code"} returns 400."""
        env = MockEnv()
        state = await _setup_oauth_state(env)

        mock_fetch = _mock_http_fetch(
            token_data={"error": "bad_verification_code"},
            user_data={},
        )

        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        with patch("src.auth.routes.http_fetch", mock_fetch):
            resp = client.get(
                f"/api/auth/callback?code=test_code&state={state}",
                follow_redirects=False,
            )
        assert resp.status_code == 400
        assert "bad_verification_code" in resp.json()["detail"]


# =========================================================================
# Missing GITHUB_CLIENT_ID
# =========================================================================


class TestMissingGitHubClientId:
    def test_missing_github_client_id_login_returns_500(self) -> None:
        """GET /login returns 500 when GITHUB_CLIENT_ID is not configured."""
        env = MockEnv(github_client_id="")
        app = _make_auth_app(env)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/auth/login", follow_redirects=False)
        assert resp.status_code == 500
        assert "GITHUB_CLIENT_ID" in resp.json()["detail"]

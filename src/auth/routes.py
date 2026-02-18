"""Auth routes for Tasche — GitHub OAuth login, callback, logout, and session.

All GitHub API communication uses ``httpx.AsyncClient``.  D1 queries use the
``d1_first`` helper from ``wrappers`` and parameterised SQL (``?`` placeholders).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from auth.dependencies import get_current_user
from auth.session import COOKIE_NAME, SESSION_TTL, create_session, delete_session
from wrappers import SafeEnv, d1_first

router = APIRouter()

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_USER_EMAILS_URL = "https://api.github.com/user/emails"

OAUTH_STATE_PREFIX = "oauth_state:"
OAUTH_STATE_TTL = 600  # 10 minutes


def _parse_allowed_emails(raw: str) -> set[str]:
    """Parse a comma-separated list of allowed emails into a set.

    Strips whitespace from each entry and ignores empty strings.
    """
    if not raw:
        return set()
    return {email.strip() for email in raw.split(",") if email.strip()}


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    """Redirect the user to GitHub's OAuth authorize page."""
    env = SafeEnv(request.scope["env"])
    client_id = env.get("GITHUB_CLIENT_ID", "")
    site_url = env.get("SITE_URL", "")
    redirect_uri = f"{site_url}/api/auth/callback"

    # Generate CSRF state token and store in KV with 10-minute TTL
    state = secrets.token_urlsafe(32)
    raw_env = request.scope["env"]
    await raw_env.SESSIONS.put(
        f"{OAUTH_STATE_PREFIX}{state}", "1", expirationTtl=OAUTH_STATE_TTL
    )

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "user:email",
        "state": state,
    }
    authorize_url = f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"
    return RedirectResponse(url=authorize_url, status_code=302)


@router.get("/callback")
async def callback(request: Request) -> RedirectResponse:
    """Handle GitHub OAuth callback: exchange code, fetch user, create session."""
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing code parameter")

    # Verify CSRF state
    state = request.query_params.get("state")
    if not state:
        raise HTTPException(status_code=400, detail="Missing state parameter")

    raw_env = request.scope["env"]
    stored_state = await raw_env.SESSIONS.get(f"{OAUTH_STATE_PREFIX}{state}")
    if stored_state is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state")
    await raw_env.SESSIONS.delete(f"{OAUTH_STATE_PREFIX}{state}")

    env = SafeEnv(raw_env)
    client_id = env.get("GITHUB_CLIENT_ID", "")
    client_secret = env.get("GITHUB_CLIENT_SECRET", "")
    site_url = env.get("SITE_URL", "")
    redirect_uri = f"{site_url}/api/auth/callback"

    async with httpx.AsyncClient() as client:
        # --- Exchange code for access token ---
        token_resp = await client.post(
            GITHUB_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        if token_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="GitHub token exchange failed")
        token_data = token_resp.json()

        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="Failed to obtain access token")

        # --- Fetch GitHub user info ---
        auth_headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        user_resp = await client.get(GITHUB_USER_URL, headers=auth_headers)
        if user_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="GitHub user info fetch failed")
        github_user = user_resp.json()

        github_id = github_user.get("id")
        if github_id is None:
            raise HTTPException(status_code=502, detail="GitHub did not return a valid user")

        email = github_user.get("email") or ""

        # If no public email, fetch from /user/emails endpoint
        if not email:
            emails_resp = await client.get(GITHUB_USER_EMAILS_URL, headers=auth_headers)
            if emails_resp.status_code == 200:
                for e in emails_resp.json():
                    if e.get("primary") and e.get("verified"):
                        email = e["email"]
                        break

    username = github_user.get("login", "")
    avatar_url = github_user.get("avatar_url", "")

    # --- Check ALLOWED_EMAILS whitelist (required) ---
    allowed_raw = env.get("ALLOWED_EMAILS", "")
    allowed_emails = _parse_allowed_emails(allowed_raw)
    if not allowed_emails:
        raise HTTPException(
            status_code=403,
            detail="ALLOWED_EMAILS is not configured. Set it in wrangler.jsonc.",
        )
    if email not in allowed_emails:
        raise HTTPException(status_code=403, detail="Email not authorized")

    # --- Upsert user in D1 ---
    db = env.DB
    now = datetime.now(UTC).isoformat()

    existing = d1_first(
        await db.prepare("SELECT id FROM users WHERE github_id = ?").bind(github_id).first()
    )

    if existing:
        user_id = existing["id"]
        await (
            db.prepare(
                "UPDATE users SET email = ?, username = ?, avatar_url = ?, updated_at = ? "
                "WHERE id = ?"
            )
            .bind(email, username, avatar_url, now, user_id)
            .run()
        )
    else:
        user_id = secrets.token_urlsafe(16)
        await (
            db.prepare(
                "INSERT INTO users (id, github_id, email, username, avatar_url, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            )
            .bind(user_id, github_id, email, username, avatar_url, now, now)
            .run()
        )

    # --- Create session ---
    session_data = {
        "user_id": user_id,
        "email": email,
        "username": username,
        "avatar_url": avatar_url,
        "created_at": now,
    }
    session_id = await create_session(raw_env.SESSIONS, session_data)

    # --- Set cookie and redirect ---
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        max_age=SESSION_TTL,
    )
    return response


@router.post("/logout")
async def logout(request: Request) -> Response:
    """Delete the session from KV and clear the session cookie."""
    session_id = request.cookies.get(COOKIE_NAME)
    if session_id:
        env = request.scope["env"]
        await delete_session(env.SESSIONS, session_id)

    response = Response(status_code=200)
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@router.get("/session")
async def session(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the current user's session data, or 401 if not authenticated."""
    return user

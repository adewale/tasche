"""Auth routes for Tasche — GitHub OAuth login, callback, logout, and session.

All outbound GitHub API communication uses ``http_fetch`` from ``src.boundary``,
which delegates to the native JS ``fetch()`` API in the Workers runtime and
falls back to httpx in CPython tests.  D1 queries use parameterised SQL
(``?`` placeholders) via the SafeD1 wrapper.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from auth.dependencies import get_current_user
from auth.session import (
    COOKIE_NAME,
    SESSION_TTL,
    create_session,
    delete_session,
    parse_allowed_emails,
)
from src.boundary import http_fetch
from utils import generate_id, now_iso

router = APIRouter()

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"


def _get_site_url(env: object, request: Request) -> str:
    """Resolve SITE_URL from config or auto-detect from request Host header.

    When SITE_URL is not configured or still contains the deploy-button
    placeholder (``<your-subdomain>``), falls back to building the URL
    from the incoming request's ``Host`` header and scheme.
    """
    site_url = env.get("SITE_URL", "")
    if not site_url or "<your-subdomain>" in site_url:
        host = request.headers.get("host", "")
        scheme = (
            "https"
            if request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"
            else "http"
        )
        site_url = f"{scheme}://{host}"
    return site_url.rstrip("/")


GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_USER_EMAILS_URL = "https://api.github.com/user/emails"

OAUTH_STATE_PREFIX = "oauth_state:"
OAUTH_STATE_TTL = 600  # 10 minutes


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    """Redirect the user to GitHub's OAuth authorize page."""
    env = request.scope["env"]
    client_id = env.get("GITHUB_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(status_code=500, detail="GITHUB_CLIENT_ID is not configured")
    site_url = _get_site_url(env, request)
    redirect_uri = f"{site_url}/api/auth/callback"

    # Generate CSRF state token and store in KV with 10-minute TTL
    state = generate_id(32)
    await env.SESSIONS.put(f"{OAUTH_STATE_PREFIX}{state}", "1", expirationTtl=OAUTH_STATE_TTL)

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

    env = request.scope["env"]
    stored_state = await env.SESSIONS.get(f"{OAUTH_STATE_PREFIX}{state}")
    if stored_state is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state")
    await env.SESSIONS.delete(f"{OAUTH_STATE_PREFIX}{state}")

    client_id = env.get("GITHUB_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(status_code=500, detail="GITHUB_CLIENT_ID is not configured")
    client_secret = env.get("GITHUB_CLIENT_SECRET", "")
    if not client_secret:
        raise HTTPException(status_code=500, detail="GITHUB_CLIENT_SECRET is not configured")
    site_url = _get_site_url(env, request)
    redirect_uri = f"{site_url}/api/auth/callback"

    # --- Exchange code for access token ---
    token_resp = await http_fetch(
        GITHUB_TOKEN_URL,
        method="POST",
        form_data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={
            "Accept": "application/json",
            "User-Agent": "tasche/1.0",
        },
    )
    if token_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="GitHub token exchange failed")
    token_data = token_resp.json()

    if "error" in token_data:
        error_detail = token_data.get("error_description", token_data["error"])
        raise HTTPException(
            status_code=400,
            detail=f"GitHub OAuth error: {error_detail}",
        )

    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="Failed to obtain access token")

    # --- Fetch GitHub user info ---
    auth_headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "tasche/1.0",
    }
    user_resp = await http_fetch(GITHUB_USER_URL, headers=auth_headers)
    if user_resp.status_code != 200:
        raise HTTPException(status_code=502, detail="GitHub user info fetch failed")
    github_user = user_resp.json()

    github_id = github_user.get("id")
    if github_id is None:
        raise HTTPException(status_code=502, detail="GitHub did not return a valid user")

    email = github_user.get("email") or ""

    # If no public email, fetch from /user/emails endpoint
    if not email:
        emails_resp = await http_fetch(GITHUB_USER_EMAILS_URL, headers=auth_headers)
        if emails_resp.status_code == 200:
            for e in emails_resp.json():
                if e.get("primary") and e.get("verified"):
                    email = e["email"]
                    break

    username = github_user.get("login", "")
    avatar_url = github_user.get("avatar_url", "")

    if not email:
        raise HTTPException(
            status_code=400,
            detail=(
                "Could not obtain a verified email from GitHub."
                " Please ensure your GitHub account has a verified email."
            ),
        )

    # --- Check ALLOWED_EMAILS whitelist (required) ---
    allowed_raw = env.get("ALLOWED_EMAILS", "")
    allowed_emails = parse_allowed_emails(allowed_raw)
    if not allowed_emails:
        raise HTTPException(
            status_code=403,
            detail="ALLOWED_EMAILS is not configured. Run: npx wrangler secret put ALLOWED_EMAILS",
        )
    if email.lower() not in allowed_emails:
        return RedirectResponse(url="/#/login?error=not_owner", status_code=302)

    # --- Upsert user in D1 ---
    db = env.DB
    now = now_iso()

    existing = await db.prepare("SELECT id FROM users WHERE github_id = ?").bind(github_id).first()

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
        user_id = generate_id()
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
    session_id = await create_session(env.SESSIONS, session_data)

    # --- Set cookie and redirect ---
    is_secure = site_url.startswith("https://")
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        path="/",
        max_age=SESSION_TTL,
    )
    return response


@router.post("/logout")
async def logout(request: Request) -> Response:
    """Delete the session from KV and clear the session cookie."""
    env = request.scope["env"]
    session_id = request.cookies.get(COOKIE_NAME)
    if session_id:
        await delete_session(env.SESSIONS, session_id)

    is_secure = _get_site_url(env, request).startswith("https://")
    response = Response(status_code=200)
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        secure=is_secure,
        samesite="lax",
    )
    return response


@router.get("/session")
async def session(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the current user's session data, or 401 if not authenticated."""
    return user

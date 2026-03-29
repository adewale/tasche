"""FastAPI dependencies for authentication.

Provides ``get_current_user`` which extracts and validates the session cookie,
returning the authenticated user's data or raising a 401 error.

When ``DISABLE_AUTH`` is set to ``"true"`` in the Worker environment, all
authentication is bypassed and a dev user is returned automatically.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request

from auth.session import (
    COOKIE_NAME,
    delete_session,
    get_session,
    parse_allowed_emails,
    refresh_session,
)
from utils import now_iso

# Module-level cache for parsed ALLOWED_EMAILS — avoids re-parsing on every request.
_allowed_emails_cache: tuple[str, set[str]] | None = None


def _cached_parse_allowed_emails(raw: str) -> set[str]:
    """Return the parsed allowed emails, caching the result at module level.

    Re-parses only when the raw string changes (e.g. env var updated).
    """
    global _allowed_emails_cache
    if _allowed_emails_cache is not None and _allowed_emails_cache[0] == raw:
        return _allowed_emails_cache[1]
    result = parse_allowed_emails(raw)
    _allowed_emails_cache = (raw, result)
    return result


# Module-level cache — avoids a D1 round-trip on every request after the first.
_dev_user: dict[str, Any] | None = None

_DEV_USER_ID = "dev"


async def _get_or_create_dev_user(db: Any) -> dict[str, Any]:
    """Return the dev user, creating it in D1 if it doesn't exist yet."""
    global _dev_user
    if _dev_user is not None:
        return dict(_dev_user)

    now = now_iso()
    await (
        db.prepare(
            "INSERT OR IGNORE INTO users (id, github_id, email, username, avatar_url, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        .bind(_DEV_USER_ID, 0, "dev@localhost", "dev", "", now, now)
        .run()
    )

    _dev_user = {
        "user_id": _DEV_USER_ID,
        "email": "dev@localhost",
        "username": "dev",
        "avatar_url": "",
        "created_at": now,
    }
    print(json.dumps({"event": "dev_mode_active", "user_id": _DEV_USER_ID}))
    return dict(_dev_user)


async def get_current_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency that returns the current authenticated user.

    When ``DISABLE_AUTH`` is ``"true"``, returns a dev user without
    requiring a session cookie or OAuth.

    Otherwise, reads the ``tasche_session`` cookie from the request, looks
    up the session in KV, and returns the stored user data dict.  Also
    re-checks the user's email against ``ALLOWED_EMAILS`` to handle
    revocation.

    Raises
    ------
    HTTPException
        401 if the cookie is missing, empty, maps to no valid session,
        or the user's email is no longer in the allowed list.
    """
    env = request.scope["env"]

    # Auth bypass — return dev user without any session or OAuth.
    if env.get("DISABLE_AUTH") == "true":
        worker_env = env.get("WORKER_ENV", "")
        if worker_env == "production":
            print(json.dumps({"event": "disable_auth_blocked", "worker_env": worker_env}))
            raise HTTPException(
                status_code=500,
                detail="DISABLE_AUTH cannot be used in production",
            )
        user_data = await _get_or_create_dev_user(env.DB)
        request.state.user_id = user_data["user_id"]
        return user_data

    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_data = await get_session(env.SESSIONS, session_id)
    if user_data is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    # Re-check ALLOWED_EMAILS to handle revocation (whitelist is required)
    allowed_raw = env.get("ALLOWED_EMAILS", "")
    allowed_emails = _cached_parse_allowed_emails(allowed_raw)

    if not allowed_emails:
        await delete_session(env.SESSIONS, session_id)
        raise HTTPException(status_code=401, detail="ALLOWED_EMAILS is not configured")

    user_email = user_data.get("email", "")
    if user_email.lower() not in allowed_emails:
        await delete_session(env.SESSIONS, session_id)
        raise HTTPException(status_code=401, detail="Access revoked")

    # Refresh session TTL on each authenticated request so active users
    # are not forced to re-authenticate every 7 days.
    await refresh_session(env.SESSIONS, session_id, user_data)

    # Store user_id on request.state so the observability middleware can
    # read it without a separate KV lookup.
    request.state.user_id = user_data.get("user_id")

    return user_data

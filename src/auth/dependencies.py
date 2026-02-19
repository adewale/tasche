"""FastAPI dependencies for authentication.

Provides ``get_current_user`` which extracts and validates the session cookie,
returning the authenticated user's data or raising a 401 error.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from auth.session import (
    COOKIE_NAME,
    delete_session,
    get_session,
    parse_allowed_emails,
    refresh_session,
)
from wrappers import SafeEnv


async def get_current_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency that returns the current authenticated user.

    Reads the ``tasche_session`` cookie from the request, looks up the
    session in KV, and returns the stored user data dict.  Also re-checks
    the user's email against ``ALLOWED_EMAILS`` to handle revocation.

    Raises
    ------
    HTTPException
        401 if the cookie is missing, empty, maps to no valid session,
        or the user's email is no longer in the allowed list.
    """
    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    env = request.scope["env"]
    user_data = await get_session(env.SESSIONS, session_id)
    if user_data is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    # Re-check ALLOWED_EMAILS to handle revocation (whitelist is required)
    safe_env = SafeEnv(env)
    allowed_raw = safe_env.get("ALLOWED_EMAILS", "")
    allowed_emails = parse_allowed_emails(allowed_raw)

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

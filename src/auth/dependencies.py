"""FastAPI dependencies for authentication.

Provides ``get_current_user`` which extracts and validates the session cookie,
returning the authenticated user's data or raising a 401 error.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from auth.session import COOKIE_NAME, get_session


async def get_current_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency that returns the current authenticated user.

    Reads the ``tasche_session`` cookie from the request, looks up the
    session in KV, and returns the stored user data dict.

    Raises
    ------
    HTTPException
        401 if the cookie is missing, empty, or maps to no valid session.
    """
    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    env = request.scope["env"]
    user_data = await get_session(env.SESSIONS, session_id)
    if user_data is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return user_data

"""Session management for Tasche authentication.

Handles creating, retrieving, and deleting sessions stored in Cloudflare KV.
Sessions are keyed as ``session:{session_id}`` and store JSON-serialised user
data with a 7-day TTL.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

SESSION_TTL = 604800  # 7 days in seconds
SESSION_PREFIX = "session:"
COOKIE_NAME = "tasche_session"


async def create_session(kv: Any, user_data: dict[str, Any]) -> str:
    """Generate a session ID, store user data in KV, and return the ID.

    Parameters
    ----------
    kv:
        The Cloudflare KV namespace binding (``env.SESSIONS``).
    user_data:
        Dict containing at least ``user_id``, ``email``, ``username``,
        ``avatar_url``, and ``created_at``.

    Returns
    -------
    str
        The generated session ID (URL-safe token).
    """
    session_id = secrets.token_urlsafe(32)
    key = f"{SESSION_PREFIX}{session_id}"
    await kv.put(key, json.dumps(user_data), expirationTtl=SESSION_TTL)
    return session_id


async def get_session(kv: Any, session_id: str) -> dict[str, Any] | None:
    """Retrieve session data from KV.

    Parameters
    ----------
    kv:
        The Cloudflare KV namespace binding.
    session_id:
        The session ID (without the ``session:`` prefix).

    Returns
    -------
    dict or None
        The stored user data dict, or ``None`` if the session does not exist.
    """
    key = f"{SESSION_PREFIX}{session_id}"
    raw = await kv.get(key)
    if raw is None:
        return None
    return json.loads(raw)


_REFRESH_INTERVAL = 3600  # Only refresh once per hour to reduce KV writes


async def refresh_session(kv: Any, session_id: str, user_data: dict[str, Any]) -> None:
    """Refresh a session's TTL by re-writing it to KV.

    Called on each authenticated request to extend the session so that
    active users are not forced to re-authenticate every 7 days.
    Skips the write if the session was refreshed less than 1 hour ago.
    """
    import time

    now = time.time()
    last_refreshed = user_data.get("refreshed_at", 0)
    if now - last_refreshed < _REFRESH_INTERVAL:
        return

    user_data["refreshed_at"] = now
    key = f"{SESSION_PREFIX}{session_id}"
    await kv.put(key, json.dumps(user_data), expirationTtl=SESSION_TTL)


async def delete_session(kv: Any, session_id: str) -> None:
    """Delete a session from KV.

    Parameters
    ----------
    kv:
        The Cloudflare KV namespace binding.
    session_id:
        The session ID (without the ``session:`` prefix).
    """
    key = f"{SESSION_PREFIX}{session_id}"
    await kv.delete(key)


def parse_allowed_emails(raw: str) -> set[str]:
    """Parse a comma-separated list of allowed emails into a set.

    Strips whitespace from each entry, lowercases for case-insensitive
    comparison, and ignores empty strings.
    """
    if not raw:
        return set()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}

"""Shared utilities for Tasche backend modules."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException


def now_iso() -> str:
    """Return the current UTC timestamp as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def generate_id(nbytes: int = 16) -> str:
    """Generate a URL-safe random ID.

    Uses 16 bytes (22 chars) for entity IDs and 32 bytes (43 chars) for
    session/CSRF tokens.
    """
    return secrets.token_urlsafe(nbytes)


async def get_user_entity(
    db: Any,
    *,
    table: str,
    entity_id: str,
    user_id: str,
    fields: str = "*",
    not_found: str = "Not found",
) -> dict[str, Any]:
    """Fetch a row by ID with ownership check, or raise 404.

    .. warning::

        The *fields* and *table* parameters are interpolated directly into
        the SQL query.  They must **never** contain user-supplied input.
    """
    row = await (
        db.prepare(f"SELECT {fields} FROM {table} WHERE id = ? AND user_id = ?")
        .bind(entity_id, user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=not_found)
    return row

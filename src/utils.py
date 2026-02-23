"""Shared utilities for Tasche backend modules."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime


def now_iso() -> str:
    """Return the current UTC timestamp as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


def generate_id(nbytes: int = 16) -> str:
    """Generate a URL-safe random ID.

    Uses 16 bytes (22 chars) for entity IDs and 32 bytes (43 chars) for
    session/CSRF tokens.
    """
    return secrets.token_urlsafe(nbytes)

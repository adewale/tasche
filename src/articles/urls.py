"""URL validation and deduplication utilities for articles.

Provides URL validation/normalisation, domain extraction, and duplicate
checking across the three URL columns in the articles table.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from wrappers import d1_first


def validate_url(url: str) -> str:
    """Validate and normalise a URL.

    Ensures the URL uses an ``http`` or ``https`` scheme and has a valid
    network location (hostname).  Returns the normalised URL string.

    Raises
    ------
    ValueError
        If the URL is invalid or uses a disallowed scheme.
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL must be a non-empty string")

    url = url.strip()

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL must use http or https scheme, got '{parsed.scheme}'")

    if not parsed.netloc:
        raise ValueError("URL must have a valid hostname")

    return parsed.geturl()


def extract_domain(url: str) -> str:
    """Extract the hostname from a URL.

    Parameters
    ----------
    url:
        A valid URL string.

    Returns
    -------
    str
        The hostname (e.g. ``"example.com"``).
    """
    parsed = urlparse(url)
    return parsed.hostname or ""


async def check_duplicate(db: Any, user_id: str, url: str) -> dict[str, Any] | None:
    """Check whether *url* already exists for *user_id* across all URL columns.

    Searches ``original_url``, ``final_url``, and ``canonical_url`` for an
    existing match.

    Parameters
    ----------
    db:
        The D1 database binding.
    user_id:
        The authenticated user's ID.
    url:
        The URL to check for duplicates.

    Returns
    -------
    dict or None
        The existing article row if a duplicate is found, otherwise ``None``.
    """
    result = d1_first(
        await db.prepare(
            "SELECT * FROM articles WHERE user_id = ? "
            "AND (original_url = ? OR final_url = ? OR canonical_url = ?)"
        )
        .bind(user_id, url, url, url)
        .first()
    )
    return result

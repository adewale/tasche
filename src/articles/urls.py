"""URL validation and deduplication utilities for articles.

Provides URL validation/normalisation, domain extraction, and duplicate
checking across the three URL columns in the articles table.
"""

from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import urlparse

from wrappers import d1_first

# Hostnames that must be blocked to prevent SSRF
_BLOCKED_HOSTNAMES = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "[::1]",
    "::1",
    "metadata.google.internal",
    "metadata.google",
    "169.254.169.254",
}


def _is_private_hostname(hostname: str) -> bool:
    """Check if a hostname resolves to a private/internal network address.

    Blocks: localhost, loopback, private RFC1918 ranges (10.*, 172.16-31.*,
    192.168.*), link-local (169.254.*), and cloud metadata endpoints.
    """
    hostname = hostname.lower().strip("[]")

    if hostname in _BLOCKED_HOSTNAMES:
        return True

    # Check IP-based patterns (manual octet check for IPv4)
    parts = hostname.split(".")
    if len(parts) == 4:
        try:
            octets = [int(p) for p in parts]
            # 10.0.0.0/8
            if octets[0] == 10:
                return True
            # 172.16.0.0/12
            if octets[0] == 172 and 16 <= octets[1] <= 31:
                return True
            # 192.168.0.0/16
            if octets[0] == 192 and octets[1] == 168:
                return True
            # 169.254.0.0/16 (link-local)
            if octets[0] == 169 and octets[1] == 254:
                return True
            # 127.0.0.0/8
            if octets[0] == 127:
                return True
            # 0.0.0.0/8
            if octets[0] == 0:
                return True
        except (ValueError, IndexError):
            pass

    # Use the ipaddress module to catch IPv6, IPv6-mapped IPv4 (e.g.
    # ::ffff:127.0.0.1), and any other numeric address formats that the
    # manual octet check above does not cover.
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local:
            return True
        # IPv6-mapped IPv4: check the embedded IPv4 address as well
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
            mapped = addr.ipv4_mapped
            if (
                mapped.is_private
                or mapped.is_loopback
                or mapped.is_reserved
                or mapped.is_link_local
            ):
                return True
    except ValueError:
        # Not a valid IP literal — it's a hostname, which is fine
        pass

    return False


def validate_url(url: str) -> str:
    """Validate and normalise a URL.

    Ensures the URL uses an ``http`` or ``https`` scheme, has a valid
    network location (hostname), and does not point to a private network
    address (SSRF protection).  Returns the normalised URL string.

    Raises
    ------
    ValueError
        If the URL is invalid, uses a disallowed scheme, or points to a
        private network address.
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL must be a non-empty string")

    url = url.strip()

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL must use http or https scheme, got '{parsed.scheme}'")

    if not parsed.netloc:
        raise ValueError("URL must have a valid hostname")

    hostname = parsed.hostname or ""
    if _is_private_hostname(hostname):
        raise ValueError("URLs pointing to private/internal networks are not allowed")

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
            "SELECT id FROM articles WHERE user_id = ? "
            "AND (original_url = ? OR final_url = ? OR canonical_url = ?)"
        )
        .bind(user_id, url, url, url)
        .first()
    )
    return result

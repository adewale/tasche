"""URL health checker for original article URLs.

Periodically checks whether the original URL for a saved article is still
accessible.  Uses HEAD requests with SSRF protection to classify the URL
into one of five states defined in spec section 7.7:

- ``available``   -- Original still accessible (2xx)
- ``paywalled``   -- Returns 401/403 (requires login/subscription)
- ``gone``        -- 404/410, page deleted
- ``domain_dead`` -- Entire domain unreachable (DNS failure, connection refused)
- ``unknown``     -- Any other error or unchecked
"""

from __future__ import annotations

from urllib.parse import urlparse

from articles.urls import _is_private_hostname
from wrappers import HttpClient

# Timeout for health check requests (seconds).
_HEALTH_CHECK_TIMEOUT = 10.0

# User-Agent for health check requests.
_USER_AGENT = "Mozilla/5.0 (compatible; Tasche/1.0; +https://github.com/tasche)"


async def check_original_url(url: str) -> str:
    """Check if an original URL is still accessible.

    Uses an HTTP HEAD request with redirect following and a 10-second timeout.
    The URL is validated against SSRF rules before making any network request.

    Parameters
    ----------
    url:
        The original URL to check.

    Returns
    -------
    str
        One of: ``'available'``, ``'paywalled'``, ``'gone'``,
        ``'domain_dead'``, ``'unknown'``.
    """
    # SSRF protection: skip private/internal URLs
    try:
        parsed = urlparse(url)
        if not parsed.scheme or parsed.scheme not in ("http", "https"):
            return "unknown"
        if not parsed.hostname:
            return "unknown"
        if _is_private_hostname(parsed.hostname):
            return "unknown"
    except Exception:
        return "unknown"

    try:
        async with HttpClient() as client:
            resp = await client.head(
                url,
                headers={"User-Agent": _USER_AGENT},
                timeout=_HEALTH_CHECK_TIMEOUT,
            )

            # SSRF check: validate the final URL after redirects
            final_hostname = urlparse(resp.url).hostname if resp.url else None
            if final_hostname and _is_private_hostname(final_hostname):
                return "unknown"

            status = resp.status_code

            if 200 <= status <= 299:
                return "available"
            if status in (401, 403):
                return "paywalled"
            if status in (404, 410):
                return "gone"

            # Other HTTP errors (5xx, etc.) — treat as unknown
            return "unknown"

    except (ConnectionError, OSError):
        # DNS failure, connection refused
        return "domain_dead"
    except TimeoutError:
        # Connect or read timeout
        return "domain_dead"
    except Exception:
        return "unknown"

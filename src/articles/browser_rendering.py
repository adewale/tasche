"""Client for Cloudflare Browser Rendering REST API.

Provides async helpers for taking screenshots and scraping rendered HTML
from web pages via Cloudflare's headless browser service.  Used by the
article processing pipeline when a simple GET returns minimal
content (JS-heavy sites).

See spec section 2.5 for details on the REST API endpoints.
"""

from __future__ import annotations

from typing import Any

BROWSER_API_BASE = (
    "https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering"
)


class BrowserRenderingError(Exception):
    """Raised when the Browser Rendering API returns an error."""


async def screenshot(
    client: Any,
    url: str,
    account_id: str,
    api_token: str,
    *,
    viewport_width: int = 1200,
    viewport_height: int = 630,
    full_page: bool = False,
) -> bytes:
    """Capture a screenshot of *url* via the Browser Rendering REST API.

    Parameters
    ----------
    client:
        An HTTP client with async ``.post()`` method (e.g. ``HttpClient``).
    url:
        The page URL to screenshot.
    account_id:
        Cloudflare account ID.
    api_token:
        Cloudflare API token with Browser Rendering permission.
    viewport_width:
        Browser viewport width in pixels (default 1200).
    viewport_height:
        Browser viewport height in pixels (default 630).
    full_page:
        If ``True``, capture the entire scrollable page.

    Returns
    -------
    bytes
        Raw image data (PNG/JPEG) from the API.

    Raises
    ------
    BrowserRenderingError
        When the API returns a non-success response.
    """
    endpoint = BROWSER_API_BASE.format(account_id=account_id) + "/screenshot"
    payload = {
        "url": url,
        "viewport": {"width": viewport_width, "height": viewport_height},
        "fullPage": full_page,
    }
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    resp = await client.post(endpoint, json=payload, headers=headers, timeout=30.0)
    if resp.status_code != 200:
        raise BrowserRenderingError(
            f"Screenshot failed: HTTP {resp.status_code} — {resp.text[:500]}"
        )
    return resp.content


async def scrape(
    client: Any,
    url: str,
    account_id: str,
    api_token: str,
) -> str:
    """Scrape the rendered HTML of *url* via the Browser Rendering REST API.

    Returns the fully-rendered DOM after JavaScript execution.

    Parameters
    ----------
    client:
        An HTTP client with async ``.post()`` method (e.g. ``HttpClient``).
    url:
        The page URL to scrape.
    account_id:
        Cloudflare account ID.
    api_token:
        Cloudflare API token with Browser Rendering permission.

    Returns
    -------
    str
        Rendered HTML string.

    Raises
    ------
    BrowserRenderingError
        When the API returns a non-success response.
    """
    endpoint = BROWSER_API_BASE.format(account_id=account_id) + "/scrape"
    payload = {"url": url}
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    resp = await client.post(endpoint, json=payload, headers=headers, timeout=30.0)
    if resp.status_code != 200:
        raise BrowserRenderingError(
            f"Scrape failed: HTTP {resp.status_code} — {resp.text[:500]}"
        )

    data = resp.json()
    # The API returns the rendered HTML in the "result" field.
    if isinstance(data, dict) and "result" in data:
        return data["result"]
    # Fallback: return the raw response text if structure is unexpected.
    return resp.text

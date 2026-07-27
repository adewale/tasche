"""Security middleware — adds HTTP security headers to all responses."""

from __future__ import annotations

from typing import Any

# Canonical security headers, shared by the ASGI middleware (API responses)
# and the static-asset path in ``entry.py`` (HTML/JS/CSS responses).
# NOTE: 'unsafe-inline' is required for style-src because the frontend
# framework (and many UI component libraries) inject inline styles for
# dynamic theming, layout calculations, and component styling.  Removing
# it would break the reader view and settings UI.  A nonce-based approach
# would require server-side rendering support to inject the nonce into
# every <style> tag, which is not feasible with a static SPA frontend
# served from Workers Assets.
# script-src deliberately has NO 'unsafe-inline': all page scripts are
# external files (sw-register.js, bookmarklet-page.js, design-language.js).
# img-src allows https://www.google.com for the favicon service used by
# the article cards (InkFavicon falls back to generated art when blocked).
# media-src allows blob: because the audio player fetches the audio file
# and hands it to an <audio> element via URL.createObjectURL (a blob: URL);
# without this the "Listen later" feature would not play.
SECURITY_HEADERS: list[tuple[str, str]] = [
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("referrer-policy", "strict-origin-when-cross-origin"),
    ("permissions-policy", "camera=(), microphone=(), geolocation=()"),
    (
        "content-security-policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';"
        " img-src 'self' data: https://www.google.com; media-src 'self' blob:;"
        " connect-src 'self'; font-src 'self'; frame-ancestors 'none'",
    ),
]

HSTS_HEADER: tuple[str, str] = (
    "strict-transport-security",
    "max-age=31536000; includeSubDomains",
)

_SECURITY_HEADERS = [(name.encode(), value.encode()) for name, value in SECURITY_HEADERS]


class SecurityHeadersMiddleware:
    """Pure ASGI middleware that appends security headers to every HTTP response.

    Works with both BaseHTTPMiddleware-based and pure ASGI observability
    middleware — it only intercepts ``http.response.start`` messages to inject
    headers and passes everything else through unchanged.

    HSTS is conditionally added when the ``SITE_URL`` environment variable
    starts with ``https://``.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(_SECURITY_HEADERS)

                # Add HSTS only for HTTPS origins.  This (API) path keys off
                # SITE_URL; the static-asset path in entry._with_security_headers
                # keys off the actual request scheme instead.  Same header, two
                # triggers — both mean "served over HTTPS".  The asset/document
                # path is the one that matters for the browser caching HSTS, so
                # a workers.dev deploy with SITE_URL unset still gets HSTS on the
                # document even though API responses here would not.
                env = scope.get("env")
                if env:
                    from wrappers import SafeEnv

                    site_url = SafeEnv(env).get("SITE_URL", "")
                    if site_url.startswith("https://"):
                        headers.append((HSTS_HEADER[0].encode(), HSTS_HEADER[1].encode()))

                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)

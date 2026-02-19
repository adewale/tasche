"""Security middleware — adds HTTP security headers to all responses."""

from __future__ import annotations

from typing import Any

_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]


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

                # Add HSTS only for HTTPS origins
                env = scope.get("env")
                if env:
                    from wrappers import SafeEnv

                    site_url = SafeEnv(env).get("SITE_URL", "")
                    if site_url.startswith("https://"):
                        headers.append(
                            (
                                b"strict-transport-security",
                                b"max-age=31536000; includeSubDomains",
                            )
                        )

                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)

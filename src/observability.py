"""Observability middleware for Tasche.

Emits one wide event (canonical log line) per request as JSON to stdout.
Workers Logs captures stdout, so this is the primary observability mechanism.

Tail sampling is applied after the request completes — errors, slow requests,
and server errors are always emitted, while successful fast requests are
sampled at a low rate to control log volume.

Infrastructure hop timing (D1, R2, KV, Queue, AI, HTTP) is captured
automatically by the Safe* wrappers in ``boundary`` via a ``WideEvent``
context variable.  This middleware creates the event and emits it.

Implemented as a pure ASGI middleware (no BaseHTTPMiddleware) to avoid
spawning background threads that are incompatible with the Pyodide runtime.
"""

from __future__ import annotations

import uuid
from typing import Any

from wide_event import begin_event, emit_event


class ObservabilityMiddleware:
    """Pure ASGI middleware that emits a single wide event per request.

    The event is built incrementally during request processing and emitted
    once in a ``finally`` block.  Infrastructure hop timing is captured
    automatically by Safe* wrappers via the ``WideEvent`` context variable.

    User ID is read from ``scope["state"]["user_id"]`` which is set by
    the ``get_current_user`` auth dependency, avoiding a duplicate KV lookup.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extract request_id from headers (cf-ray) or generate a UUID
        headers = dict(scope.get("headers", []))
        cf_ray = headers.get(b"cf-ray", b"").decode("utf-8", errors="replace")
        request_id = cf_ray if cf_ray else str(uuid.uuid4())

        event = begin_event(
            "http",
            request_id=request_id,
            method=scope.get("method", ""),
            path=scope.get("path", ""),
        )

        status_code = 500  # default in case of unhandled exception

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            event.set("status_code", status_code)
            event.set("outcome", "error" if status_code >= 400 else "success")

        except Exception as exc:
            event.set("status_code", 500)
            event.set("outcome", "error")
            event.set("error.type", type(exc).__name__)
            event.set("error.message", str(exc)[:1000])
            raise
        finally:
            # Extract user ID from request state (set by get_current_user dependency)
            # in finally block so it's captured even on exceptions.
            state = scope.get("state", {})
            if isinstance(state, dict):
                event.set("user.id", state.get("user_id"))
            else:
                event.set("user.id", getattr(state, "user_id", None))

            emit_event(event)

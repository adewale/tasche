"""Observability middleware for Tasche.

Emits one wide event (canonical log line) per request as JSON to stdout.
Workers Logs captures stdout, so this is the primary observability mechanism.

Tail sampling is applied after the request completes — errors, slow requests,
and server errors are always emitted, while successful fast requests are
sampled at a low rate to control log volume.

Implemented as a pure ASGI middleware (no BaseHTTPMiddleware) to avoid
spawning background threads that are incompatible with the Pyodide runtime.
"""

from __future__ import annotations

import json
import random
import time
import uuid
from datetime import UTC, datetime
from typing import Any

# Tail sampling thresholds
_SLOW_REQUEST_MS = 2000
_SUCCESS_SAMPLE_RATE = 0.05  # 5%


class ObservabilityMiddleware:
    """Pure ASGI middleware that emits a single wide event per request.

    The event is built incrementally during request processing and emitted
    once in a ``finally`` block.  Tail sampling decides whether the event
    is actually printed to stdout.

    User ID is read from ``scope["state"]["user_id"]`` which is set by
    the ``get_current_user`` auth dependency, avoiding a duplicate KV lookup.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        event: dict = {}
        start_time = time.monotonic()
        status_code = 500  # default in case of unhandled exception

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            # ----- Fields from the request -----
            event["timestamp"] = datetime.now(UTC).isoformat()

            # Extract request_id from headers (cf-ray) or generate a UUID
            headers = dict(scope.get("headers", []))
            cf_ray = headers.get(b"cf-ray", b"").decode("utf-8", errors="replace")
            event["request_id"] = cf_ray if cf_ray else str(uuid.uuid4())

            event["method"] = scope.get("method", "")
            event["path"] = scope.get("path", "")

            await self.app(scope, receive, send_wrapper)

            event["status_code"] = status_code
            event["outcome"] = "error" if status_code >= 400 else "success"

            # Extract user ID from request state (set by get_current_user dependency)
            # instead of performing a duplicate KV lookup.
            state = scope.get("state", {})
            if isinstance(state, dict):
                event["user.id"] = state.get("user_id")
            else:
                event["user.id"] = getattr(state, "user_id", None)

        except Exception as exc:
            event["status_code"] = 500
            event["outcome"] = "error"
            event["error.type"] = type(exc).__name__
            event["error.message"] = str(exc)
            event.setdefault("user.id", None)
            raise
        finally:
            duration_ms = (time.monotonic() - start_time) * 1000
            event["duration_ms"] = round(duration_ms, 2)

            # Tail sampling — decide after the request whether to emit
            if _should_sample(event):
                print(json.dumps(event))


def _should_sample(event: dict) -> bool:
    """Decide whether to emit this event based on tail sampling rules.

    Always emit server errors, slow requests, and client errors.
    Everything else is sampled at ``_SUCCESS_SAMPLE_RATE``.
    """
    if event.get("status_code", 0) >= 500:
        return True
    if event.get("duration_ms", 0) > _SLOW_REQUEST_MS:
        return True
    if event.get("outcome") == "error":
        return True
    return random.random() < _SUCCESS_SAMPLE_RATE

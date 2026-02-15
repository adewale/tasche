"""Observability middleware for Tasche.

Emits one wide event (canonical log line) per request as JSON to stdout.
Workers Logs captures stdout, so this is the primary observability mechanism.

Tail sampling is applied after the request completes — errors, slow requests,
and server errors are always emitted, while successful fast requests are
sampled at a low rate to control log volume.
"""

from __future__ import annotations

import json
import random
import time
import uuid
from datetime import UTC, datetime

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# Tail sampling thresholds
_SLOW_REQUEST_MS = 2000
_SUCCESS_SAMPLE_RATE = 0.05  # 5%


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that emits a single wide event per request.

    The event is built incrementally during request processing and emitted
    once in a ``finally`` block.  Tail sampling decides whether the event
    is actually printed to stdout.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        event: dict = {}
        start_time = time.monotonic()

        try:
            # ----- Fields from the request -----
            event["timestamp"] = datetime.now(UTC).isoformat()
            event["request_id"] = request.headers.get("cf-ray", str(uuid.uuid4()))
            event["method"] = request.method
            event["path"] = request.url.path

            response = await call_next(request)

            event["status_code"] = response.status_code
            event["outcome"] = "error" if response.status_code >= 400 else "success"

            # Extract user ID after call_next so that inner middleware
            # (e.g. env injection) has already populated request.scope.
            event["user.id"] = await _try_extract_user_id(request)

            return response
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


async def _try_extract_user_id(request: Request) -> str | None:
    """Attempt to extract the authenticated user ID from the session cookie.

    Returns the ``user_id`` string if a valid session exists, or ``None``
    if the cookie is missing, the env binding is unavailable, or the
    session has expired.
    """
    from auth.session import COOKIE_NAME, get_session

    try:
        session_id = request.cookies.get(COOKIE_NAME)
        if not session_id:
            return None

        env = request.scope.get("env")
        if env is None:
            return None

        user_data = await get_session(env.SESSIONS, session_id)
        if user_data is None:
            return None

        return user_data.get("user_id")
    except Exception:
        # Never let user extraction break the request
        return None


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

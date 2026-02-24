"""Wide event accumulator for canonical log lines.

A WideEvent collects timing and metadata from across the request/message
lifecycle and emits a single JSON line when complete.  Infrastructure
timing is added automatically by Safe* wrappers via the contextvar.
Domain-specific fields are added explicitly by pipelines.

Usage::

    from wide_event import current_event

    # Safe* wrappers (automatic):
    evt = current_event()
    if evt:
        evt.record_d1(elapsed_ms)

    # Pipeline code (explicit):
    evt = current_event()
    if evt:
        evt.set("article_id", article_id)
        evt.set("word_count", word_count)
"""

from __future__ import annotations

import contextvars
import json
import random
import time
from datetime import UTC, datetime
from typing import Any

# Tail sampling thresholds
_SLOW_REQUEST_MS = 2000
_SUCCESS_SAMPLE_RATE = 0.05  # 5%

# The contextvar holding the current WideEvent for this async context.
_current_event: contextvars.ContextVar[WideEvent | None] = contextvars.ContextVar(
    "wide_event", default=None
)


def current_event() -> WideEvent | None:
    """Return the WideEvent for the current async context, or ``None``."""
    return _current_event.get()


class WideEvent:
    """Accumulates timing and fields into a single JSON log line.

    Infrastructure counters (wall-clock milliseconds via ``time.monotonic``):

    - ``d1`` — D1 database queries
    - ``r2_get`` / ``r2_put`` / ``r2_del`` — R2 reads / writes / deletes
    - ``kv`` — KV operations
    - ``queue`` — Queue sends
    - ``ai`` — Workers AI calls
    - ``http`` — External HTTP fetches
    - ``svc`` — Service Binding calls (Readability)
    """

    __slots__ = (
        "_fields",
        "_start",
        "_d1_count",
        "_d1_ms",
        "_r2_get_count",
        "_r2_get_ms",
        "_r2_put_count",
        "_r2_put_ms",
        "_r2_del_count",
        "_r2_del_ms",
        "_kv_count",
        "_kv_ms",
        "_queue_count",
        "_queue_ms",
        "_ai_count",
        "_ai_ms",
        "_http_count",
        "_http_ms",
        "_svc_count",
        "_svc_ms",
    )

    def __init__(self, pipeline: str, **initial_fields: Any) -> None:
        self._fields: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "pipeline": pipeline,
        }
        self._fields.update(initial_fields)
        self._start = time.monotonic()

        self._d1_count = 0
        self._d1_ms = 0.0
        self._r2_get_count = 0
        self._r2_get_ms = 0.0
        self._r2_put_count = 0
        self._r2_put_ms = 0.0
        self._r2_del_count = 0
        self._r2_del_ms = 0.0
        self._kv_count = 0
        self._kv_ms = 0.0
        self._queue_count = 0
        self._queue_ms = 0.0
        self._ai_count = 0
        self._ai_ms = 0.0
        self._http_count = 0
        self._http_ms = 0.0
        self._svc_count = 0
        self._svc_ms = 0.0

    # -- Infrastructure recording (called by Safe* wrappers) --

    def record_d1(self, elapsed_ms: float) -> None:
        self._d1_count += 1
        self._d1_ms += elapsed_ms

    def record_r2_get(self, elapsed_ms: float) -> None:
        self._r2_get_count += 1
        self._r2_get_ms += elapsed_ms

    def record_r2_put(self, elapsed_ms: float) -> None:
        self._r2_put_count += 1
        self._r2_put_ms += elapsed_ms

    def record_r2_delete(self, elapsed_ms: float) -> None:
        self._r2_del_count += 1
        self._r2_del_ms += elapsed_ms

    def record_kv(self, elapsed_ms: float) -> None:
        self._kv_count += 1
        self._kv_ms += elapsed_ms

    def record_queue(self, elapsed_ms: float) -> None:
        self._queue_count += 1
        self._queue_ms += elapsed_ms

    def record_ai(self, elapsed_ms: float) -> None:
        self._ai_count += 1
        self._ai_ms += elapsed_ms

    def record_http(self, elapsed_ms: float) -> None:
        self._http_count += 1
        self._http_ms += elapsed_ms

    def record_service_binding(self, elapsed_ms: float) -> None:
        self._svc_count += 1
        self._svc_ms += elapsed_ms

    # -- Domain-specific fields (called by pipeline code) --

    def set(self, key: str, value: Any) -> None:
        """Set a domain-specific field on the event."""
        self._fields[key] = value

    def set_many(self, fields: dict[str, Any]) -> None:
        """Set multiple domain-specific fields at once."""
        self._fields.update(fields)

    # -- Emission --

    def finalize(self) -> dict[str, Any]:
        """Build the final event dict with all counters and fields."""
        duration_ms = (time.monotonic() - self._start) * 1000
        self._fields["duration_ms"] = round(duration_ms, 2)

        # Only include non-zero infrastructure counters
        if self._d1_count:
            self._fields["d1.count"] = self._d1_count
            self._fields["d1.ms"] = round(self._d1_ms, 2)
        if self._r2_get_count:
            self._fields["r2.get.count"] = self._r2_get_count
            self._fields["r2.get.ms"] = round(self._r2_get_ms, 2)
        if self._r2_put_count:
            self._fields["r2.put.count"] = self._r2_put_count
            self._fields["r2.put.ms"] = round(self._r2_put_ms, 2)
        if self._r2_del_count:
            self._fields["r2.del.count"] = self._r2_del_count
            self._fields["r2.del.ms"] = round(self._r2_del_ms, 2)
        if self._kv_count:
            self._fields["kv.count"] = self._kv_count
            self._fields["kv.ms"] = round(self._kv_ms, 2)
        if self._queue_count:
            self._fields["queue.count"] = self._queue_count
            self._fields["queue.ms"] = round(self._queue_ms, 2)
        if self._ai_count:
            self._fields["ai.count"] = self._ai_count
            self._fields["ai.ms"] = round(self._ai_ms, 2)
        if self._http_count:
            self._fields["http.count"] = self._http_count
            self._fields["http.ms"] = round(self._http_ms, 2)
        if self._svc_count:
            self._fields["svc.count"] = self._svc_count
            self._fields["svc.ms"] = round(self._svc_ms, 2)

        return self._fields


def begin_event(pipeline: str, **initial_fields: Any) -> WideEvent:
    """Create a WideEvent and install it as the current contextvar.

    Returns the event so callers can finalize/emit it later.
    """
    evt = WideEvent(pipeline, **initial_fields)
    _current_event.set(evt)
    return evt


def emit_event(event: WideEvent, *, force: bool = False) -> None:
    """Finalize and print the event as JSON, subject to tail sampling.

    Parameters
    ----------
    event:
        The WideEvent to emit.
    force:
        If ``True``, bypass tail sampling and always emit.
    """
    data = event.finalize()
    if force or _should_sample(data):
        print(json.dumps(data))
    _current_event.set(None)


def _should_sample(event: dict) -> bool:
    """Tail sampling: always emit errors/slow, sample successes at 5%."""
    if event.get("status_code", 0) >= 500:
        return True
    if event.get("duration_ms", 0) > _SLOW_REQUEST_MS:
        return True
    if event.get("outcome") == "error":
        return True
    # Queue/scheduled/email pipelines: always emit
    if event.get("pipeline") in ("queue", "scheduled"):
        return True
    return random.random() < _SUCCESS_SAMPLE_RATE

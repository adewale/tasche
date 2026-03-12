"""Tests for the wide event accumulator (src/wide_event.py).

Covers:
- WideEvent field accumulation and finalization
- Infrastructure counter recording (only non-zero counters emitted)
- begin_event / current_event / emit_event lifecycle
- Tail sampling rules (moved here from observability tests)
- contextvar isolation between events
"""

from __future__ import annotations

import json

from src.wide_event import WideEvent, begin_event, current_event, emit_event

# =========================================================================
# WideEvent basic functionality
# =========================================================================


class TestWideEvent:
    """Verify WideEvent accumulates fields correctly."""

    def test_initial_fields_set(self) -> None:
        """WideEvent stores pipeline name and initial fields."""
        evt = WideEvent("http", request_id="abc", method="GET")
        data = evt.finalize()

        assert data["pipeline"] == "http"
        assert data["request_id"] == "abc"
        assert data["method"] == "GET"
        assert "timestamp" in data
        assert "duration_ms" in data

    def test_set_adds_field(self) -> None:
        """set() adds a single field to the event."""
        evt = WideEvent("test")
        evt.set("article_id", "art_123")
        data = evt.finalize()

        assert data["article_id"] == "art_123"

    def test_set_many_adds_multiple_fields(self) -> None:
        """set_many() adds multiple fields at once."""
        evt = WideEvent("test")
        evt.set_many({"word_count": 500, "image_count": 3})
        data = evt.finalize()

        assert data["word_count"] == 500
        assert data["image_count"] == 3

    def test_set_overwrites_existing(self) -> None:
        """set() overwrites a previously-set field."""
        evt = WideEvent("test")
        evt.set("outcome", "pending")
        evt.set("outcome", "success")
        data = evt.finalize()

        assert data["outcome"] == "success"

    def test_duration_ms_is_non_negative(self) -> None:
        """duration_ms should be >= 0."""
        evt = WideEvent("test")
        data = evt.finalize()

        assert data["duration_ms"] >= 0

    def test_timestamp_is_iso_format(self) -> None:
        """timestamp should be ISO 8601 format."""
        evt = WideEvent("test")
        data = evt.finalize()

        from datetime import datetime

        datetime.fromisoformat(data["timestamp"])


# =========================================================================
# Infrastructure counters
# =========================================================================


class TestInfrastructureCounters:
    """Verify infrastructure timing counters work correctly."""

    def test_d1_counter(self) -> None:
        """record_d1 increments count and accumulates ms."""
        evt = WideEvent("test")
        evt.record_d1(5.0)
        evt.record_d1(3.5)
        data = evt.finalize()

        assert data["d1.count"] == 2
        assert data["d1.ms"] == 8.5

    def test_r2_get_counter(self) -> None:
        evt = WideEvent("test")
        evt.record_r2_get(10.0)
        data = evt.finalize()

        assert data["r2.get.count"] == 1
        assert data["r2.get.ms"] == 10.0

    def test_r2_put_counter(self) -> None:
        evt = WideEvent("test")
        evt.record_r2_put(15.0)
        data = evt.finalize()

        assert data["r2.put.count"] == 1
        assert data["r2.put.ms"] == 15.0

    def test_r2_delete_counter(self) -> None:
        evt = WideEvent("test")
        evt.record_r2_delete(2.0)
        data = evt.finalize()

        assert data["r2.del.count"] == 1
        assert data["r2.del.ms"] == 2.0

    def test_kv_counter(self) -> None:
        evt = WideEvent("test")
        evt.record_kv(1.5)
        data = evt.finalize()

        assert data["kv.count"] == 1
        assert data["kv.ms"] == 1.5

    def test_queue_counter(self) -> None:
        evt = WideEvent("test")
        evt.record_queue(0.5)
        data = evt.finalize()

        assert data["queue.count"] == 1
        assert data["queue.ms"] == 0.5

    def test_ai_counter(self) -> None:
        evt = WideEvent("test")
        evt.record_ai(200.0)
        data = evt.finalize()

        assert data["ai.count"] == 1
        assert data["ai.ms"] == 200.0

    def test_http_counter(self) -> None:
        evt = WideEvent("test")
        evt.record_http(50.0)
        data = evt.finalize()

        assert data["http.count"] == 1
        assert data["http.ms"] == 50.0

    def test_service_binding_counter(self) -> None:
        evt = WideEvent("test")
        evt.record_service_binding(3.0)
        data = evt.finalize()

        assert data["svc.count"] == 1
        assert data["svc.ms"] == 3.0

    def test_zero_counters_omitted(self) -> None:
        """Counters that are never recorded should not appear in output."""
        evt = WideEvent("test")
        evt.record_d1(1.0)
        data = evt.finalize()

        assert "d1.count" in data
        assert "r2.get.count" not in data
        assert "kv.count" not in data
        assert "queue.count" not in data
        assert "ai.count" not in data
        assert "http.count" not in data
        assert "svc.count" not in data

    def test_multiple_infrastructure_types(self) -> None:
        """Multiple infrastructure types can be recorded on the same event."""
        evt = WideEvent("test")
        evt.record_d1(5.0)
        evt.record_r2_get(10.0)
        evt.record_kv(1.0)
        data = evt.finalize()

        assert data["d1.count"] == 1
        assert data["r2.get.count"] == 1
        assert data["kv.count"] == 1


# =========================================================================
# begin_event / current_event / emit_event lifecycle
# =========================================================================


class TestEventLifecycle:
    """Verify the contextvar-based event lifecycle."""

    def test_begin_event_sets_current(self) -> None:
        """begin_event creates an event and makes it the current event."""
        evt = begin_event("test", request_id="r1")

        assert current_event() is evt
        assert current_event() is not None

        # Clean up
        emit_event(evt)

    def test_emit_event_clears_current(self, capsys) -> None:
        """emit_event clears the current event after emission."""
        evt = begin_event("test")
        emit_event(evt)

        assert current_event() is None

    def test_emit_event_prints_json(self, capsys) -> None:
        """emit_event prints a JSON line to stdout."""
        evt = begin_event("test", request_id="r2")
        evt.set("outcome", "success")
        emit_event(evt)

        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())

        assert data["pipeline"] == "test"
        assert data["request_id"] == "r2"
        assert data["outcome"] == "success"

    def test_emit_event_always_emits(self, capsys) -> None:
        """emit_event always emits the event."""
        evt = begin_event("test")
        evt.set("status_code", 200)
        evt.set("outcome", "success")

        emit_event(evt)

        captured = capsys.readouterr()
        assert captured.out.strip() != ""

    def test_current_event_returns_none_when_no_event(self) -> None:
        """current_event returns None when no event has been started."""
        # Ensure clean state
        from src.wide_event import _current_event

        _current_event.set(None)

        assert current_event() is None

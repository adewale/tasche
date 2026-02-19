"""Tests for the Worker entrypoint (src/entry.py).

Covers queue dispatch (unknown types, missing fields, handler exceptions,
body.to_py() conversion), the scheduled() health-check method, and SPA
fallback routing logic.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import MockD1, MockEnv

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockMessage:
    """Simulates a single queue message with ack/retry controls."""

    def __init__(self, body: Any) -> None:
        self.body = body
        self.acked = False
        self.retried = False

    def ack(self) -> None:
        self.acked = True

    def retry(self) -> None:
        self.retried = True


class _MockBatch:
    """Simulates a Workers ``MessageBatch`` object."""

    def __init__(self, messages: list[_MockMessage]) -> None:
        self.messages = messages


class _TrackingD1(MockD1):
    """MockD1 that records all SQL statements and supports configurable results."""

    def __init__(self, result_fn: Any | None = None) -> None:
        super().__init__()
        self.executed: list[tuple[str, list[Any]]] = []
        self._result_fn = result_fn

    def _execute(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        self.executed.append((sql, params))
        if self._result_fn is not None:
            return self._result_fn(sql, params)
        return []


# ---------------------------------------------------------------------------
# Queue dispatch — unknown message type
# ---------------------------------------------------------------------------


class TestQueueUnknownType:
    async def test_unknown_type_acks_and_logs(self, capsys: Any) -> None:
        """A message with an unrecognised type is acked and logged."""
        from entry import Default

        worker = Default()
        env = MockEnv()
        worker.env = env

        msg = _MockMessage({"type": "totally_unknown"})
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        assert msg.retried is False

        output = capsys.readouterr().out
        assert "queue_unknown_type" in output
        assert "totally_unknown" in output

    async def test_missing_type_defaults_to_unknown(self, capsys: Any) -> None:
        """A message with no 'type' field is treated as unknown and acked."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg = _MockMessage({"some_field": "value"})
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        output = capsys.readouterr().out
        assert "queue_unknown_type" in output


# ---------------------------------------------------------------------------
# Queue dispatch — missing required fields
# ---------------------------------------------------------------------------


class TestQueueMissingFields:
    async def test_article_processing_missing_article_id(self, capsys: Any) -> None:
        """article_processing without article_id is skipped and acked."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg = _MockMessage(
            {
                "type": "article_processing",
                "url": "https://example.com/page",
            }
        )
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        output = capsys.readouterr().out
        assert "skipped" in output

    async def test_article_processing_missing_url(self, capsys: Any) -> None:
        """article_processing without url is skipped and acked."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg = _MockMessage(
            {
                "type": "article_processing",
                "article_id": "art_123",
            }
        )
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        output = capsys.readouterr().out
        assert "skipped" in output

    async def test_tts_generation_missing_article_id(self, capsys: Any) -> None:
        """tts_generation without article_id is skipped and acked."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg = _MockMessage(
            {
                "type": "tts_generation",
                "user_id": "user_001",
            }
        )
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        output = capsys.readouterr().out
        assert "skipped" in output

    async def test_tts_generation_missing_user_id(self, capsys: Any) -> None:
        """tts_generation without user_id is skipped and acked."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg = _MockMessage(
            {
                "type": "tts_generation",
                "article_id": "art_456",
            }
        )
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        output = capsys.readouterr().out
        assert "skipped" in output


# ---------------------------------------------------------------------------
# Queue dispatch — handler exception triggers retry
# ---------------------------------------------------------------------------


class TestQueueHandlerException:
    async def test_handler_exception_calls_retry(self, capsys: Any) -> None:
        """When a handler raises, the message is retried, not acked."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        msg = _MockMessage(
            {
                "type": "article_processing",
                "article_id": "art_err",
                "url": "https://example.com/page",
            }
        )
        batch = _MockBatch([msg])

        with patch(
            "articles.processing.process_article",
            side_effect=RuntimeError("processing failed"),
        ):
            await worker.queue(batch)

        assert msg.retried is True
        assert msg.acked is False
        output = capsys.readouterr().out
        assert "queue_error" in output


# ---------------------------------------------------------------------------
# Queue dispatch — body.to_py() conversion path
# ---------------------------------------------------------------------------


class TestQueueBodyConversion:
    async def test_body_with_to_py_method(self, capsys: Any) -> None:
        """When body has a to_py() method (JsProxy), it is called for conversion."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        body_dict = {"type": "totally_unknown_type", "data": "test"}
        mock_body = MagicMock()
        mock_body.to_py = MagicMock(return_value=body_dict)
        # hasattr check: to_py exists
        mock_body.configure_mock(**{"to_py.return_value": body_dict})

        msg = _MockMessage(mock_body)
        batch = _MockBatch([msg])

        await worker.queue(batch)

        mock_body.to_py.assert_called_once()
        assert msg.acked is True

    async def test_body_as_json_string(self, capsys: Any) -> None:
        """When body is a JSON string, it is parsed via json.loads."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        body_str = json.dumps({"type": "unknown_str_type"})
        msg = _MockMessage(body_str)
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True
        output = capsys.readouterr().out
        assert "unknown_str_type" in output

    async def test_body_as_plain_dict(self) -> None:
        """When body is already a Python dict, it is used directly via _to_py_safe fallback."""
        from entry import Default

        worker = Default()
        worker.env = MockEnv()

        # A plain dict has no to_py() and is not a string, so _to_py_safe is called
        msg = _MockMessage({"type": "some_other_unknown"})
        batch = _MockBatch([msg])

        await worker.queue(batch)

        assert msg.acked is True


# ---------------------------------------------------------------------------
# scheduled() — health check logic
# ---------------------------------------------------------------------------


class TestScheduled:
    async def test_selects_and_updates_articles(self, capsys: Any) -> None:
        """scheduled() queries for articles to check and updates their status."""
        from entry import Default

        rows = [
            {"id": "art_a", "original_url": "https://example.com/a"},
            {"id": "art_b", "original_url": "https://example.com/b"},
        ]

        def execute(sql: str, params: list) -> list:
            if sql.strip().startswith("SELECT"):
                return rows
            return []

        db = _TrackingD1(result_fn=execute)
        env = MockEnv(db=db)
        worker = Default()
        worker.env = env

        with patch(
            "articles.health.check_original_url",
            new_callable=AsyncMock,
            return_value="available",
        ):
            await worker.scheduled(None)

        # Should have UPDATE statements for both articles
        update_stmts = [
            (sql, params) for sql, params in db.executed if sql.strip().startswith("UPDATE")
        ]
        assert len(update_stmts) == 2

        # Each update should set original_status to 'available'
        for sql, params in update_stmts:
            assert "original_status" in sql
            assert "available" in params

        output = capsys.readouterr().out
        assert "scheduled_health_check" in output
        log_line = json.loads(output.strip())
        assert log_line["checked"] == 2

    async def test_check_original_url_error_defaults_to_unknown(self, capsys: Any) -> None:
        """When check_original_url raises, original_status is set to 'unknown'."""
        from entry import Default

        rows = [{"id": "art_c", "original_url": "https://broken.example.com"}]

        def execute(sql: str, params: list) -> list:
            if sql.strip().startswith("SELECT"):
                return rows
            return []

        db = _TrackingD1(result_fn=execute)
        env = MockEnv(db=db)
        worker = Default()
        worker.env = env

        with patch(
            "articles.health.check_original_url",
            new_callable=AsyncMock,
            side_effect=RuntimeError("DNS failure"),
        ):
            await worker.scheduled(None)

        update_stmts = [
            (sql, params) for sql, params in db.executed if sql.strip().startswith("UPDATE")
        ]
        assert len(update_stmts) == 1
        sql, params = update_stmts[0]
        assert "unknown" in params

    async def test_scheduled_handles_top_level_error(self, capsys: Any) -> None:
        """When scheduled() has an unexpected top-level error, it logs and does not raise."""
        from entry import Default

        worker = Default()
        # Give env a DB that fails on any query
        mock_db = MagicMock()
        mock_db.prepare.side_effect = RuntimeError("DB unavailable")
        env = MockEnv()
        env.DB = mock_db
        worker.env = env

        # Should not raise — error is caught and logged
        await worker.scheduled(None)

        output = capsys.readouterr().out
        assert "scheduled_error" in output

    async def test_scheduled_no_articles_to_check(self, capsys: Any) -> None:
        """When no articles match the query, scheduled() still logs checked=0."""
        from entry import Default

        db = _TrackingD1(result_fn=lambda sql, params: [])
        env = MockEnv(db=db)
        worker = Default()
        worker.env = env

        await worker.scheduled(None)

        output = capsys.readouterr().out
        assert "scheduled_health_check" in output
        log_line = json.loads(output.strip())
        assert log_line["checked"] == 0


# ---------------------------------------------------------------------------
# SPA fallback routing
# ---------------------------------------------------------------------------


class TestSPAFallbackRouting:
    async def test_api_routes_go_to_fastapi(self) -> None:
        """Requests to /api/* are handled by the FastAPI ASGI app."""
        import sys

        from entry import Default

        worker = Default()
        env = MockEnv()
        worker.env = env

        # Mock URL parsing and ASGI fetch
        mock_url = MagicMock()
        mock_url.pathname = "/api/articles"

        mock_request = MagicMock()
        mock_request.url = "https://tasche.test/api/articles"
        mock_request.js_object = mock_request

        mock_response = MagicMock()

        mock_URL_cls = MagicMock()
        mock_URL_cls.new = MagicMock(return_value=mock_url)
        mock_Request_cls = MagicMock()

        mock_js = MagicMock(URL=mock_URL_cls, Request=mock_Request_cls)

        # Temporarily inject the mock js module so `from js import URL` works
        old_js = sys.modules.get("js")
        sys.modules["js"] = mock_js
        try:
            with patch("entry.asgi") as mock_asgi:
                mock_asgi.fetch = AsyncMock(return_value=mock_response)
                result = await worker.fetch(mock_request)

            mock_asgi.fetch.assert_called_once()
            assert result == mock_response
        finally:
            if old_js is None:
                sys.modules.pop("js", None)
            else:
                sys.modules["js"] = old_js

    async def test_non_api_routes_go_to_assets(self) -> None:
        """Non-/api/ requests are served from the ASSETS binding."""
        import sys

        from entry import Default

        worker = Default()
        env = MockEnv()
        # Add an ASSETS binding
        mock_assets = MagicMock()
        asset_response = MagicMock()
        asset_response.status = 200
        mock_assets.fetch = AsyncMock(return_value=asset_response)
        env.ASSETS = mock_assets
        worker.env = env

        mock_url = MagicMock()
        mock_url.pathname = "/about"

        mock_request = MagicMock()
        mock_request.url = "https://tasche.test/about"
        mock_request.js_object = mock_request

        mock_URL_cls = MagicMock()
        mock_URL_cls.new = MagicMock(return_value=mock_url)
        mock_Request_cls = MagicMock()

        mock_js = MagicMock(URL=mock_URL_cls, Request=mock_Request_cls)

        old_js = sys.modules.get("js")
        sys.modules["js"] = mock_js
        try:
            result = await worker.fetch(mock_request)
        finally:
            if old_js is None:
                sys.modules.pop("js", None)
            else:
                sys.modules["js"] = old_js

        mock_assets.fetch.assert_called_once_with(mock_request.js_object)
        assert result == asset_response

    async def test_404_falls_back_to_index_html(self) -> None:
        """When ASSETS returns 404, the SPA fallback serves /index.html."""
        import sys

        from entry import Default

        worker = Default()
        env = MockEnv()

        # First fetch returns 404, second (index.html) returns 200
        asset_404 = MagicMock()
        asset_404.status = 404
        index_response = MagicMock()
        index_response.status = 200

        mock_assets = MagicMock()
        mock_assets.fetch = AsyncMock(side_effect=[asset_404, index_response])
        env.ASSETS = mock_assets
        worker.env = env

        mock_url = MagicMock()
        mock_url.pathname = "/some/spa/route"

        mock_index_url = MagicMock()
        mock_js_request = MagicMock()

        mock_URL_cls = MagicMock()
        mock_URL_cls.new = MagicMock(side_effect=[mock_url, mock_index_url])

        mock_Request_cls = MagicMock()
        mock_Request_cls.new = MagicMock(return_value=mock_js_request)

        mock_request = MagicMock()
        mock_request.url = "https://tasche.test/some/spa/route"
        mock_request.js_object = mock_request

        mock_js = MagicMock(URL=mock_URL_cls, Request=mock_Request_cls)

        old_js = sys.modules.get("js")
        sys.modules["js"] = mock_js
        try:
            result = await worker.fetch(mock_request)
        finally:
            if old_js is None:
                sys.modules.pop("js", None)
            else:
                sys.modules["js"] = old_js

        # Second call should be the index.html fallback
        assert mock_assets.fetch.call_count == 2
        assert result == index_response

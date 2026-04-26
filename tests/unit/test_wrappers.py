"""Tests for src/boundary/__init__.py — the FFI boundary layer.

These tests run under standard CPython (pytest) where Pyodide is NOT
available.  They verify that every helper degrades gracefully and that
the non-Pyodide code paths produce correct results.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.boundary import (
    HAS_PYODIDE,
    HttpError,
    HttpResponse,
    JsException,
    SafeAI,
    SafeD1,
    SafeEnv,
    SafeKV,
    SafeQueue,
    SafeR2,
    SafeReadability,
    _is_js_null_or_undefined,
    _is_js_undefined,
    consume_readable_stream,
    d1_first,
    d1_null,
    d1_rows,
    get_js_null,
    get_r2_size,
    http_fetch,
    stream_r2_body,
    to_py_bytes,
)

# =========================================================================
# Sanity: Pyodide is NOT available in the test environment
# =========================================================================


class TestPyodideGuard:
    def test_has_pyodide_is_false_in_tests(self) -> None:
        """HAS_PYODIDE must be False when running under pytest/CPython."""
        assert HAS_PYODIDE is False

    def test_get_js_null_returns_none_outside_pyodide(self) -> None:
        """Outside Pyodide, get_js_null() returns None."""
        assert get_js_null() is None


# =========================================================================
# _is_js_undefined
# =========================================================================


class TestIsJsUndefined:
    def test_none_is_undefined_outside_pyodide(self) -> None:
        """Outside Pyodide, None is treated as 'undefined'."""
        assert _is_js_undefined(None) is True

    def test_string_is_not_undefined(self) -> None:
        assert _is_js_undefined("hello") is False

    def test_zero_is_not_undefined(self) -> None:
        assert _is_js_undefined(0) is False

    def test_empty_string_is_not_undefined(self) -> None:
        assert _is_js_undefined("") is False

    def test_false_is_not_undefined(self) -> None:
        assert _is_js_undefined(False) is False


class TestIsJsNullOrUndefined:
    def test_none_is_null_or_undefined(self) -> None:
        """Outside Pyodide, None maps to null/undefined."""
        assert _is_js_null_or_undefined(None) is True

    def test_string_is_not(self) -> None:
        assert _is_js_null_or_undefined("hello") is False

    def test_zero_is_not(self) -> None:
        assert _is_js_null_or_undefined(0) is False

    def test_empty_dict_is_not(self) -> None:
        assert _is_js_null_or_undefined({}) is False

    def test_false_is_not(self) -> None:
        assert _is_js_null_or_undefined(False) is False


# =========================================================================
# SafeEnv
# =========================================================================


class TestSafeEnv:
    def test_get_present_value(self) -> None:
        """get() returns the attribute value when it exists."""
        raw = SimpleNamespace(SITE_URL="https://tasche.test", DB="mock_db")
        env = SafeEnv(raw)
        assert env.get("SITE_URL") == "https://tasche.test"
        assert env.get("DB") == "mock_db"

    def test_get_missing_returns_default(self) -> None:
        """get() returns default when the attribute does not exist."""
        raw = SimpleNamespace()
        env = SafeEnv(raw)
        assert env.get("MISSING_KEY") is None
        assert env.get("MISSING_KEY", "fallback") == "fallback"

    def test_get_none_returns_default(self) -> None:
        """get() returns default when the attribute is None (JS undefined)."""
        raw = SimpleNamespace(OPTIONAL_VAR=None)
        env = SafeEnv(raw)
        assert env.get("OPTIONAL_VAR", "default") == "default"

    def test_getattr_proxies_to_underlying(self) -> None:
        """Attribute access proxies to the underlying env object for non-binding attrs."""
        raw = SimpleNamespace(CUSTOM_VAR="custom_value")
        env = SafeEnv(raw)
        assert env.CUSTOM_VAR == "custom_value"

    def test_bindings_wrapped_at_init(self) -> None:
        """Known bindings (DB, CONTENT, etc.) are wrapped in Safe* classes."""
        from tests.conftest import MockAI, MockD1, MockKV, MockQueue, MockR2, MockReadability

        raw = SimpleNamespace(
            DB=MockD1(),
            CONTENT=MockR2(),
            SESSIONS=MockKV(),
            ARTICLE_QUEUE=MockQueue(),
            AI=MockAI(),
            READABILITY=MockReadability(),
        )
        env = SafeEnv(raw)
        assert isinstance(env.DB, SafeD1)
        assert isinstance(env.CONTENT, SafeR2)
        assert isinstance(env.SESSIONS, SafeKV)
        assert isinstance(env.ARTICLE_QUEUE, SafeQueue)
        assert isinstance(env.AI, SafeAI)
        assert isinstance(env.READABILITY, SafeReadability)

    def test_idempotent_wrapping(self) -> None:
        """Wrapping an already-wrapped SafeEnv returns the same wrappers."""
        from tests.conftest import MockD1, MockR2

        raw = SimpleNamespace(DB=MockD1(), CONTENT=MockR2())
        env1 = SafeEnv(raw)
        env2 = SafeEnv(env1)
        assert env2.DB is env1.DB
        assert env2.CONTENT is env1.CONTENT

    def test_getattr_raises_for_private(self) -> None:
        """Private attributes (starting with _) raise AttributeError."""
        raw = SimpleNamespace()
        env = SafeEnv(raw)
        with pytest.raises(AttributeError):
            _ = env._private

    def test_get_empty_string_is_not_treated_as_missing(self) -> None:
        """An empty string is a valid value, not treated as missing."""
        raw = SimpleNamespace(ALLOWED_EMAILS="")
        env = SafeEnv(raw)
        assert env.get("ALLOWED_EMAILS", "default") == ""


# =========================================================================
# d1_rows
# =========================================================================


class TestD1Rows:
    def test_none_returns_empty_list(self) -> None:
        assert d1_rows(None) == []

    def test_dict_with_results(self) -> None:
        """Standard D1 .all() response shape."""
        results = {
            "results": [
                {"id": "1", "title": "First"},
                {"id": "2", "title": "Second"},
            ]
        }
        rows = d1_rows(results)
        assert len(rows) == 2
        assert rows[0] == {"id": "1", "title": "First"}
        assert rows[1] == {"id": "2", "title": "Second"}

    def test_dict_with_empty_results(self) -> None:
        results = {"results": []}
        assert d1_rows(results) == []

    def test_dict_with_none_results(self) -> None:
        results = {"results": None}
        assert d1_rows(results) == []

    def test_object_with_results_attribute(self) -> None:
        """Some D1 responses may use attribute access instead of dict."""
        obj = SimpleNamespace(results=[{"id": "1"}])
        rows = d1_rows(obj)
        assert len(rows) == 1
        assert rows[0] == {"id": "1"}


# =========================================================================
# d1_first
# =========================================================================


class TestD1First:
    def test_none_returns_none(self) -> None:
        assert d1_first(None) is None

    def test_dict_returns_dict(self) -> None:
        row = {"id": "abc", "title": "Test Article"}
        assert d1_first(row) == row

    def test_empty_dict_returns_none(self) -> None:
        """An empty dict has no useful row data — should return None."""
        assert d1_first({}) is None

    def test_namespace_converted_to_dict(self) -> None:
        """SimpleNamespace (simulating a JsProxy after to_py) is dict-ifiable."""
        obj = SimpleNamespace(id="1", title="Hello")
        result = d1_first(obj)
        assert result == {"id": "1", "title": "Hello"}

    def test_unwraps_result_wrapper_with_rows(self) -> None:
        """D1 .first() in Pyodide may return the full result wrapper.

        When the wrapper contains rows, d1_first should return the
        first row — not the wrapper dict itself.
        """
        wrapped = {
            "results": [{"id": "abc", "created_at": "2025-01-01", "status": "pending"}],
            "success": True,
            "meta": {"changes": 0},
        }
        result = d1_first(wrapped)
        assert result is not None
        assert result["id"] == "abc"
        assert "success" not in result
        assert "meta" not in result

    def test_unwraps_empty_result_wrapper(self) -> None:
        """When the result wrapper has no rows, d1_first returns None."""
        wrapped = {
            "results": [],
            "success": True,
            "meta": {"changes": 0},
        }
        assert d1_first(wrapped) is None

    def test_unwraps_result_wrapper_with_none_results(self) -> None:
        """Handle wrapper where results is None."""
        wrapped = {"results": None, "success": True}
        assert d1_first(wrapped) is None

    def test_list_returns_first_element(self) -> None:
        """If .first() returns a list, extract the first element."""
        result = d1_first([{"id": "x", "title": "Row"}])
        assert result == {"id": "x", "title": "Row"}

    def test_empty_list_returns_none(self) -> None:
        """An empty list means no rows — return None."""
        assert d1_first([]) is None

    def test_empty_namespace_returns_none(self) -> None:
        """A SimpleNamespace with no attributes (simulating JsNull.__dict__)
        should return None, not an empty dict."""
        obj = SimpleNamespace()
        assert d1_first(obj) is None


# =========================================================================
# d1_rows edge cases
# =========================================================================


class TestD1RowsEdgeCases:
    def test_bare_list_returned_directly(self) -> None:
        """If .all() returns a bare list (no wrapper), use it."""
        rows = d1_rows([{"id": "1"}, {"id": "2"}])
        assert len(rows) == 2
        assert rows[0] == {"id": "1"}

    def test_none_rows_filtered_out(self) -> None:
        """None entries in the results array are silently skipped."""
        results = {"results": [{"id": "1"}, None, {"id": "3"}]}
        rows = d1_rows(results)
        assert len(rows) == 2
        assert rows[0] == {"id": "1"}
        assert rows[1] == {"id": "3"}

    def test_empty_bare_list(self) -> None:
        assert d1_rows([]) == []


# =========================================================================
# to_py_bytes
# =========================================================================


class TestToPyBytes:
    def test_bytes_passthrough(self) -> None:
        """Python bytes pass through unchanged."""
        data = b"hello world"
        assert to_py_bytes(data) is data

    def test_none_returns_empty_bytes(self) -> None:
        assert to_py_bytes(None) == b""

    def test_bytearray_converted(self) -> None:
        """bytearray is converted to bytes."""
        data = bytearray(b"test")
        result = to_py_bytes(data)
        assert result == b"test"
        assert isinstance(result, bytes)

    def test_memoryview_converted(self) -> None:
        """memoryview is converted to bytes."""
        data = memoryview(b"test")
        result = to_py_bytes(data)
        assert result == b"test"
        assert isinstance(result, bytes)


# =========================================================================
# JsException stub
# =========================================================================


class TestJsException:
    def test_is_exception_subclass(self) -> None:
        """JsException stub must be an Exception subclass."""
        assert issubclass(JsException, Exception)

    def test_can_be_raised_and_caught(self) -> None:
        """JsException can be used in try/except."""
        with pytest.raises(JsException):
            raise JsException("test error")


# =========================================================================
# d1_null — None→null conversion for D1 bind parameters
# =========================================================================


class TestD1Null:
    def test_string_passthrough(self) -> None:
        assert d1_null("hello") == "hello"

    def test_int_passthrough(self) -> None:
        assert d1_null(42) == 42

    def test_zero_passthrough(self) -> None:
        """0 is a valid value, not None — must not be converted to null."""
        assert d1_null(0) == 0

    def test_empty_string_passthrough(self) -> None:
        """Empty string is a valid value, not None."""
        assert d1_null("") == ""

    def test_false_passthrough(self) -> None:
        """False is a valid value, not None."""
        assert d1_null(False) is False


# =========================================================================
# consume_readable_stream — JS ReadableStream/ArrayBuffer → Python bytes
# =========================================================================


class TestConsumeReadableStream:
    async def test_plain_bytes_passthrough(self) -> None:
        """Plain bytes are returned unchanged."""
        data = b"hello world"
        result = await consume_readable_stream(data)
        assert result == b"hello world"
        assert isinstance(result, bytes)

    async def test_none_returns_empty_bytes(self) -> None:
        result = await consume_readable_stream(None)
        assert result == b""

    async def test_bytearray_converted(self) -> None:
        data = bytearray(b"test data")
        result = await consume_readable_stream(data)
        assert result == b"test data"
        assert isinstance(result, bytes)

    async def test_object_with_array_buffer(self) -> None:
        """Objects with only .arrayBuffer() (no getReader) are consumed via arrayBuffer."""

        class ArrayBufferOnly:
            async def arrayBuffer(self):
                return b"audio data"

        result = await consume_readable_stream(ArrayBufferOnly())
        assert result == b"audio data"

    async def test_object_without_array_buffer(self) -> None:
        """Objects without .arrayBuffer() fall through to to_py_bytes."""
        data = memoryview(b"raw bytes")
        result = await consume_readable_stream(data)
        assert result == b"raw bytes"

    async def test_array_buffer_returns_bytearray(self) -> None:
        """When arrayBuffer() returns bytearray, it is converted to bytes."""

        class ArrayBufferBytearray:
            async def arrayBuffer(self):
                return bytearray(b"chunk")

        result = await consume_readable_stream(ArrayBufferBytearray())
        assert result == b"chunk"
        assert isinstance(result, bytes)


# =========================================================================
# stream_r2_body — async generator for R2 ReadableStream chunks
# =========================================================================


def _make_reader_result(*, done: bool, value: bytes | None = None) -> SimpleNamespace:
    """Create a mock ReadableStream reader result."""
    return SimpleNamespace(done=done, value=value)


class TestStreamR2Body:
    async def test_streams_readable_stream_chunks(self) -> None:
        """When R2 object has a body with getReader, yields chunks."""
        chunk1 = b"first chunk"
        chunk2 = b"second chunk"

        reader = MagicMock()
        reader.read = AsyncMock(
            side_effect=[
                _make_reader_result(done=False, value=chunk1),
                _make_reader_result(done=False, value=chunk2),
                _make_reader_result(done=True),
            ]
        )
        reader.releaseLock = MagicMock()

        body = MagicMock()
        body.getReader = MagicMock(return_value=reader)

        r2_obj = SimpleNamespace(body=body)

        chunks = []
        async for chunk in stream_r2_body(r2_obj):
            chunks.append(chunk)

        assert chunks == [b"first chunk", b"second chunk"]
        reader.releaseLock.assert_called_once()

    async def test_releases_lock_on_exception(self) -> None:
        """Reader lock is released even if an error occurs during reading."""
        reader = MagicMock()
        reader.read = AsyncMock(side_effect=RuntimeError("read failed"))
        reader.releaseLock = MagicMock()

        body = MagicMock()
        body.getReader = MagicMock(return_value=reader)

        r2_obj = SimpleNamespace(body=body)

        with pytest.raises(RuntimeError, match="read failed"):
            async for _ in stream_r2_body(r2_obj):
                pass

        reader.releaseLock.assert_called_once()

    async def test_skips_none_chunks(self) -> None:
        """Chunks where value is None are silently skipped."""
        reader = MagicMock()
        reader.read = AsyncMock(
            side_effect=[
                _make_reader_result(done=False, value=b"data"),
                _make_reader_result(done=False, value=None),
                _make_reader_result(done=True),
            ]
        )
        reader.releaseLock = MagicMock()

        body = MagicMock()
        body.getReader = MagicMock(return_value=reader)

        r2_obj = SimpleNamespace(body=body)

        chunks = []
        async for chunk in stream_r2_body(r2_obj):
            chunks.append(chunk)

        assert chunks == [b"data"]

    async def test_fallback_no_body(self) -> None:
        """When R2 object has no body, falls back to consume_readable_stream."""
        r2_obj = MagicMock(spec=[])  # No attributes at all
        r2_obj.arrayBuffer = AsyncMock(return_value=b"full content")

        chunks = []
        async for chunk in stream_r2_body(r2_obj):
            chunks.append(chunk)

        assert chunks == [b"full content"]

    async def test_fallback_body_without_get_reader(self) -> None:
        """When body exists but has no getReader, falls back to full load."""

        class R2ObjWithArrayBuffer:
            """R2 object whose body has no getReader — falls back to arrayBuffer."""

            body = SimpleNamespace()  # body exists but no getReader

            async def arrayBuffer(self):
                return b"buffered"

        chunks = []
        async for chunk in stream_r2_body(R2ObjWithArrayBuffer()):
            chunks.append(chunk)

        assert chunks == [b"buffered"]

    async def test_single_chunk_stream(self) -> None:
        """A stream that yields exactly one chunk works correctly."""
        reader = MagicMock()
        reader.read = AsyncMock(
            side_effect=[
                _make_reader_result(done=False, value=b"only chunk"),
                _make_reader_result(done=True),
            ]
        )
        reader.releaseLock = MagicMock()

        body = MagicMock()
        body.getReader = MagicMock(return_value=reader)

        r2_obj = SimpleNamespace(body=body)

        chunks = []
        async for chunk in stream_r2_body(r2_obj):
            chunks.append(chunk)

        assert chunks == [b"only chunk"]

    async def test_empty_stream(self) -> None:
        """A stream that is immediately done yields nothing."""
        reader = MagicMock()
        reader.read = AsyncMock(
            return_value=_make_reader_result(done=True),
        )
        reader.releaseLock = MagicMock()

        body = MagicMock()
        body.getReader = MagicMock(return_value=reader)

        r2_obj = SimpleNamespace(body=body)

        chunks = []
        async for chunk in stream_r2_body(r2_obj):
            chunks.append(chunk)

        assert chunks == []
        reader.releaseLock.assert_called_once()


# =========================================================================
# get_r2_size — extract size from R2 object
# =========================================================================


class TestGetR2Size:
    def test_returns_size_as_int(self) -> None:
        r2_obj = SimpleNamespace(size=12345)
        assert get_r2_size(r2_obj) == 12345

    def test_returns_none_when_no_size(self) -> None:
        r2_obj = SimpleNamespace()
        assert get_r2_size(r2_obj) is None

    def test_returns_none_when_size_is_none(self) -> None:
        r2_obj = SimpleNamespace(size=None)
        assert get_r2_size(r2_obj) is None

    def test_converts_string_size(self) -> None:
        """Some JS interop may return size as a string-like number."""
        r2_obj = SimpleNamespace(size="4096")
        assert get_r2_size(r2_obj) == 4096

    def test_zero_size(self) -> None:
        """Zero is a valid size (empty file)."""
        r2_obj = SimpleNamespace(size=0)
        assert get_r2_size(r2_obj) == 0


# =========================================================================
# HttpError
# =========================================================================


class TestHttpError:
    def test_is_exception_subclass(self) -> None:
        assert issubclass(HttpError, Exception)

    def test_status_code_attribute(self) -> None:
        err = HttpError(404, "Not Found")
        assert err.status_code == 404

    def test_message_formatting(self) -> None:
        err = HttpError(500, "Internal Server Error")
        assert "500" in str(err)
        assert "Internal Server Error" in str(err)

    def test_empty_message(self) -> None:
        err = HttpError(403)
        assert err.status_code == 403
        assert "403" in str(err)

    def test_can_be_caught(self) -> None:
        with pytest.raises(HttpError) as exc_info:
            raise HttpError(502, "Bad Gateway")
        assert exc_info.value.status_code == 502


# =========================================================================
# HttpResponse
# =========================================================================


class TestHttpResponse:
    def test_json_parsing(self) -> None:
        resp = HttpResponse(
            status_code=200,
            _body=b'{"key": "value", "count": 42}',
        )
        data = resp.json()
        assert data == {"key": "value", "count": 42}

    def test_json_parsing_array(self) -> None:
        resp = HttpResponse(status_code=200, _body=b"[1, 2, 3]")
        assert resp.json() == [1, 2, 3]

    def test_text_property(self) -> None:
        resp = HttpResponse(status_code=200, _body=b"Hello World")
        assert resp.text == "Hello World"

    def test_text_utf8(self) -> None:
        resp = HttpResponse(status_code=200, _body="Ünïcödé".encode())
        assert resp.text == "Ünïcödé"

    def test_content_property(self) -> None:
        body = b"\x89PNG\r\n\x1a\n"
        resp = HttpResponse(status_code=200, _body=body)
        assert resp.content == body
        assert isinstance(resp.content, bytes)

    def test_headers_with_values(self) -> None:
        resp = HttpResponse(
            status_code=200,
            _body=b"",
            _headers={"content-type": "application/json", "x-custom": "value"},
        )
        assert resp.headers["content-type"] == "application/json"
        assert resp.headers["x-custom"] == "value"

    def test_headers_when_none(self) -> None:
        resp = HttpResponse(status_code=200, _body=b"")
        assert resp.headers == {}

    def test_url_attribute(self) -> None:
        resp = HttpResponse(status_code=200, _body=b"", url="https://example.com/page")
        assert resp.url == "https://example.com/page"

    def test_url_default_empty(self) -> None:
        resp = HttpResponse(status_code=200, _body=b"")
        assert resp.url == ""

    def test_raise_for_status_200(self) -> None:
        resp = HttpResponse(status_code=200, _body=b"OK")
        resp.raise_for_status()  # Should not raise

    def test_raise_for_status_201(self) -> None:
        resp = HttpResponse(status_code=201, _body=b"Created")
        resp.raise_for_status()  # Should not raise

    def test_raise_for_status_301(self) -> None:
        resp = HttpResponse(status_code=301, _body=b"Moved")
        resp.raise_for_status()  # 3xx should not raise

    def test_raise_for_status_400(self) -> None:
        resp = HttpResponse(status_code=400, _body=b"Bad Request")
        with pytest.raises(HttpError) as exc_info:
            resp.raise_for_status()
        assert exc_info.value.status_code == 400

    def test_raise_for_status_404(self) -> None:
        resp = HttpResponse(status_code=404, _body=b"Not Found")
        with pytest.raises(HttpError) as exc_info:
            resp.raise_for_status()
        assert exc_info.value.status_code == 404

    def test_raise_for_status_500(self) -> None:
        resp = HttpResponse(status_code=500, _body=b"Internal Server Error")
        with pytest.raises(HttpError) as exc_info:
            resp.raise_for_status()
        assert exc_info.value.status_code == 500

    def test_raise_for_status_truncates_long_body(self) -> None:
        """Error message should not include the entire response body."""
        long_body = b"x" * 1000
        resp = HttpResponse(status_code=500, _body=long_body)
        with pytest.raises(HttpError) as exc_info:
            resp.raise_for_status()
        # The text[:500] truncation in raise_for_status
        assert len(str(exc_info.value)) < 600


# =========================================================================
# http_fetch — CPython path (uses httpx)
# =========================================================================


class TestHttpFetch:
    """Tests for http_fetch's CPython code path (httpx-based)."""

    async def test_get_request(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"response body"
        mock_resp.url = "https://example.com/"
        mock_resp.headers = {"content-type": "text/html"}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await http_fetch("https://example.com/")

        assert resp.status_code == 200
        assert resp.text == "response body"
        assert resp.url == "https://example.com/"

    async def test_post_with_json(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.content = b'{"id": "abc"}'
        mock_resp.url = "https://api.example.com/items"
        mock_resp.headers = {}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await http_fetch(
                "https://api.example.com/items",
                method="POST",
                json_data={"title": "Test"},
            )

        assert resp.status_code == 201
        # Verify JSON content-type was set
        call_args = mock_client.request.call_args
        assert call_args.kwargs["headers"]["Content-Type"] == "application/json"
        # Verify body contains serialized JSON
        assert '"title"' in call_args.kwargs["content"]

    async def test_post_with_form_data(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"ok"
        mock_resp.url = "https://example.com/form"
        mock_resp.headers = {}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            resp = await http_fetch(
                "https://example.com/form",
                method="POST",
                form_data={"grant_type": "authorization_code", "code": "abc123"},
            )

        assert resp.status_code == 200
        call_args = mock_client.request.call_args
        assert call_args.kwargs["headers"]["Content-Type"] == "application/x-www-form-urlencoded"

    async def test_custom_headers(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b""
        mock_resp.url = "https://api.example.com/"
        mock_resp.headers = {}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await http_fetch(
                "https://api.example.com/",
                headers={"Authorization": "Bearer token123"},
            )

        call_args = mock_client.request.call_args
        assert call_args.kwargs["headers"]["Authorization"] == "Bearer token123"

    async def test_timeout_raises_timeout_error(self) -> None:
        import httpx

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(TimeoutError):
                await http_fetch("https://slow.example.com/", timeout=1.0)

    async def test_connection_error_raises_connection_error(self) -> None:
        import httpx

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ConnectionError):
                await http_fetch("https://down.example.com/")

    async def test_follow_redirects_default_true(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b""
        mock_resp.url = "https://example.com/final"
        mock_resp.headers = {}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
            await http_fetch("https://example.com/redirect")

        # Verify follow_redirects=True was passed to AsyncClient
        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["follow_redirects"] is True

    async def test_follow_redirects_false(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 302
        mock_resp.content = b""
        mock_resp.url = "https://example.com/login"
        mock_resp.headers = {"location": "https://example.com/callback"}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
            resp = await http_fetch("https://example.com/login", follow_redirects=False)

        assert resp.status_code == 302
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["follow_redirects"] is False


# =========================================================================
# SafeReadability
# =========================================================================


class TestSafeReadability:
    async def test_parse_returns_dict(self) -> None:
        """SafeReadability.parse() returns a plain Python dict."""
        mock_binding = AsyncMock()
        mock_binding.parse.return_value = {
            "title": "Test",
            "html": "<p>Content</p>",
            "excerpt": "Content",
            "byline": "Author",
        }
        wrapper = SafeReadability(mock_binding)
        result = await wrapper.parse("<html>...</html>", "https://example.com")
        assert result == {
            "title": "Test",
            "html": "<p>Content</p>",
            "excerpt": "Content",
            "byline": "Author",
        }
        mock_binding.parse.assert_awaited_once_with("<html>...</html>", "https://example.com")

    async def test_parse_handles_null_byline(self) -> None:
        """SafeReadability.parse() handles null byline correctly."""
        mock_binding = AsyncMock()
        mock_binding.parse.return_value = {
            "title": "No Author",
            "html": "<p>Content</p>",
            "excerpt": "Content",
            "byline": None,
        }
        wrapper = SafeReadability(mock_binding)
        result = await wrapper.parse("<html>...</html>", "https://example.com")
        assert result["byline"] is None

    def test_safe_env_none_readability(self) -> None:
        """SafeEnv with no READABILITY attribute sets it to None."""
        raw = SimpleNamespace()
        env = SafeEnv(raw)
        assert env.READABILITY is None

    def test_safe_env_idempotent_readability(self) -> None:
        """Wrapping an already-wrapped SafeEnv preserves READABILITY."""
        from tests.conftest import MockReadability

        raw = SimpleNamespace(READABILITY=MockReadability())
        env1 = SafeEnv(raw)
        env2 = SafeEnv(env1)
        assert env2.READABILITY is env1.READABILITY

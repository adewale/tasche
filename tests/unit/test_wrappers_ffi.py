"""FFI boundary contract tests — exercise the REAL Pyodide code paths.

The existing test_wrappers.py runs with HAS_PYODIDE=False, so it only tests
the CPython fallback path.  These tests monkeypatch HAS_PYODIDE=True and use
JS-type fakes (FakeJsProxy, JsNull, fake js module) to exercise the actual
conversion logic that runs in production.

These tests would have caught the 3 historical production bugs:
  1. JsNull leaking through _to_py_safe (JsNull is NOT a JsProxy)
  2. Python None → JS undefined breaking D1 .bind()
  3. Python bytes → PyProxy breaking R2 .put()
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import src.wrappers as wrappers_mod

# =========================================================================
# Fake JS types — simulate Pyodide's JsProxy / JsNull / js module in CPython
# =========================================================================


class FakeJsProxy:
    """Simulates pyodide.ffi.JsProxy.  Has .to_py() like the real thing."""

    def __init__(self, py_value: Any = None) -> None:
        self._py_value = py_value

    def to_py(self) -> Any:
        return self._py_value


class JsNull:
    """Simulates Pyodide's JsNull sentinel.

    Critical: type(x).__name__ == "JsNull" must be True.
    Critical: JsNull is NOT a JsProxy subclass — isinstance(x, JsProxy) is False.
    """

    pass


class _Undefined:
    """Singleton representing JS undefined."""

    _instance = None

    def __new__(cls) -> _Undefined:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "js.undefined"


class _FakeJSON:
    """Simulates js.JSON with .parse("null") returning JsNull."""

    def parse(self, text: str) -> Any:
        if text == "null":
            return JsNull()
        raise ValueError(f"FakeJSON cannot parse: {text}")


class _FakeObject:
    """Simulates js.Object with .fromEntries()."""

    @staticmethod
    def fromEntries(entries: Any) -> dict:
        # In real Pyodide, Object.fromEntries converts an iterable of
        # [key, value] pairs to a plain JS Object.  Our fake just returns
        # the dict since to_js will call this as dict_converter.
        if isinstance(entries, dict):
            return entries
        return dict(entries)


class FakeJsModule:
    """Simulates the `js` module available in Pyodide."""

    def __init__(self) -> None:
        self.undefined = _Undefined()
        self.JSON = _FakeJSON()
        self.Object = _FakeObject()


def fake_to_js(value: Any, *, dict_converter: Any = None) -> Any:
    """Simulates pyodide.ffi.to_js().

    - For dicts: applies dict_converter if provided (like Object.fromEntries)
    - For bytes/bytearray/memoryview: wraps in FakeJsProxy (simulates Uint8Array)
    - For lists: applies dict_converter recursively to nested dicts
    """
    if isinstance(value, dict):
        if dict_converter is not None:
            converted = {k: fake_to_js(v, dict_converter=dict_converter) for k, v in value.items()}
            return dict_converter(converted)
        return value
    if isinstance(value, (list, tuple)):
        items = [fake_to_js(v, dict_converter=dict_converter) for v in value]
        return items
    if isinstance(value, (bytes, bytearray, memoryview)):
        return FakeJsProxy(bytes(value))
    return value


# =========================================================================
# Fixture: monkeypatch wrappers module to simulate Pyodide environment
# =========================================================================


@pytest.fixture()
def pyodide_fakes(monkeypatch: pytest.MonkeyPatch) -> FakeJsModule:
    """Patch src.wrappers to behave as if running inside Pyodide.

    Patches 4 module-level globals:
      - HAS_PYODIDE → True
      - JsProxy → FakeJsProxy
      - js → FakeJsModule()
      - to_js → fake_to_js
    """
    fake_js = FakeJsModule()
    monkeypatch.setattr(wrappers_mod, "HAS_PYODIDE", True)
    monkeypatch.setattr(wrappers_mod, "JsProxy", FakeJsProxy)
    monkeypatch.setattr(wrappers_mod, "js", fake_js)
    monkeypatch.setattr(wrappers_mod, "to_js", fake_to_js)
    return fake_js


# =========================================================================
# Core conversion: _is_js_null_or_undefined
# =========================================================================


class TestIsJsNullOrUndefinedFFI:
    def test_jsnull_detected(self, pyodide_fakes: FakeJsModule) -> None:
        """JsNull is detected as null (the historical bug: JsNull is NOT a JsProxy)."""
        assert wrappers_mod._is_js_null_or_undefined(JsNull()) is True

    def test_js_undefined_detected(self, pyodide_fakes: FakeJsModule) -> None:
        """js.undefined singleton is detected."""
        assert wrappers_mod._is_js_null_or_undefined(pyodide_fakes.undefined) is True

    def test_python_none_not_treated_as_jsnull(self, pyodide_fakes: FakeJsModule) -> None:
        """When HAS_PYODIDE=True, Python None is NOT JsNull or undefined."""
        assert wrappers_mod._is_js_null_or_undefined(None) is False

    def test_jsproxy_not_null(self, pyodide_fakes: FakeJsModule) -> None:
        """A JsProxy wrapping a dict is NOT null/undefined."""
        assert wrappers_mod._is_js_null_or_undefined(FakeJsProxy({"a": 1})) is False


# =========================================================================
# Core conversion: _is_js_undefined
# =========================================================================


class TestIsJsUndefinedFFI:
    def test_undefined_detected(self, pyodide_fakes: FakeJsModule) -> None:
        assert wrappers_mod._is_js_undefined(pyodide_fakes.undefined) is True

    def test_jsnull_is_not_undefined(self, pyodide_fakes: FakeJsModule) -> None:
        """JsNull is null, not undefined — they are distinct in JS."""
        assert wrappers_mod._is_js_undefined(JsNull()) is False

    def test_python_none_not_undefined_in_pyodide(self, pyodide_fakes: FakeJsModule) -> None:
        assert wrappers_mod._is_js_undefined(None) is False


# =========================================================================
# Core conversion: get_js_null / d1_null
# =========================================================================


class TestGetJsNullFFI:
    def test_returns_jsnull_instance(self, pyodide_fakes: FakeJsModule) -> None:
        """In Pyodide, get_js_null() returns a JsNull (not Python None)."""
        result = wrappers_mod.get_js_null()
        assert type(result).__name__ == "JsNull"

    def test_result_is_detected_as_null(self, pyodide_fakes: FakeJsModule) -> None:
        result = wrappers_mod.get_js_null()
        assert wrappers_mod._is_js_null_or_undefined(result) is True


class TestD1NullFFI:
    def test_none_becomes_jsnull(self, pyodide_fakes: FakeJsModule) -> None:
        """The historical bug: None→undefined breaks D1 .bind(). d1_null must convert."""
        result = wrappers_mod.d1_null(None)
        assert type(result).__name__ == "JsNull"

    def test_non_none_passes_through(self, pyodide_fakes: FakeJsModule) -> None:
        assert wrappers_mod.d1_null("hello") == "hello"
        assert wrappers_mod.d1_null(42) == 42
        assert wrappers_mod.d1_null(0) == 0


# =========================================================================
# Core conversion: _to_py_safe
# =========================================================================


class TestToPySafeFFI:
    def test_jsproxy_dict_converted(self, pyodide_fakes: FakeJsModule) -> None:
        """JsProxy wrapping a dict is converted to a plain Python dict."""
        proxy = FakeJsProxy({"title": "Test", "id": "abc123"})
        result = wrappers_mod._to_py_safe(proxy)
        assert result == {"title": "Test", "id": "abc123"}
        assert isinstance(result, dict)

    def test_jsproxy_list_converted(self, pyodide_fakes: FakeJsModule) -> None:
        proxy = FakeJsProxy([1, 2, 3])
        result = wrappers_mod._to_py_safe(proxy)
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_nested_jsnull_scrubbed_from_dict(self, pyodide_fakes: FakeJsModule) -> None:
        """JsNull values inside a converted dict become Python None."""
        proxy = FakeJsProxy({"title": "Test", "author": JsNull()})
        result = wrappers_mod._to_py_safe(proxy)
        assert result == {"title": "Test", "author": None}

    def test_nested_jsnull_scrubbed_from_list(self, pyodide_fakes: FakeJsModule) -> None:
        proxy = FakeJsProxy(["a", JsNull(), "b"])
        result = wrappers_mod._to_py_safe(proxy)
        assert result == ["a", None, "b"]

    def test_js_undefined_becomes_none(self, pyodide_fakes: FakeJsModule) -> None:
        result = wrappers_mod._to_py_safe(pyodide_fakes.undefined)
        assert result is None

    def test_jsnull_becomes_none(self, pyodide_fakes: FakeJsModule) -> None:
        result = wrappers_mod._to_py_safe(JsNull())
        assert result is None

    def test_plain_dict_with_jsnull_scrubbed(self, pyodide_fakes: FakeJsModule) -> None:
        """Plain dicts (not JsProxy) may contain JsNull from .to_py() recursion."""
        data = {"name": "Test", "value": JsNull()}
        result = wrappers_mod._to_py_safe(data)
        assert result == {"name": "Test", "value": None}

    def test_plain_list_with_jsnull_scrubbed(self, pyodide_fakes: FakeJsModule) -> None:
        data = ["a", JsNull(), "c"]
        result = wrappers_mod._to_py_safe(data)
        assert result == ["a", None, "c"]

    def test_depth_limit_returns_value_as_is(self, pyodide_fakes: FakeJsModule) -> None:
        proxy = FakeJsProxy({"key": "value"})
        result = wrappers_mod._to_py_safe(proxy, depth=wrappers_mod.MAX_CONVERSION_DEPTH + 1)
        assert result is proxy

    def test_deeply_nested_jsproxy(self, pyodide_fakes: FakeJsModule) -> None:
        """Nested JsProxy inside a converted dict is recursively converted."""
        inner = FakeJsProxy({"nested": True})
        outer = FakeJsProxy({"id": "1", "meta": inner})
        result = wrappers_mod._to_py_safe(outer)
        assert result == {"id": "1", "meta": {"nested": True}}

    def test_jsproxy_to_py_error_returns_none(self, pyodide_fakes: FakeJsModule) -> None:
        """If .to_py() raises, _to_py_safe returns None (not crash)."""

        class BadProxy(FakeJsProxy):
            def to_py(self) -> Any:
                raise RuntimeError("conversion failed")

        result = wrappers_mod._to_py_safe(BadProxy())
        assert result is None


# =========================================================================
# D1 result helpers: d1_first / d1_rows
# =========================================================================


class TestD1FirstFFI:
    def test_jsnull_returns_none(self, pyodide_fakes: FakeJsModule) -> None:
        """D1 .first() returns JsNull when no rows match → must become None."""
        result = wrappers_mod.d1_first(JsNull())
        assert result is None

    def test_jsproxy_row_converted_to_dict(self, pyodide_fakes: FakeJsModule) -> None:
        proxy = FakeJsProxy({"id": "abc", "title": "Test Article"})
        result = wrappers_mod.d1_first(proxy)
        assert result == {"id": "abc", "title": "Test Article"}

    def test_nested_jsnull_in_row_fields(self, pyodide_fakes: FakeJsModule) -> None:
        """Row fields that are JS null must become Python None."""
        proxy = FakeJsProxy({"id": "abc", "author": JsNull(), "excerpt": JsNull()})
        result = wrappers_mod.d1_first(proxy)
        assert result == {"id": "abc", "author": None, "excerpt": None}

    def test_js_undefined_returns_none(self, pyodide_fakes: FakeJsModule) -> None:
        result = wrappers_mod.d1_first(pyodide_fakes.undefined)
        assert result is None

    def test_python_none_returns_none(self, pyodide_fakes: FakeJsModule) -> None:
        result = wrappers_mod.d1_first(None)
        assert result is None

    def test_empty_dict_returns_none(self, pyodide_fakes: FakeJsModule) -> None:
        """An empty dict from .first() means no useful data."""
        proxy = FakeJsProxy({})
        result = wrappers_mod.d1_first(proxy)
        assert result is None

    def test_result_wrapper_unwrapped(self, pyodide_fakes: FakeJsModule) -> None:
        """D1 in Pyodide may return {results: [...], success, meta} from .first()."""
        proxy = FakeJsProxy(
            {
                "results": [{"id": "abc", "title": "Test"}],
                "success": True,
                "meta": {},
            }
        )
        result = wrappers_mod.d1_first(proxy)
        assert result == {"id": "abc", "title": "Test"}


class TestD1RowsFFI:
    def test_jsnull_returns_empty_list(self, pyodide_fakes: FakeJsModule) -> None:
        result = wrappers_mod.d1_rows(JsNull())
        assert result == []

    def test_js_undefined_returns_empty_list(self, pyodide_fakes: FakeJsModule) -> None:
        result = wrappers_mod.d1_rows(pyodide_fakes.undefined)
        assert result == []

    def test_jsproxy_result_set_converted(self, pyodide_fakes: FakeJsModule) -> None:
        """JsProxy wrapping D1 .all() result → list of dicts."""
        proxy = FakeJsProxy(
            {
                "results": [
                    {"id": "1", "title": "First"},
                    {"id": "2", "title": "Second"},
                ]
            }
        )
        result = wrappers_mod.d1_rows(proxy)
        assert len(result) == 2
        assert result[0] == {"id": "1", "title": "First"}
        assert result[1] == {"id": "2", "title": "Second"}

    def test_jsnull_in_row_fields_scrubbed(self, pyodide_fakes: FakeJsModule) -> None:
        proxy = FakeJsProxy({"results": [{"id": "1", "author": JsNull()}]})
        result = wrappers_mod.d1_rows(proxy)
        assert result == [{"id": "1", "author": None}]

    def test_none_rows_filtered(self, pyodide_fakes: FakeJsModule) -> None:
        """None entries in results list are filtered out."""
        proxy = FakeJsProxy({"results": [{"id": "1"}, None, {"id": "2"}]})
        result = wrappers_mod.d1_rows(proxy)
        assert len(result) == 2

    def test_bare_list_result(self, pyodide_fakes: FakeJsModule) -> None:
        """Some D1 paths return a bare list without the results wrapper."""
        proxy = FakeJsProxy([{"id": "1"}, {"id": "2"}])
        result = wrappers_mod.d1_rows(proxy)
        assert len(result) == 2


# =========================================================================
# Python → JS conversion: _to_js_value
# =========================================================================


class TestToJsValueFFI:
    def test_dict_converted_with_fromEntries(self, pyodide_fakes: FakeJsModule) -> None:
        """Dicts use dict_converter=Object.fromEntries (not Map)."""
        result = wrappers_mod._to_js_value({"type": "article", "id": "abc"})
        assert isinstance(result, dict)
        assert result == {"type": "article", "id": "abc"}

    def test_list_converted(self, pyodide_fakes: FakeJsModule) -> None:
        result = wrappers_mod._to_js_value([1, 2, 3])
        assert result == [1, 2, 3]

    def test_tuple_converted(self, pyodide_fakes: FakeJsModule) -> None:
        result = wrappers_mod._to_js_value((1, 2))
        assert result == [1, 2]

    def test_none_returns_none(self, pyodide_fakes: FakeJsModule) -> None:
        """None passes through (becomes JS undefined on the FFI)."""
        assert wrappers_mod._to_js_value(None) is None

    def test_primitives_pass_through(self, pyodide_fakes: FakeJsModule) -> None:
        assert wrappers_mod._to_js_value("hello") == "hello"
        assert wrappers_mod._to_js_value(42) == 42
        assert wrappers_mod._to_js_value(True) is True


# =========================================================================
# to_js_bytes / to_py_bytes
# =========================================================================


class TestToJsBytesFFI:
    def test_bytes_converted_to_jsproxy(self, pyodide_fakes: FakeJsModule) -> None:
        """The historical bug: bytes → PyProxy breaks R2. Must convert via to_js()."""
        result = wrappers_mod.to_js_bytes(b"hello")
        assert isinstance(result, FakeJsProxy)

    def test_bytearray_converted(self, pyodide_fakes: FakeJsModule) -> None:
        result = wrappers_mod.to_js_bytes(bytearray(b"data"))
        assert isinstance(result, FakeJsProxy)

    def test_memoryview_converted(self, pyodide_fakes: FakeJsModule) -> None:
        result = wrappers_mod.to_js_bytes(memoryview(b"view"))
        assert isinstance(result, FakeJsProxy)


class TestToPyBytesFFI:
    def test_jsproxy_bytes_converted(self, pyodide_fakes: FakeJsModule) -> None:
        """JsProxy wrapping bytes (from Uint8Array.to_py()) → Python bytes."""
        proxy = FakeJsProxy(b"audio data")
        result = wrappers_mod.to_py_bytes(proxy)
        assert result == b"audio data"
        assert isinstance(result, bytes)

    def test_jsproxy_memoryview_converted(self, pyodide_fakes: FakeJsModule) -> None:
        proxy = FakeJsProxy(memoryview(b"chunk"))
        result = wrappers_mod.to_py_bytes(proxy)
        assert result == b"chunk"
        assert isinstance(result, bytes)

    def test_none_returns_empty_bytes(self, pyodide_fakes: FakeJsModule) -> None:
        assert wrappers_mod.to_py_bytes(None) == b""

    def test_plain_bytes_pass_through(self, pyodide_fakes: FakeJsModule) -> None:
        assert wrappers_mod.to_py_bytes(b"plain") == b"plain"


# =========================================================================
# get_r2_size
# =========================================================================


class TestGetR2SizeFFI:
    def test_jsnull_size_returns_none(self, pyodide_fakes: FakeJsModule) -> None:
        r2_obj = SimpleNamespace(size=JsNull())
        assert wrappers_mod.get_r2_size(r2_obj) is None

    def test_undefined_size_returns_none(self, pyodide_fakes: FakeJsModule) -> None:
        r2_obj = SimpleNamespace(size=pyodide_fakes.undefined)
        assert wrappers_mod.get_r2_size(r2_obj) is None

    def test_valid_size_returned(self, pyodide_fakes: FakeJsModule) -> None:
        r2_obj = SimpleNamespace(size=12345)
        assert wrappers_mod.get_r2_size(r2_obj) == 12345

    def test_missing_size_returns_none(self, pyodide_fakes: FakeJsModule) -> None:
        r2_obj = SimpleNamespace()
        assert wrappers_mod.get_r2_size(r2_obj) is None


# =========================================================================
# SafeD1 wrapper
# =========================================================================


class TestSafeD1FFI:
    def test_bind_converts_none_to_jsnull(self, pyodide_fakes: FakeJsModule) -> None:
        """bind() must convert None → JsNull so D1 gets null, not undefined."""
        captured_args: list[Any] = []

        class FakeStmt:
            def bind(self, *args: Any) -> FakeStmt:
                captured_args.extend(args)
                return self

        stmt = wrappers_mod.SafeD1Statement(FakeStmt())
        stmt.bind("value", None, 42, None)
        assert captured_args[0] == "value"
        assert type(captured_args[1]).__name__ == "JsNull"
        assert captured_args[2] == 42
        assert type(captured_args[3]).__name__ == "JsNull"

    @pytest.mark.asyncio()
    async def test_first_jsproxy_to_dict(self, pyodide_fakes: FakeJsModule) -> None:
        """first() converts JsProxy result to Python dict."""
        row = FakeJsProxy({"id": "abc", "title": "Test"})

        class FakeStmt:
            async def first(self) -> Any:
                return row

        stmt = wrappers_mod.SafeD1Statement(FakeStmt())
        result = await stmt.first()
        assert result == {"id": "abc", "title": "Test"}

    @pytest.mark.asyncio()
    async def test_first_jsnull_to_none(self, pyodide_fakes: FakeJsModule) -> None:
        """first() converts JsNull (no matching row) to Python None."""

        class FakeStmt:
            async def first(self) -> Any:
                return JsNull()

        stmt = wrappers_mod.SafeD1Statement(FakeStmt())
        result = await stmt.first()
        assert result is None

    @pytest.mark.asyncio()
    async def test_all_converts_rows(self, pyodide_fakes: FakeJsModule) -> None:
        """all() converts JsProxy result set to list of dicts."""
        rows = FakeJsProxy({"results": [{"id": "1"}, {"id": "2"}]})

        class FakeStmt:
            async def all(self) -> Any:
                return rows

        stmt = wrappers_mod.SafeD1Statement(FakeStmt())
        result = await stmt.all()
        assert result == [{"id": "1"}, {"id": "2"}]

    @pytest.mark.asyncio()
    async def test_all_jsnull_returns_empty(self, pyodide_fakes: FakeJsModule) -> None:

        class FakeStmt:
            async def all(self) -> Any:
                return JsNull()

        stmt = wrappers_mod.SafeD1Statement(FakeStmt())
        result = await stmt.all()
        assert result == []

    def test_prepare_wraps_statement(self, pyodide_fakes: FakeJsModule) -> None:
        class FakeDB:
            def prepare(self, sql: str) -> Any:
                return SimpleNamespace(sql=sql)

        db = wrappers_mod.SafeD1(FakeDB())
        stmt = db.prepare("SELECT * FROM articles")
        assert isinstance(stmt, wrappers_mod.SafeD1Statement)


# =========================================================================
# SafeR2 wrapper
# =========================================================================


class TestSafeR2FFI:
    @pytest.mark.asyncio()
    async def test_put_bytes_converted(self, pyodide_fakes: FakeJsModule) -> None:
        """put() converts bytes → FakeJsProxy (simulating Uint8Array)."""
        captured: dict[str, Any] = {}

        class FakeR2:
            async def put(self, key: str, data: Any, **kw: Any) -> None:
                captured["key"] = key
                captured["data"] = data

        r2 = wrappers_mod.SafeR2(FakeR2())
        await r2.put("articles/123/content.html", b"<h1>Hello</h1>")
        assert captured["key"] == "articles/123/content.html"
        assert isinstance(captured["data"], FakeJsProxy)

    @pytest.mark.asyncio()
    async def test_put_string_not_converted(self, pyodide_fakes: FakeJsModule) -> None:
        """Strings pass through — R2 accepts JS strings natively."""
        captured: dict[str, Any] = {}

        class FakeR2:
            async def put(self, key: str, data: Any, **kw: Any) -> None:
                captured["data"] = data

        r2 = wrappers_mod.SafeR2(FakeR2())
        await r2.put("key", "string data")
        assert captured["data"] == "string data"

    @pytest.mark.asyncio()
    async def test_get_jsnull_returns_none(self, pyodide_fakes: FakeJsModule) -> None:
        """get() converts JsNull (missing key) to Python None."""

        class FakeR2:
            async def get(self, key: str) -> Any:
                return JsNull()

        r2 = wrappers_mod.SafeR2(FakeR2())
        result = await r2.get("missing/key")
        assert result is None

    @pytest.mark.asyncio()
    async def test_get_undefined_returns_none(self, pyodide_fakes: FakeJsModule) -> None:

        class FakeR2:
            async def get(self, key: str) -> Any:
                return pyodide_fakes.undefined

        r2 = wrappers_mod.SafeR2(FakeR2())
        result = await r2.get("missing/key")
        assert result is None

    @pytest.mark.asyncio()
    async def test_get_valid_object_returned(self, pyodide_fakes: FakeJsModule) -> None:
        r2_obj = SimpleNamespace(body="content", size=100)

        class FakeR2:
            async def get(self, key: str) -> Any:
                return r2_obj

        r2 = wrappers_mod.SafeR2(FakeR2())
        result = await r2.get("articles/123/content.html")
        assert result is r2_obj

    @pytest.mark.asyncio()
    async def test_list_jsproxy_converted(self, pyodide_fakes: FakeJsModule) -> None:
        list_result = FakeJsProxy({"objects": [{"key": "a"}, {"key": "b"}]})

        class FakeR2:
            async def list(self, **kw: Any) -> Any:
                return list_result

        r2 = wrappers_mod.SafeR2(FakeR2())
        result = await r2.list(prefix="articles/")
        assert isinstance(result, dict)
        assert result["objects"] == [{"key": "a"}, {"key": "b"}]


# =========================================================================
# SafeKV wrapper
# =========================================================================


class TestSafeKVFFI:
    @pytest.mark.asyncio()
    async def test_get_jsnull_returns_none(self, pyodide_fakes: FakeJsModule) -> None:

        class FakeKV:
            async def get(self, key: str, **kw: Any) -> Any:
                return JsNull()

        kv = wrappers_mod.SafeKV(FakeKV())
        result = await kv.get("missing-session")
        assert result is None

    @pytest.mark.asyncio()
    async def test_get_undefined_returns_none(self, pyodide_fakes: FakeJsModule) -> None:

        class FakeKV:
            async def get(self, key: str, **kw: Any) -> Any:
                return pyodide_fakes.undefined

        kv = wrappers_mod.SafeKV(FakeKV())
        result = await kv.get("missing-session")
        assert result is None

    @pytest.mark.asyncio()
    async def test_get_valid_value_returned(self, pyodide_fakes: FakeJsModule) -> None:

        class FakeKV:
            async def get(self, key: str, **kw: Any) -> Any:
                return '{"user_id": "u1"}'

        kv = wrappers_mod.SafeKV(FakeKV())
        result = await kv.get("session-abc")
        assert result == '{"user_id": "u1"}'


# =========================================================================
# SafeQueue wrapper
# =========================================================================


class TestSafeQueueFFI:
    @pytest.mark.asyncio()
    async def test_send_dict_converted(self, pyodide_fakes: FakeJsModule) -> None:
        """send() converts dict → JS Object via dict_converter (not Map)."""
        captured: list[Any] = []

        class FakeQueue:
            async def send(self, message: Any, **kw: Any) -> None:
                captured.append(message)

        q = wrappers_mod.SafeQueue(FakeQueue())
        await q.send({"type": "process_article", "article_id": "abc"})
        assert isinstance(captured[0], dict)
        assert captured[0]["type"] == "process_article"


# =========================================================================
# SafeAI wrapper
# =========================================================================


class TestSafeAIFFI:
    @pytest.mark.asyncio()
    async def test_run_dict_inputs_converted(self, pyodide_fakes: FakeJsModule) -> None:
        captured: dict[str, Any] = {}

        class FakeAI:
            async def run(self, model: str, inputs: Any, **kw: Any) -> Any:
                captured["model"] = model
                captured["inputs"] = inputs
                return FakeJsProxy(b"audio bytes")

        ai = wrappers_mod.SafeAI(FakeAI())
        await ai.run("@cf/meta/m2m100", {"text": "Hello", "source_lang": "en"})
        assert captured["model"] == "@cf/meta/m2m100"
        assert isinstance(captured["inputs"], dict)

    @pytest.mark.asyncio()
    async def test_run_jsnull_result_returns_none(self, pyodide_fakes: FakeJsModule) -> None:

        class FakeAI:
            async def run(self, model: str, inputs: Any, **kw: Any) -> Any:
                return JsNull()

        ai = wrappers_mod.SafeAI(FakeAI())
        result = await ai.run("@cf/meta/m2m100", {"text": "Hello"})
        assert result is None

    @pytest.mark.asyncio()
    async def test_run_undefined_result_returns_none(self, pyodide_fakes: FakeJsModule) -> None:

        class FakeAI:
            async def run(self, model: str, inputs: Any, **kw: Any) -> Any:
                return pyodide_fakes.undefined

        ai = wrappers_mod.SafeAI(FakeAI())
        result = await ai.run("@cf/meta/m2m100", {})
        assert result is None


# =========================================================================
# SafeReadability wrapper
# =========================================================================


class TestSafeReadabilityFFI:
    @pytest.mark.asyncio()
    async def test_parse_jsproxy_to_dict(self, pyodide_fakes: FakeJsModule) -> None:
        result_proxy = FakeJsProxy(
            {
                "title": "Test Article",
                "html": "<p>Content</p>",
                "excerpt": "A test",
                "byline": "Author",
            }
        )

        class FakeBinding:
            async def parse(self, html: str, url: str) -> Any:
                return result_proxy

        r = wrappers_mod.SafeReadability(FakeBinding())
        result = await r.parse("<html>...</html>", "https://example.com")
        assert result == {
            "title": "Test Article",
            "html": "<p>Content</p>",
            "excerpt": "A test",
            "byline": "Author",
        }

    @pytest.mark.asyncio()
    async def test_parse_jsnull_fields_scrubbed(self, pyodide_fakes: FakeJsModule) -> None:
        """Fields like byline/excerpt may be JS null — must become Python None."""
        result_proxy = FakeJsProxy(
            {
                "title": "Test",
                "html": "<p>OK</p>",
                "excerpt": JsNull(),
                "byline": JsNull(),
            }
        )

        class FakeBinding:
            async def parse(self, html: str, url: str) -> Any:
                return result_proxy

        r = wrappers_mod.SafeReadability(FakeBinding())
        result = await r.parse("<html>...</html>", "https://example.com")
        assert result["excerpt"] is None
        assert result["byline"] is None


# =========================================================================
# SafeEnv wrapper
# =========================================================================


class TestSafeEnvFFI:
    def test_get_undefined_returns_default(self, pyodide_fakes: FakeJsModule) -> None:
        """get() with js.undefined value returns the default."""
        raw = SimpleNamespace(OPTIONAL_VAR=pyodide_fakes.undefined)
        env = wrappers_mod.SafeEnv(raw)
        assert env.get("OPTIONAL_VAR") is None
        assert env.get("OPTIONAL_VAR", "fallback") == "fallback"

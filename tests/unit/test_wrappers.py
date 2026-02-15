"""Tests for src/wrappers.py — the FFI boundary layer.

These tests run under standard CPython (pytest) where Pyodide is NOT
available.  They verify that every helper degrades gracefully and that
the non-Pyodide code paths produce correct results.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.wrappers import (
    HAS_PYODIDE,
    SafeEnv,
    _is_js_undefined,
    _to_js_value,
    _to_py_safe,
    d1_first,
    d1_rows,
    get_js_null,
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
# _to_py_safe
# =========================================================================


class TestToPySafe:
    def test_none_returns_none(self) -> None:
        assert _to_py_safe(None) is None

    def test_string_passthrough(self) -> None:
        assert _to_py_safe("hello") == "hello"

    def test_int_passthrough(self) -> None:
        assert _to_py_safe(42) == 42

    def test_float_passthrough(self) -> None:
        assert _to_py_safe(3.14) == pytest.approx(3.14)

    def test_bool_passthrough(self) -> None:
        assert _to_py_safe(True) is True
        assert _to_py_safe(False) is False

    def test_dict_passthrough(self) -> None:
        data = {"key": "value", "nested": {"a": 1}}
        assert _to_py_safe(data) == data

    def test_list_passthrough(self) -> None:
        data = [1, "two", 3.0, None]
        assert _to_py_safe(data) == data

    def test_mock_jsproxy_not_converted_without_pyodide(self) -> None:
        """When HAS_PYODIDE is False, a mock JsProxy is returned as-is
        (no isinstance check against the real JsProxy type)."""
        mock = MagicMock()
        mock.to_py.return_value = {"key": "value"}
        # Since JsProxy is None when not in Pyodide, isinstance check
        # won't match — the mock is returned unchanged.
        result = _to_py_safe(mock)
        assert result is mock

    def test_depth_limit_prevents_infinite_recursion(self) -> None:
        """Passing a depth beyond MAX_CONVERSION_DEPTH returns value as-is."""
        result = _to_py_safe("deep", depth=100)
        assert result == "deep"


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


# =========================================================================
# _to_js_value
# =========================================================================


class TestToJsValue:
    def test_none_returns_none(self) -> None:
        """None passes through unchanged (becomes JS undefined on FFI)."""
        assert _to_js_value(None) is None

    def test_string_passthrough(self) -> None:
        assert _to_js_value("hello") == "hello"

    def test_int_passthrough(self) -> None:
        assert _to_js_value(42) == 42

    def test_dict_passthrough_outside_pyodide(self) -> None:
        """Outside Pyodide, dicts are returned as-is (no to_js call)."""
        data = {"key": "value"}
        assert _to_js_value(data) == data

    def test_list_passthrough_outside_pyodide(self) -> None:
        """Outside Pyodide, lists are returned as-is (no to_js call)."""
        data = [1, 2, 3]
        assert _to_js_value(data) == data

    def test_bool_passthrough(self) -> None:
        assert _to_js_value(True) is True


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
        """Attribute access proxies to the underlying env object."""
        raw = SimpleNamespace(DB="my_db")
        env = SafeEnv(raw)
        assert env.DB == "my_db"

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

    def test_empty_dict_returns_empty_dict(self) -> None:
        assert d1_first({}) == {}

    def test_namespace_converted_to_dict(self) -> None:
        """SimpleNamespace (simulating a JsProxy after to_py) is dict-ifiable."""
        obj = SimpleNamespace(id="1", title="Hello")
        result = d1_first(obj)
        assert result == {"id": "1", "title": "Hello"}

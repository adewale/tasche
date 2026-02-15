"""FFI boundary layer for Cloudflare Python Workers.

This module abstracts the JavaScript/Python boundary so that application code
can be imported and tested in both the Workers runtime (Pyodide) and standard
CPython (pytest).  Every public helper gracefully degrades when the ``js``
module is unavailable.

Key design decisions
--------------------
* **HAS_PYODIDE guard** – a single ``try/except ImportError`` at module level
  determines whether we are running inside Pyodide.  All JS-specific code is
  gated behind this flag.
* **No module-level PRNG** – calling ``random`` or ``secrets`` at import time
  would break the Wasm snapshot that Workers uses for fast cold starts.
* **``to_py()`` is a method on JsProxy** – it is *not* a standalone function.
* **``to_js()`` needs ``dict_converter=Object.fromEntries``** for dicts,
  otherwise Python dicts become JS ``Map`` objects instead of plain Objects.
* **Python ``None`` maps to JS ``undefined``** (not ``null``).  When a real
  JSON ``null`` is needed, use ``js.JSON.parse("null")``.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Pyodide detection
# ---------------------------------------------------------------------------

HAS_PYODIDE = False

try:
    import js  # type: ignore[import-not-found]
    from pyodide.ffi import JsProxy, to_js  # type: ignore[import-not-found]

    HAS_PYODIDE = True
except ImportError:
    js = None  # type: ignore[assignment]
    JsProxy = None  # type: ignore[assignment, misc]
    to_js = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# JS null sentinel
# ---------------------------------------------------------------------------


def get_js_null() -> Any:
    """Return a true JS ``null`` value.

    Python ``None`` becomes JS ``undefined`` on the FFI boundary.  When a real
    ``null`` is required (e.g. for JSON serialisation), use this helper.

    Called as a function (not a module-level constant) to avoid executing JS
    code during the Wasm snapshot phase.
    """
    if HAS_PYODIDE:
        return js.JSON.parse("null")
    return None


JS_NULL = None  # Lazy; call get_js_null() at runtime when needed.


# ---------------------------------------------------------------------------
# JsProxy -> Python conversion
# ---------------------------------------------------------------------------

MAX_CONVERSION_DEPTH = 20


def _to_py_safe(value: Any, depth: int = 0) -> Any:
    """Recursively convert a JsProxy value into native Python types.

    Handles nested objects, arrays, ``null``, and ``undefined`` gracefully.
    Falls through to returning the original value when it is already a Python
    primitive or when we are not running in Pyodide.

    Parameters
    ----------
    value:
        The value to convert.  May be a JsProxy, a Python primitive, or
        ``None``.
    depth:
        Current recursion depth.  Conversion stops at ``MAX_CONVERSION_DEPTH``
        to prevent infinite loops on circular references.
    """
    if depth > MAX_CONVERSION_DEPTH:
        return value

    # None / non-Pyodide fast path
    if value is None or not HAS_PYODIDE:
        return value

    # Check for JS undefined
    if _is_js_undefined(value):
        return None

    # JsProxy objects need conversion
    if isinstance(value, JsProxy):
        # Use the built-in .to_py() first — it handles most cases.
        try:
            converted = value.to_py()
        except Exception:
            return value

        # to_py() may return nested JsProxy objects inside dicts/lists;
        # recurse to clean them up.
        if isinstance(converted, dict):
            return {k: _to_py_safe(v, depth + 1) for k, v in converted.items()}
        if isinstance(converted, list):
            return [_to_py_safe(item, depth + 1) for item in converted]
        return converted

    # Already a Python type
    return value


# ---------------------------------------------------------------------------
# JS undefined detection
# ---------------------------------------------------------------------------


def _is_js_undefined(value: Any) -> bool:
    """Return ``True`` if *value* represents JavaScript ``undefined``.

    In Pyodide, ``undefined`` is exposed as a special singleton on the ``js``
    module.  Outside Pyodide we simply check for ``None``.
    """
    if not HAS_PYODIDE:
        return value is None

    # Pyodide exposes js.undefined as the singleton for JS undefined.
    try:
        return value is js.undefined
    except AttributeError:
        return False


# ---------------------------------------------------------------------------
# Python -> JS conversion
# ---------------------------------------------------------------------------


def _to_js_value(value: Any) -> Any:
    """Convert a Python value to a JS-compatible representation.

    When running in Pyodide, dicts are converted with
    ``dict_converter=Object.fromEntries`` so they become plain JS Objects
    rather than ``Map`` instances.

    Outside Pyodide the value is returned unchanged (useful for tests).
    """
    if not HAS_PYODIDE or value is None:
        return value

    if isinstance(value, dict):
        return to_js(value, dict_converter=js.Object.fromEntries)

    if isinstance(value, (list, tuple)):
        return to_js(value)

    # Primitives (str, int, float, bool) cross the FFI boundary as-is.
    return value


# ---------------------------------------------------------------------------
# SafeEnv — typed access to Worker bindings
# ---------------------------------------------------------------------------


class SafeEnv:
    """Thin wrapper around a Worker ``env`` object.

    Provides a ``.get(key, default)`` accessor that handles both missing
    attributes and JS ``undefined`` values — common when optional bindings or
    vars are not configured.

    Usage in a FastAPI handler::

        env = SafeEnv(request.scope["env"])
        allowed = env.get("ALLOWED_EMAILS", "")
    """

    def __init__(self, env: Any) -> None:
        self._env = env

    def get(self, key: str, default: Any = None) -> Any:
        """Return the binding/var for *key*, or *default* if missing/undefined."""
        try:
            val = getattr(self._env, key)
        except AttributeError:
            return default

        if val is None or _is_js_undefined(val):
            return default

        return val

    def __getattr__(self, key: str) -> Any:
        """Proxy attribute access to the underlying env object."""
        if key.startswith("_"):
            raise AttributeError(key)
        return getattr(self._env, key)


# ---------------------------------------------------------------------------
# D1 result helpers
# ---------------------------------------------------------------------------


def d1_rows(results: Any) -> list[dict[str, Any]]:
    """Convert D1 ``stmt.all()`` results to a list of Python dicts.

    D1's ``.all()`` returns an object with a ``results`` property containing
    an array of row objects.  Each row is a JsProxy that must be converted.

    Outside Pyodide (in tests), *results* is expected to be a dict-like object
    with a ``"results"`` key containing a list of dicts already.
    """
    if results is None:
        return []

    if HAS_PYODIDE:
        converted = _to_py_safe(results)
    else:
        converted = results

    # Handle both attribute access (.results) and dict access (["results"])
    if isinstance(converted, dict):
        rows = converted.get("results", [])
    elif hasattr(converted, "results"):
        rows = _to_py_safe(converted.results)
    else:
        rows = []

    if rows is None:
        return []

    return [dict(row) if not isinstance(row, dict) else row for row in rows]


def d1_first(results: Any) -> dict[str, Any] | None:
    """Convert D1 ``stmt.first()`` result to a Python dict or ``None``.

    D1's ``.first()`` returns a single row object (JsProxy) or ``null``
    (which becomes ``None`` on the Python side).

    Outside Pyodide (in tests), *results* is expected to already be a dict
    or ``None``.
    """
    if results is None:
        return None

    if HAS_PYODIDE:
        converted = _to_py_safe(results)
    else:
        converted = results

    if converted is None:
        return None

    if isinstance(converted, dict):
        return converted

    # Final attempt — if it has __dict__ (e.g. SimpleNamespace, JsProxy after to_py),
    # extract its attributes as a dict.
    if hasattr(converted, "__dict__"):
        return dict(vars(converted))

    try:
        return dict(converted)
    except (TypeError, ValueError):
        return None

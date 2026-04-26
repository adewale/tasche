"""FFI boundary layer for Cloudflare Python Workers.

This module abstracts the JavaScript/Python boundary so that application code
can be imported and tested in both the Workers runtime (Pyodide) and standard
CPython (pytest).  Every public helper gracefully degrades when the ``js``
module is unavailable.

Key design decisions
--------------------
* **HAS_PYODIDE guard** – local JS-specific HTTP code is gated behind this flag.
* **No module-level PRNG** – calling ``random`` or ``secrets`` at import time
  would break the Wasm snapshot that Workers uses for fast cold starts.
* Generic JS/Python conversion is delegated to CFBoundary.
"""

from __future__ import annotations

# NOTE: This file is intentionally retained as Tasche's application compatibility
# layer during the CFBoundary migration.  It preserves Tasche's historical wrapper
# API (SafeReadability, HttpResponse shape, observability hooks, and binding-name
# properties) while CFBoundary stabilizes its generic API.  Generic behavior should
# move to CFBoundary incrementally; application-specific binding names remain here.
import time
from dataclasses import dataclass
from typing import Any

import cfboundary.ffi as cf_boundary

# ---------------------------------------------------------------------------
# Pyodide detection
# ---------------------------------------------------------------------------

# App-local JS runtime flag used only for Tasche-specific JS APIs such as
# ``safe_http_fetch``. Generic FFI conversion runtime state lives in CFBoundary.
HAS_PYODIDE = False

try:
    import js  # type: ignore[import-not-found]

    HAS_PYODIDE = True
except ModuleNotFoundError as exc:
    if exc.name != "js":
        raise
    js = None  # type: ignore[assignment]

JsException = cf_boundary.JsException


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
    return cf_boundary.js_null()


def d1_null(value: Any) -> Any:
    """Convert Python ``None`` to JS ``null`` for D1 bind parameters.

    D1 rejects ``undefined`` (which is what Python ``None`` becomes across
    the Pyodide FFI boundary).  Use this helper to wrap any nullable value
    before passing it to ``stmt.bind()``.

    Outside Pyodide, returns the value unchanged.
    """
    return cf_boundary.d1_null(value)


async def consume_readable_stream(value: Any) -> bytes:
    """Consume a JS ReadableStream or ArrayBuffer into Python bytes."""
    return await cf_boundary.consume_readable_stream(value)


async def stream_r2_body(r2_obj: Any) -> Any:
    """Yield Python byte chunks from an R2 object's body."""
    async for chunk in cf_boundary.stream_r2_body(r2_obj):
        yield chunk


def get_r2_size(r2_obj: Any) -> int | None:
    """Extract the size property from an R2 object."""
    return cf_boundary.get_r2_size(r2_obj)


def to_js_bytes(data: bytes | bytearray | memoryview) -> Any:
    """Convert Python bytes-like values to a JS Uint8Array in Workers."""
    return cf_boundary.to_js_bytes(data)


# ---------------------------------------------------------------------------
# JsProxy -> Python conversion
# ---------------------------------------------------------------------------

MAX_CONVERSION_DEPTH = 20


def _to_py_safe(value: Any, depth: int = 0) -> Any:
    """Recursively convert JsProxy/null/undefined values to native Python."""
    return cf_boundary.to_py(value) if depth <= MAX_CONVERSION_DEPTH else value


def to_py_bytes(value: Any) -> bytes:
    """Convert a JS buffer to Python bytes."""
    return cf_boundary.to_py_bytes(value)


# ---------------------------------------------------------------------------
# JS undefined detection
# ---------------------------------------------------------------------------


def is_js_null(value: Any) -> bool:
    """Return ``True`` if *value* represents JavaScript ``null`` or ``undefined``.

    This is the **single canonical check** for JS nullish values.  All code
    that needs to detect JS ``null`` / ``undefined`` should call this function
    rather than checking ``type(value).__name__`` or other ad-hoc patterns.

    In Pyodide, ``undefined`` is a singleton on the ``js`` module, and
    ``null`` is a ``JsNull`` type that is **not** Python ``None``.
    Outside Pyodide we simply check for ``None``.
    """
    if not cf_boundary.is_pyodide_runtime():
        return value is None
    if value is None:
        return False
    return cf_boundary.is_js_missing(value)


# Backward-compatible alias for code that imported the private name.
_is_js_null_or_undefined = is_js_null


def _is_js_undefined(value: Any) -> bool:
    """Return ``True`` if *value* represents JavaScript ``undefined``.

    In Pyodide, ``undefined`` is exposed as a special singleton on the ``js``
    module.  Outside Pyodide we simply check for ``None``.
    """
    if not cf_boundary.is_pyodide_runtime():
        return value is None
    if value is None:
        return False
    return cf_boundary.is_js_missing(value) and not cf_boundary.is_js_null(value)


# ---------------------------------------------------------------------------
# Python -> JS conversion
# ---------------------------------------------------------------------------


def _to_js_value(value: Any) -> Any:
    """Convert a Python value to a JS-compatible representation."""
    return cf_boundary.to_js(value)


# ---------------------------------------------------------------------------
# Wide event timing helper — records elapsed time to the current WideEvent
# ---------------------------------------------------------------------------


def _record(method_name: str, t0: float) -> None:
    """Record elapsed time to the current WideEvent, if one exists.

    Called by Safe* wrappers after each binding operation.  When no
    WideEvent is active (e.g. in tests), this is a no-op.
    """
    try:
        from wide_event import current_event

        evt = current_event()
        if evt:
            getattr(evt, method_name)((time.monotonic() - t0) * 1000)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Safe* binding wrappers — construction-time wrapping for all Cloudflare
# bindings.  Application code receives these wrappers (via SafeEnv) and
# never touches raw JS bindings directly.
# ---------------------------------------------------------------------------


class SafeD1Statement:
    """Wraps a D1 prepared statement with automatic FFI conversion.

    - ``bind()`` converts every ``None`` parameter to JS ``null`` via ``d1_null()``.
    - ``first()`` converts the JsProxy result to a Python dict via ``d1_first()``.
    - ``all()`` converts the JsProxy result to a list of dicts via ``d1_rows()``.
    - ``run()`` passes through (no result conversion needed).
    """

    def __init__(self, stmt: Any) -> None:
        self._stmt = stmt

    def bind(self, *args: Any) -> SafeD1Statement:
        """Bind parameters with automatic None→null conversion."""
        safe_args = [d1_null(a) for a in args]
        self._stmt = self._stmt.bind(*safe_args)
        return self

    async def first(self) -> dict[str, Any] | None:
        """Execute and return the first row as a Python dict, or ``None``."""
        t0 = time.monotonic()
        try:
            return d1_first(await self._stmt.first())
        finally:
            _record("record_d1", t0)

    async def all(self) -> list[dict[str, Any]]:
        """Execute and return all rows as a list of Python dicts."""
        t0 = time.monotonic()
        try:
            return d1_rows(await self._stmt.all())
        finally:
            _record("record_d1", t0)

    async def run(self) -> Any:
        """Execute a write statement (INSERT/UPDATE/DELETE).

        Returns the D1 result object (with ``meta.changes``, etc.)
        converted to native Python types.
        """
        t0 = time.monotonic()
        try:
            return _to_py_safe(await self._stmt.run())
        finally:
            _record("record_d1", t0)


class SafeD1:
    """Wraps a D1 database binding so all queries go through SafeD1Statement."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def prepare(self, sql: str) -> SafeD1Statement:
        """Create a prepared statement wrapped in ``SafeD1Statement``."""
        try:
            from wide_event import current_event

            evt = current_event()
            if evt:
                evt.record_d1_prepare(sql)
        except ImportError:
            pass
        return SafeD1Statement(self._db.prepare(sql))


class SafeR2:
    """Wraps an R2 bucket binding with automatic FFI conversion for writes.

    - ``put()`` converts Python bytes/bytearray/memoryview to JS Uint8Array.
    - ``get()`` passes through (R2Objects are handled by ``stream_r2_body``).
    - ``list()`` converts JsProxy results to Python dicts.
    """

    def __init__(self, r2: Any) -> None:
        self._r2 = r2

    async def put(self, key: str, data: Any, **kwargs: Any) -> None:
        """Write a value to R2 with automatic bytes→Uint8Array conversion."""
        t0 = time.monotonic()
        try:
            if isinstance(data, (bytes, bytearray, memoryview)):
                data = to_js_bytes(data)
            await self._r2.put(key, data, **kwargs)
        finally:
            _record("record_r2_put", t0)

    async def get(self, key: str) -> Any:
        """Retrieve an object from R2.  Returns ``None`` for missing keys."""
        t0 = time.monotonic()
        try:
            result = await self._r2.get(key)
            if result is None or is_js_null(result):
                return None
            return result
        finally:
            _record("record_r2_get", t0)

    async def delete(self, key: str) -> None:
        """Delete an object from R2."""
        t0 = time.monotonic()
        try:
            return await self._r2.delete(key)
        finally:
            _record("record_r2_delete", t0)

    async def list(self, **kwargs: Any) -> Any:
        """List objects with automatic JsProxy→dict conversion."""
        t0 = time.monotonic()
        try:
            result = await self._r2.list(**kwargs)
            if not isinstance(result, dict):
                return _to_py_safe(result)
            return result
        finally:
            _record("record_r2_get", t0)


class SafeKV:
    """Wraps a KV namespace binding.  Thin passthrough for consistency."""

    def __init__(self, kv: Any) -> None:
        self._kv = kv

    async def get(self, key: str, **kwargs: Any) -> str | None:
        """Retrieve a value by key.  Returns ``None`` for missing keys."""
        t0 = time.monotonic()
        try:
            result = await self._kv.get(key, **kwargs)
            if result is None or is_js_null(result):
                return None
            return result
        finally:
            _record("record_kv", t0)

    async def put(self, key: str, value: str, **kwargs: Any) -> None:
        """Store a value with optional ``expirationTtl``."""
        t0 = time.monotonic()
        try:
            await self._kv.put(key, value, **kwargs)
        finally:
            _record("record_kv", t0)

    async def delete(self, key: str) -> None:
        """Delete a key."""
        t0 = time.monotonic()
        try:
            await self._kv.delete(key)
        finally:
            _record("record_kv", t0)


class SafeQueue:
    """Wraps a Queue producer binding with automatic dict→JS conversion."""

    def __init__(self, queue: Any) -> None:
        self._queue = queue

    async def send(self, message: Any, **kwargs: Any) -> None:
        """Send a message with automatic dict→JS Object conversion.

        Injects ``enqueued_at`` (ISO timestamp) into dict messages for
        queue backpressure visibility.  The consumer computes
        ``queue.wait_ms`` from this field.
        """
        t0 = time.monotonic()
        try:
            if isinstance(message, dict):
                from datetime import UTC, datetime

                message["enqueued_at"] = datetime.now(UTC).isoformat()
                message = _to_js_value(message)
            await self._queue.send(message, **kwargs)
        finally:
            _record("record_queue", t0)


class SafeAI:
    """Wraps a Workers AI binding with automatic input conversion."""

    def __init__(self, ai: Any) -> None:
        self._ai = ai

    async def run(self, model: str, inputs: Any = None, **kwargs: Any) -> Any:
        """Run an AI model with automatic dict→JS Object input conversion."""
        t0 = time.monotonic()
        try:
            if isinstance(inputs, dict):
                inputs = _to_js_value(inputs)
            result = await self._ai.run(model, inputs, **kwargs)
            if result is None or is_js_null(result):
                return None
            return result
        finally:
            _record("record_ai", t0)


class SafeReadability:
    """Wraps a Readability JS Worker Service Binding with automatic result conversion."""

    def __init__(self, binding: Any) -> None:
        self._binding = binding

    async def parse(self, html: str, url: str) -> dict[str, Any]:
        """Extract article content via the Readability Service Binding.

        Parameters
        ----------
        html:
            Raw HTML of the fetched page.
        url:
            Final URL after redirects (used by Readability for resolving relative URLs).

        Returns
        -------
        dict
            Keys: ``title``, ``html``, ``excerpt``, ``byline`` — matching
            the contract of ``extraction.extract_article()``.
        """
        t0 = time.monotonic()
        try:
            result = await self._binding.parse(html, url)
            return _to_py_safe(result)
        finally:
            _record("record_service_binding", t0)


# ---------------------------------------------------------------------------
# SafeEnv — typed, safe access to all Worker bindings
# ---------------------------------------------------------------------------


class SafeEnv:
    """Construction-time wrapper for a Worker ``env`` object.

    Wraps every Cloudflare binding at ``__init__`` time so application code
    can never accidentally bypass the FFI boundary::

        env = SafeEnv(request.scope["env"])
        # env.DB is SafeD1 — auto-converts None→null, JsProxy→dict
        # env.CONTENT is SafeR2 — auto-converts bytes→Uint8Array
        # env.SESSIONS is SafeKV
        # env.ARTICLE_QUEUE is SafeQueue — auto-converts dict→JS Object
        # env.AI is SafeAI — auto-converts dict→JS Object
        # env.READABILITY is SafeReadability — auto-converts JsProxy→dict
        # env.get("SITE_URL") still works for env vars

    Idempotent: wrapping an already-wrapped ``SafeEnv`` returns itself.
    """

    def __init__(self, env: Any) -> None:
        # Idempotency guard — don't double-wrap
        if isinstance(env, SafeEnv):
            self._env = env._env
            self.DB = env.DB
            self.CONTENT = env.CONTENT
            self.SESSIONS = env.SESSIONS
            self.ARTICLE_QUEUE = env.ARTICLE_QUEUE
            self.AI = env.AI
            self.READABILITY = env.READABILITY
            return

        self._env = env
        db = getattr(env, "DB", None)
        self.DB = SafeD1(db) if db is not None else None
        content = getattr(env, "CONTENT", None)
        self.CONTENT = SafeR2(content) if content is not None else None
        sessions = getattr(env, "SESSIONS", None)
        self.SESSIONS = SafeKV(sessions) if sessions is not None else None
        queue = getattr(env, "ARTICLE_QUEUE", None)
        self.ARTICLE_QUEUE = SafeQueue(queue) if queue is not None else None
        ai = getattr(env, "AI", None)
        self.AI = SafeAI(ai) if ai is not None else None
        readability = getattr(env, "READABILITY", None)
        self.READABILITY = SafeReadability(readability) if readability is not None else None

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
    if results is None or is_js_null(results):
        return []

    converted = _to_py_safe(results)

    # Handle both attribute access (.results) and dict access (["results"])
    if isinstance(converted, list):
        # .all() returned a bare list (no wrapper) — use directly
        rows = converted
    elif isinstance(converted, dict):
        rows = converted.get("results", [])
    elif hasattr(converted, "results"):
        rows = _to_py_safe(converted.results)
    else:
        rows = []

    if rows is None:
        return []

    return [dict(row) if not isinstance(row, dict) else row for row in rows if row is not None]


def d1_first(results: Any) -> dict[str, Any] | None:
    """Convert D1 ``stmt.first()`` result to a Python dict or ``None``.

    D1's ``.first()`` returns a single row object (JsProxy) or ``null``
    (which becomes ``None`` on the Python side).

    Outside Pyodide (in tests), *results* is expected to already be a dict
    or ``None``.
    """
    if results is None or is_js_null(results):
        return None

    converted = _to_py_safe(results)

    if converted is None:
        return None

    if isinstance(converted, dict):
        # D1's .first() in Pyodide may return the full result wrapper
        # ({results: [...], success, meta}) instead of just the row.
        # Detect and unwrap this case.
        if "results" in converted and "success" in converted:
            rows = converted.get("results")
            if isinstance(rows, list) and rows:
                return rows[0]
            return None
        # An empty dict has no useful row data.
        if not converted:
            return None
        return converted

    # .first() might return a list instead of a single row
    if isinstance(converted, list):
        return converted[0] if converted else None

    # Final attempt — if it has __dict__ (e.g. SimpleNamespace, JsProxy after to_py),
    # extract its attributes as a dict.
    if hasattr(converted, "__dict__"):
        result = dict(vars(converted))
        return result if result else None

    try:
        return dict(converted)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Cross-runtime HTTP
# ---------------------------------------------------------------------------


class HttpError(Exception):
    """Raised by ``HttpResponse.raise_for_status()`` for 4xx/5xx responses."""

    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


@dataclass
class HttpResponse:
    """HTTP response wrapper that works across Pyodide and CPython.

    Stores the response body as bytes so both text and binary (images,
    screenshots) payloads are supported.
    """

    status_code: int
    _body: bytes
    url: str = ""
    _headers: dict[str, str] | None = None

    def json(self) -> Any:
        """Parse the response body as JSON."""
        import json as _json

        return _json.loads(self._body)

    @property
    def text(self) -> str:
        """Return the response body decoded as UTF-8."""
        return self._body.decode("utf-8")

    @property
    def content(self) -> bytes:
        """Return the raw response body bytes."""
        return self._body

    @property
    def headers(self) -> dict[str, str]:
        """Return response headers as a dict."""
        return self._headers or {}

    def raise_for_status(self) -> None:
        """Raise :class:`HttpError` if the status code is 4xx or 5xx."""
        if self.status_code >= 400:
            raise HttpError(self.status_code, self.text[:500])


async def http_fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    form_data: dict[str, str] | None = None,
    json_data: Any = None,
    timeout: float = 10.0,
    follow_redirects: bool = True,
) -> HttpResponse:
    """Perform an HTTP request that works in both Pyodide and CPython.

    In the Workers runtime (Pyodide), uses the native JS ``fetch()`` API via
    the FFI.  httpx does not reliably transmit request headers when running
    inside workerd, which causes 403s from APIs that require User-Agent.

    In CPython (tests), falls back to httpx.
    """
    t0 = time.monotonic()
    try:
        all_headers = dict(headers or {})

        if json_data is not None:
            import json as _json

            body = _json.dumps(json_data)
            all_headers.setdefault("Content-Type", "application/json")

        if form_data:
            from urllib.parse import urlencode

            body = urlencode(form_data)
            all_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

        if HAS_PYODIDE:
            import asyncio

            opts: dict[str, Any] = {"method": method, "headers": all_headers}
            if body is not None:
                opts["body"] = body
            if not follow_redirects:
                opts["redirect"] = "manual"
            js_opts = _to_js_value(opts)

            try:
                response = await asyncio.wait_for(js.fetch(url, js_opts), timeout=timeout)
            except TimeoutError as exc:
                raise TimeoutError(f"Request timed out after {timeout}s") from exc
            except Exception as exc:
                msg = str(exc)
                if "timeout" in msg.lower():
                    raise TimeoutError(msg) from exc
                raise ConnectionError(msg) from exc

            array_buffer = await response.arrayBuffer()
            body_bytes = to_py_bytes(array_buffer)

            # Convert JS Headers to a Python dict
            try:
                headers_obj = js.Object.fromEntries(response.headers.entries())
                resp_headers = _to_py_safe(headers_obj) or {}
            except Exception:
                resp_headers = {}

            return HttpResponse(
                status_code=int(response.status),
                _body=body_bytes,
                url=str(response.url) if response.url else url,
                _headers=resp_headers,
            )
        else:
            import httpx

            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout),
                    follow_redirects=follow_redirects,
                ) as client:
                    resp = await client.request(method, url, headers=all_headers, content=body)
                    return HttpResponse(
                        status_code=resp.status_code,
                        _body=resp.content,
                        url=str(resp.url),
                        _headers=dict(resp.headers),
                    )
            except httpx.TimeoutException as exc:
                raise TimeoutError(str(exc)) from exc
            except httpx.ConnectError as exc:
                raise ConnectionError(str(exc)) from exc
    finally:
        _record("record_http", t0)

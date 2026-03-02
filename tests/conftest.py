"""Shared test fixtures and mock infrastructure for Tasche.

Provides mock implementations of all Cloudflare bindings so that application
code can be tested without the Workers runtime.  Every mock stores data
in-memory and exposes the same async interface that the real bindings provide.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest  # noqa: I001
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth.session import COOKIE_NAME, create_session
from src.utils import generate_id

# ---------------------------------------------------------------------------
# Mock D1 (Database)
# ---------------------------------------------------------------------------


class MockD1Statement:
    """Simulates a D1 prepared statement.

    Stores the SQL and bound parameters, then delegates execution to the
    parent ``MockD1`` instance's ``_execute`` callback.
    """

    def __init__(self, sql: str, db: MockD1) -> None:
        self._sql = sql
        self._db = db
        self._params: list[Any] = []

    def bind(self, *args: Any) -> MockD1Statement:
        """Bind positional parameters to the statement.

        Validates that the number of bound parameters matches the number
        of ``?`` placeholders in the SQL statement.
        """
        expected = len(re.findall(r"\?", self._sql))
        actual = len(args)
        if actual != expected:
            raise ValueError(
                f"Parameter count mismatch: SQL has {expected} placeholder(s) "
                f"but {actual} parameter(s) were bound. SQL: {self._sql!r}"
            )
        self._params = list(args)
        return self

    async def all(self) -> dict[str, Any]:
        """Execute and return all matching rows."""
        rows = self._db._execute(self._sql, self._params)
        return {"results": rows, "success": True}

    async def first(self) -> dict[str, Any] | None:
        """Execute and return the first matching row, or ``None``."""
        rows = self._db._execute(self._sql, self._params)
        if rows:
            return rows[0]
        return None

    async def run(self) -> dict[str, Any]:
        """Execute a statement that does not return rows (INSERT/UPDATE/DELETE).

        Tracks row changes: ``meta.changes`` reflects the number of rows
        returned by ``_execute`` (0 when no match, otherwise the count).
        """
        rows = self._db._execute(self._sql, self._params)
        if isinstance(rows, dict) and "changes" in rows:
            changes = rows["changes"]
        elif rows:
            changes = len(rows)
        elif self._is_write_statement():
            changes = 1
        else:
            changes = 0
        return {"success": True, "meta": {"changes": changes}}

    def _is_write_statement(self) -> bool:
        """Check if the SQL is a write statement (INSERT/UPDATE/DELETE)."""
        stripped = self._sql.strip().upper()
        return stripped.startswith(("INSERT", "UPDATE", "DELETE"))


class MockD1:
    """In-memory mock of a Cloudflare D1 database binding.

    By default, ``_execute`` returns an empty list.  Override it via the
    constructor or by assigning a new callable to customise query results::

        db = MockD1(execute=lambda sql, params: [{"id": "1", "title": "Test"}])
    """

    def __init__(self, execute: Any | None = None) -> None:
        self._execute_fn = execute

    def _execute(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        if self._execute_fn is not None:
            return self._execute_fn(sql, params)
        return []

    def prepare(self, sql: str) -> MockD1Statement:
        """Create a prepared statement."""
        return MockD1Statement(sql, self)


# ---------------------------------------------------------------------------
# Mock KV (Key-Value) with TTL tracking
# ---------------------------------------------------------------------------


class MockKV:
    """In-memory mock of a Cloudflare KV namespace binding.

    Supports TTL tracking: when ``expirationTtl`` is passed to ``put()``,
    the key will expire after the given number of seconds.  Use
    ``advance_time(seconds)`` to simulate time progression, or expired keys
    will be checked against real wall-clock time.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._expiry: dict[str, float] = {}
        self._time_offset: float = 0.0

    def _now(self) -> float:
        """Return the current (possibly simulated) time."""
        return time.monotonic() + self._time_offset

    def advance_time(self, seconds: float) -> None:
        """Advance the simulated clock by *seconds*.

        After calling this, any keys whose TTL has elapsed will appear
        expired on the next ``get()`` call.
        """
        self._time_offset += seconds

    async def get(self, key: str, **kwargs: Any) -> str | None:
        """Retrieve a value by key.  Returns ``None`` if not found or expired."""
        if key in self._expiry and self._now() >= self._expiry[key]:
            # Key has expired — clean up and return None
            self._store.pop(key, None)
            del self._expiry[key]
            return None
        return self._store.get(key)

    async def put(self, key: str, value: str, **kwargs: Any) -> None:
        """Store a value.  Honors ``expirationTtl`` (seconds) for TTL tracking."""
        self._store[key] = value
        ttl = kwargs.get("expirationTtl")
        if ttl is not None:
            self._expiry[key] = self._now() + ttl
        else:
            # No TTL — remove any previous expiry
            self._expiry.pop(key, None)

    async def delete(self, key: str) -> None:
        """Delete a key."""
        self._store.pop(key, None)
        self._expiry.pop(key, None)


# ---------------------------------------------------------------------------
# Mock R2 (Object Storage) with httpMetadata support
# ---------------------------------------------------------------------------


@dataclass
class MockR2Object:
    """Represents an object returned from ``MockR2.get()``."""

    key: str
    body: bytes
    size: int = 0
    httpMetadata: dict[str, str] = field(default_factory=dict)

    async def text(self) -> str:
        return self.body.decode("utf-8")

    async def arrayBuffer(self) -> bytes:
        return self.body


class MockR2:
    """In-memory mock of a Cloudflare R2 bucket binding.

    Stores and returns ``httpMetadata`` on ``put()``/``get()``.
    """

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self._metadata: dict[str, dict[str, str]] = {}

    async def put(self, key: str, value: bytes | str, **kwargs: Any) -> None:
        """Store an object.  Preserves ``httpMetadata`` if provided."""
        if isinstance(value, str):
            value = value.encode("utf-8")
        self._store[key] = value
        http_metadata = kwargs.get("httpMetadata")
        if http_metadata is not None:
            self._metadata[key] = dict(http_metadata)
        else:
            self._metadata.pop(key, None)

    async def get(self, key: str) -> MockR2Object | None:
        """Retrieve an object.  Returns ``None`` if not found."""
        data = self._store.get(key)
        if data is None:
            return None
        return MockR2Object(
            key=key,
            body=data,
            size=len(data),
            httpMetadata=self._metadata.get(key, {}),
        )

    async def delete(self, key: str) -> None:
        """Delete an object."""
        self._store.pop(key, None)
        self._metadata.pop(key, None)

    async def list(self, **kwargs: Any) -> dict[str, Any]:
        """List objects.  Supports optional ``prefix`` filtering and pagination."""
        prefix = kwargs.get("prefix", "")
        cursor = kwargs.get("cursor")
        page_size = kwargs.get("limit", 1000)

        all_objects = sorted(
            [{"key": k, "size": len(v)} for k, v in self._store.items() if k.startswith(prefix)],
            key=lambda o: o["key"],
        )

        # Simulate cursor-based pagination
        start = 0
        if cursor is not None:
            for i, obj in enumerate(all_objects):
                if obj["key"] > cursor:
                    start = i
                    break
            else:
                return {"objects": [], "truncated": False}

        end = start + page_size
        page = all_objects[start:end]
        truncated = end < len(all_objects)
        result: dict[str, Any] = {"objects": page, "truncated": truncated}
        if truncated:
            result["cursor"] = page[-1]["key"]
        return result


# ---------------------------------------------------------------------------
# Mock Queue
# ---------------------------------------------------------------------------


class MockQueue:
    """In-memory mock of a Cloudflare Queue producer binding."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, message: Any, **kwargs: Any) -> None:
        """Enqueue a message.  The body is stored as-is."""
        if isinstance(message, str):
            message = json.loads(message)
        self.messages.append(message)


# ---------------------------------------------------------------------------
# Mock AI
# ---------------------------------------------------------------------------


class MockAI:
    """In-memory mock of the Workers AI binding.

    Returns a configurable response for any model invocation.
    """

    def __init__(self, response: Any = None) -> None:
        self._response = response or b""
        self.calls: list[dict[str, Any]] = []

    async def run(self, model: str, inputs: Any = None, **kwargs: Any) -> Any:
        """Record the call and return the configured response.

        Accepts inputs as a positional arg (matching the Workers AI calling
        convention ``ai.run(model, inputs)``) or as keyword arguments.
        """
        call: dict[str, Any] = {"model": model}
        if inputs is not None:
            # inputs may be a dict (in tests) or a JsProxy (in production)
            if isinstance(inputs, dict):
                call.update(inputs)
            else:
                call["inputs"] = inputs
        call.update(kwargs)
        self.calls.append(call)
        return self._response


class MockReadability:
    """In-memory mock of the Readability Service Binding.

    Returns a configurable extraction result for any ``parse()`` call.
    """

    def __init__(self, response: dict[str, Any] | None = None) -> None:
        self._response = response or {
            "title": "Test Article",
            "html": "<p>Test content with enough text for processing.</p>",
            "excerpt": "Test content with enough text for processing.",
            "byline": "Test Author",
        }
        self.calls: list[dict[str, str]] = []

    async def parse(self, html: str, url: str) -> dict[str, Any]:
        """Record the call and return the configured response."""
        self.calls.append({"html_length": str(len(html)), "url": url})
        return self._response


# ---------------------------------------------------------------------------
# Mock Env (combines all bindings)
# ---------------------------------------------------------------------------


class MockEnv:
    """Aggregates all mock bindings under the attribute names used in
    ``wrangler.jsonc``.

    Binding names:
    - ``DB`` — D1 database
    - ``CONTENT`` — R2 bucket
    - ``SESSIONS`` — KV namespace
    - ``ARTICLE_QUEUE`` — Queue producer
    - ``AI`` — Workers AI
    - ``READABILITY`` — Readability Service Binding (optional)
    - ``ALLOWED_EMAILS`` — env var (string)
    - ``SITE_URL`` — env var (string)
    """

    def __init__(
        self,
        *,
        db: MockD1 | None = None,
        content: MockR2 | None = None,
        sessions: MockKV | None = None,
        article_queue: MockQueue | None = None,
        ai: MockAI | None = None,
        readability: MockReadability | None = None,
        allowed_emails: str = "test@example.com",
        site_url: str = "https://tasche.test",
        github_client_id: str = "test_client_id",
        github_client_secret: str = "test_client_secret",
        disable_auth: str | None = None,
        tts_model: str | None = None,
    ) -> None:
        self.DB = db or MockD1()
        self.CONTENT = content or MockR2()
        self.SESSIONS = sessions or MockKV()
        self.ARTICLE_QUEUE = article_queue or MockQueue()
        self.AI = ai or MockAI()
        self.READABILITY = readability
        self.ALLOWED_EMAILS = allowed_emails
        self.SITE_URL = site_url
        self.GITHUB_CLIENT_ID = github_client_id
        self.GITHUB_CLIENT_SECRET = github_client_secret
        self.DISABLE_AUTH = disable_auth
        self.TTS_MODEL = tts_model


# ---------------------------------------------------------------------------
# Article factory
# ---------------------------------------------------------------------------


class ArticleFactory:
    """Convenience factory for creating article dicts in tests.

    Provides sensible defaults for every field so tests only need to
    override the fields they care about::

        article = ArticleFactory.create(title="Custom Title")
    """

    _counter = 0

    @classmethod
    def create(cls, **overrides: Any) -> dict[str, Any]:
        """Return a complete article dict with defaults and *overrides* applied."""
        cls._counter += 1
        n = cls._counter

        article_id = overrides.get("id", generate_id())

        defaults: dict[str, Any] = {
            "id": article_id,
            "user_id": "user_001",
            "original_url": f"https://example.com/article-{n}",
            "final_url": f"https://example.com/article-{n}",
            "canonical_url": f"https://example.com/article-{n}",
            "domain": "example.com",
            "title": f"Test Article {n}",
            "excerpt": f"Excerpt for article {n}.",
            "author": "Test Author",
            "word_count": 1200,
            "reading_time_minutes": 6,
            "image_count": 3,
            "status": "ready",
            "reading_status": "unread",
            "is_favorite": 0,
            "audio_key": None,
            "audio_duration_seconds": None,
            "audio_status": None,
            "html_key": f"articles/{article_id}/content.html",
            "thumbnail_key": f"articles/{article_id}/thumbnail.webp",
            "original_key": None,
            "markdown_content": f"# Test Article {n}\n\nThis is the markdown content.",
            "original_status": "unknown",
            "scroll_position": 0.0,
            "reading_progress": 0.0,
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-01T00:00:00",
        }

        defaults.update(overrides)
        return defaults


# ---------------------------------------------------------------------------
# Shared user data constant
# ---------------------------------------------------------------------------

USER_DATA: dict[str, Any] = {
    "user_id": "user_001",
    "email": "test@example.com",
    "username": "tester",
    "avatar_url": "https://github.com/avatar.png",
    "created_at": "2025-01-01T00:00:00",
}

# ---------------------------------------------------------------------------
# Unified TrackingD1
# ---------------------------------------------------------------------------


class TrackingD1(MockD1):
    """MockD1 that records all SQL statements executed against it.

    Optionally accepts a ``result_fn(sql, params)`` callback to return
    custom results for specific queries.  When no callback is provided,
    all queries return an empty list.

    Usage::

        db = TrackingD1()  # returns [] for everything
        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
    """

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
# SQL param parsing helper
# ---------------------------------------------------------------------------


def parse_update_params(sql: str, params: list[Any]) -> dict[str, Any]:
    """Parse an UPDATE ... SET col1 = ?, col2 = ? ... statement into a dict.

    Maps each SET column name to its corresponding bound parameter value.
    Also includes a ``_where`` key with the WHERE clause parameters.

    Example::

        sql = "UPDATE articles SET title = ?, status = ? WHERE id = ?"
        params = ["My Title", "ready", "art_001"]
        result = parse_update_params(sql, params)
        # {"title": "My Title", "status": "ready", "_where": ["art_001"]}
    """
    # Extract the SET clause between SET and WHERE
    set_match = re.search(r"SET\s+(.*?)\s+WHERE", sql, re.IGNORECASE | re.DOTALL)
    if not set_match:
        return {"_raw_params": params}

    set_clause = set_match.group(1)
    # Split on commas, extract column names (before " = ?")
    columns = []
    for part in set_clause.split(","):
        part = part.strip()
        col_match = re.match(r"(\w+)\s*=\s*\?", part)
        if col_match:
            columns.append(col_match.group(1))

    result: dict[str, Any] = {}
    for i, col in enumerate(columns):
        if i < len(params):
            result[col] = params[i]

    # Remaining params are WHERE clause params
    result["_where"] = params[len(columns) :]
    return result


# ---------------------------------------------------------------------------
# Shared test app helpers
# ---------------------------------------------------------------------------


def _make_test_app(env: Any, *routers: tuple[Any, str]) -> FastAPI:
    """Create a FastAPI app with env injection and the given routers.

    Wraps ``env`` in ``SafeEnv`` to match the production entry point
    (``entry.py``) which wraps env before any handler sees it.

    Each router argument should be a ``(router, prefix)`` tuple::

        app = _make_test_app(env, (articles_router, "/api/articles"))
        app = _make_test_app(
            env, (tags_router, "/api/tags"), (article_tags_router, "/api/articles"),
        )
    """
    from src.wrappers import SafeEnv

    test_app = FastAPI()
    safe_env = SafeEnv(env)

    @test_app.middleware("http")
    async def inject_env(request, call_next):
        request.scope["env"] = safe_env
        return await call_next(request)

    for router_item, prefix in routers:
        test_app.include_router(router_item, prefix=prefix)

    return test_app


async def _authenticated_client(
    env: MockEnv,
    *routers: tuple[Any, str],
    user_data: dict[str, Any] | None = None,
) -> tuple[TestClient, str]:
    """Create a test client with a valid session cookie.

    Builds the app with the given routers and returns a ``(client, session_id)``
    tuple ready for making authenticated requests.  The session cookie is set
    on the client instance so callers do not need to pass ``cookies=`` per
    request::

        client, sid = await _authenticated_client(
            env,
            (articles_router, "/api/articles"),
        )
        resp = client.get("/api/articles")
    """
    data = user_data or USER_DATA
    session_id = await create_session(env.SESSIONS, data)
    app = _make_test_app(env, *routers)
    client = TestClient(app, raise_server_exceptions=False, cookies={COOKIE_NAME: session_id})
    return client, session_id


# ---------------------------------------------------------------------------
# Processing test helpers (moved from test_processing.py)
# ---------------------------------------------------------------------------

SAMPLE_HTML = """
<html>
<head>
    <title>Test Article Title</title>
    <link rel="canonical" href="https://example.com/canonical-url">
</head>
<body>
    <article>
        <h1>Test Article Title</h1>
        <p>This is the first paragraph with enough content for readability
        to consider it as the main article body. We need substantial text
        here so the extraction algorithm can identify the primary content
        area of the page and extract it correctly.</p>
        <p>Second paragraph with additional text to pad the content and
        ensure that readability treats this as a real article. The algorithm
        uses various heuristics including text length, paragraph count,
        and link density to determine what constitutes an article.</p>
        <p>Third paragraph provides even more content. This should give us
        enough text to count words and calculate a reasonable reading time
        estimate for our tests.</p>
        <img src="https://cdn.example.com/photo1.jpg">
        <img src="https://cdn.example.com/photo2.jpg">
    </article>
</body>
</html>
"""


def _make_mock_response(
    *,
    status_code: int = 200,
    text: str = SAMPLE_HTML,
    content: bytes | None = None,
    url: str = "https://example.com/article",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Create a mock HTTP response compatible with HttpResponse interface."""
    from src.wrappers import HttpError

    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    # Default content to encoded text so resp.content stays consistent with resp.text
    resp.content = content if content is not None else text.encode("utf-8")
    resp.url = url
    resp.headers = headers or {"content-type": "text/html"}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = HttpError(status_code, f"HTTP {status_code}")
    return resp


async def _noop_screenshot(url, account_id, api_token, **kwargs):
    """Mock screenshot that returns fake image data."""
    return b"FAKE_SCREENSHOT"


def _browser_env(env: MockEnv) -> MockEnv:
    """Add Browser Rendering config to a MockEnv."""
    env.CF_ACCOUNT_ID = "test-account"
    env.CF_API_TOKEN = "test-token"
    return env


def _make_mock_http_fetch(
    page_response: MagicMock | None = None,
    image_response: MagicMock | None = None,
) -> AsyncMock:
    """Create a mock http_fetch function.

    The first call returns *page_response* (the HTML page), subsequent
    calls return *image_response* (downloaded images).
    """
    if page_response is None:
        page_response = _make_mock_response()
    if image_response is None:
        image_response = _make_mock_response(
            content=b"fake-image-bytes",
            headers={"content-type": "image/jpeg"},
        )

    call_count = 0

    async def _mock_fetch(url, **kwargs):
        nonlocal call_count
        call_count += 1
        # First call is the page fetch, subsequent calls are image downloads
        if call_count == 1:
            return page_response
        return image_response

    return AsyncMock(side_effect=_mock_fetch)


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_env() -> MockEnv:
    """Return a ``MockEnv`` with all bindings initialised to defaults."""
    return MockEnv()


@pytest.fixture
def mock_db() -> MockD1:
    """Return a standalone ``MockD1`` instance."""
    return MockD1()


@pytest.fixture
def mock_r2() -> MockR2:
    """Return a standalone ``MockR2`` instance."""
    return MockR2()


@pytest.fixture(autouse=True)
def _reset_article_factory() -> None:
    """Reset ArticleFactory counter between tests to prevent ordering issues."""
    ArticleFactory._counter = 0


# ---------------------------------------------------------------------------
# Test helper factory
# ---------------------------------------------------------------------------


def make_test_helpers(*routers: tuple[Any, str]):
    """Create ``_make_app`` and ``_authenticated_client`` helpers for a set of routers.

    Usage in test files::

        _make_app, _authenticated_client = make_test_helpers(
            (router, "/api/articles"),
        )
    """

    def _make_app(env: Any) -> FastAPI:
        return _make_test_app(env, *routers)

    async def _auth_client(
        env: MockEnv,
        user_data: dict[str, Any] | None = None,
    ) -> tuple[TestClient, str]:
        return await _authenticated_client(env, *routers, user_data=user_data)

    return _make_app, _auth_client

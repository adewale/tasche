"""Shared test fixtures and mock infrastructure for Tasche.

Provides mock implementations of all Cloudflare bindings so that application
code can be tested without the Workers runtime.  Every mock stores data
in-memory and exposes the same async interface that the real bindings provide.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from typing import Any

import pytest  # noqa: I001

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
        """Bind positional parameters to the statement."""
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
        """Execute a statement that does not return rows (INSERT/UPDATE/DELETE)."""
        self._db._execute(self._sql, self._params)
        return {"success": True, "meta": {"changes": 1}}


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
# Mock KV (Key-Value)
# ---------------------------------------------------------------------------


class MockKV:
    """In-memory mock of a Cloudflare KV namespace binding."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str, **kwargs: Any) -> str | None:
        """Retrieve a value by key.  Returns ``None`` if not found."""
        return self._store.get(key)

    async def put(self, key: str, value: str, **kwargs: Any) -> None:
        """Store a value.  Ignores ``expirationTtl`` and other options."""
        self._store[key] = value

    async def delete(self, key: str) -> None:
        """Delete a key."""
        self._store.pop(key, None)


# ---------------------------------------------------------------------------
# Mock R2 (Object Storage)
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
    """In-memory mock of a Cloudflare R2 bucket binding."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def put(self, key: str, value: bytes | str, **kwargs: Any) -> None:
        """Store an object."""
        if isinstance(value, str):
            value = value.encode("utf-8")
        self._store[key] = value

    async def get(self, key: str) -> MockR2Object | None:
        """Retrieve an object.  Returns ``None`` if not found."""
        data = self._store.get(key)
        if data is None:
            return None
        return MockR2Object(key=key, body=data, size=len(data))

    async def delete(self, key: str) -> None:
        """Delete an object."""
        self._store.pop(key, None)

    async def list(self, **kwargs: Any) -> dict[str, Any]:
        """List objects.  Supports optional ``prefix`` filtering and pagination."""
        prefix = kwargs.get("prefix", "")
        cursor = kwargs.get("cursor")
        page_size = kwargs.get("limit", 1000)

        all_objects = sorted(
            [
                {"key": k, "size": len(v)}
                for k, v in self._store.items()
                if k.startswith(prefix)
            ],
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

    async def run(self, model: str, **kwargs: Any) -> Any:
        """Record the call and return the configured response."""
        self.calls.append({"model": model, **kwargs})
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
        allowed_emails: str = "test@example.com",
        site_url: str = "https://tasche.test",
        github_client_id: str = "test_client_id",
        github_client_secret: str = "test_client_secret",
    ) -> None:
        self.DB = db or MockD1()
        self.CONTENT = content or MockR2()
        self.SESSIONS = sessions or MockKV()
        self.ARTICLE_QUEUE = article_queue or MockQueue()
        self.AI = ai or MockAI()
        self.ALLOWED_EMAILS = allowed_emails
        self.SITE_URL = site_url
        self.GITHUB_CLIENT_ID = github_client_id
        self.GITHUB_CLIENT_SECRET = github_client_secret


# ---------------------------------------------------------------------------
# Article factory
# ---------------------------------------------------------------------------


def _make_id() -> str:
    return secrets.token_urlsafe(16)


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

        article_id = overrides.get("id", _make_id())

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
def mock_kv() -> MockKV:
    """Return a standalone ``MockKV`` instance."""
    return MockKV()


@pytest.fixture
def mock_r2() -> MockR2:
    """Return a standalone ``MockR2`` instance."""
    return MockR2()


@pytest.fixture
def mock_queue() -> MockQueue:
    """Return a standalone ``MockQueue`` instance."""
    return MockQueue()


@pytest.fixture(autouse=True)
def _reset_article_factory() -> None:
    """Reset ArticleFactory counter between tests to prevent ordering issues."""
    ArticleFactory._counter = 0


@pytest.fixture
def article_factory() -> type[ArticleFactory]:
    """Return the ``ArticleFactory`` class for use in tests."""
    return ArticleFactory

"""Tests for Feed CRUD API (src/feeds/routes.py).

Covers listing, adding, deleting feeds, refreshing, and OPML import.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.auth.session import COOKIE_NAME
from src.feeds.routes import router
from tests.conftest import (
    MockD1,
    MockEnv,
    _make_test_app,
    make_feed,
)
from tests.conftest import (
    _authenticated_client as _authenticated_client_base,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROUTERS = ((router, "/api/feeds"),)


def _make_app(env):
    return _make_test_app(env, *_ROUTERS)


async def _authenticated_client(env: MockEnv) -> tuple[TestClient, str]:
    return await _authenticated_client_base(env, *_ROUTERS)


# Sample RSS for validation during add
_SAMPLE_RSS = """\
<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    <item>
      <title>Post 1</title>
      <link>https://example.com/post-1</link>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


# ---------------------------------------------------------------------------
# GET /api/feeds — List feeds
# ---------------------------------------------------------------------------


class TestListFeeds:
    async def test_lists_feeds(self) -> None:
        feed = make_feed()

        def execute(sql: str, params: list) -> list:
            if "SELECT * FROM feeds" in sql:
                return [feed]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        resp = client.get("/api/feeds", cookies={COOKIE_NAME: session_id})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Example Feed"

    async def test_empty_list(self) -> None:
        db = MockD1()
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        resp = client.get("/api/feeds", cookies={COOKIE_NAME: session_id})
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_requires_auth(self) -> None:
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api/feeds")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/feeds — Add feed
# ---------------------------------------------------------------------------


class TestAddFeed:
    async def test_adds_feed_successfully(self) -> None:
        inserts = []

        def execute(sql: str, params: list) -> list:
            if "SELECT id FROM feeds WHERE url" in sql:
                return []  # No duplicate
            if "INSERT INTO feeds" in sql:
                inserts.append(params)
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        # Mock the HTTP fetch for feed validation
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.text = _SAMPLE_RSS
        mock_resp.raise_for_status = lambda: None

        with patch("src.feeds.routes.HttpClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_client_instance

            resp = client.post(
                "/api/feeds",
                json={"url": "https://example.com/feed.xml"},
                cookies={COOKIE_NAME: session_id},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["title"] == "Test Feed"
        assert data["url"] == "https://example.com/feed.xml"
        assert len(inserts) == 1

    async def test_rejects_empty_url(self) -> None:
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/feeds",
            json={"url": ""},
            cookies={COOKIE_NAME: session_id},
        )
        assert resp.status_code == 422

    async def test_rejects_non_http_url(self) -> None:
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/feeds",
            json={"url": "ftp://example.com/feed"},
            cookies={COOKIE_NAME: session_id},
        )
        assert resp.status_code == 422

    async def test_rejects_duplicate_url(self) -> None:
        def execute(sql: str, params: list) -> list:
            if "SELECT id FROM feeds WHERE url" in sql:
                return [{"id": "existing_feed"}]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/feeds",
            json={"url": "https://example.com/feed.xml"},
            cookies={COOKIE_NAME: session_id},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# DELETE /api/feeds/{feed_id}
# ---------------------------------------------------------------------------


class TestDeleteFeed:
    async def test_deletes_feed(self) -> None:
        feed = make_feed()
        deleted = []

        def execute(sql: str, params: list) -> list:
            if "SELECT id FROM feeds WHERE id" in sql:
                return [feed]
            if "DELETE FROM feeds" in sql:
                deleted.append(params)
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        resp = client.delete(
            "/api/feeds/feed_001",
            cookies={COOKIE_NAME: session_id},
        )
        assert resp.status_code == 204
        assert len(deleted) == 1

    async def test_returns_404_for_missing_feed(self) -> None:
        db = MockD1()
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        resp = client.delete(
            "/api/feeds/nonexistent",
            cookies={COOKIE_NAME: session_id},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/feeds/{feed_id}/refresh
# ---------------------------------------------------------------------------


class TestRefreshFeed:
    async def test_refresh_returns_result(self) -> None:
        feed = make_feed()

        def execute(sql: str, params: list) -> list:
            if "SELECT * FROM feeds WHERE id" in sql:
                return [feed]
            if "UPDATE feeds SET" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.text = _SAMPLE_RSS
        mock_resp.raise_for_status = lambda: None

        with patch("src.feeds.processing.HttpClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_client_instance

            resp = client.post(
                "/api/feeds/feed_001/refresh",
                cookies={COOKIE_NAME: session_id},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "new_articles" in data
        assert "errors" in data

    async def test_refresh_returns_404_for_missing_feed(self) -> None:
        db = MockD1()
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/feeds/nonexistent/refresh",
            cookies={COOKIE_NAME: session_id},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/feeds/refresh-all
# ---------------------------------------------------------------------------


class TestRefreshAllFeeds:
    async def test_refresh_all_returns_summary(self) -> None:
        feed = make_feed()

        def execute(sql: str, params: list) -> list:
            if "SELECT * FROM feeds WHERE is_active = 1" in sql:
                return [feed]
            if "UPDATE feeds SET" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.text = _SAMPLE_RSS
        mock_resp.raise_for_status = lambda: None

        with patch("src.feeds.processing.HttpClient") as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client_instance.get = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_client_instance

            resp = client.post(
                "/api/feeds/refresh-all",
                cookies={COOKIE_NAME: session_id},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "feeds_checked" in data
        assert "total_new_articles" in data


# ---------------------------------------------------------------------------
# POST /api/feeds/import-opml
# ---------------------------------------------------------------------------


class TestImportOPML:
    async def test_imports_feeds_from_opml(self) -> None:
        inserts = []

        def execute(sql: str, params: list) -> list:
            if "SELECT id FROM feeds WHERE url" in sql:
                return []  # No duplicates
            if "INSERT INTO feeds" in sql:
                inserts.append(params)
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        opml = """\
<?xml version="1.0"?>
<opml version="2.0">
  <head><title>Import</title></head>
  <body>
    <outline type="rss" text="Feed A" xmlUrl="https://a.example.com/feed"
             htmlUrl="https://a.example.com" />
    <outline type="rss" text="Feed B" xmlUrl="https://b.example.com/rss"
             htmlUrl="https://b.example.com" />
  </body>
</opml>
"""

        resp = client.post(
            "/api/feeds/import-opml",
            json={"opml": opml},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2
        assert data["skipped"] == 0
        assert len(inserts) == 2

    async def test_skips_duplicate_feeds(self) -> None:
        def execute(sql: str, params: list) -> list:
            if "SELECT id FROM feeds WHERE url" in sql:
                return [{"id": "existing"}]  # All are duplicates
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        opml = """\
<?xml version="1.0"?>
<opml version="2.0">
  <head><title>Import</title></head>
  <body>
    <outline type="rss" text="Feed A" xmlUrl="https://a.example.com/feed" />
  </body>
</opml>
"""

        resp = client.post(
            "/api/feeds/import-opml",
            json={"opml": opml},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 0
        assert data["skipped"] == 1

    async def test_rejects_empty_opml(self) -> None:
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/feeds/import-opml",
            json={"opml": ""},
            cookies={COOKIE_NAME: session_id},
        )
        assert resp.status_code == 422

    async def test_rejects_invalid_opml(self) -> None:
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/feeds/import-opml",
            json={"opml": "not valid xml at all"},
            cookies={COOKIE_NAME: session_id},
        )
        assert resp.status_code == 422

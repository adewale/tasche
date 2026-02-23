"""Tests for feed refresh logic (src/feeds/processing.py).

Covers refresh_feed and refresh_all_feeds with mocked HTTP and D1.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from src.feeds.processing import refresh_all_feeds, refresh_feed
from src.wrappers import SafeEnv
from tests.conftest import MockD1, MockEnv, MockQueue, make_feed

# ---------------------------------------------------------------------------
# Sample feed data
# ---------------------------------------------------------------------------

_SAMPLE_RSS = """\
<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Blog</title>
    <link>https://example.com</link>
    <item>
      <title>New Post</title>
      <link>https://example.com/new-post</link>
      <pubDate>Tue, 02 Jan 2024 12:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Old Post</title>
      <link>https://example.com/old-post</link>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def _mock_http_response(text: str = _SAMPLE_RSS, status_code: int = 200):
    """Create a mock HTTP response."""
    resp = AsyncMock()
    resp.status_code = status_code
    resp.text = text
    resp.raise_for_status = lambda: None
    if status_code >= 400:
        from src.wrappers import HttpError

        resp.raise_for_status = lambda: (_ for _ in ()).throw(HttpError(status_code, "error"))
    return resp


def _mock_client(resp):
    """Create a mock HttpClient context manager."""
    mock = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock.get = AsyncMock(return_value=resp)
    return mock


# ---------------------------------------------------------------------------
# refresh_feed tests
# ---------------------------------------------------------------------------


class TestRefreshFeed:
    async def test_creates_articles_for_new_entries(self) -> None:
        """New feed entries should create articles and enqueue them."""
        inserts: list[tuple[str, list]] = []
        queue = MockQueue()

        def execute(sql: str, params: list) -> list:
            if "INSERT INTO articles" in sql:
                inserts.append((sql, params))
                return []
            # check_duplicate returns no match
            if "original_url = ?" in sql:
                return []
            # UPDATE feeds
            if "UPDATE feeds" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db, article_queue=queue))
        feed = make_feed()

        with patch("src.feeds.processing.HttpClient") as MockClient:
            MockClient.return_value = _mock_client(_mock_http_response())
            result = await refresh_feed(env, feed, "user_001")

        assert result["new_articles"] == 2
        assert len(inserts) == 2
        assert len(queue.messages) == 2
        # Verify queue messages are article_processing type
        for msg in queue.messages:
            assert msg["type"] == "article_processing"
            assert msg["user_id"] == "user_001"

    async def test_skips_entries_older_than_last_published(self) -> None:
        """Entries older than last_entry_published should be skipped."""
        inserts: list[tuple[str, list]] = []
        queue = MockQueue()

        def execute(sql: str, params: list) -> list:
            if "INSERT INTO articles" in sql:
                inserts.append((sql, params))
                return []
            if "original_url = ?" in sql:
                return []
            if "UPDATE feeds" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db, article_queue=queue))
        # Set last_entry_published after the "Old Post" date
        feed = make_feed(last_entry_published="Mon, 01 Jan 2024 12:00:00 GMT")

        with patch("src.feeds.processing.HttpClient") as MockClient:
            MockClient.return_value = _mock_client(_mock_http_response())
            result = await refresh_feed(env, feed, "user_001")

        # Only "New Post" should be created (published after last_entry_published)
        assert result["new_articles"] == 1

    async def test_skips_duplicate_urls(self) -> None:
        """Articles that already exist should be skipped."""
        inserts: list[tuple[str, list]] = []
        queue = MockQueue()

        def execute(sql: str, params: list) -> list:
            if "INSERT INTO articles" in sql:
                inserts.append((sql, params))
                return []
            # check_duplicate always finds a match
            if "original_url = ?" in sql:
                return [{"id": "existing", "created_at": "2024-01-01", "status": "ready"}]
            if "UPDATE feeds" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db, article_queue=queue))
        feed = make_feed()

        with patch("src.feeds.processing.HttpClient") as MockClient:
            MockClient.return_value = _mock_client(_mock_http_response())
            result = await refresh_feed(env, feed, "user_001")

        assert result["new_articles"] == 0
        assert len(inserts) == 0

    async def test_handles_fetch_error(self) -> None:
        """HTTP errors should be caught and reported in errors list."""
        queue = MockQueue()

        def execute(sql: str, params: list) -> list:
            if "UPDATE feeds" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db, article_queue=queue))
        feed = make_feed()

        with patch("src.feeds.processing.HttpClient") as MockClient:
            resp = _mock_http_response(status_code=404)
            MockClient.return_value = _mock_client(resp)
            result = await refresh_feed(env, feed, "user_001")

        assert result["new_articles"] == 0
        assert len(result["errors"]) > 0

    async def test_handles_invalid_feed_xml(self) -> None:
        """Invalid XML should be caught and reported in errors list."""
        queue = MockQueue()

        def execute(sql: str, params: list) -> list:
            if "UPDATE feeds" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db, article_queue=queue))
        feed = make_feed()

        with patch("src.feeds.processing.HttpClient") as MockClient:
            MockClient.return_value = _mock_client(
                _mock_http_response(text="<html>Not a feed</html>")
            )
            result = await refresh_feed(env, feed, "user_001")

        assert result["new_articles"] == 0
        assert len(result["errors"]) > 0


# ---------------------------------------------------------------------------
# refresh_all_feeds tests
# ---------------------------------------------------------------------------


class TestRefreshAllFeeds:
    async def test_refreshes_all_active_feeds(self) -> None:
        feed1 = make_feed(id="feed_001", url="https://a.example.com/feed.xml")
        feed2 = make_feed(id="feed_002", url="https://b.example.com/feed.xml")
        queue = MockQueue()

        def execute(sql: str, params: list) -> list:
            if "SELECT * FROM feeds WHERE is_active = 1" in sql:
                return [feed1, feed2]
            if "original_url = ?" in sql:
                return []
            if "INSERT INTO articles" in sql:
                return []
            if "UPDATE feeds" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db, article_queue=queue))

        with patch("src.feeds.processing.HttpClient") as MockClient:
            MockClient.return_value = _mock_client(_mock_http_response())
            result = await refresh_all_feeds(env, "user_001")

        assert result["feeds_checked"] == 2
        assert result["total_new_articles"] >= 0

    async def test_empty_feeds_list(self) -> None:
        """When no active feeds exist, should return zeroes."""
        queue = MockQueue()

        def execute(sql: str, params: list) -> list:
            if "SELECT * FROM feeds WHERE is_active = 1" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = SafeEnv(MockEnv(db=db, article_queue=queue))

        result = await refresh_all_feeds(env, "user_001")

        assert result["feeds_checked"] == 0
        assert result["total_new_articles"] == 0

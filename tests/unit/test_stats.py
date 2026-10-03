"""Tests for the reading statistics endpoint (src/stats/routes.py).

GET /api/stats runs against ``SqliteD1``: real SQLite with the production
migrations. Each test seeds rows and checks the statistics the production SQL
computes from them, so a wrong filter, grouping, window or ordering fails here.
Expected values are counted by hand from the seeded rows.

The streak arithmetic itself has property tests in test_property.py; the tests
here cover the SQL that feeds it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.stats.routes import router
from tests.conftest import MockEnv, make_test_helpers
from tests.sqlite_d1 import SqliteD1

_make_app, _authenticated_client = make_test_helpers((router, "/api/stats"))

USER = "user_001"  # the user _authenticated_client signs in as
OTHER_USER = "user_002"

_ids = count()


def _ago(*, days: float = 0, minutes: float = 0) -> datetime:
    return datetime.now(UTC) - timedelta(days=days, minutes=minutes)


def _add_article(
    db: SqliteD1,
    *,
    user_id: str = USER,
    created: datetime | None = None,
    updated: datetime | None = None,
    **columns: Any,
) -> None:
    """Insert an article the way the app writes it (timestamps from now_iso())."""
    article_id = f"a{next(_ids)}"
    created = created or _ago(days=100)
    db.insert(
        "articles",
        id=article_id,
        user_id=user_id,
        original_url=f"https://example.com/{article_id}",
        created_at=created.isoformat(),
        updated_at=(updated or created).isoformat(),
        **columns,
    )


@pytest.fixture
def db() -> SqliteD1:
    db = SqliteD1()
    for user_id in (USER, OTHER_USER):
        db.insert("users", id=user_id, email=f"{user_id}@example.com", username=user_id)
    return db


async def _stats(db: SqliteD1) -> dict[str, Any]:
    client, _ = await _authenticated_client(MockEnv(db=db))
    resp = client.get("/api/stats")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Response contract
# ---------------------------------------------------------------------------


class TestGetStats:
    async def test_requires_auth(self) -> None:
        client = TestClient(_make_app(MockEnv()), raise_server_exceptions=False)
        assert client.get("/api/stats").status_code == 401

    async def test_empty_library_returns_zeros(self, db: SqliteD1) -> None:
        """SUM/AVG over no rows are NULL in SQL; the API must still return numbers."""
        assert await _stats(db) == {
            "total_articles": 0,
            "total_words_read": 0,
            "articles_by_status": {"unread": 0, "archived": 0},
            "articles_this_week": 0,
            "articles_this_month": 0,
            "archived_this_week": 0,
            "archived_this_month": 0,
            "top_domains": [],
            "reading_streak_days": 0,
            "avg_reading_time_minutes": 0,
            "articles_by_month": [],
        }

    async def test_cache_control(self, db: SqliteD1) -> None:
        client, _ = await _authenticated_client(MockEnv(db=db))
        resp = client.get("/api/stats")
        assert resp.headers["cache-control"] == "private, max-age=120"

    async def test_other_users_articles_are_excluded_from_every_stat(self, db: SqliteD1) -> None:
        for days in (0, 1, 2):
            _add_article(
                db,
                user_id=OTHER_USER,
                created=_ago(days=days),
                reading_status="archived",
                word_count=500,
                reading_time_minutes=4,
                domain="other.example",
            )

        stats = await _stats(db)

        assert stats["total_articles"] == 0
        assert stats["total_words_read"] == 0
        assert stats["articles_by_status"] == {"unread": 0, "archived": 0}
        assert stats["articles_this_week"] == 0
        assert stats["archived_this_month"] == 0
        assert stats["top_domains"] == []
        assert stats["reading_streak_days"] == 0
        assert stats["avg_reading_time_minutes"] == 0
        assert stats["articles_by_month"] == []


# ---------------------------------------------------------------------------
# Totals and breakdowns
# ---------------------------------------------------------------------------


class TestTotals:
    async def test_total_articles_counts_every_status(self, db: SqliteD1) -> None:
        _add_article(db, reading_status="unread", status="pending")
        _add_article(db, reading_status="archived", status="ready")
        _add_article(db, reading_status="unread", status="failed")

        assert (await _stats(db))["total_articles"] == 3

    async def test_words_read_sums_archived_articles_only(self, db: SqliteD1) -> None:
        _add_article(db, reading_status="archived", word_count=1200)
        _add_article(db, reading_status="archived", word_count=300)
        _add_article(db, reading_status="archived", word_count=None)
        _add_article(db, reading_status="unread", word_count=9000)

        assert (await _stats(db))["total_words_read"] == 1500

    async def test_articles_by_status(self, db: SqliteD1) -> None:
        _add_article(db, reading_status="unread")
        _add_article(db, reading_status="unread")
        _add_article(db, reading_status="archived")

        assert (await _stats(db))["articles_by_status"] == {"unread": 2, "archived": 1}

    async def test_avg_reading_time_ignores_missing_values(self, db: SqliteD1) -> None:
        for minutes in (5, 7, 10, None):
            _add_article(db, reading_time_minutes=minutes)

        # (5 + 7 + 10) / 3 = 7.33...; counting the NULL as 0 would give 5.5.
        assert (await _stats(db))["avg_reading_time_minutes"] == 7.3


class TestTopDomains:
    async def test_most_saved_first_and_null_domains_skipped(self, db: SqliteD1) -> None:
        for domain, n in (("b.example", 1), ("a.example", 3), ("c.example", 2), (None, 5)):
            for _ in range(n):
                _add_article(db, domain=domain)

        assert (await _stats(db))["top_domains"] == [
            {"domain": "a.example", "count": 3},
            {"domain": "c.example", "count": 2},
            {"domain": "b.example", "count": 1},
        ]

    async def test_limited_to_ten_most_saved(self, db: SqliteD1) -> None:
        # d1 has 1 article, d2 has 2, ... d11 has 11.
        for n in range(1, 12):
            for _ in range(n):
                _add_article(db, domain=f"d{n}.example")

        domains = [d["domain"] for d in (await _stats(db))["top_domains"]]

        assert domains == [f"d{n}.example" for n in range(11, 1, -1)]


# ---------------------------------------------------------------------------
# Weekly / monthly activity windows
# ---------------------------------------------------------------------------


class TestActivityWindows:
    async def test_saved_counts_use_created_at(self, db: SqliteD1) -> None:
        for days in (1, 6, 8, 29, 31):
            _add_article(db, created=_ago(days=days), updated=_ago(days=0))

        stats = await _stats(db)

        assert stats["articles_this_week"] == 2  # 1 and 6 days ago
        assert stats["articles_this_month"] == 4  # all but 31 days ago

    async def test_archived_counts_use_updated_at_of_archived_articles(self, db: SqliteD1) -> None:
        for days in (2, 10, 40):
            _add_article(
                db, created=_ago(days=60), updated=_ago(days=days), reading_status="archived"
            )
        # Recently touched but not archived: not counted.
        _add_article(db, created=_ago(days=60), updated=_ago(days=1), reading_status="unread")

        stats = await _stats(db)

        assert stats["archived_this_week"] == 1
        assert stats["archived_this_month"] == 2

    async def test_week_window_excludes_articles_just_over_seven_days_old(
        self, db: SqliteD1
    ) -> None:
        """Regression: ISO timestamps ('...T...') were compared as text against
        datetime('now', ...) ('... ...'). 'T' sorts after ' ', so anything saved on
        the cutoff's calendar day counted, up to a day older than the window."""
        _add_article(db, created=_ago(days=7, minutes=-1), updated=_ago(days=7, minutes=-1))
        _add_article(
            db,
            created=_ago(days=7, minutes=1),
            updated=_ago(days=7, minutes=1),
            reading_status="archived",
        )

        stats = await _stats(db)

        assert stats["articles_this_week"] == 1
        assert stats["archived_this_week"] == 0

    async def test_month_window_excludes_articles_just_over_thirty_days_old(
        self, db: SqliteD1
    ) -> None:
        _add_article(db, created=_ago(days=30, minutes=-1))
        _add_article(
            db,
            created=_ago(days=30, minutes=1),
            updated=_ago(days=30, minutes=1),
            reading_status="archived",
        )

        stats = await _stats(db)

        assert stats["articles_this_month"] == 1
        assert stats["archived_this_month"] == 0


class TestArticlesByMonth:
    async def test_merges_saved_and_archived_months_in_order(self, db: SqliteD1) -> None:
        # At least 65 days apart, so each falls in a different calendar month.
        recent, middle, older = _ago(days=5), _ago(days=70), _ago(days=140)
        _add_article(db, created=middle, updated=recent, reading_status="archived")
        _add_article(db, created=older, reading_status="unread")
        _add_article(db, created=older, reading_status="unread")

        assert (await _stats(db))["articles_by_month"] == [
            {"month": older.strftime("%Y-%m"), "saved": 2, "archived": 0},
            {"month": middle.strftime("%Y-%m"), "saved": 1, "archived": 0},
            {"month": recent.strftime("%Y-%m"), "saved": 0, "archived": 1},
        ]

    async def test_excludes_articles_older_than_twelve_months(self, db: SqliteD1) -> None:
        _add_article(db, created=_ago(days=400), updated=_ago(days=380), reading_status="archived")

        assert (await _stats(db))["articles_by_month"] == []


# ---------------------------------------------------------------------------
# Reading streak (the SQL that feeds _calculate_streak)
# ---------------------------------------------------------------------------


class TestReadingStreak:
    async def test_counts_consecutive_archive_days_ending_today(self, db: SqliteD1) -> None:
        for days in (0, 0, 1, 2, 4):  # two archived today; gap at 3 days ago
            _add_article(db, updated=_ago(days=days), reading_status="archived")

        assert (await _stats(db))["reading_streak_days"] == 3

    async def test_can_start_yesterday(self, db: SqliteD1) -> None:
        for days in (1, 2):
            _add_article(db, updated=_ago(days=days), reading_status="archived")

        assert (await _stats(db))["reading_streak_days"] == 2

    async def test_only_archived_articles_count(self, db: SqliteD1) -> None:
        _add_article(db, updated=_ago(days=0), reading_status="unread")
        _add_article(db, updated=_ago(days=1), reading_status="unread")

        assert (await _stats(db))["reading_streak_days"] == 0

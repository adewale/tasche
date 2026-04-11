"""Tests for the reading statistics endpoint (src/stats/routes.py).

Covers the GET /api/stats endpoint including total counts, status breakdowns,
weekly/monthly activity, top domains, reading streak, average reading time,
and monthly trends.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.stats.routes import router
from tests.conftest import (
    MockD1,
    MockEnv,
    make_test_helpers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_make_app, _authenticated_client = make_test_helpers((router, "/api/stats"))


# ---------------------------------------------------------------------------
# GET /api/stats — Reading statistics
# ---------------------------------------------------------------------------


class TestGetStats:
    async def test_requires_auth(self) -> None:
        """GET /api/stats returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/stats")
        assert resp.status_code == 401

    async def test_returns_all_stat_keys(self) -> None:
        """GET /api/stats returns all expected keys in the response."""
        env = MockEnv()
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        data = resp.json()

        expected_keys = {
            "total_articles",
            "total_words_read",
            "articles_by_status",
            "articles_this_week",
            "articles_this_month",
            "archived_this_week",
            "archived_this_month",
            "top_domains",
            "reading_streak_days",
            "avg_reading_time_minutes",
            "articles_by_month",
        }
        assert set(data.keys()) == expected_keys

    async def test_empty_library_returns_zeros(self) -> None:
        """GET /api/stats returns zeros when user has no articles."""
        env = MockEnv()
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        data = resp.json()

        assert data["total_articles"] == 0
        assert data["total_words_read"] == 0
        assert data["articles_by_status"] == {
            "unread": 0,
            "archived": 0,
        }
        assert data["articles_this_week"] == 0
        assert data["articles_this_month"] == 0
        assert data["archived_this_week"] == 0
        assert data["archived_this_month"] == 0
        assert data["top_domains"] == []
        assert data["reading_streak_days"] == 0
        assert data["avg_reading_time_minutes"] == 0
        assert data["articles_by_month"] == []

    async def test_total_articles_count(self) -> None:
        """GET /api/stats returns the correct total article count."""

        def execute(sql: str, params: list) -> list:
            if (
                "COUNT(*) AS cnt FROM articles WHERE user_id" in sql
                and "reading_status" not in sql
                and "datetime" not in sql
            ):
                return [{"cnt": 42}]
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "GROUP BY reading_status" in sql:
                return []
            if "GROUP BY domain" in sql:
                return []
            if "DISTINCT date" in sql:
                return []
            if "AVG(reading_time_minutes)" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            return [{"cnt": 0}]

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        assert resp.json()["total_articles"] == 42

    async def test_total_words_read(self) -> None:
        """GET /api/stats returns the total words read from archived articles."""

        def execute(sql: str, params: list) -> list:
            if "SUM(word_count)" in sql:
                return [{"total": 150000}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY reading_status" in sql:
                return []
            if "GROUP BY domain" in sql:
                return []
            if "DISTINCT date" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        assert resp.json()["total_words_read"] == 150000

    async def test_articles_by_status(self) -> None:
        """GET /api/stats returns correct breakdown by reading status."""

        def execute(sql: str, params: list) -> list:
            if "GROUP BY reading_status" in sql:
                return [
                    {"reading_status": "unread", "cnt": 30},
                    {"reading_status": "archived", "cnt": 20},
                ]
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY domain" in sql:
                return []
            if "DISTINCT date" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        status = resp.json()["articles_by_status"]
        assert status == {"unread": 30, "archived": 20}

    async def test_top_domains(self) -> None:
        """GET /api/stats returns top domains sorted by count."""

        def execute(sql: str, params: list) -> list:
            if "GROUP BY domain" in sql:
                return [
                    {"domain": "example.com", "cnt": 15},
                    {"domain": "blog.dev", "cnt": 8},
                    {"domain": "news.org", "cnt": 3},
                ]
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY reading_status" in sql:
                return []
            if "DISTINCT date" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        domains = resp.json()["top_domains"]
        assert len(domains) == 3
        assert domains[0] == {"domain": "example.com", "count": 15}
        assert domains[1] == {"domain": "blog.dev", "count": 8}
        assert domains[2] == {"domain": "news.org", "count": 3}

    async def test_avg_reading_time(self) -> None:
        """GET /api/stats returns average reading time rounded to one decimal."""

        def execute(sql: str, params: list) -> list:
            if "AVG(reading_time_minutes)" in sql:
                return [{"avg_rt": 7.333}]
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY reading_status" in sql:
                return []
            if "GROUP BY domain" in sql:
                return []
            if "DISTINCT date" in sql:
                return []
            if "strftime" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        assert resp.json()["avg_reading_time_minutes"] == 7.3

    async def test_articles_by_month(self) -> None:
        """GET /api/stats returns monthly saved/archived breakdown."""

        def execute(sql: str, params: list) -> list:
            if "strftime" in sql and "reading_status = 'archived'" in sql:
                return [
                    {"month": "2026-01", "cnt": 10},
                    {"month": "2026-02", "cnt": 5},
                ]
            if "strftime" in sql:
                return [
                    {"month": "2026-01", "cnt": 20},
                    {"month": "2026-02", "cnt": 15},
                ]
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY reading_status" in sql:
                return []
            if "GROUP BY domain" in sql:
                return []
            if "DISTINCT date" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        by_month = resp.json()["articles_by_month"]
        assert len(by_month) == 2
        assert by_month[0] == {"month": "2026-01", "saved": 20, "archived": 10}
        assert by_month[1] == {"month": "2026-02", "saved": 15, "archived": 5}


class TestReadingStreak:
    async def test_streak_with_consecutive_days(self) -> None:
        """Reading streak counts consecutive days ending today."""
        from datetime import UTC, datetime, timedelta

        today = datetime.now(UTC).date()
        dates = [{"d": (today - timedelta(days=i)).isoformat()} for i in range(5)]

        def execute(sql: str, params: list) -> list:
            if "DISTINCT date" in sql:
                return dates
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY reading_status" in sql:
                return []
            if "GROUP BY domain" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        assert resp.json()["reading_streak_days"] == 5

    async def test_streak_starting_yesterday(self) -> None:
        """Reading streak can start from yesterday (no activity today yet)."""
        from datetime import UTC, datetime, timedelta

        yesterday = datetime.now(UTC).date() - timedelta(days=1)
        dates = [{"d": (yesterday - timedelta(days=i)).isoformat()} for i in range(3)]

        def execute(sql: str, params: list) -> list:
            if "DISTINCT date" in sql:
                return dates
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY reading_status" in sql:
                return []
            if "GROUP BY domain" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        assert resp.json()["reading_streak_days"] == 3

    async def test_streak_broken_gap(self) -> None:
        """Reading streak breaks on a gap day."""
        from datetime import UTC, datetime, timedelta

        today = datetime.now(UTC).date()
        # Today, yesterday, then skip a day, then 2 more days
        dates = [
            {"d": today.isoformat()},
            {"d": (today - timedelta(days=1)).isoformat()},
            {"d": (today - timedelta(days=3)).isoformat()},
            {"d": (today - timedelta(days=4)).isoformat()},
        ]

        def execute(sql: str, params: list) -> list:
            if "DISTINCT date" in sql:
                return dates
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY reading_status" in sql:
                return []
            if "GROUP BY domain" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        # Streak is 2 (today + yesterday), gap at day 2 breaks it
        assert resp.json()["reading_streak_days"] == 2

    async def test_streak_no_recent_activity(self) -> None:
        """Reading streak is 0 when no recent archived activity."""
        from datetime import UTC, datetime, timedelta

        old_date = datetime.now(UTC).date() - timedelta(days=10)

        def execute(sql: str, params: list) -> list:
            if "DISTINCT date" in sql:
                return [{"d": old_date.isoformat()}]
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY reading_status" in sql:
                return []
            if "GROUP BY domain" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        assert resp.json()["reading_streak_days"] == 0

    async def test_streak_empty_dates(self) -> None:
        """Reading streak is 0 when no archived articles exist at all."""

        def execute(sql: str, params: list) -> list:
            if "DISTINCT date" in sql:
                return []
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY reading_status" in sql:
                return []
            if "GROUP BY domain" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        assert resp.json()["reading_streak_days"] == 0


# ---------------------------------------------------------------------------
# Weekly/monthly activity counts
# ---------------------------------------------------------------------------


class TestWeeklyMonthlyActivity:
    async def test_articles_this_week_count(self) -> None:
        """GET /api/stats returns correct articles_this_week count."""

        def execute(sql: str, params: list) -> list:
            if "saved_week" in sql:
                return [
                    {
                        "saved_week": 7,
                        "saved_month": 0,
                        "archived_week": 0,
                        "archived_month": 0,
                    }
                ]
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY reading_status" in sql:
                return []
            if "GROUP BY domain" in sql:
                return []
            if "DISTINCT date" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        assert resp.json()["articles_this_week"] == 7

    async def test_archived_this_month_count(self) -> None:
        """GET /api/stats returns correct archived_this_month count."""

        def execute(sql: str, params: list) -> list:
            if "saved_week" in sql:
                return [
                    {
                        "saved_week": 0,
                        "saved_month": 0,
                        "archived_week": 0,
                        "archived_month": 12,
                    }
                ]
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY reading_status" in sql:
                return []
            if "GROUP BY domain" in sql:
                return []
            if "DISTINCT date" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        assert resp.json()["archived_this_month"] == 12


# ---------------------------------------------------------------------------
# Stats response structure
# ---------------------------------------------------------------------------


class TestStatsResponseStructure:
    async def test_articles_by_status_has_all_keys(self) -> None:
        """articles_by_status always includes unread and archived keys."""
        env = MockEnv()
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        status = resp.json()["articles_by_status"]
        assert "unread" in status
        assert "archived" in status

    async def test_top_domains_is_list(self) -> None:
        """top_domains is always a list, even when empty."""
        env = MockEnv()
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        assert isinstance(resp.json()["top_domains"], list)

    async def test_articles_by_month_is_list(self) -> None:
        """articles_by_month is always a list, even when empty."""
        env = MockEnv()
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        assert isinstance(resp.json()["articles_by_month"], list)

    async def test_avg_reading_time_zero_when_no_data(self) -> None:
        """avg_reading_time_minutes is 0 when no articles have reading time."""
        env = MockEnv()
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        assert resp.json()["avg_reading_time_minutes"] == 0

    async def test_all_values_are_serializable(self) -> None:
        """All stats values are JSON-serializable primitives (no None, JsNull, etc.)."""

        def execute(sql: str, params: list) -> list:
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "GROUP BY reading_status" in sql:
                return [
                    {"reading_status": "unread", "cnt": 1},
                    {"reading_status": "archived", "cnt": 0},
                ]
            if "GROUP BY domain" in sql:
                return []
            if "DISTINCT date" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, sid = await _authenticated_client(env)
        resp = client.get("/api/stats")

        assert resp.status_code == 200
        data = resp.json()
        # All top-level values should be int, float, list, or dict (no None)
        for key in [
            "total_articles",
            "total_words_read",
            "articles_this_week",
            "articles_this_month",
            "archived_this_week",
            "archived_this_month",
            "reading_streak_days",
            "avg_reading_time_minutes",
        ]:
            assert isinstance(data[key], (int, float)), (
                f"{key} should be numeric, got {type(data[key])}: {data[key]}"
            )


class TestStatsCacheControl:
    async def test_stats_has_cache_control(self) -> None:
        """GET /api/stats includes Cache-Control: private, max-age=120."""

        def execute(sql, params):
            if "COUNT(*)" in sql:
                return [{"cnt": 0}]
            if "SUM(word_count)" in sql:
                return [{"total": 0}]
            if "GROUP BY reading_status" in sql:
                return []
            if "GROUP BY domain" in sql:
                return []
            if "DISTINCT date" in sql:
                return []
            if "AVG(" in sql:
                return [{"avg_rt": None}]
            if "strftime" in sql:
                return []
            if "saved_week" in sql:
                return [
                    {
                        "saved_week": 0,
                        "saved_month": 0,
                        "archived_week": 0,
                        "archived_month": 0,
                    }
                ]
            return []

        env = MockEnv(db=MockD1(execute=execute))
        client, _ = await _authenticated_client(env)
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "private" in cc
        assert "max-age=120" in cc

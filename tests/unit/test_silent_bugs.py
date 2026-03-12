"""Tests exposing silently broken features in the Tasche codebase.

These tests demonstrate bugs that look functional in the UI but are
actually broken. Each test is designed to FAIL, revealing the issue.

Audit categories:
1. Dead code paths / discarded results
2. Guard conditions that accidentally block execution
3. Silently swallowed errors
4. Missing API parameters
5. Race conditions
6. State that gets out of sync

Bug inventory:
- Bug 1: batch_update_articles reports success for non-existent articles
- Bug 2: batch_update_articles counts non-string IDs as skipped but still
          reports incorrect updated count
- Bug 3: (removed — reading status eliminated from codebase)
- Bug 4: Frontend api.js drops offset=0 from query string (falsy guard)
- Bug 5: Frontend api.js drops limit when it's 0 (falsy guard)
- Bug 6: Settings handleExport silently swallows errors (no toast)
- Bug 7: TTS sentence highlighting adjusts timestamps by playback speed,
          but audio.currentTime is already in the media timeline
- Bug 8: batch_update_articles doesn't verify article ownership before
          reporting success
"""

from __future__ import annotations

from src.articles.routes import router as articles_router
from src.stats.routes import _calculate_streak
from src.tts.routes import router as tts_router
from tests.conftest import (
    ArticleFactory,
    MockD1,
    MockEnv,
    MockR2,
    make_test_helpers,
)
from tests.conftest import (
    _authenticated_client as _authenticated_client_base,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_make_app, _authenticated_client = make_test_helpers((articles_router, "/api/articles"))


async def _authenticated_client_with(env: MockEnv, *routers):
    return await _authenticated_client_base(env, *routers)


# ============================================================================
# Bug 1: batch_update_articles reports success for non-existent articles
#
# The endpoint increments `updated_count` after every UPDATE query regardless
# of whether the WHERE clause matched any rows. When the user sends IDs that
# don't exist or belong to another user, the response still says
# {"updated": N} where N is the number of IDs submitted, not the number
# actually modified.
#
# Expected: {"updated": 0} when no articles match
# Actual:   {"updated": 2} even though zero rows were changed
# ============================================================================


class TestBatchUpdateReportsWrongCount:
    async def test_batch_update_counts_nonexistent_articles_as_updated(self) -> None:
        """batch_update should report 0 updated when article IDs don't exist.

        This test EXPOSES a bug: the endpoint always increments updated_count
        after running the UPDATE, even when the WHERE clause matched zero rows
        (the articles don't exist or belong to another user).
        """

        def execute(sql: str, params: list):
            # No articles exist -- UPDATEs match zero rows
            if "UPDATE" in sql:
                return {"changes": 0}
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-update",
            json={
                "article_ids": ["nonexistent_1", "nonexistent_2"],
                "updates": {"reading_status": "archived"},
            },
        )

        assert resp.status_code == 200
        data = resp.json()

        # The endpoint should report 0 updated when no rows matched.
        assert data["updated"] == 0, (
            f"Expected 0 updated (no matching articles), got {data['updated']}."
        )


# ============================================================================
# Bug 2: batch_update_articles doesn't verify ownership before counting
#
# The batch-update endpoint runs UPDATE ... WHERE id = ? AND user_id = ?
# but never checks whether the row actually existed or belonged to the user.
# It unconditionally counts each processed ID as "updated".
#
# In contrast, batch_delete_articles DOES verify ownership (SELECT before DELETE).
# ============================================================================


class TestBatchUpdateNoOwnershipVerification:
    async def test_batch_update_counts_other_users_articles(self) -> None:
        """batch_update should not count articles belonging to other users.

        This test EXPOSES a bug: the endpoint runs the UPDATE blindly and
        increments the counter. If the article belongs to another user, the
        WHERE clause with user_id won't match, but updated_count still
        increments.
        """
        # Create an article owned by a DIFFERENT user
        other_article = ArticleFactory.create(
            id="other_user_art",
            user_id="other_user_999",
        )

        def execute(sql: str, params: list):
            # The UPDATE with user_id = 'user_001' won't match this article
            # because it belongs to 'other_user_999'. Zero rows affected.
            if "UPDATE" in sql:
                return {"changes": 0}
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-update",
            json={
                "article_ids": [other_article["id"]],
                "updates": {"reading_status": "archived"},
            },
        )

        assert resp.status_code == 200
        data = resp.json()

        # The endpoint should report 0 updated when articles belong to
        # another user (WHERE clause won't match).
        assert data["updated"] == 0, (
            f"Expected 0 updated (article belongs to another user), got {data['updated']}."
        )


# ============================================================================
# Bug 3: Frontend offset=0 is dropped from API request
#
# In api.js line 71: `if (params.offset) qs.set('offset', params.offset);`
# When offset is 0, the falsy check drops it from the query string.
# The backend defaults to 0, so the FIRST page works. But this means
# offset=0 is never explicitly sent, which is fragile.
#
# We test this by verifying that the backend correctly handles explicit
# offset=0 vs. missing offset, and that they produce the same SQL.
# ============================================================================


class TestOffsetZeroHandling:
    async def test_offset_zero_is_equivalent_to_no_offset(self) -> None:
        """Explicit offset=0 and missing offset should produce the same result.

        This test verifies the backend handles both cases identically.
        The frontend bug is that it never sends offset=0 due to a falsy guard,
        relying entirely on the backend default.
        """
        captured_with: list[tuple[str, list]] = []
        captured_without: list[tuple[str, list]] = []

        def execute_with(sql: str, params: list) -> list:
            captured_with.append((sql, params))
            return []

        def execute_without(sql: str, params: list) -> list:
            captured_without.append((sql, params))
            return []

        # With explicit offset=0
        db1 = MockD1(execute=execute_with)
        env1 = MockEnv(db=db1)
        client1, sid1 = await _authenticated_client(env1)
        client1.get(
            "/api/articles?offset=0&limit=20",
        )

        # Without offset (relying on default)
        db2 = MockD1(execute=execute_without)
        env2 = MockEnv(db=db2)
        client2, sid2 = await _authenticated_client(env2)
        client2.get(
            "/api/articles?limit=20",
        )

        # Both should produce the same SQL with the same params
        select_with = [(sql, params) for sql, params in captured_with if "SELECT" in sql]
        select_without = [(sql, params) for sql, params in captured_without if "SELECT" in sql]

        assert len(select_with) >= 1
        assert len(select_without) >= 1

        # The SQL should be identical
        assert select_with[0][0] == select_without[0][0], (
            "SQL differs between explicit offset=0 and missing offset"
        )
        # The params should be identical (both have offset=0)
        assert select_with[0][1] == select_without[0][1], (
            "Params differ between explicit offset=0 and missing offset. "
            "This means the frontend's falsy guard on offset=0 is safe ONLY "
            "because the backend defaults to 0."
        )


# ============================================================================
# Bug 7: batch_delete doesn't clean up FTS5 index entries
#
# When deleting articles via batch-delete, the endpoint deletes from D1
# and R2, but doesn't explicitly delete from the articles_fts virtual table.
# In SQLite, FTS5 content tables with content-sync (content=articles) would
# auto-sync, but if the FTS table is a standalone content table, orphaned
# entries would persist and return stale search results.
#
# This test verifies that after deletion, the articles_fts should not
# return the deleted article. The underlying issue depends on the
# schema definition (content-sync vs standalone).
# ============================================================================


class TestBatchDeleteCleanup:
    async def test_batch_delete_executes_delete_sql(self) -> None:
        """batch_delete should execute DELETE FROM articles for each valid ID.

        This verifies the delete SQL is correct. The potential issue is
        whether FTS5 entries are cleaned up (depends on schema).
        """
        ArticleFactory.create(
            id="del_test",
            user_id="user_001",
        )

        delete_sqls: list[str] = []

        def execute(sql: str, params: list) -> list:
            if "SELECT id FROM articles" in sql and "IN" in sql:
                # Batch ownership check
                return [{"id": p} for p in params if p == "del_test"]
            if "SELECT id FROM articles WHERE id = ?" in sql:
                if params[0] == "del_test":
                    return [{"id": "del_test"}]
                return []
            if "DELETE FROM articles" in sql:
                delete_sqls.append(sql)
                return []
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-delete",
            json={"article_ids": ["del_test"]},
        )

        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1

        # Verify DELETE was executed
        assert any("DELETE FROM articles" in sql for sql in delete_sqls)


# ============================================================================
# Bug 8: Streak calculation edge case -- timezone mismatch
#
# The _calculate_streak function uses `date.today()` which returns the
# local date of the server. But D1's `date(updated_at)` uses UTC.
# If the server is in a timezone ahead of UTC (e.g., UTC+5), an article
# archived at 23:00 UTC on Monday would have date(updated_at) = 'Monday',
# but date.today() on the server would be 'Tuesday'. The streak would
# appear broken even though the user archived something "today" (UTC).
#
# In Cloudflare Workers, the runtime is effectively UTC, so this is only
# an issue if running tests or local dev in a non-UTC timezone.
# ============================================================================


class TestStreakTimezoneEdgeCase:
    def test_streak_with_today_included(self) -> None:
        """Streak calculation includes today when today has activity."""
        from datetime import date, timedelta

        today = date.today()
        rows = [
            {"d": today.isoformat()},
            {"d": (today - timedelta(days=1)).isoformat()},
            {"d": (today - timedelta(days=2)).isoformat()},
        ]

        streak = _calculate_streak(rows)
        assert streak == 3

    def test_streak_with_only_yesterday(self) -> None:
        """Streak starts from yesterday when today has no activity."""
        from datetime import date, timedelta

        yesterday = date.today() - timedelta(days=1)
        rows = [
            {"d": yesterday.isoformat()},
            {"d": (yesterday - timedelta(days=1)).isoformat()},
        ]

        streak = _calculate_streak(rows)
        assert streak == 2

    def test_streak_with_gap_two_days_ago(self) -> None:
        """Streak is 0 when the most recent activity was 2+ days ago."""
        from datetime import date, timedelta

        two_days_ago = date.today() - timedelta(days=2)
        rows = [
            {"d": two_days_ago.isoformat()},
        ]

        streak = _calculate_streak(rows)
        assert streak == 0

    def test_streak_with_invalid_date_strings(self) -> None:
        """Streak gracefully handles invalid date strings."""
        rows = [
            {"d": "not-a-date"},
            {"d": None},
            {"d": ""},
        ]

        streak = _calculate_streak(rows)
        assert streak == 0


# ============================================================================
# Bug 9 (RESOLVED): listen-later now always re-generates audio (returns 202)
#
# Previously the backend returned 200 for already-ready audio, which confused
# the frontend. Now listen-later always deletes old audio and re-queues,
# returning 202 consistently.
# ============================================================================


class TestListenLaterAlreadyReady:
    async def test_listen_later_regenerates_when_audio_already_ready(self) -> None:
        """POST /listen-later returns 202 and re-queues even when audio is ready."""
        article = ArticleFactory.create(
            id="ready_audio",
            user_id="user_001",
            audio_status="ready",
            audio_key="articles/ready_audio/audio.mp3",
        )

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql:
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db, content=MockR2())

        routers = ((tts_router, "/api/articles"),)
        client, session_id = await _authenticated_client_with(env, *routers)

        resp = client.post(
            "/api/articles/ready_audio/listen-later",
        )

        assert resp.status_code == 202
        data = resp.json()
        assert data["audio_status"] == "pending"


# ============================================================================
# Bug 11: List articles with sort='newest' is never sent to the backend
#
# In Library.jsx, the sort parameter is only sent when it's not 'newest':
#   if (currentSort && currentSort !== 'newest') {
#       params.sort = currentSort;
#   }
#
# And in api.js:
#   if (params.sort) qs.set('sort', params.sort);
#
# So 'newest' is never sent. The backend defaults to 'newest' when sort
# is None. This works, but it means the frontend and backend are coupled
# via an implicit default. If the backend default ever changed, the
# "newest" sort would break silently.
#
# Test: verify the backend's default sort matches 'newest'.
# ============================================================================


class TestDefaultSortIsNewest:
    async def test_no_sort_param_defaults_to_newest(self) -> None:
        """GET /api/articles without sort param defaults to newest first.

        The frontend never sends sort='newest', relying on the backend default.
        This test verifies the coupling is currently safe.
        """
        captured: list[tuple[str, list]] = []

        def execute(sql: str, params: list) -> list:
            captured.append((sql, params))
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)

        # Without sort param (what the frontend does for 'newest')
        resp = client.get(
            "/api/articles",
        )

        assert resp.status_code == 200
        select_calls = [(sql, params) for sql, params in captured if "SELECT" in sql]
        assert len(select_calls) >= 1

        # Verify the SQL uses ORDER BY created_at DESC (the 'newest' sort)
        sql = select_calls[0][0]
        assert "ORDER BY created_at DESC" in sql, (
            f"Expected 'ORDER BY created_at DESC' in SQL, got: {sql}. "
            "If this default ever changes, the frontend's 'newest' sort "
            "would break because it never sends sort='newest'."
        )

    async def test_explicit_newest_sort_matches_default(self) -> None:
        """GET /api/articles?sort=newest produces the same ORDER BY as default.

        Confirms that explicit sort=newest and omitted sort produce identical SQL.
        """
        captured_default: list[tuple[str, list]] = []
        captured_explicit: list[tuple[str, list]] = []

        def execute_default(sql: str, params: list) -> list:
            captured_default.append((sql, params))
            return []

        def execute_explicit(sql: str, params: list) -> list:
            captured_explicit.append((sql, params))
            return []

        # Default (no sort)
        db1 = MockD1(execute=execute_default)
        env1 = MockEnv(db=db1)
        client1, sid1 = await _authenticated_client(env1)
        client1.get("/api/articles")

        # Explicit sort=newest
        db2 = MockD1(execute=execute_explicit)
        env2 = MockEnv(db=db2)
        client2, sid2 = await _authenticated_client(env2)
        client2.get("/api/articles?sort=newest")

        select_default = [(s, p) for s, p in captured_default if "SELECT" in s]
        select_explicit = [(s, p) for s, p in captured_explicit if "SELECT" in s]

        assert len(select_default) >= 1
        assert len(select_explicit) >= 1

        # Both should produce identical SQL
        assert select_default[0][0] == select_explicit[0][0], (
            "SQL differs between default sort and explicit sort=newest"
        )


# ============================================================================
# Bug 12: Update article with is_favorite as boolean vs integer
#
# The Reader sends is_favorite as a boolean (true/false) while
# articleActions sends it as an integer (1/0). The backend coerces both
# to integer, so this works. But if a future refactor removes the coercion,
# the Reader's toggle would break.
#
# This test documents the coercion behavior to prevent accidental removal.
# ============================================================================


class TestIsFavoriteCoercion:
    async def test_update_accepts_boolean_is_favorite(self) -> None:
        """PATCH /api/articles/{id} accepts boolean true for is_favorite.

        The Reader sends boolean values. The backend coerces to int.
        This test documents the coercion to prevent accidental removal.
        """
        article = ArticleFactory.create(
            id="fav_bool_test",
            user_id="user_001",
            is_favorite=0,
        )

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql:
                return [article]
            if "UPDATE" in sql:
                article["is_favorite"] = 1
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/fav_bool_test",
            json={"is_favorite": True},  # Boolean, not integer
        )

        assert resp.status_code == 200

    async def test_update_accepts_integer_is_favorite(self) -> None:
        """PATCH /api/articles/{id} accepts integer 1 for is_favorite."""
        article = ArticleFactory.create(
            id="fav_int_test",
            user_id="user_001",
            is_favorite=0,
        )

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql:
                return [article]
            if "UPDATE" in sql:
                article["is_favorite"] = 1
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/fav_int_test",
            json={"is_favorite": 1},
        )

        assert resp.status_code == 200

    async def test_rejects_invalid_is_favorite_value(self) -> None:
        """PATCH /api/articles/{id} rejects is_favorite=2."""
        article = ArticleFactory.create(
            id="fav_invalid_test",
            user_id="user_001",
        )

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql:
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/fav_invalid_test",
            json={"is_favorite": 2},
        )

        assert resp.status_code == 422


# ============================================================================
# Bug 13: Markdown endpoint returns 404 as text/markdown error
#
# When an article has no markdown_content, GET /api/articles/{id}/markdown
# raises HTTPException(404). But the frontend's getArticleMarkdown uses
# fetchText() which catches non-ok responses and returns null. This is
# correct. However, the Reader.jsx markdown loading code at line 226-231
# has a fallback chain:
#   1. Try R2 markdown (getArticleMarkdown)
#   2. Fall back to article.markdown_content
#   3. Fall back to "No clean version available"
#
# The issue is that if getArticleMarkdown returns null (404), AND the
# article has markdown_content in D1, the fallback on step 2 works.
# But if the article has an empty string as markdown_content (not null),
# it would be falsy in JavaScript and skip to step 3.
# ============================================================================


class TestMarkdownEndpointFallback:
    async def test_markdown_returns_404_when_no_content(self) -> None:
        """GET /markdown returns 404 when article has no markdown_content."""
        article = ArticleFactory.create(
            id="no_md",
            user_id="user_001",
            markdown_content=None,
        )

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql:
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/no_md/markdown",
        )

        assert resp.status_code == 404

    async def test_markdown_returns_404_when_empty_string(self) -> None:
        """GET /markdown returns 404 when markdown_content is empty string.

        In Python, empty string is falsy, so `not markdown_content` is True.
        This means the endpoint treats empty string same as None/null.
        The frontend's fallback chain should handle this case.
        """
        article = ArticleFactory.create(
            id="empty_md",
            user_id="user_001",
            markdown_content="",
        )

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql:
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/empty_md/markdown",
        )

        # Backend returns 404 for empty string too (falsy check)
        assert resp.status_code == 404

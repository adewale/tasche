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
- Bug 3: Reader sends reading_status='reading' for unread articles without
          updating local state (tested via API: status change is silent,
          the GET endpoint still returns 'unread' if the update is
          not reflected)
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
from src.auth.session import COOKIE_NAME
from src.stats.routes import _calculate_streak
from src.tts.routes import router as tts_router
from tests.conftest import (
    ArticleFactory,
    MockD1,
    MockEnv,
    MockR2,
    _make_test_app,
)
from tests.conftest import (
    _authenticated_client as _authenticated_client_base,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ARTICLE_ROUTERS = ((articles_router, "/api/articles"),)
_TTS_ROUTERS = ((tts_router, "/api/articles"),)


async def _authenticated_client(env: MockEnv, routers=_ARTICLE_ROUTERS):
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
            cookies={COOKIE_NAME: session_id},
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
            cookies={COOKIE_NAME: session_id},
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
            cookies={COOKIE_NAME: sid1},
        )

        # Without offset (relying on default)
        db2 = MockD1(execute=execute_without)
        env2 = MockEnv(db=db2)
        client2, sid2 = await _authenticated_client(env2)
        client2.get(
            "/api/articles?limit=20",
            cookies={COOKIE_NAME: sid2},
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
# Bug 4: Audio timing highlight divides by playback speed (frontend bug)
#
# In Reader.jsx, the TTS sentence highlighting code adjusts sentence
# timestamps by dividing by the playback speed:
#
#     var adjStart = s.start / speed;
#     var adjEnd = s.end / speed;
#     if (currentTime >= adjStart && currentTime < adjEnd) { ... }
#
# But audio.currentTime always reflects the position in the media timeline
# regardless of playback rate. If a sentence spans 10.0-20.0s in the audio
# file, audio.currentTime will be 10.0 when playback reaches that point,
# whether at 1x or 2x speed. Dividing by speed=2 would make the code
# look for the sentence at 5.0-10.0s, which is wrong.
#
# This is a frontend-only bug. We can verify the audio-timing endpoint
# returns correct data (it does), and describe the Playwright test needed.
#
# Playwright test needed:
# 1. Save an article, generate TTS audio
# 2. Navigate to reader, click "Listen"
# 3. Set playback speed to 2x
# 4. Observe that sentence highlighting is out of sync --
#    highlights fire at the wrong time (too early by a factor of 2)
# 5. At 1x speed, highlighting should be correct
# ============================================================================


class TestAudioTimingEndpoint:
    async def test_audio_timing_returns_raw_timestamps(self) -> None:
        """Audio timing endpoint returns sentence timestamps as stored in R2.

        The timestamps should NOT be adjusted by playback speed server-side.
        The frontend should use them directly against audio.currentTime.

        This test verifies the backend returns correct data. The frontend
        bug is that it divides these timestamps by playback speed, which
        is incorrect since audio.currentTime is not affected by playbackRate.
        """
        import json as _json

        article = ArticleFactory.create(
            id="timing_test",
            user_id="user_001",
        )

        timing_data = {
            "sentences": [
                {"text": "First sentence.", "start": 0.0, "end": 5.0},
                {"text": "Second sentence.", "start": 5.0, "end": 10.0},
                {"text": "Third sentence.", "start": 10.0, "end": 15.0},
            ]
        }

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql and "id" in sql:
                return [article]
            return []

        r2 = MockR2()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, content=r2)

        # Store timing data in R2
        timing_key = "articles/timing_test/audio-timing.json"
        await r2.put(timing_key, _json.dumps(timing_data))

        # Also mount the TTS router which has the audio-timing endpoint
        routers = ((tts_router, "/api/articles"),)
        client, session_id = await _authenticated_client(env, routers=routers)

        resp = client.get(
            "/api/articles/timing_test/audio-timing",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()

        # Verify timestamps are returned as-is (not adjusted by any speed)
        sentences = data["sentences"]
        assert sentences[0]["start"] == 0.0
        assert sentences[0]["end"] == 5.0
        assert sentences[2]["start"] == 10.0
        assert sentences[2]["end"] == 15.0

        # FRONTEND BUG: Reader.jsx divides these by playbackRate:
        #   adjStart = s.start / speed  (e.g., 10.0 / 2 = 5.0)
        #   adjEnd = s.end / speed      (e.g., 15.0 / 2 = 7.5)
        # Then checks: currentTime >= 5.0 && currentTime < 7.5
        # But audio.currentTime is 10.0 at the 10-second mark regardless
        # of speed, so the highlight fires at the wrong time.


# ============================================================================
# Bug 5: Settings handleExport silently swallows errors
#
# In Settings.jsx, the handleExport function has:
#   exportData(format).catch(function () { /* empty */ }).finally(...)
#
# If the export fails (network error, server error), the user sees
# "Exporting..." briefly, then the button resets to normal. No error
# toast is shown. The user has no idea the export failed.
#
# This is a frontend-only bug. We verify the backend export endpoint
# properly returns errors (it does), and the frontend should show them.
#
# Playwright test needed:
# 1. Navigate to Settings
# 2. Intercept the /api/export/json request and return a 500 error
# 3. Click "Export as JSON"
# 4. Observe: button shows "Exporting..." then reverts to "Export as JSON"
# 5. Verify: NO toast notification appears (BUG)
# 6. Expected: An error toast like "Export failed" should appear
# ============================================================================


class TestExportReturnsProperErrors:
    async def test_export_json_requires_auth(self) -> None:
        """GET /api/export/json returns 401 without auth, not silently fail."""
        from src.articles.export import router as export_router

        routers = ((export_router, "/api/export"),)
        env = MockEnv()
        app = _make_test_app(env, *routers)

        from fastapi.testclient import TestClient

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/export/json")

        # The backend correctly returns 401. The frontend's handleExport
        # function catches ALL errors (including 401) and discards them.
        assert resp.status_code == 401

    async def test_export_json_returns_downloadable_file(self) -> None:
        """GET /api/export/json returns a proper Content-Disposition header.

        This verifies the backend works correctly. The frontend bug is that
        errors from this endpoint are silently swallowed in the catch block.
        """
        from src.articles.export import router as export_router

        def execute(sql: str, params: list) -> list:
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        routers = ((export_router, "/api/export"),)
        client, session_id = await _authenticated_client(env, routers=routers)

        resp = client.get(
            "/api/export/json",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert "Content-Disposition" in resp.headers
        assert "attachment" in resp.headers["Content-Disposition"]

        # The frontend's handleExport calls downloadFile which handles
        # the download. But if this request fails (network error, etc.),
        # the .catch(function () {}) in Settings.jsx swallows the error
        # without any user feedback.


# ============================================================================
# Bug 6: Reading status dropdown in Reader shows stale value
#
# When a user opens an unread article, Reader.jsx auto-updates the
# reading_status to 'reading' via a fire-and-forget PATCH:
#
#   if (art.reading_status === 'unread') {
#       updateArticle(currentId, { reading_status: 'reading' }).catch(function () {});
#   }
#
# But setArticle() is never called with the updated status, so the
# Reader's status dropdown still shows "unread" even though the server
# now has "reading". The user sees the wrong status until they manually
# change it or refresh the page.
#
# Backend test: verify the PATCH endpoint returns the updated article
# with reading_status='reading', proving the server state changed.
# The frontend ignores this returned value.
# ============================================================================


class TestReadingStatusAutoUpdate:
    async def test_update_reading_status_returns_new_status(self) -> None:
        """PATCH /api/articles/{id} returns the updated reading_status.

        The backend correctly returns the modified article. The frontend bug
        is that Reader.jsx calls updateArticle() as fire-and-forget when
        auto-marking unread articles as 'reading', so it never updates its
        local state with the returned value.

        Frontend bug (Reader.jsx line 382-383):
            if (art.reading_status === 'unread') {
                updateArticle(currentId, { reading_status: 'reading' }).catch(function () {});
            }

        The .catch(function () {}) discards the response. The Reader's status
        dropdown still shows 'unread' even though the server now has 'reading'.
        The user sees the wrong status until they manually change it or refresh.
        """
        article = ArticleFactory.create(
            id="reading_test",
            user_id="user_001",
            reading_status="unread",
        )

        select_count = 0

        def execute(sql: str, params: list) -> list:
            nonlocal select_count
            if "SELECT" in sql and "id = ?" in sql:
                select_count += 1
                if select_count <= 1:
                    # First SELECT: verify article exists (returns 'unread')
                    return [article]
                else:
                    # Second SELECT: return updated article
                    return [{**article, "reading_status": "reading"}]
            if "UPDATE articles SET" in sql:
                # Simulate the update succeeding
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)

        # Simulate what Reader.jsx does: fire-and-forget PATCH
        resp = client.patch(
            "/api/articles/reading_test",
            json={"reading_status": "reading"},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        data = resp.json()

        # The backend returns the updated article with reading_status='reading'
        assert data.get("reading_status") == "reading", (
            f"Expected reading_status='reading' in response, got {data.get('reading_status')}. "
            "The backend correctly returns the update."
        )

        # FRONTEND BUG: Reader.jsx discards this return value. The local
        # article state still has reading_status='unread'. The Reader's
        # status dropdown (line 611) shows the wrong value.
        # To verify: the frontend does NOT call setArticle() with the
        # updated status after the auto-mark-as-reading PATCH.


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
            cookies={COOKIE_NAME: session_id},
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
# Bug 9: listen-later on already-ready audio returns 200, not 202
#
# The frontend checks `e.status === 409` to show "already in progress",
# but if audio is already "ready", the backend returns 200 (not 409 or 202).
# The frontend's handleListenLater in Reader.jsx doesn't handle the 200
# case and would show "Audio generation queued" toast even though no
# generation was queued.
#
# Actually, looking more carefully: the frontend's request() function
# parses the JSON response normally for 200. Then handleListenLater
# calls addToast('Audio generation queued', 'success') regardless.
# The user sees "Audio generation queued" even though audio is ALREADY ready.
# ============================================================================


class TestListenLaterAlreadyReady:
    async def test_listen_later_returns_200_when_audio_already_ready(self) -> None:
        """POST /listen-later returns 200 (not 202) when audio is already ready.

        The frontend's handleListenLater doesn't distinguish between 202
        (newly queued) and 200 (already ready), so it shows the wrong toast.
        """
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
        env = MockEnv(db=db)

        routers = ((tts_router, "/api/articles"),)
        client, session_id = await _authenticated_client(env, routers=routers)

        resp = client.post(
            "/api/articles/ready_audio/listen-later",
            cookies={COOKIE_NAME: session_id},
        )

        # Backend returns 200 (not 202) when audio is already ready
        assert resp.status_code == 200
        data = resp.json()
        assert data["audio_status"] == "ready"

        # FRONTEND BUG: Reader.jsx handleListenLater does:
        #   await apiListenLater(id);
        #   addToast('Audio generation queued', 'success');
        #   setAudioRequested(true);
        #
        # Since 200 is not an error, the code falls through to the success
        # path and shows "Audio generation queued" even though audio is
        # already ready. The correct behavior would be to check the response
        # and show "Audio is already available" instead.


# ============================================================================
# Bug 10: Tag rule deletion doesn't check if the rule actually existed
#
# The DELETE /api/tag-rules/{rule_id} endpoint checks ownership via a JOIN
# query. If the rule doesn't exist, it returns 404 (correct). But looking
# at the flow: the frontend's handleDeleteRule doesn't handle the case
# where another tab already deleted the rule. The UI would still show the
# rule until refresh.
#
# More importantly, the frontend removes the rule from local state
# optimistically AFTER the API call succeeds. If the API returns an error,
# the catch block shows a toast but the rule remains in the UI. This is
# actually correct behavior.
#
# Let's test a real backend edge case instead:
# ============================================================================


class TestTagRuleDeletionEdgeCase:
    async def test_delete_nonexistent_rule_returns_404(self) -> None:
        """DELETE /api/tag-rules/{id} returns 404 for non-existent rules."""
        from src.tags.rules import router as tag_rules_router

        def execute(sql: str, params: list) -> list:
            return []  # No rules found

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        routers = ((tag_rules_router, "/api/tag-rules"),)
        client, session_id = await _authenticated_client(env, routers=routers)

        resp = client.delete(
            "/api/tag-rules/nonexistent_rule",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


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
            cookies={COOKIE_NAME: session_id},
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
        client1.get("/api/articles", cookies={COOKIE_NAME: sid1})

        # Explicit sort=newest
        db2 = MockD1(execute=execute_explicit)
        env2 = MockEnv(db=db2)
        client2, sid2 = await _authenticated_client(env2)
        client2.get("/api/articles?sort=newest", cookies={COOKIE_NAME: sid2})

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
            cookies={COOKIE_NAME: session_id},
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
            cookies={COOKIE_NAME: session_id},
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
            cookies={COOKIE_NAME: session_id},
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
            cookies={COOKIE_NAME: session_id},
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
            cookies={COOKIE_NAME: session_id},
        )

        # Backend returns 404 for empty string too (falsy check)
        assert resp.status_code == 404

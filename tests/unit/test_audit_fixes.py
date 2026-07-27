"""Tests for the 2026-06-12 audit fixes.

Covers:
- entry._parse_range_header (audio Range request support)
- entry._with_security_headers (security headers on static assets)
- auth.session.dev_bypass_allowed (hardened DISABLE_AUTH guard)
- wrappers.SafeQueue.send (no caller-dict mutation)
- wide_event.emit_event (non-serialisable fields must not raise)
- articles.routes._enqueue_or_fail (whitelisted rollback SQL + user_id scoping)
- articles.processing user_id scoping of status writes
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import MockEnv, MockQueue, TrackingD1

# =========================================================================
# entry._parse_range_header
# =========================================================================


class TestParseRangeHeader:
    def _parse(self, value):
        from entry import _parse_range_header

        return _parse_range_header(value)

    def test_absent_header_returns_none(self) -> None:
        assert self._parse(None) is None
        assert self._parse("") is None

    def test_bounded_range(self) -> None:
        assert self._parse("bytes=0-99") == {"offset": 0, "length": 100}
        assert self._parse("bytes=500-999") == {"offset": 500, "length": 500}

    def test_open_ended_range(self) -> None:
        assert self._parse("bytes=1000-") == {"offset": 1000}

    def test_suffix_range(self) -> None:
        assert self._parse("bytes=-500") == {"suffix": 500}

    def test_case_insensitive_unit_and_whitespace(self) -> None:
        assert self._parse("Bytes=0-9") == {"offset": 0, "length": 10}
        assert self._parse("  bytes=0-9  ") == {"offset": 0, "length": 10}

    def test_multi_range_unsupported_returns_none(self) -> None:
        # RFC 9110 permits ignoring Range; the caller serves 200 full body.
        assert self._parse("bytes=0-99,200-299") is None

    def test_malformed_returns_none(self) -> None:
        assert self._parse("bytes=") is None
        assert self._parse("bytes=-") is None
        assert self._parse("bytes=abc-def") is None
        assert self._parse("bytes=99-0") is None  # end before start
        assert self._parse("bytes=-0") is None  # zero-length suffix
        assert self._parse("items=0-99") is None  # unknown unit
        assert self._parse("0-99") is None  # missing unit


# =========================================================================
# entry._partial_content_fields — 206/200 header math (CI-covers the part of
# _serve_audio that otherwise only runs in the gated e2e suite)
# =========================================================================


class TestPartialContentFields:
    def _fields(self, total, suffix=None, offset=None, length=None):
        from entry import _partial_content_fields

        return _partial_content_fields(total, suffix, offset, length)

    def test_no_range_serves_full_body(self) -> None:
        assert self._fields(1000) == (200, "1000", None)

    def test_no_range_unknown_total(self) -> None:
        assert self._fields(None) == (200, None, None)

    def test_bounded_range(self) -> None:
        # bytes=0-99 → R2 applies offset=0,length=100
        assert self._fields(1000, offset=0, length=100) == (206, "100", "bytes 0-99/1000")

    def test_offset_zero_is_honored_not_treated_as_falsy(self) -> None:
        # Regression: offset 0 must not be coerced away.
        status, _cl, cr = self._fields(1000, offset=0, length=10)
        assert status == 206
        assert cr == "bytes 0-9/1000"

    def test_open_ended_range_derives_count_from_total(self) -> None:
        # bytes=500- → offset=500, length not reflected → count = total-offset
        assert self._fields(1000, offset=500) == (206, "500", "bytes 500-999/1000")

    def test_suffix_range(self) -> None:
        # bytes=-50 → R2 reflects suffix=50
        assert self._fields(1000, suffix=50) == (206, "50", "bytes 950-999/1000")

    def test_suffix_larger_than_object_clamps_to_whole(self) -> None:
        assert self._fields(1000, suffix=5000) == (206, "1000", "bytes 0-999/1000")

    def test_suffix_without_total_falls_back_to_200_not_malformed_206(self) -> None:
        # The bug this guards: a 206 with neither Content-Range nor
        # Content-Length must never be produced.
        status, cl, cr = self._fields(None, suffix=50)
        assert status == 200
        assert cr is None

    def test_offset_without_total_or_length_falls_back_to_200(self) -> None:
        status, cl, cr = self._fields(None, offset=10)
        assert status == 200
        assert cr is None

    def test_206_always_has_both_headers(self) -> None:
        # Whenever status is 206, both Content-Length and Content-Range are set.
        for kwargs in ({"offset": 0, "length": 100}, {"suffix": 50}, {"offset": 500}):
            status, cl, cr = self._fields(1000, **kwargs)
            if status == 206:
                assert cl is not None and cr is not None


# =========================================================================
# entry._cors_allowed — production CORS gate (the wiring uses import-time
# _is_production, so the decision predicate is what we can unit-test)
# =========================================================================


class TestCorsAllowed:
    def test_production_disables_cors(self) -> None:
        from entry import _cors_allowed

        assert _cors_allowed("production") is False

    def test_dev_and_staging_allow_cors(self) -> None:
        from entry import _cors_allowed

        assert _cors_allowed("development") is True
        assert _cors_allowed("staging") is True
        assert _cors_allowed("") is True


# =========================================================================
# entry._with_security_headers — static assets get the canonical headers
# =========================================================================


class TestAssetSecurityHeaders:
    def test_all_canonical_headers_applied(self) -> None:
        from entry import _with_security_headers
        from security import SECURITY_HEADERS

        js_mod = MagicMock()
        resp = MagicMock()

        out = _with_security_headers(js_mod, resp, https=False)

        js_mod.Response.new.assert_called_once_with(resp.body, resp)
        assert out is js_mod.Response.new.return_value
        set_calls = {call.args[0]: call.args[1] for call in out.headers.set.call_args_list}
        for name, value in SECURITY_HEADERS:
            assert set_calls.get(name) == value
        assert "strict-transport-security" not in set_calls

    def test_hsts_only_on_https(self) -> None:
        from entry import _with_security_headers

        js_mod = MagicMock()
        out = _with_security_headers(js_mod, MagicMock(), https=True)
        set_names = {call.args[0] for call in out.headers.set.call_args_list}
        assert "strict-transport-security" in set_names

    def test_csp_constant_shared_with_asgi_middleware(self) -> None:
        """The bytes headers used by the middleware derive from SECURITY_HEADERS."""
        from security import _SECURITY_HEADERS, SECURITY_HEADERS

        assert _SECURITY_HEADERS == [(n.encode(), v.encode()) for n, v in SECURITY_HEADERS]
        names = {n for n, _ in SECURITY_HEADERS}
        assert "content-security-policy" in names
        assert "x-frame-options" in names
        assert "x-content-type-options" in names

    def test_csp_allows_blob_media_for_audio_player(self) -> None:
        """media-src must allow blob: — the audio player plays a blob: URL.

        AudioPlayer fetches /audio and assigns URL.createObjectURL(blob) to an
        <audio> element; without media-src blob: the strict CSP blocks playback.
        """
        from security import SECURITY_HEADERS

        csp = dict(SECURITY_HEADERS)["content-security-policy"]
        assert "media-src 'self' blob:" in csp, csp

    def test_csp_img_src_covers_thumbnails_favicons_canvas(self) -> None:
        """img-src must cover same-origin thumbnails, data: canvas output, favicons."""
        from security import SECURITY_HEADERS

        csp = dict(SECURITY_HEADERS)["content-security-policy"]
        assert "img-src 'self' data: https://www.google.com" in csp, csp


# =========================================================================
# entry._authenticate_raw_request — audio path mirrors the loud 500 guard
# =========================================================================


class _FakeHeaders:
    def __init__(self, cookie: str | None = None) -> None:
        self._cookie = cookie

    def get(self, name: str):
        return self._cookie if name == "cookie" else None


class _FakeRequest:
    def __init__(self, cookie: str | None = None) -> None:
        self.headers = _FakeHeaders(cookie)


class TestRawRequestAuthGuard:
    """The raw audio auth path must behave like get_current_user for DISABLE_AUTH."""

    def _worker(self, **env_kwargs):
        from src.entry import Default
        from src.wrappers import SafeEnv

        worker = Default()
        env = MockEnv(**env_kwargs)
        worker.env = env
        return worker, SafeEnv(env)

    async def test_misconfigured_disable_auth_returns_500(self) -> None:
        """DISABLE_AUTH=true with no WORKER_ENV → 500, not a silent 401."""
        worker, safe_env = self._worker(disable_auth="true", worker_env=None)
        user_id, error, status = await worker._authenticate_raw_request(_FakeRequest(), safe_env)
        assert user_id is None
        assert status == 500
        assert "WORKER_ENV" in error

    async def test_production_disable_auth_returns_500(self) -> None:
        worker, safe_env = self._worker(disable_auth="true", worker_env="production")
        _uid, _error, status = await worker._authenticate_raw_request(_FakeRequest(), safe_env)
        assert status == 500

    async def test_eligible_env_bypasses_to_dev_user(self) -> None:
        worker, safe_env = self._worker(disable_auth="true", worker_env="staging")
        user_id, error, status = await worker._authenticate_raw_request(_FakeRequest(), safe_env)
        assert (user_id, error, status) == ("dev", None, 200)

    async def test_no_cookie_is_401(self) -> None:
        worker, safe_env = self._worker(disable_auth=None, worker_env="production")
        _uid, error, status = await worker._authenticate_raw_request(_FakeRequest(), safe_env)
        assert status == 401
        assert error == "Not authenticated"


# =========================================================================
# auth.session.dev_bypass_allowed — hardened DISABLE_AUTH guard
# =========================================================================


class TestDevBypassAllowed:
    @pytest.mark.parametrize(
        ("disable_auth", "worker_env", "expected"),
        [
            ("true", "development", True),
            ("true", "staging", True),
            ("true", "production", False),
            ("true", None, False),  # default deploy path: no WORKER_ENV
            ("true", "", False),
            ("true", "prod", False),
            ("false", "development", False),
            (None, "development", False),
            ("TRUE", "development", False),  # exact-match only
        ],
    )
    def test_guard_matrix(self, disable_auth, worker_env, expected) -> None:
        from src.auth.session import dev_bypass_allowed

        assert dev_bypass_allowed(disable_auth, worker_env) is expected


# =========================================================================
# wrappers.SafeQueue.send — must not mutate the caller's dict
# =========================================================================


class TestSafeQueueSendNoMutation:
    async def test_caller_dict_unchanged_but_message_enriched(self) -> None:
        from src.wrappers import SafeQueue

        queue = MockQueue()
        safe = SafeQueue(queue)
        original = {"type": "article_processing", "article_id": "a1"}

        await safe.send(original)

        assert "enqueued_at" not in original, "send() must not mutate the caller's dict"
        assert len(queue.messages) == 1
        assert queue.messages[0]["enqueued_at"]
        assert queue.messages[0]["article_id"] == "a1"


# =========================================================================
# wide_event.emit_event — non-serialisable fields must not raise
# =========================================================================


class TestEmitEventRobustness:
    def test_non_serialisable_field_is_stringified(self, capsys) -> None:
        from src.wide_event import begin_event, emit_event

        evt = begin_event("http", request_id="r1")
        evt.set("weird", object())

        emit_event(evt)  # must not raise

        out = capsys.readouterr().out
        assert '"weird"' in out
        assert '"pipeline": "http"' in out


# =========================================================================
# articles.routes._enqueue_or_fail — whitelisted rollback SQL + user scoping
# =========================================================================


class _FailingQueue:
    async def send(self, message, **kwargs):
        raise RuntimeError("queue unavailable")


class TestEnqueueOrFailRollback:
    async def test_rollback_scopes_by_user_id(self) -> None:
        from fastapi import HTTPException

        from src.articles.routes import _enqueue_or_fail
        from src.wrappers import SafeEnv

        db = TrackingD1()
        env = SafeEnv(MockEnv(db=db))
        env.ARTICLE_QUEUE = _FailingQueue()

        with pytest.raises(HTTPException) as exc_info:
            await _enqueue_or_fail(env, env.DB, {"type": "article_processing"}, "art_1", "user_001")

        assert exc_info.value.status_code == 503
        assert len(db.executed) == 1
        sql, params = db.executed[0]
        assert "user_id = ?" in sql
        assert params[0] == "failed"
        assert params[2:] == ["art_1", "user_001"]

    async def test_audio_status_field_uses_whitelisted_sql(self) -> None:
        from fastapi import HTTPException

        from src.articles.routes import _enqueue_or_fail
        from src.wrappers import SafeEnv

        db = TrackingD1()
        env = SafeEnv(MockEnv(db=db))
        env.ARTICLE_QUEUE = _FailingQueue()

        with pytest.raises(HTTPException):
            await _enqueue_or_fail(
                env,
                env.DB,
                {"type": "tts_generation"},
                "art_2",
                "user_001",
                status_field="audio_status",
                rollback_value=None,
            )

        sql, params = db.executed[0]
        assert sql.startswith("UPDATE articles SET audio_status = ?")
        assert params[0] is None

    async def test_unknown_status_field_rejected(self) -> None:
        from src.articles.routes import _enqueue_or_fail
        from src.wrappers import SafeEnv

        env = SafeEnv(MockEnv(db=TrackingD1()))
        env.ARTICLE_QUEUE = _FailingQueue()

        with pytest.raises(KeyError):
            await _enqueue_or_fail(
                env,
                env.DB,
                {},
                "art_3",
                "user_001",
                status_field="title; DROP TABLE articles",
            )


# =========================================================================
# articles.processing — status writes scoped by user_id when available
# =========================================================================


class TestProcessingUserScoping:
    async def test_set_status_includes_user_id_when_present(self) -> None:
        from src.articles.processing import _set_status
        from src.wrappers import SafeEnv

        db = TrackingD1()
        env = SafeEnv(MockEnv(db=db))

        await _set_status(env.DB, "art_9", "processing", "user_001")

        sql, params = db.executed[0]
        assert "user_id = ?" in sql
        assert params == ["processing", params[1], "art_9", "user_001"]

    async def test_set_status_works_without_user_id(self) -> None:
        """Messages enqueued before user_id existed must still be processable."""
        from src.articles.processing import _set_status
        from src.wrappers import SafeEnv

        db = TrackingD1()
        env = SafeEnv(MockEnv(db=db))

        await _set_status(env.DB, "art_9", "failed", None)

        sql, params = db.executed[0]
        assert "user_id" not in sql
        assert params[0] == "failed"
        assert params[2] == "art_9"

    async def test_auto_enqueue_tts_update_scopes_by_user_id(self) -> None:
        """The atomic pending->generating TTS update is scoped by user_id."""
        from src.articles.processing import process_article
        from src.wrappers import SafeEnv

        ready_article = {
            "id": "art_tts",
            "user_id": "user_001",
            "audio_status": "pending",
            "status": "ready",
            "markdown_content": "# Hi\n\nbody",
        }

        def execute(sql, params):
            # The pre-supplied raw.html lookup and content fetch return nothing;
            # the auto-enqueue SELECT returns a pending article owned by user_001.
            if "SELECT audio_status, user_id" in sql:
                return [ready_article]
            return []

        db = TrackingD1(result_fn=execute)
        env = SafeEnv(MockEnv(db=db))

        # Pre-supply raw HTML so the pipeline skips the network fetch entirely.
        await env.CONTENT.put("articles/art_tts/raw.html", b"<article><p>hello world</p></article>")

        await process_article("art_tts", "https://example.com/x", env, user_id="user_001")

        # The atomic pending->generating guard binds (generating, id, user_id,
        # pending) — find it by its exact parameter signature.
        guard = [
            (sql, params)
            for sql, params in db.executed
            if params == ["generating", "art_tts", "user_001", "pending"]
        ]
        assert guard, f"user_id-scoped atomic TTS guard UPDATE not found in {db.executed}"
        assert "user_id = ?" in guard[-1][0]

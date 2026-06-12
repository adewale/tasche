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

"""Tests for Phase 6 — TTS / Listen Later (src/tts/).

Covers the listen-later endpoint, audio streaming endpoint, TTS processing
pipeline (happy path and failure handling), and authentication enforcement.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.auth.session import COOKIE_NAME, create_session
from src.tts.routes import router
from tests.conftest import (
    ArticleFactory,
    MockAI,
    MockD1,
    MockEnv,
    MockQueue,
    MockR2,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_DATA: dict[str, Any] = {
    "user_id": "user_001",
    "email": "test@example.com",
    "username": "tester",
    "avatar_url": "https://github.com/avatar.png",
    "created_at": "2025-01-01T00:00:00",
}


def _make_app(env: Any) -> FastAPI:
    """Create a FastAPI app with the TTS router and env injection."""
    test_app = FastAPI()

    @test_app.middleware("http")
    async def inject_env(request, call_next):
        request.scope["env"] = env
        return await call_next(request)

    test_app.include_router(router, prefix="/api/articles")
    return test_app


async def _authenticated_client(env: MockEnv) -> tuple[TestClient, str]:
    """Create a test client with a valid session cookie."""
    session_id = await create_session(env.SESSIONS, _USER_DATA)
    app = _make_app(env)
    client = TestClient(app, raise_server_exceptions=False)
    return client, session_id


# ---------------------------------------------------------------------------
# Tracking D1 mock
# ---------------------------------------------------------------------------


class _TrackingD1(MockD1):
    """MockD1 that records all SQL statements and supports configurable results."""

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
# POST /api/articles/{article_id}/listen-later
# ---------------------------------------------------------------------------


class TestListenLater:
    async def test_sets_status_and_enqueues_job(self) -> None:
        """POST listen-later sets audio_status='pending' and enqueues TTS job."""
        article = ArticleFactory.create(id="art_tts1", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql:
                return [article]
            return []

        db = _TrackingD1(result_fn=execute)
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_tts1/listen-later",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 202
        data = resp.json()
        assert data["id"] == "art_tts1"
        assert data["audio_status"] == "pending"

        # Verify D1 UPDATE was called with listen_later and audio_status
        update_calls = [
            (sql, params)
            for sql, params in db.executed
            if sql.startswith("UPDATE")
        ]
        assert len(update_calls) >= 1
        update_sql = update_calls[0][0]
        assert "listen_later = 1" in update_sql
        assert "audio_status = 'pending'" in update_sql

        # Verify queue message was sent
        assert len(queue.messages) == 1
        msg = queue.messages[0]
        assert msg["type"] == "tts_generation"
        assert msg["article_id"] == "art_tts1"

    async def test_returns_404_for_missing_article(self) -> None:
        """POST listen-later returns 404 when article does not exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/nonexistent/listen-later",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id}/audio
# ---------------------------------------------------------------------------


class TestGetAudio:
    async def test_streams_audio(self) -> None:
        """GET audio returns audio/mpeg content from R2."""
        audio_bytes = b"\xff\xfb\x90\x00" + b"\x00" * 100  # Fake MP3 data
        article = ArticleFactory.create(
            id="art_audio1",
            user_id="user_001",
            audio_key="articles/art_audio1/audio.mp3",
            audio_status="ready",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_audio1":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        # Store audio in R2
        await r2.put("articles/art_audio1/audio.mp3", audio_bytes)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_audio1/audio",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert resp.content == audio_bytes

    async def test_returns_404_when_no_audio_key(self) -> None:
        """GET audio returns 404 when article has no audio_key."""
        article = ArticleFactory.create(
            id="art_noaudio",
            user_id="user_001",
            audio_key=None,
            audio_status=None,
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_noaudio":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_noaudio/audio",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    async def test_returns_404_when_r2_object_missing(self) -> None:
        """GET audio returns 404 when R2 does not have the audio file."""
        article = ArticleFactory.create(
            id="art_norfile",
            user_id="user_001",
            audio_key="articles/art_norfile/audio.mp3",
            audio_status="ready",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_norfile":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()  # Empty — no audio stored
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_norfile/audio",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    async def test_returns_409_when_audio_generating(self) -> None:
        """GET audio returns 409 when audio is still being generated."""
        article = ArticleFactory.create(
            id="art_gen",
            user_id="user_001",
            audio_key=None,
            audio_status="generating",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_gen":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_gen/audio",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 409
        assert "still being generated" in resp.json()["detail"]

    async def test_returns_404_for_missing_article(self) -> None:
        """GET audio returns 404 when article does not exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/nonexistent/audio",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TTS Processing Pipeline
# ---------------------------------------------------------------------------


class TestTTSProcessing:
    async def test_happy_path_from_r2_markdown(self) -> None:
        """TTS processing fetches markdown from R2, calls AI, stores audio."""
        article = ArticleFactory.create(
            id="art_proc1",
            user_id="user_001",
            markdown_key="articles/art_proc1/content.md",
        )

        db = _TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = b"\xff\xfb\x90\x00" + b"\x00" * 200
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai)

        # Store markdown in R2
        await r2.put("articles/art_proc1/content.md", "# Hello World\n\nThis is test content.")

        from tts.processing import process_tts

        await process_tts("art_proc1", env)

        # Verify AI was called with the correct model
        assert len(ai.calls) == 1
        assert ai.calls[0]["model"] == "@cf/deepgram/aura-2-en"
        assert "Hello World" in ai.calls[0]["text"]

        # Verify audio was stored in R2
        assert "articles/art_proc1/audio.mp3" in r2._store
        assert r2._store["articles/art_proc1/audio.mp3"] == fake_audio

        # Verify D1 was updated with audio_status='ready'
        ready_updates = [
            (sql, params)
            for sql, params in db.executed
            if "UPDATE" in sql and "ready" in str(params) and "audio_status" in sql
        ]
        assert len(ready_updates) >= 1

        # Verify audio_key is set in the final update
        final_sql, final_params = ready_updates[-1]
        assert "articles/art_proc1/audio.mp3" in final_params

    async def test_falls_back_to_d1_markdown(self) -> None:
        """TTS processing uses D1 markdown_content when R2 has no markdown."""
        article = ArticleFactory.create(
            id="art_proc2",
            user_id="user_001",
            markdown_key=None,
            markdown_content="# Fallback Content\n\nThis came from D1.",
        )

        db = _TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()  # No markdown in R2
        fake_audio = b"\xff\xfb\x90\x00" + b"\x00" * 100
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai)

        from tts.processing import process_tts

        await process_tts("art_proc2", env)

        # Verify AI was called with the D1 fallback content
        assert len(ai.calls) == 1
        assert "Fallback Content" in ai.calls[0]["text"]

        # Verify audio was stored
        assert "articles/art_proc2/audio.mp3" in r2._store

    async def test_sets_generating_status_first(self) -> None:
        """The first D1 operation sets audio_status to 'generating'."""
        article = ArticleFactory.create(
            id="art_proc3",
            user_id="user_001",
            markdown_key="articles/art_proc3/content.md",
        )

        db = _TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        ai = MockAI(response=b"fake-audio")
        env = MockEnv(db=db, content=r2, ai=ai)

        await r2.put("articles/art_proc3/content.md", "Some markdown content here.")

        from tts.processing import process_tts

        await process_tts("art_proc3", env)

        # First executed statement should set audio_status to 'generating'
        assert len(db.executed) >= 1
        first_sql, first_params = db.executed[0]
        assert "UPDATE" in first_sql
        assert "generating" in first_params


# ---------------------------------------------------------------------------
# TTS Processing Failure
# ---------------------------------------------------------------------------


class TestTTSProcessingFailure:
    async def test_sets_failed_on_missing_markdown(self) -> None:
        """When no markdown content is found, audio_status is set to 'failed'."""
        article = ArticleFactory.create(
            id="art_fail1",
            user_id="user_001",
            markdown_key=None,
            markdown_content=None,
        )

        db = _TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        ai = MockAI(response=b"fake-audio")
        env = MockEnv(db=db, content=r2, ai=ai)

        from tts.processing import process_tts

        await process_tts("art_fail1", env)

        # Should have 'failed' status update
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if "UPDATE" in sql and "failed" in str(params)
        ]
        assert len(failed_updates) >= 1

    async def test_sets_failed_on_ai_error(self) -> None:
        """When Workers AI raises an error, audio_status is set to 'failed'."""
        article = ArticleFactory.create(
            id="art_fail2",
            user_id="user_001",
            markdown_key="articles/art_fail2/content.md",
        )

        db = _TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        await r2.put("articles/art_fail2/content.md", "Some content for TTS.")

        # Create an AI mock that raises an error
        ai = MockAI()

        async def _failing_run(model, **kwargs):
            raise RuntimeError("AI model unavailable")

        ai.run = _failing_run

        env = MockEnv(db=db, content=r2, ai=ai)

        from tts.processing import process_tts

        await process_tts("art_fail2", env)

        # Should have 'failed' status update
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if "UPDATE" in sql and "failed" in str(params)
        ]
        assert len(failed_updates) >= 1


# ---------------------------------------------------------------------------
# Authentication enforcement
# ---------------------------------------------------------------------------


class TestTTSAuthRequired:
    def test_listen_later_returns_401_without_auth(self) -> None:
        """POST listen-later returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/articles/some_id/listen-later")
        assert resp.status_code == 401

    def test_get_audio_returns_401_without_auth(self) -> None:
        """GET audio returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles/some_id/audio")
        assert resp.status_code == 401

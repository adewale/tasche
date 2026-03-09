"""Tests for Phase 6 — TTS / Listen Later (src/tts/).

Covers the listen-later endpoint, audio streaming endpoint, TTS processing
pipeline (happy path and failure handling), and authentication enforcement.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.tts.routes import router
from tests.conftest import (
    ArticleFactory,
    MockAI,
    MockD1,
    MockEnv,
    MockQueue,
    MockR2,
    TrackingD1,
    make_test_helpers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_make_app, _authenticated_client = make_test_helpers((router, "/api/articles"))


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

        db = TrackingD1(result_fn=execute)
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_tts1/listen-later",
        )

        assert resp.status_code == 202
        data = resp.json()
        assert data["id"] == "art_tts1"
        assert data["audio_status"] == "pending"

        # Verify D1 UPDATE was called with audio_status
        update_calls = [(sql, params) for sql, params in db.executed if sql.startswith("UPDATE")]
        assert len(update_calls) >= 1
        update_sql = update_calls[0][0]
        assert "audio_status = 'pending'" in update_sql

        # Verify queue message was sent with voice preference
        assert len(queue.messages) == 1
        msg = queue.messages[0]
        assert msg["type"] == "tts_generation"
        assert msg["article_id"] == "art_tts1"
        assert "tts_voice" in msg

    async def test_includes_voice_preference_in_queue_message(self) -> None:
        """POST listen-later includes user's tts_voice preference in the queue message."""
        article = ArticleFactory.create(id="art_voice_q", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "user_preferences" in sql and "SELECT" in sql:
                return [{"tts_voice": "orion"}]
            if sql.startswith("SELECT") and "id = ?" in sql:
                return [article]
            return []

        db = TrackingD1(result_fn=execute)
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, _ = await _authenticated_client(env)
        resp = client.post("/api/articles/art_voice_q/listen-later")

        assert resp.status_code == 202
        assert queue.messages[0]["tts_voice"] == "orion"

    async def test_returns_404_for_missing_article(self) -> None:
        """POST listen-later returns 404 when article does not exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/nonexistent/listen-later",
        )

        assert resp.status_code == 404

    # ---------------------------------------------------------------------------
    # GET /api/articles/{article_id}/audio
    # ---------------------------------------------------------------------------

    async def test_returns_409_when_already_pending(self) -> None:
        """POST listen-later returns 409 when audio is already pending."""
        article = ArticleFactory.create(
            id="art_dup1",
            user_id="user_001",
            audio_status="pending",
        )

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql:
                return [article]
            return []

        db = TrackingD1(result_fn=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_dup1/listen-later",
        )

        assert resp.status_code == 409
        assert "already in progress" in resp.json()["detail"]

    async def test_requeues_when_stuck_generating(self) -> None:
        """POST listen-later re-queues when audio is stuck at generating."""
        article = ArticleFactory.create(
            id="art_dup2",
            user_id="user_001",
            audio_status="generating",
        )

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql:
                return [article]
            return []

        db = TrackingD1(result_fn=execute)
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_dup2/listen-later",
        )

        assert resp.status_code == 202
        assert resp.json()["audio_status"] == "pending"
        assert len(queue.messages) == 1
        assert queue.messages[0]["type"] == "tts_generation"

    async def test_returns_200_when_already_ready(self) -> None:
        """POST listen-later returns 200 with existing data when ready."""
        article = ArticleFactory.create(
            id="art_ready",
            user_id="user_001",
            audio_status="ready",
            audio_key="articles/art_ready/audio.mp3",
        )

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql:
                return [article]
            return []

        db = TrackingD1(result_fn=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_ready/listen-later",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["audio_status"] == "ready"
        assert data["audio_key"] == "articles/art_ready/audio.mp3"

    async def test_enqueues_when_failed(self) -> None:
        """POST listen-later enqueues when previous attempt failed."""
        article = ArticleFactory.create(
            id="art_fail",
            user_id="user_001",
            audio_status="failed",
        )

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql:
                return [article]
            return []

        db = TrackingD1(result_fn=execute)
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_fail/listen-later",
        )

        assert resp.status_code == 202
        assert len(queue.messages) == 1


class TestGetAudio:
    async def test_streams_audio(self) -> None:
        """GET audio returns audio/wav content from R2 for WAV data."""
        audio_bytes = b"RIFF" + b"\x00" * 100  # Fake WAV data
        article = ArticleFactory.create(
            id="art_audio1",
            user_id="user_001",
            audio_key="articles/art_audio1/audio.wav",
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
        await r2.put("articles/art_audio1/audio.wav", audio_bytes)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_audio1/audio",
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/wav"
        assert resp.content == audio_bytes

    async def test_streams_mp3_audio(self) -> None:
        """GET audio returns audio/mpeg content from R2 for MP3 data."""
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
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert resp.content == audio_bytes

    async def test_streams_ogg_audio(self) -> None:
        """GET audio returns audio/ogg content from R2 for OGG Opus data."""
        audio_bytes = b"OggS" + b"\x00" * 100  # Fake OGG data
        article = ArticleFactory.create(
            id="art_audio_ogg",
            user_id="user_001",
            audio_key="articles/art_audio_ogg/audio.ogg",
            audio_status="ready",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_audio_ogg":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        await r2.put("articles/art_audio_ogg/audio.ogg", audio_bytes)

        client, session_id = await _authenticated_client(env)
        resp = client.get("/api/articles/art_audio_ogg/audio")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/ogg"
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
        )

        assert resp.status_code == 404

    async def test_returns_404_when_r2_object_missing(self) -> None:
        """GET audio returns 404 when R2 does not have the audio file."""
        article = ArticleFactory.create(
            id="art_norfile",
            user_id="user_001",
            audio_key="articles/art_norfile/audio.wav",
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
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# TTS Processing Pipeline
# ---------------------------------------------------------------------------


class TestTTSProcessing:
    async def test_happy_path_melotts(self) -> None:
        """TTS processing uses MeloTTS when configured, decoding base64 audio."""
        article = ArticleFactory.create(
            id="art_proc1",
            user_id="user_001",
            markdown_content="# Hello World\n\nThis is test content.",
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = b"RIFF" + b"\x00" * 200
        ai = MockAI(response={"audio": base64.b64encode(fake_audio).decode()})
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="melotts")

        from tts.processing import process_tts

        await process_tts("art_proc1", env, user_id="user_001")

        # Verify AI was called with the MeloTTS model and prompt key
        assert len(ai.calls) == 1
        assert ai.calls[0]["model"] == "@cf/myshell-ai/melotts"
        assert "Hello World" in ai.calls[0]["prompt"]
        assert ai.calls[0]["lang"] == "en"

        # Verify audio was stored in R2 (MeloTTS returns WAV)
        assert "articles/art_proc1/audio.wav" in r2._store
        assert r2._store["articles/art_proc1/audio.wav"] == fake_audio

        # Verify D1 was updated with audio_status='ready'
        ready_updates = [
            (sql, params)
            for sql, params in db.executed
            if "UPDATE" in sql and "ready" in str(params) and "audio_status" in sql
        ]
        assert len(ready_updates) >= 1

        # Verify audio_key is set in the final update
        final_sql, final_params = ready_updates[-1]
        assert "articles/art_proc1/audio.wav" in final_params

    async def test_happy_path_aura_model(self) -> None:
        """TTS processing uses Deepgram Aura with Opus encoding params."""
        article = ArticleFactory.create(
            id="art_aura",
            user_id="user_001",
            markdown_content="# Hello World\n\nThis is test content.",
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        # OGG Opus audio (starts with OggS magic bytes)
        fake_audio = b"OggS" + b"\x00" * 200
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        from tts.processing import process_tts

        await process_tts("art_aura", env, user_id="user_001")

        # Verify AI was called with full Deepgram params
        assert len(ai.calls) == 1
        call = ai.calls[0]
        assert call["model"] == "@cf/deepgram/aura-2-en"
        assert "Hello World" in call["text"]
        assert call["speaker"] == "athena"
        assert call["encoding"] == "opus"
        assert call["container"] == "ogg"
        assert call["bit_rate"] == 24000
        assert "sample_rate" not in call  # Opus handles sample rate internally

        # Verify audio was stored as .ogg in R2
        assert "articles/art_aura/audio.ogg" in r2._store

    async def test_aura_model_with_voice(self) -> None:
        """TTS processing passes tts_voice to Deepgram Aura model."""
        article = ArticleFactory.create(
            id="art_voice",
            user_id="user_001",
            markdown_content="# Hello\n\nTest content.",
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = b"OggS" + b"\x00" * 200
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        from tts.processing import process_tts

        await process_tts("art_voice", env, user_id="user_001", tts_voice="orion")

        assert ai.calls[0]["speaker"] == "orion"

    async def test_uses_d1_markdown_content(self) -> None:
        """TTS processing uses D1 markdown_content for speech generation."""
        article = ArticleFactory.create(
            id="art_proc2",
            user_id="user_001",
            markdown_content="# Fallback Content\n\nThis came from D1.",
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = b"RIFF" + b"\x00" * 100
        ai = MockAI(response={"audio": base64.b64encode(fake_audio).decode()})
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="melotts")

        from tts.processing import process_tts

        await process_tts("art_proc2", env, user_id="user_001")

        # Verify AI was called with the D1 content (MeloTTS uses "prompt" key)
        assert len(ai.calls) == 1
        assert "Fallback Content" in ai.calls[0]["prompt"]

        # Verify audio was stored (MeloTTS returns WAV)
        assert "articles/art_proc2/audio.wav" in r2._store

    async def test_sets_generating_status_first(self) -> None:
        """The first D1 operation sets audio_status to 'generating'."""
        article = ArticleFactory.create(
            id="art_proc3",
            user_id="user_001",
            markdown_content="Some markdown content here.",
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = b"fake-audio"
        ai = MockAI(response={"audio": base64.b64encode(fake_audio).decode()})
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="melotts")

        from tts.processing import process_tts

        await process_tts("art_proc3", env, user_id="user_001")

        # First statement is the idempotency check (SELECT), second sets 'generating'
        assert len(db.executed) >= 2
        first_sql, _ = db.executed[0]
        assert "SELECT" in first_sql and "audio_status" in first_sql
        second_sql, second_params = db.executed[1]
        assert "UPDATE" in second_sql
        assert "generating" in second_params


# ---------------------------------------------------------------------------
# TTS Processing Failure
# ---------------------------------------------------------------------------


class TestTTSProcessingFailure:
    async def test_sets_failed_on_missing_markdown(self) -> None:
        """When article is ready but has no markdown, audio_status is set to 'failed'."""
        article = ArticleFactory.create(
            id="art_fail1",
            user_id="user_001",
            markdown_content=None,
            status="ready",
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        ai = MockAI(response=b"fake-audio")
        env = MockEnv(db=db, content=r2, ai=ai)

        from tts.processing import process_tts

        await process_tts("art_fail1", env, user_id="user_001")

        # Should have an UPDATE that sets audio_status to 'failed'
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if sql.startswith("UPDATE") and "audio_status" in sql
        ]
        assert len(failed_updates) >= 1
        # The last such update should have "failed" as the first bound param
        last_sql, last_params = failed_updates[-1]
        assert last_params[0] == "failed"

    async def test_retries_when_article_still_processing(self) -> None:
        """When article is still processing, TTS raises RuntimeError for queue retry."""
        article = ArticleFactory.create(
            id="art_race",
            user_id="user_001",
            markdown_content=None,
            status="processing",
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        ai = MockAI(response=b"fake-audio")
        env = MockEnv(db=db, content=r2, ai=ai)

        from tts.processing import process_tts

        # Should raise RuntimeError (retryable), NOT set audio_status to 'failed'
        with pytest.raises(RuntimeError, match="still processing"):
            await process_tts("art_race", env, user_id="user_001", raise_on_error=True)

        # audio_status should NOT be set to 'failed'
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if sql.startswith("UPDATE") and "audio_status" in sql and "failed" in str(params)
        ]
        assert len(failed_updates) == 0

    async def test_retries_when_article_still_pending(self) -> None:
        """When article is still pending, TTS raises RuntimeError for queue retry."""
        article = ArticleFactory.create(
            id="art_race_pending",
            user_id="user_001",
            markdown_content=None,
            status="pending",
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        ai = MockAI(response=b"fake-audio")
        env = MockEnv(db=db, content=r2, ai=ai)

        from tts.processing import process_tts

        with pytest.raises(RuntimeError, match="still pending"):
            await process_tts("art_race_pending", env, user_id="user_001", raise_on_error=True)

    async def test_ai_runtime_error_sets_failed_then_reraises(self) -> None:
        """RuntimeError from Workers AI sets audio_status='failed' then re-raises."""
        article = ArticleFactory.create(
            id="art_fail2",
            user_id="user_001",
            markdown_content="Some content for TTS.",
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()

        # Create an AI mock that raises an error
        ai = MockAI()

        async def _failing_run(model, inputs=None, **kwargs):
            raise RuntimeError("AI model unavailable")

        ai.run = _failing_run

        env = MockEnv(db=db, content=r2, ai=ai)

        from tts.processing import process_tts

        with pytest.raises(RuntimeError, match="AI model unavailable"):
            await process_tts("art_fail2", env, user_id="user_001")

        # audio_status should be set to 'failed' before re-raising
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if sql.startswith("UPDATE") and "audio_status" in sql and "failed" in str(params)
        ]
        assert len(failed_updates) == 1

    async def test_sets_failed_on_value_error(self) -> None:
        """ValueError (permanent error) sets audio_status to 'failed'."""
        article = ArticleFactory.create(
            id="art_fail_ve",
            user_id="user_001",
            markdown_content="",  # Empty content triggers ValueError
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        ai = MockAI(response=b"fake-audio")
        env = MockEnv(db=db, content=r2, ai=ai)

        from tts.processing import process_tts

        await process_tts("art_fail_ve", env, user_id="user_001")

        # Should have an UPDATE that sets audio_status to 'failed'
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if sql.startswith("UPDATE") and "audio_status" in sql
        ]
        assert len(failed_updates) >= 1
        last_sql, last_params = failed_updates[-1]
        assert last_params[0] == "failed"


# ---------------------------------------------------------------------------
# TTS Transient Error (queue retry)
# ---------------------------------------------------------------------------


class TestTTSTransientErrorRetry:
    async def test_connection_error_sets_failed_then_reraises(self) -> None:
        """ConnectionError sets audio_status='failed' then re-raises for queue retry.

        If the queue retries, process_tts will reset to 'generating' at the top.
        If retries are exhausted, the article correctly shows 'failed' instead
        of being stuck at 'generating' forever.
        """
        article = ArticleFactory.create(
            id="art_transient",
            user_id="user_001",
            markdown_content="Some content for TTS.",
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()

        # Create an AI mock that raises ConnectionError (transient)
        ai = MockAI()

        async def _transient_fail(model, inputs=None, **kwargs):
            raise ConnectionError("Temporary network failure")

        ai.run = _transient_fail

        env = MockEnv(db=db, content=r2, ai=ai)

        import pytest

        from tts.processing import process_tts

        with pytest.raises(ConnectionError):
            await process_tts("art_transient", env, user_id="user_001")

        # audio_status should be set to 'failed' before re-raising
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if sql.startswith("UPDATE") and "audio_status" in sql and "failed" in str(params)
        ]
        assert len(failed_updates) == 1

        # Verify 'generating' was also set (step 1, before the error)
        generating_updates = [
            (sql, params)
            for sql, params in db.executed
            if sql.startswith("UPDATE") and "generating" in str(params)
        ]
        assert len(generating_updates) >= 1


# ---------------------------------------------------------------------------
# TTS Empty Audio Response
# ---------------------------------------------------------------------------


class TestTTSEmptyAudioResponse:
    async def test_empty_audio_sets_failed(self) -> None:
        """When Workers AI returns empty audio, audio_status is set to 'failed'."""
        article = ArticleFactory.create(
            id="art_empty_audio",
            user_id="user_001",
            markdown_content="Some content for TTS.",
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        # MeloTTS with empty base64 audio
        ai = MockAI(response={"audio": ""})
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="melotts")

        from tts.processing import process_tts

        await process_tts("art_empty_audio", env, user_id="user_001")

        # Should have an UPDATE that sets audio_status to 'failed'
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if sql.startswith("UPDATE") and "audio_status" in sql
        ]
        assert len(failed_updates) >= 1
        last_sql, last_params = failed_updates[-1]
        assert last_params[0] == "failed"


# ---------------------------------------------------------------------------
# Authentication enforcement
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# strip_markdown unit tests
# ---------------------------------------------------------------------------


class TestStripMarkdown:
    def test_removes_headings(self) -> None:
        """Heading markers (#) are removed, text is kept."""
        from tts.processing import strip_markdown

        assert "Hello" in strip_markdown("# Hello")
        assert "#" not in strip_markdown("# Hello")

    def test_removes_bold_italic(self) -> None:
        """Bold and italic markers are removed."""
        from tts.processing import strip_markdown

        result = strip_markdown("This is **bold** and *italic* text")
        assert "bold" in result
        assert "italic" in result
        assert "**" not in result
        assert "*" not in result

    def test_converts_links_to_text(self) -> None:
        """Links [text](url) become just text."""
        from tts.processing import strip_markdown

        result = strip_markdown("Visit [Google](https://google.com)")
        assert "Google" in result
        assert "https://google.com" not in result

    def test_removes_images(self) -> None:
        """Images ![alt](url) are removed entirely."""
        from tts.processing import strip_markdown

        result = strip_markdown("See ![photo](https://img.com/x.jpg)")
        assert "photo" not in result
        assert "img.com" not in result

    def test_removes_code_blocks(self) -> None:
        """Code blocks are removed."""
        from tts.processing import strip_markdown

        md = "Before\n```python\nprint('hello')\n```\nAfter"
        result = strip_markdown(md)
        assert "print" not in result
        assert "Before" in result
        assert "After" in result

    def test_removes_inline_code(self) -> None:
        """Inline code backticks are removed but content kept."""
        from tts.processing import strip_markdown

        result = strip_markdown("Use `print()` function")
        assert "print()" in result
        assert "`" not in result

    def test_removes_blockquotes(self) -> None:
        """Blockquote markers are removed."""
        from tts.processing import strip_markdown

        result = strip_markdown("> This is a quote")
        assert "This is a quote" in result
        assert ">" not in result

    def test_removes_horizontal_rules(self) -> None:
        """Horizontal rules are removed."""
        from tts.processing import strip_markdown

        result = strip_markdown("Above\n---\nBelow")
        assert "Above" in result
        assert "Below" in result
        assert "---" not in result

    def test_removes_list_markers(self) -> None:
        """List markers (-, *, 1.) are removed."""
        from tts.processing import strip_markdown

        result = strip_markdown("- item one\n* item two\n1. item three")
        assert "item one" in result
        assert "item two" in result
        assert "item three" in result

    def test_removes_html_tags(self) -> None:
        """HTML tags are removed."""
        from tts.processing import strip_markdown

        result = strip_markdown("Some <em>text</em> here")
        assert "text" in result
        assert "<em>" not in result

    def test_empty_input(self) -> None:
        """Empty string returns empty string."""
        from tts.processing import strip_markdown

        assert strip_markdown("") == ""

    def test_none_input(self) -> None:
        """None returns None."""
        from tts.processing import strip_markdown

        assert strip_markdown(None) is None


# ---------------------------------------------------------------------------
# TTS text truncation
# ---------------------------------------------------------------------------


class TestTTSTextTruncation:
    async def test_tts_text_truncation(self) -> None:
        """Markdown > 100,000 chars is truncated with a message appended."""
        long_markdown = "Hello world. " * 10_000  # ~130,000 chars
        article = ArticleFactory.create(
            id="art_trunc",
            user_id="user_001",
            markdown_content=long_markdown,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = b"RIFF" + b"\x00" * 100
        ai = MockAI(response={"audio": base64.b64encode(fake_audio).decode()})
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="melotts")

        from tts.processing import process_tts

        await process_tts("art_trunc", env, user_id="user_001")

        # Verify AI was called with chunked text (multiple calls) and
        # the last chunk contains the truncation message (MeloTTS uses "prompt" key)
        assert len(ai.calls) >= 1
        all_text = " ".join(call["prompt"] for call in ai.calls)
        assert "Content has been truncated" in all_text
        assert len(all_text) < len(long_markdown)


# ---------------------------------------------------------------------------
# _estimate_duration edge cases
# ---------------------------------------------------------------------------


class TestEstimateDuration:
    def test_empty_text(self) -> None:
        """Empty text returns at least 1 second."""
        from tts.processing import _estimate_duration

        assert _estimate_duration("") >= 1

    def test_single_word(self) -> None:
        """Single word returns at least 1 second."""
        from tts.processing import _estimate_duration

        assert _estimate_duration("hello") >= 1

    def test_long_text(self) -> None:
        """Long text returns proportional duration."""
        from tts.processing import _estimate_duration

        # 1500 words at 150 wpm = 10 minutes = 600 seconds
        text = "word " * 1500
        duration = _estimate_duration(text)
        assert duration == 600


# ---------------------------------------------------------------------------
# Sentence splitting and timing generation
# ---------------------------------------------------------------------------


class TestChunkText:
    def test_short_text_single_chunk(self) -> None:
        """Text under the limit stays in a single chunk."""
        from tts.processing import chunk_text

        result = chunk_text("Hello world. This is short.", max_chars=1900)
        assert len(result) == 1
        assert result[0] == "Hello world. This is short."

    def test_long_text_splits_at_sentences(self) -> None:
        """Long text is split at sentence boundaries."""
        from tts.processing import chunk_text

        # Each sentence is ~15 chars, so 200 sentences = ~3000 chars
        text = "Hello world. " * 200
        result = chunk_text(text.strip(), max_chars=500)
        assert len(result) > 1
        for chunk in result:
            # Each chunk should be at or under the limit
            assert len(chunk) <= 510  # Small tolerance for sentence joining

    def test_empty_text(self) -> None:
        """Empty text returns empty list."""
        from tts.processing import chunk_text

        assert chunk_text("") == []
        assert chunk_text("   ") == []

    def test_all_text_preserved(self) -> None:
        """All original sentences appear across chunks."""
        from tts.processing import chunk_text, split_sentences

        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        chunks = chunk_text(text, max_chars=40)
        # Recombine and verify all sentences are present
        recombined = " ".join(chunks)
        for sentence in split_sentences(text):
            assert sentence in recombined


class TestSplitSentences:
    def test_basic_splitting(self) -> None:
        """Splits text on sentence-ending punctuation followed by whitespace."""
        from tts.processing import split_sentences

        result = split_sentences("First sentence. Second sentence. Third one.")
        assert len(result) == 3
        assert result[0] == "First sentence."
        assert result[1] == "Second sentence."
        assert result[2] == "Third one."

    def test_multiple_punctuation_types(self) -> None:
        """Splits on periods, question marks, and exclamation marks."""
        from tts.processing import split_sentences

        result = split_sentences("Hello! How are you? I am fine.")
        assert len(result) == 3
        assert result[0] == "Hello!"
        assert result[1] == "How are you?"
        assert result[2] == "I am fine."

    def test_empty_input(self) -> None:
        """Empty string returns empty list."""
        from tts.processing import split_sentences

        assert split_sentences("") == []
        assert split_sentences("   ") == []

    def test_none_input(self) -> None:
        """None returns empty list."""
        from tts.processing import split_sentences

        assert split_sentences(None) == []

    def test_single_sentence(self) -> None:
        """Single sentence without trailing punctuation returns one element."""
        from tts.processing import split_sentences

        result = split_sentences("Just one sentence")
        assert len(result) == 1
        assert result[0] == "Just one sentence"

    def test_sentence_with_no_space_after_period(self) -> None:
        """Periods not followed by whitespace are not split points."""
        from tts.processing import split_sentences

        result = split_sentences("Version 3.14 is out. See docs.")
        assert len(result) == 2
        assert "3.14" in result[0]

    def test_multiline_text(self) -> None:
        """Handles text with newlines between sentences."""
        from tts.processing import split_sentences

        text = "First sentence.\nSecond sentence.\nThird."
        result = split_sentences(text)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Enqueue failure rollback
# ---------------------------------------------------------------------------


class TestEnqueueFailureRollback:
    async def test_enqueue_failure_rollback(self) -> None:
        """When queue.send() fails, audio_status is rolled back to NULL."""
        article = ArticleFactory.create(
            id="art_qfail",
            user_id="user_001",
            audio_status=None,
        )

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql:
                return [article]
            return []

        class FailingQueue:
            messages: list = []

            async def send(self, message: Any, **kwargs: Any) -> None:
                raise RuntimeError("Queue unavailable")

        db = TrackingD1(result_fn=execute)
        queue = FailingQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_qfail/listen-later",
        )

        assert resp.status_code == 503

        # Verify audio_status was rolled back to NULL (via parameterized query)
        rollback_updates = [
            (sql, params)
            for sql, params in db.executed
            if sql.startswith("UPDATE") and "audio_status = ?" in sql and None in params
        ]
        assert len(rollback_updates) >= 1


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


# ---------------------------------------------------------------------------
# Multi-chunk TTS — regression test for audio truncation
# ---------------------------------------------------------------------------


class TestMultiChunkTTS:
    """Verify that multi-chunk TTS concatenates all chunks, not just the first."""

    async def test_multi_chunk_produces_concatenated_audio(self) -> None:
        """When text is split into N chunks, all N audio responses are joined."""
        # Create content long enough to produce multiple TTS chunks (>1900 chars)
        long_text = "This is a test sentence with enough words to matter. " * 80
        article = ArticleFactory.create(
            id="art_multi1",
            user_id="user_001",
            markdown_content=long_text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()

        # Each chunk should return distinct audio bytes so we can verify concatenation.
        # First chunk starts with RIFF (WAV magic bytes) so concatenation is detected as WAV.
        chunk_responses = [b"RIFF" + bytes([i]) * 200 for i in range(10)]
        call_idx = {"n": 0}

        class MultiResponseAI:
            def __init__(self):
                self.calls = []

            async def run(self, model, inputs=None, **kwargs):
                call = {"model": model}
                if isinstance(inputs, dict):
                    call.update(inputs)
                self.calls.append(call)
                idx = min(call_idx["n"], len(chunk_responses) - 1)
                call_idx["n"] += 1
                # Return MeloTTS format (base64-encoded audio)
                return {"audio": base64.b64encode(chunk_responses[idx]).decode()}

        ai = MultiResponseAI()
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="melotts")

        from tts.processing import process_tts

        result = await process_tts("art_multi1", env, user_id="user_001")

        # Multiple AI calls should have been made
        assert len(ai.calls) > 1, f"Expected multiple chunks, got {len(ai.calls)}"

        # Audio stored in R2 should be concatenation of ALL chunks (WAV format)
        stored = r2._store.get("articles/art_multi1/audio.wav")
        assert stored is not None
        assert len(stored) > 202, "Audio should be larger than a single chunk"

        # The stored audio should contain bytes from multiple chunk responses
        expected = b"".join(chunk_responses[: len(ai.calls)])
        assert stored == expected

        # Diagnostics should report per-chunk data
        assert result is not None
        assert result["chunks"] == len(ai.calls)
        assert len(result["chunk_sizes"]) == len(ai.calls)
        assert all(s > 0 for s in result["chunk_sizes"])
        assert result["total_bytes"] == len(expected)

    async def test_empty_chunk_audio_is_skipped_not_crash(self) -> None:
        """If one AI chunk returns empty bytes, it's skipped gracefully."""
        long_text = "Some text for TTS processing and chunking. " * 80
        article = ArticleFactory.create(
            id="art_empty1",
            user_id="user_001",
            markdown_content=long_text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()

        call_idx = {"n": 0}

        class SometimesEmptyAI:
            def __init__(self):
                self.calls = []

            async def run(self, model, inputs=None, **kwargs):
                call = {"model": model}
                if isinstance(inputs, dict):
                    call.update(inputs)
                self.calls.append(call)
                call_idx["n"] += 1
                # Every other chunk returns empty (MeloTTS format)
                if call_idx["n"] % 2 == 0:
                    return {"audio": ""}
                audio = b"RIFF" + b"\x00" * 100
                return {"audio": base64.b64encode(audio).decode()}

        ai = SometimesEmptyAI()
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="melotts")

        from tts.processing import process_tts

        result = await process_tts("art_empty1", env, user_id="user_001")

        # Should succeed with partial audio (non-empty chunks concatenated)
        assert result is not None
        assert result["chunks"] > 0
        assert result["total_bytes"] > 0


# ---------------------------------------------------------------------------
# consume_readable_stream — ReadableStream consumption
# ---------------------------------------------------------------------------


class TestConsumeReadableStream:
    """Test consume_readable_stream with mock ReadableStream-like objects."""

    async def test_plain_bytes_passthrough(self) -> None:
        """Plain bytes pass through unchanged."""
        from wrappers import consume_readable_stream

        result = await consume_readable_stream(b"hello world")
        assert result == b"hello world"

    async def test_none_returns_empty_bytes(self) -> None:
        """None input returns empty bytes."""
        from wrappers import consume_readable_stream

        result = await consume_readable_stream(None)
        assert result == b""

    async def test_multi_chunk_reader(self) -> None:
        """A ReadableStream with multiple chunks returns all data concatenated."""
        from wrappers import consume_readable_stream

        chunks = [b"chunk1-", b"chunk2-", b"chunk3"]

        class MockReadResult:
            def __init__(self, done, value=None):
                self.done = done
                self.value = value

        class MockReader:
            def __init__(self):
                self._idx = 0

            async def read(self):
                if self._idx >= len(chunks):
                    return MockReadResult(done=True)
                chunk = chunks[self._idx]
                self._idx += 1
                return MockReadResult(done=False, value=chunk)

            def releaseLock(self):
                pass

        class MockStream:
            """Mock with getReader() — the preferred path."""

            def getReader(self):
                return MockReader()

        result = await consume_readable_stream(MockStream())
        assert result == b"chunk1-chunk2-chunk3"

    async def test_getReader_preferred_over_arrayBuffer(self) -> None:
        """When both getReader and arrayBuffer exist, getReader is used."""
        from wrappers import consume_readable_stream

        class MockReadResult:
            def __init__(self, done, value=None):
                self.done = done
                self.value = value

        class MockReader:
            def __init__(self):
                self._done = False

            async def read(self):
                if self._done:
                    return MockReadResult(done=True)
                self._done = True
                return MockReadResult(done=False, value=b"from-reader")

            def releaseLock(self):
                pass

        class MockStreamWithBoth:
            """Has both getReader and arrayBuffer. getReader should win."""

            def getReader(self):
                return MockReader()

            async def arrayBuffer(self):
                return b"from-arrayBuffer"

        result = await consume_readable_stream(MockStreamWithBoth())
        assert result == b"from-reader", (
            "getReader() should be preferred over arrayBuffer() to avoid truncation"
        )

    async def test_arrayBuffer_fallback(self) -> None:
        """When only arrayBuffer exists (no getReader), use it as fallback."""
        from wrappers import consume_readable_stream

        class MockArrayBufferOnly:
            async def arrayBuffer(self):
                return b"from-buffer"

        result = await consume_readable_stream(MockArrayBufferOnly())
        assert result == b"from-buffer"


# ---------------------------------------------------------------------------
# Helpers for OGG Opus test data
# ---------------------------------------------------------------------------


def _make_ogg_opus_data(duration_seconds: float, pre_skip: int = 312) -> bytes:
    """Build minimal OGG Opus byte sequence with controlled duration.

    Creates two OGG pages:
    - Page 1: OpusHead ID header with pre_skip
    - Page 2: Final page with granule_position encoding the duration
    """
    import struct

    total_samples = int(duration_seconds * 48000) + pre_skip

    # Page 1: ID header page
    # OpusHead: magic(8) + version(1) + channels(1) + pre_skip(2)
    #         + sample_rate(4) + gain(2) + mapping(1) = 19 bytes
    opus_head = b"OpusHead"
    opus_head += struct.pack("<B", 1)  # version
    opus_head += struct.pack("<B", 1)  # channels
    opus_head += struct.pack("<H", pre_skip)
    opus_head += struct.pack("<I", 48000)  # input sample rate
    opus_head += struct.pack("<h", 0)  # output gain
    opus_head += struct.pack("<B", 0)  # channel mapping family

    page1 = b"OggS"  # capture pattern
    page1 += struct.pack("<B", 0)  # version
    page1 += struct.pack("<B", 2)  # header type (BOS)
    page1 += struct.pack("<q", 0)  # granule position
    page1 += struct.pack("<I", 1)  # serial number
    page1 += struct.pack("<I", 0)  # page sequence
    page1 += struct.pack("<I", 0)  # checksum (don't care)
    page1 += struct.pack("<B", 1)  # num segments
    page1 += struct.pack("<B", len(opus_head))  # segment table
    page1 += opus_head

    # Page 2: data page with final granule position
    fake_audio = b"\x00" * 100  # dummy audio data
    page2 = b"OggS"
    page2 += struct.pack("<B", 0)  # version
    page2 += struct.pack("<B", 4)  # header type (EOS)
    page2 += struct.pack("<q", total_samples)  # granule position
    page2 += struct.pack("<I", 1)  # serial number
    page2 += struct.pack("<I", 2)  # page sequence
    page2 += struct.pack("<I", 0)  # checksum
    page2 += struct.pack("<B", 1)  # num segments
    page2 += struct.pack("<B", len(fake_audio))
    page2 += fake_audio

    return page1 + page2


# ---------------------------------------------------------------------------
# OGG Duration Parsing Tests
# ---------------------------------------------------------------------------


class TestOggDuration:
    def test_valid_ogg_opus(self):
        from tts.processing import _ogg_duration_seconds

        data = _make_ogg_opus_data(5.0)
        duration = _ogg_duration_seconds(data)
        assert abs(duration - 5.0) < 0.01

    def test_empty_data(self):
        from tts.processing import _ogg_duration_seconds

        assert _ogg_duration_seconds(b"") == 0.0

    def test_short_data(self):
        from tts.processing import _ogg_duration_seconds

        assert _ogg_duration_seconds(b"OggS" + b"\x00" * 10) == 0.0

    def test_non_ogg_data(self):
        from tts.processing import _ogg_duration_seconds

        assert _ogg_duration_seconds(b"RIFF" + b"\x00" * 100) == 0.0

    def test_various_durations(self):
        from tts.processing import _ogg_duration_seconds

        for seconds in [0.5, 1.0, 10.0, 60.0, 300.0]:
            data = _make_ogg_opus_data(seconds)
            duration = _ogg_duration_seconds(data)
            assert abs(duration - seconds) < 0.01, f"Expected ~{seconds}s, got {duration}s"


# ---------------------------------------------------------------------------
# chunk_text_with_sentences Tests
# ---------------------------------------------------------------------------


class TestChunkTextWithSentences:
    def test_single_sentence(self):
        from tts.processing import chunk_text_with_sentences

        result = chunk_text_with_sentences("Hello world.")
        assert len(result) == 1
        assert result[0]["text"] == "Hello world."
        assert result[0]["sentences"] == ["Hello world."]

    def test_multiple_sentences_single_chunk(self):
        from tts.processing import chunk_text_with_sentences

        text = "First sentence. Second sentence. Third sentence."
        result = chunk_text_with_sentences(text)
        assert len(result) == 1
        assert len(result[0]["sentences"]) == 3

    def test_respects_max_chars(self):
        from tts.processing import chunk_text_with_sentences

        text = "Short. " * 50  # Creates many sentences
        result = chunk_text_with_sentences(text, max_chars=50)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk["text"]) <= 60  # Allow slight overrun for single sentences

    def test_empty_text(self):
        from tts.processing import chunk_text_with_sentences

        assert chunk_text_with_sentences("") == []
        assert chunk_text_with_sentences("   ") == []

    def test_preserves_all_sentences(self):
        from tts.processing import chunk_text_with_sentences, split_sentences

        text = "One. Two. Three. Four. Five."
        result = chunk_text_with_sentences(text)
        all_sentences = []
        for chunk in result:
            all_sentences.extend(chunk["sentences"])
        assert all_sentences == split_sentences(text)

    def test_chunk_text_matches_text_field(self):
        from tts.processing import chunk_text_with_sentences

        text = "Hello world. How are you? I am fine. Thanks for asking."
        result = chunk_text_with_sentences(text, max_chars=40)
        for chunk in result:
            assert chunk["text"] == " ".join(chunk["sentences"])


# ---------------------------------------------------------------------------
# _build_timing_manifest Tests
# ---------------------------------------------------------------------------


class TestBuildTimingManifest:
    def test_basic_manifest(self):
        from tts.processing import _build_timing_manifest

        chunks = [
            {"text": "Hello. World.", "sentences": ["Hello.", "World."]},
        ]
        # 2.4 seconds of OGG audio
        audio_parts = [_make_ogg_opus_data(2.4)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        assert manifest["version"] == 1
        assert len(manifest["sentences"]) == 2
        assert manifest["sentences"][0]["start_ms"] == 0
        assert manifest["sentences"][-1]["end_ms"] == manifest["total_duration_ms"]

    def test_continuity(self):
        """Each sentence starts where the previous one ends."""
        from tts.processing import _build_timing_manifest

        chunks = [
            {"text": "A. B. C.", "sentences": ["A.", "B.", "C."]},
            {"text": "D. E.", "sentences": ["D.", "E."]},
        ]
        audio_parts = [_make_ogg_opus_data(3.0), _make_ogg_opus_data(2.0)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        sentences = manifest["sentences"]
        for i in range(1, len(sentences)):
            assert sentences[i]["start_ms"] == sentences[i - 1]["end_ms"]

    def test_proportional_distribution(self):
        """Longer sentences get more time."""
        from tts.processing import _build_timing_manifest

        chunks = [
            {
                "text": "Hi. A much longer sentence here.",
                "sentences": ["Hi.", "A much longer sentence here."],
            },
        ]
        audio_parts = [_make_ogg_opus_data(4.0)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        s0 = manifest["sentences"][0]
        s1 = manifest["sentences"][1]
        # "Hi." is 1 syllable, other is 7 syllables — s1 should get significantly more time
        assert (s1["end_ms"] - s1["start_ms"]) > (s0["end_ms"] - s0["start_ms"]) * 2

    def test_fallback_on_invalid_audio(self):
        """Uses word-count estimate when OGG parsing fails."""
        from tts.processing import _build_timing_manifest

        chunks = [
            {"text": "Hello world sentence.", "sentences": ["Hello world sentence."]},
        ]
        audio_parts = [b"not-ogg-data"]
        manifest = _build_timing_manifest(chunks, audio_parts)

        # Should still produce a manifest with positive duration
        assert manifest["total_duration_ms"] > 0
        assert len(manifest["sentences"]) == 1

    def test_many_chunks_timing_adds_up(self):
        """Total duration equals sum of all chunk durations."""
        from tts.processing import _build_timing_manifest

        chunks = [
            {
                "text": "Sentence one. Sentence two.",
                "sentences": ["Sentence one.", "Sentence two."],
            },
            {"text": "Sentence three.", "sentences": ["Sentence three."]},
            {
                "text": "Sentence four. Sentence five.",
                "sentences": ["Sentence four.", "Sentence five."],
            },
        ]
        durations = [2.5, 1.0, 3.0]
        audio_parts = [_make_ogg_opus_data(d) for d in durations]
        manifest = _build_timing_manifest(chunks, audio_parts)

        expected_total_ms = sum(d * 1000 for d in durations)
        assert abs(manifest["total_duration_ms"] - expected_total_ms) < 50  # Allow rounding

    def test_single_sentence_per_chunk(self):
        """When each chunk has exactly one sentence, timing is exact."""
        from tts.processing import _build_timing_manifest

        chunks = [
            {"text": "First.", "sentences": ["First."]},
            {"text": "Second.", "sentences": ["Second."]},
        ]
        audio_parts = [_make_ogg_opus_data(2.0), _make_ogg_opus_data(3.0)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        s0 = manifest["sentences"][0]
        s1 = manifest["sentences"][1]
        # First sentence should be ~2000ms
        assert abs((s0["end_ms"] - s0["start_ms"]) - 2000) < 50
        # Second should be ~3000ms
        assert abs((s1["end_ms"] - s1["start_ms"]) - 3000) < 50

    def test_empty_chunks(self):
        """Empty input produces empty manifest."""
        from tts.processing import _build_timing_manifest

        manifest = _build_timing_manifest([], [])
        assert manifest["version"] == 1
        assert manifest["total_duration_ms"] == 0
        assert manifest["sentences"] == []

    def test_all_sentences_have_text(self):
        """Every sentence in the manifest has non-empty text."""
        from tts.processing import _build_timing_manifest

        chunks = [
            {"text": "A. B. C.", "sentences": ["A.", "B.", "C."]},
            {"text": "D.", "sentences": ["D."]},
        ]
        audio_parts = [_make_ogg_opus_data(3.0), _make_ogg_opus_data(1.0)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        for s in manifest["sentences"]:
            assert "text" in s
            assert len(s["text"]) > 0
            assert "start_ms" in s
            assert "end_ms" in s
            assert s["end_ms"] > s["start_ms"]

    def test_mixed_valid_and_invalid_audio(self):
        """Handles mix of OGG chunks and invalid chunks."""
        from tts.processing import _build_timing_manifest

        chunks = [
            {"text": "Valid chunk.", "sentences": ["Valid chunk."]},
            {"text": "Invalid chunk.", "sentences": ["Invalid chunk."]},
        ]
        audio_parts = [_make_ogg_opus_data(2.0), b"not-ogg"]
        manifest = _build_timing_manifest(chunks, audio_parts)

        assert len(manifest["sentences"]) == 2
        assert manifest["sentences"][0]["start_ms"] == 0
        # Both should have positive durations
        for s in manifest["sentences"]:
            assert s["end_ms"] > s["start_ms"]


# ---------------------------------------------------------------------------
# OGG Duration Edge Cases
# ---------------------------------------------------------------------------


class TestOggDurationEdgeCases:
    def test_pre_skip_larger_than_granule(self):
        """Returns 0.0 when pre_skip exceeds granule position."""
        from tts.processing import _ogg_duration_seconds

        # Build OGG with pre_skip=5000, duration=0.0
        # _make_ogg_opus_data(0.0, pre_skip=5000) creates granule = 0*48000 + 5000 = 5000
        # which equals pre_skip, so last_granule <= pre_skip => 0.0
        data = _make_ogg_opus_data(0.0, pre_skip=5000)
        duration = _ogg_duration_seconds(data)
        assert duration == 0.0

    def test_multiple_pages(self):
        """Handles OGG data with many pages (simulated by concatenating)."""
        from tts.processing import _ogg_duration_seconds

        # Create two separate OGG streams and concatenate
        data1 = _make_ogg_opus_data(3.0)
        data2 = _make_ogg_opus_data(5.0)
        # The parser scans ALL OggS pages and keeps the last valid granule
        combined = data1 + data2
        duration = _ogg_duration_seconds(combined)
        # Should get a positive duration from the last OGG stream found
        assert duration > 0

    def test_zero_duration(self):
        """Zero-length audio returns 0.0."""
        from tts.processing import _ogg_duration_seconds

        data = _make_ogg_opus_data(0.0)
        duration = _ogg_duration_seconds(data)
        assert duration == 0.0

    def test_garbage_bytes_in_middle(self):
        """OGG data with garbage bytes between valid pages."""
        from tts.processing import _ogg_duration_seconds

        data = _make_ogg_opus_data(2.0)
        # Find second OggS page
        second_page = data.index(b"OggS", 4)
        modified = data[:second_page] + b"\xff\xfe\xfd" * 10 + data[second_page:]
        duration = _ogg_duration_seconds(modified)
        assert abs(duration - 2.0) < 0.01


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id}/audio-timing
# ---------------------------------------------------------------------------


class TestGetAudioTiming:
    """Tests for GET /api/articles/{id}/audio-timing."""

    async def test_returns_timing_json(self) -> None:
        """Returns timing JSON when audio is ready and timing exists."""
        import json as _json

        timing_data = {
            "version": 1,
            "total_duration_ms": 5000,
            "sentences": [
                {"text": "Hello.", "start_ms": 0, "end_ms": 2500},
                {"text": "World.", "start_ms": 2500, "end_ms": 5000},
            ],
        }
        article = ArticleFactory.create(
            id="art_timing1",
            user_id="user_001",
            audio_status="ready",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_timing1":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        # Store timing JSON in R2
        await r2.put(
            "articles/art_timing1/audio-timing.json",
            _json.dumps(timing_data).encode("utf-8"),
        )

        client, session_id = await _authenticated_client(env)
        resp = client.get("/api/articles/art_timing1/audio-timing")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"
        body = resp.json()
        assert body["version"] == 1
        assert body["total_duration_ms"] == 5000
        assert len(body["sentences"]) == 2
        assert body["sentences"][0]["text"] == "Hello."

    async def test_returns_404_when_no_timing_file(self) -> None:
        """Returns 404 when audio is ready but no timing file in R2."""
        article = ArticleFactory.create(
            id="art_notiming",
            user_id="user_001",
            audio_status="ready",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_notiming":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()  # Empty — no timing file stored
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.get("/api/articles/art_notiming/audio-timing")

        assert resp.status_code == 404
        assert "No audio timing available" in resp.json()["detail"]

    async def test_returns_404_when_audio_not_ready(self) -> None:
        """Returns 404 when audio_status is not 'ready'."""
        article = ArticleFactory.create(
            id="art_generating",
            user_id="user_001",
            audio_status="generating",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_generating":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get("/api/articles/art_generating/audio-timing")

        assert resp.status_code == 404

    async def test_returns_404_when_no_audio(self) -> None:
        """Returns 404 when article has no audio at all."""
        article = ArticleFactory.create(
            id="art_noaudio_t",
            user_id="user_001",
            audio_status=None,
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_noaudio_t":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get("/api/articles/art_noaudio_t/audio-timing")

        assert resp.status_code == 404

    def test_requires_authentication(self) -> None:
        """Returns 401 without session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles/some_id/audio-timing")
        assert resp.status_code == 401

    async def test_cache_headers(self) -> None:
        """Response includes immutable cache headers."""
        import json as _json

        timing_data = {
            "version": 1,
            "total_duration_ms": 1000,
            "sentences": [{"text": "Test.", "start_ms": 0, "end_ms": 1000}],
        }
        article = ArticleFactory.create(
            id="art_cache",
            user_id="user_001",
            audio_status="ready",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_cache":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        await r2.put(
            "articles/art_cache/audio-timing.json",
            _json.dumps(timing_data).encode("utf-8"),
        )

        client, session_id = await _authenticated_client(env)
        resp = client.get("/api/articles/art_cache/audio-timing")

        assert resp.status_code == 200
        assert "immutable" in resp.headers.get("cache-control", "")

    async def test_returns_404_for_missing_article(self) -> None:
        """Returns 404 when article does not exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get("/api/articles/nonexistent/audio-timing")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# process_tts timing manifest integration
# ---------------------------------------------------------------------------


class TestProcessTTSTimingIntegration:
    """Verify process_tts stores timing manifest and uses measured duration."""

    async def test_process_tts_stores_timing_manifest(self) -> None:
        """process_tts stores audio-timing.json alongside audio in R2."""
        import json as _json

        article = ArticleFactory.create(
            id="art_timing_int",
            user_id="user_001",
            markdown_content="# Test\n\nFirst sentence. Second sentence. Third sentence.",
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(3.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        from tts.processing import process_tts

        await process_tts("art_timing_int", env, user_id="user_001")

        # Verify audio-timing.json exists in R2
        timing_key = "articles/art_timing_int/audio-timing.json"
        assert timing_key in r2._store, (
            f"Expected {timing_key} in R2, got keys: {list(r2._store.keys())}"
        )

        # Parse and validate structure
        timing = _json.loads(r2._store[timing_key])
        assert timing["version"] == 1
        assert timing["total_duration_ms"] > 0
        assert isinstance(timing["sentences"], list)
        assert len(timing["sentences"]) > 0

        # Each sentence should have the required fields
        for s in timing["sentences"]:
            assert "text" in s
            assert "start_ms" in s
            assert "end_ms" in s
            assert s["end_ms"] > s["start_ms"]
            assert len(s["text"]) > 0

    async def test_process_tts_uses_measured_duration(self) -> None:
        """process_tts uses OGG-measured duration instead of word-count estimate."""
        # Use markdown with many words — word-count estimate would give a very different
        # number from the actual OGG duration
        long_text = "Hello world this is a test. " * 40  # ~280 words
        article = ArticleFactory.create(
            id="art_dur_meas",
            user_id="user_001",
            markdown_content=long_text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        # Return OGG audio with a specific known duration (5.0 seconds)
        fake_audio = _make_ogg_opus_data(5.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        from tts.processing import process_tts

        await process_tts("art_dur_meas", env, user_id="user_001")

        # Find the final UPDATE that sets audio_status='ready'
        ready_updates = [
            (sql, params)
            for sql, params in db.executed
            if "UPDATE" in sql and "ready" in str(params) and "audio_status" in sql
        ]
        assert len(ready_updates) >= 1

        # The audio_duration_seconds should be derived from OGG measurement, not
        # word-count estimate. The word-count estimate for ~280 words at 150 wpm
        # would be ~112 seconds, while the measured OGG total should be ~5s * num_chunks.
        # Get the duration from the final UPDATE params
        from tests.conftest import parse_update_params

        final_sql, final_params = ready_updates[-1]
        parsed = parse_update_params(final_sql, final_params)
        stored_duration = parsed.get("audio_duration_seconds")

        # The word-count estimate would be ~112s for 280 words.
        # The measured duration from OGG should be much shorter (~5s per chunk).
        # Since each AI call returns the same 5.0s audio, total is 5.0 * num_chunks.
        # This should be well under 60s, not the ~112s the estimate would produce.
        assert stored_duration is not None
        assert stored_duration < 60, (
            f"Expected measured OGG duration (not word-count estimate), got {stored_duration}s"
        )

    async def test_process_tts_timing_sentence_count_matches_text(self) -> None:
        """Timing manifest sentence count matches text sentence count."""
        import json as _json

        article = ArticleFactory.create(
            id="art_scount",
            user_id="user_001",
            markdown_content=(
                "First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence."
            ),
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(5.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        from tts.processing import process_tts, split_sentences, strip_markdown

        await process_tts("art_scount", env, user_id="user_001")

        timing_key = "articles/art_scount/audio-timing.json"
        assert timing_key in r2._store

        timing = _json.loads(r2._store[timing_key])
        # Count expected sentences from the stripped markdown
        stripped = strip_markdown(article["markdown_content"])
        expected_sentences = split_sentences(stripped)
        assert len(timing["sentences"]) == len(expected_sentences)

        # Verify sentence text matches
        for ts, es in zip(timing["sentences"], expected_sentences):
            assert ts["text"] == es

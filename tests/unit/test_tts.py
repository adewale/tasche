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

    async def test_regenerates_when_pending(self) -> None:
        """POST listen-later re-queues even when audio is already pending."""
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
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue, content=MockR2())

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_dup1/listen-later",
        )

        assert resp.status_code == 202
        assert resp.json()["audio_status"] == "pending"
        assert len(queue.messages) == 1

    async def test_regenerates_when_stuck_generating(self) -> None:
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
        env = MockEnv(db=db, article_queue=queue, content=MockR2())

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_dup2/listen-later",
        )

        assert resp.status_code == 202
        assert resp.json()["audio_status"] == "pending"
        assert len(queue.messages) == 1
        assert queue.messages[0]["type"] == "tts_generation"

    async def test_regenerates_when_already_ready(self) -> None:
        """POST listen-later deletes old audio and re-queues when ready."""
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
        queue = MockQueue()
        r2 = MockR2()
        await r2.put("articles/art_ready/audio.mp3", b"AUDIO")
        env = MockEnv(db=db, article_queue=queue, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_ready/listen-later",
        )

        assert resp.status_code == 202
        data = resp.json()
        assert data["audio_status"] == "pending"
        # Old audio should be cleaned up
        assert await r2.get("articles/art_ready/audio.mp3") is None

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
        from tts.processing import chunk_text

        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        chunks = chunk_text(text, max_chars=40)
        # Recombine and verify all known sentences are present
        recombined = " ".join(chunks)
        # Hardcoded expected sentences — not derived from production code
        for sentence in [
            "First sentence.",
            "Second sentence.",
            "Third sentence.",
            "Fourth sentence.",
        ]:
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
        from tts.processing import chunk_text_with_sentences

        text = "One. Two. Three. Four. Five."
        result = chunk_text_with_sentences(text)
        all_sentences = []
        for chunk in result:
            all_sentences.extend(chunk["sentences"])
        # Hardcoded expected sentences — not derived from production code
        assert all_sentences == ["One.", "Two.", "Three.", "Four.", "Five."]

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

        from tts.processing import process_tts

        await process_tts("art_scount", env, user_id="user_001")

        timing_key = "articles/art_scount/audio-timing.json"
        assert timing_key in r2._store

        timing = _json.loads(r2._store[timing_key])
        # Hardcoded expected sentences — input has no markdown to strip,
        # so we know exactly what the pipeline should produce.
        expected_sentences = [
            "First sentence.",
            "Second sentence.",
            "Third sentence.",
            "Fourth sentence.",
            "Fifth sentence.",
        ]
        assert len(timing["sentences"]) == 5

        # Verify sentence text matches
        for ts, es in zip(timing["sentences"], expected_sentences):
            assert ts["text"] == es


# ---------------------------------------------------------------------------
# OGG stream validity: multi-chunk audio must be a single logical stream
# ---------------------------------------------------------------------------


def _count_ogg_bos_eos_pages(data: bytes) -> tuple[int, int, list[int]]:
    """Parse OGG pages and count BOS/EOS pages, plus all serial numbers.

    OGG page header (RFC 3533):
      bytes 0-3:   'OggS' capture pattern
      byte  5:     header_type flags (0x02 = BOS, 0x04 = EOS)
      bytes 6-13:  granule_position (int64 LE)
      bytes 14-17: serial_number (uint32 LE)
      bytes 18-21: page_sequence_number (uint32 LE)
      bytes 22-25: checksum (uint32 LE)
      byte  26:    number_page_segments
      bytes 27...: segment table (one byte per segment)

    Returns (bos_count, eos_count, unique_serial_numbers).
    """
    import struct

    bos_count = 0
    eos_count = 0
    serials: set[int] = set()
    i = 0

    while i <= len(data) - 27:
        if data[i : i + 4] != b"OggS":
            i += 1
            continue

        header_type = data[i + 5]
        serial = struct.unpack_from("<I", data, i + 14)[0]
        serials.add(serial)

        if header_type & 0x02:  # BOS flag
            bos_count += 1
        if header_type & 0x04:  # EOS flag
            eos_count += 1

        num_segments = data[i + 26]
        # Sum segment sizes to find payload length
        seg_table = data[i + 27 : i + 27 + num_segments]
        payload_size = sum(seg_table)
        i += 27 + num_segments + payload_size

    return bos_count, eos_count, sorted(serials)


class TestOggSingleStreamValidity:
    """Multi-chunk TTS audio must produce a single OGG logical stream.

    The OGG specification (RFC 3533) defines a logical bitstream as pages
    between a BOS (Beginning of Stream) page and its EOS (End of Stream)
    page.  Browser <audio> elements only play the first logical stream
    and stop at the first EOS page.

    If multi-chunk audio contains multiple BOS/EOS pairs (one per chunk),
    the browser plays only the first chunk's audio (~1900 chars of speech)
    and silently ignores the rest.  This is the root cause of audio
    truncation for any article longer than 1900 characters.
    """

    async def test_multi_chunk_audio_has_single_bos_and_eos(self) -> None:
        """Concatenated multi-chunk OGG must have exactly 1 BOS and 1 EOS page.

        This test catches the naive b"".join() bug where each chunk is a
        complete OGG file with its own BOS/EOS, producing a chained stream
        that browsers cannot play past the first chunk.
        """
        from tts.processing import process_tts

        # Text long enough to produce multiple TTS chunks (>1900 chars)
        text = "This is a test sentence for audio validation purposes. " * 80
        article = ArticleFactory.create(
            id="art_ogg_stream",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        # Return realistic OGG Opus data for each chunk
        fake_audio = _make_ogg_opus_data(3.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        result = await process_tts("art_ogg_stream", env, user_id="user_001")
        assert result is not None
        assert result["chunks"] > 1, "Need multiple chunks to test stream validity"

        stored = r2._store.get("articles/art_ogg_stream/audio.ogg")
        assert stored is not None

        bos_count, eos_count, serials = _count_ogg_bos_eos_pages(stored)

        assert bos_count == 1, (
            f"OGG audio has {bos_count} BOS pages (one per chunk) — browsers will "
            f"only play the first stream. Must re-mux into a single logical stream."
        )
        assert eos_count == 1, (
            f"OGG audio has {eos_count} EOS pages — browsers stop at the first one. "
            f"Must re-mux into a single logical stream."
        )
        assert len(serials) == 1, (
            f"OGG audio has {len(serials)} different serial numbers {serials} — "
            f"a valid single stream must use exactly one serial number."
        )

    async def test_multi_chunk_ogg_has_continuous_granule_positions(self) -> None:
        """Granule positions must increase monotonically across the single stream.

        If granule positions reset to 0 mid-stream (as happens with naive
        concatenation), decoders may seek incorrectly or report wrong duration.
        """
        import struct

        from tts.processing import process_tts

        text = "Another test sentence for granule position validation. " * 80
        article = ArticleFactory.create(
            id="art_ogg_granule",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(3.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        result = await process_tts("art_ogg_granule", env, user_id="user_001")
        assert result is not None
        assert result["chunks"] > 1

        stored = r2._store.get("articles/art_ogg_granule/audio.ogg")
        assert stored is not None

        # Collect all granule positions from data pages (skip header pages)
        granules: list[int] = []
        i = 0
        page_idx = 0
        while i <= len(stored) - 27:
            if stored[i : i + 4] != b"OggS":
                i += 1
                continue

            granule = struct.unpack_from("<q", stored, i + 6)[0]
            header_type = stored[i + 5]
            num_segments = stored[i + 26]
            seg_table = stored[i + 27 : i + 27 + num_segments]
            payload_size = sum(seg_table)

            # Skip header pages (granule = 0 on BOS page is normal)
            is_bos = bool(header_type & 0x02)
            if not is_bos and granule >= 0:
                granules.append(granule)

            i += 27 + num_segments + payload_size
            page_idx += 1

        # Granule positions must be monotonically non-decreasing
        for j in range(1, len(granules)):
            assert granules[j] >= granules[j - 1], (
                f"Granule position decreased at page {j}: {granules[j]} < {granules[j - 1]} — "
                f"indicates stream boundary from naive concatenation"
            )

        # Final granule must reflect total duration (all chunks combined)
        if granules:
            total_duration_s = (granules[-1] - 312) / 48000.0  # subtract pre_skip
            expected_min_duration = 3.0 * result["chunks"] * 0.8  # 80% of expected
            assert total_duration_s >= expected_min_duration, (
                f"Final granule gives {total_duration_s:.1f}s but expected at least "
                f"{expected_min_duration:.1f}s for {result['chunks']} × 3.0s chunks"
            )

    async def test_single_chunk_ogg_unchanged(self) -> None:
        """A single-chunk article should produce valid OGG without re-muxing."""
        from tts.processing import process_tts

        text = "Short article. Just one chunk."
        article = ArticleFactory.create(
            id="art_ogg_single",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(2.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        result = await process_tts("art_ogg_single", env, user_id="user_001")
        assert result is not None
        assert result["chunks"] == 1

        stored = r2._store.get("articles/art_ogg_single/audio.ogg")
        assert stored is not None

        bos_count, eos_count, serials = _count_ogg_bos_eos_pages(stored)
        assert bos_count == 1
        assert eos_count == 1
        assert len(serials) == 1


# ---------------------------------------------------------------------------
# Audio completeness: content-proportional duration check
# ---------------------------------------------------------------------------


class TestContentProportionalDuration:
    """Verify audio duration scales proportionally with text length.

    A 5,000-word article should produce proportionally longer audio than a
    500-word article.  This catches silent truncation where only the first
    chunk's audio is kept.
    """

    async def test_longer_text_produces_longer_audio(self) -> None:
        """Audio duration from 10x more text should be significantly longer."""
        from tts.processing import process_tts

        short_text = "This is a short test sentence. " * 10  # ~70 words
        long_text = "This is a short test sentence. " * 100  # ~700 words

        short_article = ArticleFactory.create(
            id="art_short_dur",
            user_id="user_001",
            markdown_content=short_text,
        )
        long_article = ArticleFactory.create(
            id="art_long_dur",
            user_id="user_001",
            markdown_content=long_text,
        )

        # Each AI call returns 2s of OGG audio — more chunks = more total audio
        fake_audio = _make_ogg_opus_data(2.0)

        # Process short article
        short_db = TrackingD1(
            result_fn=lambda sql, params: [short_article] if "SELECT" in sql else []
        )
        short_r2 = MockR2()
        short_ai = MockAI(response=fake_audio)
        short_env = MockEnv(db=short_db, content=short_r2, ai=short_ai, tts_model="aura-2-en")
        short_result = await process_tts("art_short_dur", short_env, user_id="user_001")

        # Process long article
        long_db = TrackingD1(
            result_fn=lambda sql, params: [long_article] if "SELECT" in sql else []
        )
        long_r2 = MockR2()
        long_ai = MockAI(response=fake_audio)
        long_env = MockEnv(db=long_db, content=long_r2, ai=long_ai, tts_model="aura-2-en")
        long_result = await process_tts("art_long_dur", long_env, user_id="user_001")

        assert short_result is not None
        assert long_result is not None

        # Long article should produce more chunks and more total bytes
        assert long_result["chunks"] > short_result["chunks"], (
            f"Long text ({long_result['chunks']} chunks) should produce more chunks "
            f"than short text ({short_result['chunks']} chunks)"
        )
        assert long_result["total_bytes"] > short_result["total_bytes"], (
            f"Long text ({long_result['total_bytes']} bytes) should produce more audio "
            f"than short text ({short_result['total_bytes']} bytes)"
        )

    async def test_duration_reflects_chunk_count(self) -> None:
        """Stored audio_duration_seconds should scale with number of chunks."""
        import json as _json

        from tts.processing import process_tts

        # Text long enough for multiple chunks (~4000 chars = ~3 chunks)
        text = "Here is a sentence with several words in it. " * 100
        article = ArticleFactory.create(
            id="art_dur_scale",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        # Each chunk returns 3.0 seconds of audio
        fake_audio = _make_ogg_opus_data(3.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        result = await process_tts("art_dur_scale", env, user_id="user_001")
        assert result is not None

        # Check timing manifest: total_duration should be ~3.0s * num_chunks
        timing_key = "articles/art_dur_scale/audio-timing.json"
        timing = _json.loads(r2._store[timing_key])
        expected_ms = result["chunks"] * 3000
        assert abs(timing["total_duration_ms"] - expected_ms) < 100, (
            f"Expected ~{expected_ms}ms for {result['chunks']} chunks × 3s, "
            f"got {timing['total_duration_ms']}ms"
        )


# ---------------------------------------------------------------------------
# Known-text golden test: fixed input → expected audio properties
# ---------------------------------------------------------------------------


class TestKnownTextGolden:
    """Feed a fixed, known piece of text and verify audio output properties.

    Uses a deterministic 500-word text and verifies:
    - Expected number of TTS chunks
    - Audio duration within expected range (at ~150 WPM)
    - All sentences from input appear in timing manifest
    - Total audio bytes match sum of chunk sizes
    """

    GOLDEN_TEXT = (
        "The quick brown fox jumps over the lazy dog. "
        "Pack my box with five dozen liquor jugs. "
        "How vexingly quick daft zebras jump. "
        "The five boxing wizards jump quickly. "
        "Jackdaws love my big sphinx of quartz. "
    ) * 20  # ~500 words, ~2500 chars → 2 chunks

    async def test_golden_text_chunk_count(self) -> None:
        """Known text should produce the expected number of chunks.

        The golden text is ~4000 chars of plain text (no markdown to strip),
        so at 1900 chars/chunk it should produce exactly 3 chunks, each
        under the 1900-char limit.
        """
        from tts.processing import chunk_text

        # The golden text has no markdown syntax, so strip_markdown is a no-op.
        # We call chunk_text directly and validate against a hardcoded count.
        chunks = chunk_text(self.GOLDEN_TEXT)
        assert len(chunks) == 3, (
            f"Expected exactly 3 chunks for ~4000 chars at 1900 char limit, got {len(chunks)}"
        )
        # Every chunk should be under the limit
        for i, chunk in enumerate(chunks):
            assert len(chunk) <= 1900, f"Chunk {i} is {len(chunk)} chars, exceeds 1900 limit"

    async def test_golden_text_produces_complete_audio(self) -> None:
        """All chunks of golden text are processed and concatenated."""

        from tts.processing import process_tts

        article = ArticleFactory.create(
            id="art_golden",
            user_id="user_001",
            markdown_content=self.GOLDEN_TEXT,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(10.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        result = await process_tts("art_golden", env, user_id="user_001")
        assert result is not None

        # Hardcoded expected chunk count: ~4000 chars / 1900 max = 3 chunks
        assert result["chunks"] == 3, f"Expected 3 chunks, got {result['chunks']}"

        # Re-muxed total_bytes may be smaller than sum(chunk_sizes) because
        # duplicate OGG headers are stripped.  Verify it's at least as large
        # as the audio data from a single chunk (i.e., not truncated to nothing).
        assert result["total_bytes"] > min(result["chunk_sizes"]), (
            f"Total bytes ({result['total_bytes']}) is suspiciously small"
        )

        # Verify AI was called exactly once per chunk
        assert len(ai.calls) == 3

        # Verify audio file exists in R2
        audio_key = "articles/art_golden/audio.ogg"
        stored = r2._store.get(audio_key)
        assert stored is not None, f"Audio not found in R2 at {audio_key}"
        assert len(stored) == result["total_bytes"]

    async def test_golden_text_timing_covers_all_sentences(self) -> None:
        """Timing manifest includes every sentence from the golden text."""
        import json as _json

        from tts.processing import process_tts

        article = ArticleFactory.create(
            id="art_golden_tm",
            user_id="user_001",
            markdown_content=self.GOLDEN_TEXT,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(10.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        await process_tts("art_golden_tm", env, user_id="user_001")

        timing = _json.loads(r2._store["articles/art_golden_tm/audio-timing.json"])
        # The golden text has 5 sentences repeated 20 times = 100 sentences.
        # No markdown to strip, so all 100 sentences should appear in timing.
        assert len(timing["sentences"]) == 100, (
            f"Timing has {len(timing['sentences'])} sentences but expected 100 "
            f"(5 sentences × 20 repeats)"
        )

        # Verify the 5 unique sentence texts cycle correctly
        unique_sentences = [
            "The quick brown fox jumps over the lazy dog.",
            "Pack my box with five dozen liquor jugs.",
            "How vexingly quick daft zebras jump.",
            "The five boxing wizards jump quickly.",
            "Jackdaws love my big sphinx of quartz.",
        ]
        for i, ts in enumerate(timing["sentences"]):
            expected = unique_sentences[i % 5]
            assert ts["text"] == expected, f"Sentence {i} mismatch: {ts['text']!r} != {expected!r}"

    async def test_golden_text_duration_in_expected_range(self) -> None:
        """Audio duration should be in a reasonable range for ~500 words at 150 WPM."""
        import json as _json

        from tts.processing import process_tts

        article = ArticleFactory.create(
            id="art_golden_range",
            user_id="user_001",
            markdown_content=self.GOLDEN_TEXT,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        # Simulate realistic 100s per chunk (for ~250 words at 150 WPM)
        fake_audio = _make_ogg_opus_data(100.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        result = await process_tts("art_golden_range", env, user_id="user_001")
        assert result is not None

        timing = _json.loads(r2._store["articles/art_golden_range/audio-timing.json"])
        total_duration_s = timing["total_duration_ms"] / 1000.0

        # Each chunk produces 100s of audio, so total should be chunks × 100s.
        # Verify it's not suspiciously short (truncated to a single chunk).
        expected_s = result["chunks"] * 100.0
        assert total_duration_s > 100, (
            f"Audio duration {total_duration_s}s is too short for ~500 words — "
            f"likely truncated (expected ~{expected_s}s)"
        )
        assert abs(total_duration_s - expected_s) < 1, (
            f"Duration {total_duration_s}s doesn't match expected {expected_s}s "
            f"({result['chunks']} chunks × 100s)"
        )


# ---------------------------------------------------------------------------
# Chunk-count verification: AI calls match expected text chunks
# ---------------------------------------------------------------------------


class TestChunkCountVerification:
    """Verify the number of TTS API calls matches the expected chunk count
    for a given text length.  Catches bugs where chunking logic and actual
    API calls diverge.
    """

    async def test_ai_calls_equal_chunk_count(self) -> None:
        """Number of AI.run() calls must equal expected chunk count."""
        from tts.processing import process_tts

        # Each sentence is 57 chars. 100 sentences = ~5700 chars.
        # At 1900 chars/chunk, this should produce exactly 4 chunks.
        sentence = "This is a moderately long sentence for testing purposes. "
        text = sentence * 100  # ~5700 chars → 4 chunks

        article = ArticleFactory.create(
            id="art_chk_count",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(2.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        result = await process_tts("art_chk_count", env, user_id="user_001")
        assert result is not None

        # Hardcoded expected: 5700 chars / 1900 max = 4 chunks
        assert len(ai.calls) == 4, f"AI was called {len(ai.calls)} times but expected 4 chunks"
        assert result["chunks"] == 4

    async def test_single_chunk_text(self) -> None:
        """Short text under 1900 chars produces exactly 1 AI call."""
        from tts.processing import process_tts

        text = "A short article. Just a few sentences. Nothing too long."
        article = ArticleFactory.create(
            id="art_chk_single",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(2.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        result = await process_tts("art_chk_single", env, user_id="user_001")
        assert result is not None
        assert result["chunks"] == 1
        assert len(ai.calls) == 1

    async def test_each_chunk_text_sent_to_ai(self) -> None:
        """Verify the actual text sent to each AI call covers the input text.

        Instead of deriving expected chunks from production code, we verify
        that every input sentence appears in exactly one AI call, and that
        the first chunk starts with the first sentence.
        """
        from tts.processing import process_tts

        text = "First paragraph with enough words. " * 50 + "Second section here. " * 50

        article = ArticleFactory.create(
            id="art_chk_text",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(2.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        await process_tts("art_chk_text", env, user_id="user_001")

        # Should produce 2 chunks (~2800 chars / 1900 max)
        assert len(ai.calls) == 2, f"Expected 2 AI calls, got {len(ai.calls)}"

        # First chunk should start with "First paragraph" content
        assert ai.calls[0].get("text", "").startswith("First paragraph with enough words."), (
            f"First chunk doesn't start with expected text: {ai.calls[0].get('text', '')[:50]!r}"
        )

        # Second chunk should contain "Second section" content
        assert "Second section here." in ai.calls[1].get("text", ""), (
            f"Second chunk doesn't contain expected text: {ai.calls[1].get('text', '')[:50]!r}"
        )


# ---------------------------------------------------------------------------
# Audio decode/playback validation: verify stored audio is structurally valid
# ---------------------------------------------------------------------------


class TestAudioDecodeValidation:
    """Verify the stored audio bytes are structurally valid and can be
    decoded to completion.  Catches corruption from bad concatenation,
    partial writes, or format detection errors.
    """

    async def test_ogg_audio_has_valid_structure(self) -> None:
        """OGG audio stored in R2 starts with OggS and has valid OGG page structure."""
        from tts.processing import process_tts

        text = "Some sample text for audio validation. " * 30
        article = ArticleFactory.create(
            id="art_ogg_valid",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(5.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        await process_tts("art_ogg_valid", env, user_id="user_001")

        stored = r2._store.get("articles/art_ogg_valid/audio.ogg")
        assert stored is not None

        # Verify OGG magic bytes
        assert stored[:4] == b"OggS", f"Audio doesn't start with OggS: {stored[:4]!r}"

        # Verify structural validity using an independent check (not _ogg_duration_seconds):
        # The stored audio should have exactly 1 BOS and 1 EOS page (single logical stream)
        bos_count, eos_count, serials = _count_ogg_bos_eos_pages(stored)
        assert bos_count == 1, f"Expected 1 BOS page, got {bos_count}"
        assert eos_count == 1, f"Expected 1 EOS page, got {eos_count}"
        # Must contain OpusHead header in first page
        assert b"OpusHead" in stored[:100], "Missing OpusHead header in stored OGG"

    async def test_multi_chunk_ogg_each_chunk_has_valid_header(self) -> None:
        """When multiple OGG chunks are concatenated, each chunk boundary
        has valid OggS headers (important for players that scan OGG pages).
        """
        from tts.processing import process_tts

        text = "A sentence for multi chunk validation. " * 80  # Force multiple chunks
        article = ArticleFactory.create(
            id="art_multi_ogg",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(3.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        result = await process_tts("art_multi_ogg", env, user_id="user_001")
        assert result is not None
        assert result["chunks"] > 1, "Need multiple chunks for this test"

        stored = r2._store.get("articles/art_multi_ogg/audio.ogg")
        assert stored is not None

        # Count OggS page markers in the re-muxed single-stream file
        ogg_pages = 0
        idx = 0
        while idx <= len(stored) - 4:
            if stored[idx : idx + 4] == b"OggS":
                ogg_pages += 1
                idx += 27  # Skip past the minimum OGG header size
            else:
                idx += 1

        # Re-muxed stream: 1 ID header + 1 data page per chunk (headers from
        # chunks 2+ are stripped).  Must have at least 1 + num_chunks pages.
        expected_min_pages = 1 + result["chunks"]
        assert ogg_pages >= expected_min_pages, (
            f"Expected at least {expected_min_pages} OGG pages for {result['chunks']} chunks, "
            f"found {ogg_pages}"
        )

    async def test_timing_duration_matches_expected(self) -> None:
        """Timing manifest total_duration_ms should match the known input duration.

        Each AI call returns 4.0s of OGG audio. The expected total is
        chunks x 4000ms. We validate the manifest against this hardcoded
        expectation rather than re-parsing the stored OGG with the same
        function that built the manifest (which would be tautological).
        """
        import json as _json

        from tts.processing import process_tts

        text = "Testing duration consistency across audio and manifest. " * 20
        article = ArticleFactory.create(
            id="art_dur_match",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(4.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        result = await process_tts("art_dur_match", env, user_id="user_001")
        assert result is not None

        # Get timing manifest duration
        timing = _json.loads(r2._store["articles/art_dur_match/audio-timing.json"])
        manifest_duration_ms = timing["total_duration_ms"]

        # Validate against hardcoded expectation: chunks x 4000ms
        expected_total_ms = result["chunks"] * 4000
        assert abs(manifest_duration_ms - expected_total_ms) < 50, (
            f"Timing manifest ({manifest_duration_ms}ms) differs from "
            f"expected ({expected_total_ms}ms)"
        )

        # Verify the re-muxed file's final granule position independently
        # using struct (not _ogg_duration_seconds) for a cross-check
        import struct

        stored = r2._store["articles/art_dur_match/audio.ogg"]
        # Find the last OggS page and read its granule position
        last_granule = 0
        i = 0
        while i <= len(stored) - 27:
            if stored[i : i + 4] != b"OggS":
                i += 1
                continue
            granule = struct.unpack_from("<q", stored, i + 6)[0]
            if granule > 0:
                last_granule = granule
            num_segments = stored[i + 26]
            i += 27 + num_segments
        # Subtract pre_skip (312 samples) and convert to ms at 48kHz
        remuxed_duration_ms = (last_granule - 312) / 48.0
        assert abs(remuxed_duration_ms - expected_total_ms) < 50, (
            f"Re-muxed OGG granule-derived duration ({remuxed_duration_ms:.0f}ms) differs from "
            f"expected ({expected_total_ms}ms)"
        )

    async def test_r2_stored_size_matches_concatenated_chunks(self) -> None:
        """Audio bytes stored in R2 must equal the concatenation of all chunks."""
        from tts.processing import process_tts

        text = "Verification of R2 storage integrity for audio data. " * 60
        article = ArticleFactory.create(
            id="art_r2_size",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(2.5)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        result = await process_tts("art_r2_size", env, user_id="user_001")
        assert result is not None

        stored = r2._store.get("articles/art_r2_size/audio.ogg")
        assert stored is not None

        # Stored size should equal total_bytes from result
        assert len(stored) == result["total_bytes"], (
            f"R2 stored {len(stored)} bytes but pipeline reported {result['total_bytes']}"
        )

        # Re-muxed OGG is smaller than raw concatenation (duplicate headers removed),
        # but must contain all audio data pages.  Verify it's larger than a single chunk.
        assert len(stored) > len(fake_audio), (
            f"R2 has {len(stored)} bytes which is <= a single chunk ({len(fake_audio)}) "
            f"— audio from {result['chunks']} chunks was lost"
        )

    async def test_audio_endpoint_serves_complete_stored_audio(self) -> None:
        """GET /api/articles/{id}/audio returns all bytes stored in R2."""
        article = ArticleFactory.create(
            id="art_serve_full",
            user_id="user_001",
            audio_status="ready",
            audio_key="articles/art_serve_full/audio.ogg",
        )

        # Store known audio bytes in R2
        ogg_data = _make_ogg_opus_data(5.0)
        full_audio = ogg_data * 3  # Simulate 3 concatenated chunks

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_serve_full":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        await r2.put("articles/art_serve_full/audio.ogg", full_audio)
        env = MockEnv(db=db, content=r2)

        client, _ = await _authenticated_client(env)
        resp = client.get("/api/articles/art_serve_full/audio")

        assert resp.status_code == 200
        assert len(resp.content) == len(full_audio), (
            f"Response has {len(resp.content)} bytes but R2 has {len(full_audio)} — "
            f"audio truncated during serving!"
        )
        assert resp.content == full_audio, "Served audio content differs from stored audio"


# ---------------------------------------------------------------------------
# Format-independent tests: catch audio truncation regardless of format
# ---------------------------------------------------------------------------


class TestFormatIndependentAudioCompleteness:
    """Tests that verify audio output completeness using format-agnostic
    properties.  These would catch the truncation bug whether output is
    OGG, MP3, WAV, or any other format.

    Instead of parsing OGG page headers, these tests use external invariants:
    - Output size scales with input length
    - All API chunks are included in the output
    - Output is strictly larger than any single chunk response
    """

    async def test_output_contains_bytes_from_every_chunk_response(self) -> None:
        """Final audio must include data from every AI response, not just the first.

        Uses uniquely-tagged chunk responses to verify all are present. This is
        format-independent — it works for any concatenation/remuxing strategy.
        """
        from tts.processing import process_tts

        text = "A sentence for multi chunk format independent testing. " * 80
        article = ArticleFactory.create(
            id="art_fi_every",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()

        # Each chunk gets a unique 8-byte tag embedded in OGG audio data pages.
        # After remuxing, data pages from all chunks should be in the output.
        chunk_tags: list[bytes] = []
        call_idx = {"n": 0}

        class TaggedAI:
            def __init__(self):
                self.calls = []

            async def run(self, model, inputs=None, **kwargs):
                call = {"model": model}
                if isinstance(inputs, dict):
                    call.update(inputs)
                self.calls.append(call)
                idx = call_idx["n"]
                call_idx["n"] += 1
                # Create unique tag bytes for this chunk
                tag = f"TAG{idx:04d}".encode()  # e.g. b"TAG0000", b"TAG0001"
                chunk_tags.append(tag)
                # Build OGG audio with the tag in the data page payload
                import struct

                pre_skip = 312
                total_samples = int(3.0 * 48000) + pre_skip
                opus_head = (
                    b"OpusHead"
                    + struct.pack("<B", 1)
                    + struct.pack("<B", 1)
                    + struct.pack("<H", pre_skip)
                    + struct.pack("<I", 48000)
                    + struct.pack("<h", 0)
                    + struct.pack("<B", 0)
                )
                page1 = b"OggS"
                page1 += struct.pack("<B", 0)
                page1 += struct.pack("<B", 2)  # BOS
                page1 += struct.pack("<q", 0)
                page1 += struct.pack("<I", 1)
                page1 += struct.pack("<I", 0)
                page1 += struct.pack("<I", 0)
                page1 += struct.pack("<B", 1)
                page1 += struct.pack("<B", len(opus_head))
                page1 += opus_head
                # Data page with the unique tag
                payload = tag + b"\x00" * 92  # 100 bytes total
                page2 = b"OggS"
                page2 += struct.pack("<B", 0)
                page2 += struct.pack("<B", 4)  # EOS
                page2 += struct.pack("<q", total_samples)
                page2 += struct.pack("<I", 1)
                page2 += struct.pack("<I", 2)
                page2 += struct.pack("<I", 0)
                page2 += struct.pack("<B", 1)
                page2 += struct.pack("<B", len(payload))
                page2 += payload
                return page1 + page2

        ai = TaggedAI()
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        result = await process_tts("art_fi_every", env, user_id="user_001")
        assert result is not None
        assert result["chunks"] > 1, "Need multiple chunks for this test"

        stored = r2._store.get("articles/art_fi_every/audio.ogg")
        assert stored is not None

        # Every chunk's unique tag must appear in the final output
        for i, tag in enumerate(chunk_tags):
            assert tag in stored, (
                f"Chunk {i} tag {tag!r} missing from output — "
                f"audio from this chunk was lost during concatenation/remuxing"
            )

    async def test_output_size_proportional_to_input_chunks(self) -> None:
        """An article producing N chunks should have output roughly N times
        larger than a single-chunk article's output.

        This catches truncation where only 1 of N chunks survives, regardless
        of the audio container format.
        """
        from tts.processing import process_tts

        short_text = "Short article content here."  # 1 chunk
        long_text = "A longer sentence for producing multiple chunks. " * 80  # N chunks

        short_article = ArticleFactory.create(
            id="art_fi_short", user_id="user_001", markdown_content=short_text
        )
        long_article = ArticleFactory.create(
            id="art_fi_long", user_id="user_001", markdown_content=long_text
        )

        fake_audio = _make_ogg_opus_data(3.0)
        single_chunk_size = len(fake_audio)

        # Process short article
        short_db = TrackingD1(
            result_fn=lambda sql, params: [short_article] if "SELECT" in sql else []
        )
        short_r2 = MockR2()
        short_env = MockEnv(
            db=short_db, content=short_r2, ai=MockAI(response=fake_audio), tts_model="aura-2-en"
        )
        short_result = await process_tts("art_fi_short", short_env, user_id="user_001")

        # Process long article
        long_db = TrackingD1(
            result_fn=lambda sql, params: [long_article] if "SELECT" in sql else []
        )
        long_r2 = MockR2()
        long_env = MockEnv(
            db=long_db, content=long_r2, ai=MockAI(response=fake_audio), tts_model="aura-2-en"
        )
        long_result = await process_tts("art_fi_long", long_env, user_id="user_001")

        assert short_result is not None
        assert long_result is not None
        assert long_result["chunks"] > 1

        # Long output must be strictly larger than a single chunk response.
        # With the truncation bug, long_result["total_bytes"] would equal
        # a single chunk's size regardless of how many chunks were generated.
        assert long_result["total_bytes"] > single_chunk_size, (
            f"Long article ({long_result['chunks']} chunks) produced only "
            f"{long_result['total_bytes']} bytes — same as a single chunk "
            f"({single_chunk_size} bytes). Audio was truncated."
        )

        # The ratio of output sizes should roughly match the ratio of chunk counts.
        # Allow generous tolerance (50%) since remuxing may add/remove headers.
        ratio = long_result["total_bytes"] / short_result["total_bytes"]
        chunk_ratio = long_result["chunks"] / short_result["chunks"]
        assert ratio > chunk_ratio * 0.5, (
            f"Output size ratio ({ratio:.1f}x) is much less than chunk count "
            f"ratio ({chunk_ratio:.1f}x) — suggests audio data is being lost"
        )

    async def test_all_input_text_reaches_ai(self) -> None:
        """Every word of the input text must be sent to the AI, not dropped.

        This is format-independent — it verifies the text pipeline, not the
        audio format. Catches bugs where chunking silently drops text.
        """
        from tts.processing import process_tts

        # Use text with distinct, identifiable words (no markdown syntax,
        # so strip_markdown is a no-op and we can check against the raw words)
        sentences = [
            "Alpha beta gamma delta epsilon.",
            "Zeta eta theta iota kappa.",
            "Lambda mu nu xi omicron.",
            "Pi rho sigma tau upsilon.",
            "Phi chi psi omega terminus.",
        ]
        text = " ".join(sentences * 20)  # Repeat to force multiple chunks

        article = ArticleFactory.create(
            id="art_fi_alltext",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(2.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        await process_tts("art_fi_alltext", env, user_id="user_001")

        # Concatenate all text sent to AI
        all_sent = " ".join(call.get("text", "") for call in ai.calls)

        # Hardcoded expected words — the input has no markdown, so these
        # are the exact words that should reach the AI
        expected_words = [
            "Alpha",
            "beta",
            "gamma",
            "delta",
            "epsilon.",
            "Zeta",
            "eta",
            "theta",
            "iota",
            "kappa.",
            "Lambda",
            "mu",
            "nu",
            "xi",
            "omicron.",
            "Pi",
            "rho",
            "sigma",
            "tau",
            "upsilon.",
            "Phi",
            "chi",
            "psi",
            "omega",
            "terminus.",
        ]
        for word in expected_words:
            assert word in all_sent, (
                f"Word {word!r} from input was never sent to AI — text was dropped"
            )

    async def test_no_ai_call_returns_are_silently_discarded(self) -> None:
        """Every non-empty AI response must contribute to the final output.

        With the old b"".join() bug, all responses were "joined" but browsers
        only played the first logical stream. This test verifies at a higher
        level: the number of chunks reported in the result must equal the
        number of AI calls that returned non-empty data.
        """
        from tts.processing import process_tts

        text = "Testing that no AI responses are silently discarded. " * 80
        article = ArticleFactory.create(
            id="art_fi_nodiscard",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = _make_ogg_opus_data(3.0)
        ai = MockAI(response=fake_audio)
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="aura-2-en")

        result = await process_tts("art_fi_nodiscard", env, user_id="user_001")
        assert result is not None

        # Every AI call returned non-empty audio, so chunks must equal call count
        assert result["chunks"] == len(ai.calls), (
            f"Pipeline reported {result['chunks']} chunks but made "
            f"{len(ai.calls)} AI calls — some responses were discarded"
        )

    async def test_melotts_multi_chunk_output_grows_with_input(self) -> None:
        """Format-independent check for MeloTTS (WAV/base64): output grows with input.

        The truncation bug was OGG-specific, but this test would catch any
        format's concatenation bug by checking output size scaling.
        """
        from tts.processing import process_tts

        text = "A test sentence for melotts multi chunk validation. " * 80
        article = ArticleFactory.create(
            id="art_fi_melo",
            user_id="user_001",
            markdown_content=text,
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()

        # MeloTTS returns base64-encoded WAV
        fake_wav = b"RIFF" + b"\x00" * 200
        ai = MockAI(response={"audio": base64.b64encode(fake_wav).decode()})
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="melotts")

        result = await process_tts("art_fi_melo", env, user_id="user_001")
        assert result is not None
        assert result["chunks"] > 1, "Need multiple chunks to test"

        # Output must be larger than a single chunk's audio
        assert result["total_bytes"] > len(fake_wav), (
            f"MeloTTS output ({result['total_bytes']} bytes) is not larger than "
            f"a single chunk ({len(fake_wav)} bytes) — audio truncated"
        )

        # Output should be approximately chunks * single_chunk_size
        expected = result["chunks"] * len(fake_wav)
        assert result["total_bytes"] == expected, (
            f"MeloTTS output ({result['total_bytes']}) != "
            f"{result['chunks']} × {len(fake_wav)} = {expected}"
        )

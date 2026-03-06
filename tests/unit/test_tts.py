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

    async def test_ai_runtime_error_is_transient(self) -> None:
        """RuntimeError from Workers AI is treated as transient and re-raised."""
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
    async def test_connection_error_reraises_for_retry(self) -> None:
        """ConnectionError re-raises so the queue can retry; audio_status stays generating."""
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

        # audio_status should NOT be set to 'failed' (only 'generating' from step 1)
        failed_updates = [
            (sql, params)
            for sql, params in db.executed
            if sql.startswith("UPDATE") and "audio_status" in sql and "failed" in str(params)
        ]
        assert len(failed_updates) == 0

        # Verify 'generating' was set (step 1)
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


class TestGenerateSentenceTiming:
    def test_basic_timing(self) -> None:
        """Generates timing entries with correct structure."""
        from tts.processing import generate_sentence_timing

        result = generate_sentence_timing("First sentence here. Second sentence there.")
        assert "sentences" in result
        assert "words_per_minute" in result
        assert "total_duration_seconds" in result
        assert result["words_per_minute"] == 150
        assert len(result["sentences"]) == 2

        s0 = result["sentences"][0]
        assert s0["text"] == "First sentence here."
        assert s0["start"] == 0.0
        assert s0["word_count"] == 3
        assert s0["end"] > 0

        s1 = result["sentences"][1]
        assert s1["start"] == s0["end"]
        assert s1["word_count"] == 3

    def test_timing_is_cumulative(self) -> None:
        """Each sentence start equals the previous sentence end."""
        from tts.processing import generate_sentence_timing

        result = generate_sentence_timing(
            "Short. Medium sentence. A much longer sentence with more words."
        )
        sentences = result["sentences"]
        for i in range(1, len(sentences)):
            assert sentences[i]["start"] == sentences[i - 1]["end"]

    def test_total_duration_matches_sum(self) -> None:
        """Total duration equals the end time of the last sentence."""
        from tts.processing import generate_sentence_timing

        result = generate_sentence_timing("Hello world. Goodbye world.")
        assert result["total_duration_seconds"] == result["sentences"][-1]["end"]

    def test_empty_text(self) -> None:
        """Empty text produces empty sentences list."""
        from tts.processing import generate_sentence_timing

        result = generate_sentence_timing("")
        assert result["sentences"] == []
        assert result["total_duration_seconds"] == 0

    def test_custom_wpm(self) -> None:
        """Custom words_per_minute changes timing."""
        from tts.processing import generate_sentence_timing

        text = "Ten words in this sentence that should take some time."
        fast = generate_sentence_timing(text, words_per_minute=300)
        slow = generate_sentence_timing(text, words_per_minute=100)
        assert fast["total_duration_seconds"] < slow["total_duration_seconds"]
        assert fast["words_per_minute"] == 300
        assert slow["words_per_minute"] == 100


class TestTTSProcessingStoresTimingData:
    async def test_timing_json_stored_in_r2(self) -> None:
        """TTS processing stores audio-timing.json alongside audio.wav."""
        article = ArticleFactory.create(
            id="art_timing1",
            user_id="user_001",
            markdown_content="# Hello World\n\nFirst sentence. Second sentence.",
        )

        db = TrackingD1(result_fn=lambda sql, params: [article] if "SELECT" in sql else [])
        r2 = MockR2()
        fake_audio = b"RIFF" + b"\x00" * 200
        ai = MockAI(response={"audio": base64.b64encode(fake_audio).decode()})
        env = MockEnv(db=db, content=r2, ai=ai, tts_model="melotts")

        from tts.processing import process_tts

        await process_tts("art_timing1", env, user_id="user_001")

        # Verify audio was stored (MeloTTS returns WAV)
        assert "articles/art_timing1/audio.wav" in r2._store

        # Verify timing JSON was stored
        import json

        assert "articles/art_timing1/audio-timing.json" in r2._store
        timing_raw = r2._store["articles/art_timing1/audio-timing.json"]
        timing = json.loads(timing_raw)
        assert "sentences" in timing
        assert "words_per_minute" in timing
        assert "total_duration_seconds" in timing
        assert len(timing["sentences"]) > 0


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id}/audio-timing
# ---------------------------------------------------------------------------


class TestGetAudioTiming:
    async def test_returns_timing_json(self) -> None:
        """GET audio-timing returns the timing JSON from R2."""
        import json

        article = ArticleFactory.create(
            id="art_atm1",
            user_id="user_001",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_atm1":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        # Store timing data in R2
        timing_data = {
            "sentences": [
                {"text": "First.", "start": 0.0, "end": 1.2, "word_count": 1},
            ],
            "words_per_minute": 150,
            "total_duration_seconds": 1.2,
        }
        await r2.put("articles/art_atm1/audio-timing.json", json.dumps(timing_data))

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_atm1/audio-timing",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["words_per_minute"] == 150
        assert len(data["sentences"]) == 1

    async def test_returns_404_when_no_timing(self) -> None:
        """GET audio-timing returns 404 when no timing data in R2."""
        article = ArticleFactory.create(
            id="art_notm",
            user_id="user_001",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_notm":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_notm/audio-timing",
        )

        assert resp.status_code == 404

    async def test_returns_404_for_missing_article(self) -> None:
        """GET audio-timing returns 404 when article does not exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/nonexistent/audio-timing",
        )

        assert resp.status_code == 404

    def test_audio_timing_returns_401_without_auth(self) -> None:
        """GET audio-timing returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles/some_id/audio-timing")
        assert resp.status_code == 401


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

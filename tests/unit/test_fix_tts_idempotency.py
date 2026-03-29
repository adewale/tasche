"""Tests for TTS idempotency fix (audit issue 80).

Verifies that the listen-later endpoint skips regeneration when audio is
already ready and article content hasn't changed, and that audio_generated_at
is set after successful TTS generation.
"""

from __future__ import annotations

from src.tts.routes import router
from tests.conftest import (
    ArticleFactory,
    MockAI,
    MockEnv,
    MockQueue,
    MockR2,
    TrackingD1,
    make_test_helpers,
    parse_update_params,
)

_make_app, _authenticated_client = make_test_helpers((router, "/api/articles"))


class TestTTSIdempotency:
    """Tests for TTS regeneration idempotency in the listen-later endpoint."""

    async def test_skips_regeneration_when_content_unchanged(self) -> None:
        """When audio is ready and content hasn't changed, return existing audio."""
        article = ArticleFactory.create(
            id="art_idem1",
            user_id="user_001",
            audio_status="ready",
            audio_key="articles/art_idem1/audio.ogg",
            audio_duration_seconds=120,
            updated_at="2025-06-01T00:00:00",
            audio_generated_at="2025-06-02T00:00:00",  # generated AFTER last update
        )

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql:
                return [article]
            return []

        db = TrackingD1(result_fn=execute)
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, _ = await _authenticated_client(env)
        resp = client.post("/api/articles/art_idem1/listen-later")

        assert resp.status_code == 202
        data = resp.json()
        assert data["audio_status"] == "ready"
        assert data["skipped"] is True
        assert data["audio_key"] == "articles/art_idem1/audio.ogg"
        assert data["audio_duration_seconds"] == 120

        # No queue message should have been sent
        assert len(queue.messages) == 0

        # No UPDATE should have been executed
        update_calls = [(sql, p) for sql, p in db.executed if sql.startswith("UPDATE")]
        assert len(update_calls) == 0

    async def test_skips_when_timestamps_equal(self) -> None:
        """When updated_at == audio_generated_at, skip regeneration."""
        article = ArticleFactory.create(
            id="art_idem_eq",
            user_id="user_001",
            audio_status="ready",
            audio_key="articles/art_idem_eq/audio.ogg",
            audio_duration_seconds=60,
            updated_at="2025-06-01T12:00:00",
            audio_generated_at="2025-06-01T12:00:00",
        )

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql:
                return [article]
            return []

        db = TrackingD1(result_fn=execute)
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, _ = await _authenticated_client(env)
        resp = client.post("/api/articles/art_idem_eq/listen-later")

        assert resp.status_code == 202
        data = resp.json()
        assert data["skipped"] is True
        assert len(queue.messages) == 0

    async def test_regenerates_when_content_changed(self) -> None:
        """When audio is ready but content changed after generation, regenerate."""
        article = ArticleFactory.create(
            id="art_regen1",
            user_id="user_001",
            audio_status="ready",
            audio_key="articles/art_regen1/audio.ogg",
            audio_duration_seconds=90,
            updated_at="2025-06-05T00:00:00",  # updated AFTER audio was generated
            audio_generated_at="2025-06-01T00:00:00",
        )

        def execute(sql: str, params: list) -> list:
            if "user_preferences" in sql and "SELECT" in sql:
                return [{"tts_voice": "athena"}]
            if sql.startswith("SELECT") and "id = ?" in sql:
                return [article]
            return []

        db = TrackingD1(result_fn=execute)
        queue = MockQueue()
        r2 = MockR2()
        env = MockEnv(db=db, article_queue=queue, content=r2)

        client, _ = await _authenticated_client(env)
        resp = client.post("/api/articles/art_regen1/listen-later")

        assert resp.status_code == 202
        data = resp.json()
        assert data["audio_status"] == "pending"
        assert "skipped" not in data

        # Queue message should have been sent
        assert len(queue.messages) == 1
        assert queue.messages[0]["type"] == "tts_generation"

    async def test_generates_when_no_audio_exists(self) -> None:
        """When no audio exists (audio_status is None), generate normally."""
        article = ArticleFactory.create(
            id="art_new1",
            user_id="user_001",
            audio_status=None,
            audio_key=None,
            audio_duration_seconds=None,
            audio_generated_at=None,
        )

        def execute(sql: str, params: list) -> list:
            if "user_preferences" in sql and "SELECT" in sql:
                return [{"tts_voice": "athena"}]
            if sql.startswith("SELECT") and "id = ?" in sql:
                return [article]
            return []

        db = TrackingD1(result_fn=execute)
        queue = MockQueue()
        r2 = MockR2()
        env = MockEnv(db=db, article_queue=queue, content=r2)

        client, _ = await _authenticated_client(env)
        resp = client.post("/api/articles/art_new1/listen-later")

        assert resp.status_code == 202
        data = resp.json()
        assert data["audio_status"] == "pending"
        assert len(queue.messages) == 1

    async def test_generates_when_audio_failed(self) -> None:
        """When audio_status is 'failed', always regenerate (no idempotency skip)."""
        article = ArticleFactory.create(
            id="art_fail1",
            user_id="user_001",
            audio_status="failed",
            audio_key=None,
            audio_generated_at="2025-06-01T00:00:00",
            updated_at="2025-05-01T00:00:00",
        )

        def execute(sql: str, params: list) -> list:
            if "user_preferences" in sql and "SELECT" in sql:
                return [{"tts_voice": "athena"}]
            if sql.startswith("SELECT") and "id = ?" in sql:
                return [article]
            return []

        db = TrackingD1(result_fn=execute)
        queue = MockQueue()
        r2 = MockR2()
        env = MockEnv(db=db, article_queue=queue, content=r2)

        client, _ = await _authenticated_client(env)
        resp = client.post("/api/articles/art_fail1/listen-later")

        assert resp.status_code == 202
        data = resp.json()
        assert data["audio_status"] == "pending"
        assert len(queue.messages) == 1

    async def test_regenerates_when_audio_generated_at_is_null(self) -> None:
        """When audio is ready but audio_generated_at is NULL (legacy), regenerate."""
        article = ArticleFactory.create(
            id="art_legacy1",
            user_id="user_001",
            audio_status="ready",
            audio_key="articles/art_legacy1/audio.ogg",
            audio_duration_seconds=60,
            updated_at="2025-06-01T00:00:00",
            audio_generated_at=None,  # legacy row without the new column
        )

        def execute(sql: str, params: list) -> list:
            if "user_preferences" in sql and "SELECT" in sql:
                return [{"tts_voice": "athena"}]
            if sql.startswith("SELECT") and "id = ?" in sql:
                return [article]
            return []

        db = TrackingD1(result_fn=execute)
        queue = MockQueue()
        r2 = MockR2()
        env = MockEnv(db=db, article_queue=queue, content=r2)

        client, _ = await _authenticated_client(env)
        resp = client.post("/api/articles/art_legacy1/listen-later")

        assert resp.status_code == 202
        data = resp.json()
        assert data["audio_status"] == "pending"
        assert len(queue.messages) == 1


class TestAudioGeneratedAtTimestamp:
    """Tests that audio_generated_at is set after successful TTS generation."""

    async def test_sets_audio_generated_at_on_success(self) -> None:
        """process_tts sets audio_generated_at in the final D1 UPDATE."""
        from src.tts.processing import process_tts

        article = ArticleFactory.create(
            id="art_ts1",
            user_id="user_001",
            audio_status="pending",
            markdown_content="Hello world. This is a test article.",
            status="ready",
        )

        executed_updates: list[tuple[str, list]] = []

        def execute(sql: str, params: list) -> list:
            if sql.startswith("UPDATE"):
                executed_updates.append((sql, params))
                return []
            if sql.startswith("SELECT") and "audio_status" in sql:
                return [{"audio_status": "pending"}]
            if sql.startswith("SELECT"):
                return [article]
            return []

        db = TrackingD1(result_fn=execute)
        r2 = MockR2()

        # Mock AI that returns minimal valid OGG data
        fake_audio = b"OggS" + b"\x00" * 100
        ai = MockAI(response=fake_audio)

        env = MockEnv(db=db, content=r2, ai=ai)

        await process_tts("art_ts1", env, user_id="user_001")

        # Find the final UPDATE that sets audio_status = 'ready'
        final_updates = [
            (sql, params)
            for sql, params in executed_updates
            if "audio_status" in sql and "audio_generated_at" in sql
        ]
        assert len(final_updates) >= 1, (
            f"Expected an UPDATE with audio_generated_at, got: {executed_updates}"
        )

        final_sql, final_params = final_updates[-1]
        assert "audio_generated_at = ?" in final_sql
        # The audio_generated_at param should be a non-empty ISO timestamp
        parsed = parse_update_params(final_sql, final_params)
        assert parsed.get("audio_generated_at") is not None
        assert "T" in str(parsed["audio_generated_at"])  # ISO format check
        assert parsed.get("audio_status") == "ready"

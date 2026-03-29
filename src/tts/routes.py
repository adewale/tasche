"""TTS (Listen Later) routes for Tasche.

Provides endpoints for requesting TTS generation and streaming the resulting
audio.  All endpoints require authentication via ``get_current_user``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import Response

from articles.routes import _enqueue_or_fail, _get_user_article
from auth.dependencies import get_current_user
from utils import now_iso

router = APIRouter()


@router.post("/{article_id}/listen-later", status_code=202)
async def listen_later(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Queue a TTS generation job for an article.

    Sets ``audio_status = 'pending'`` in D1, then
    enqueues a ``tts_generation`` message to ``ARTICLE_QUEUE``.

    Always allows re-generation — if audio already exists (any state),
    the old audio and timing files are deleted from R2 first.

    Returns 202 Accepted with the article ID and audio status.
    """
    env = request.scope["env"]
    db = env.DB
    r2 = env.CONTENT
    user_id = user["user_id"]

    # Verify article exists and belongs to user; fetch fields needed for
    # the idempotency check (audio_status, updated_at, audio_generated_at).
    article = await _get_user_article(
        db,
        article_id,
        user_id,
        fields="id, audio_status, audio_key, audio_duration_seconds,"
        " updated_at, audio_generated_at",
    )

    # Idempotency: if audio is already ready and content hasn't changed
    # since the last generation, return the existing audio info.
    audio_status = article.get("audio_status")
    if audio_status == "ready":
        updated_at = article.get("updated_at") or ""
        audio_generated_at = article.get("audio_generated_at") or ""
        if audio_generated_at and updated_at <= audio_generated_at:
            return {
                "id": article_id,
                "audio_status": "ready",
                "audio_key": article.get("audio_key"),
                "audio_duration_seconds": article.get("audio_duration_seconds"),
                "skipped": True,
            }

    # Delete any existing audio files (list-based, format-independent)
    from articles.storage import delete_audio_content

    await delete_audio_content(r2, article_id)

    now = now_iso()

    # Read user's voice preference
    pref = await (
        db.prepare("SELECT tts_voice FROM user_preferences WHERE user_id = ?").bind(user_id).first()
    )
    tts_voice = pref.get("tts_voice") if pref else "athena"

    # Update D1: reset audio state
    await (
        db.prepare(
            "UPDATE articles SET audio_status = 'pending', "
            "audio_key = NULL, audio_duration_seconds = NULL, "
            "updated_at = ? WHERE id = ? AND user_id = ?"
        )
        .bind(now, article_id, user_id)
        .run()
    )

    # Enqueue TTS generation job
    await _enqueue_or_fail(
        env,
        db,
        {
            "type": "tts_generation",
            "article_id": article_id,
            "user_id": user_id,
            "tts_voice": tts_voice,
        },
        article_id,
        status_field="audio_status",
        rollback_value=None,
    )

    return {"id": article_id, "audio_status": "pending"}


@router.get("/{article_id}/audio")
async def get_audio(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    """Stream the audio file for an article from R2.

    Auto-detects the audio format (WAV or MP3) from magic bytes.
    Returns 404 if no audio is available for the article.
    """
    env = request.scope["env"]
    db = env.DB
    r2 = env.CONTENT
    user_id = user["user_id"]

    # Fetch article to get audio_key and check audio_status
    article = await _get_user_article(db, article_id, user_id, fields="id, audio_status, audio_key")

    audio_status = article.get("audio_status")
    if audio_status != "ready":
        if audio_status in ("pending", "generating"):
            raise HTTPException(
                status_code=409,
                detail="Audio is still being generated",
            )
        raise HTTPException(
            status_code=404,
            detail="No audio available for this article",
        )

    audio_key = article.get("audio_key")
    if not audio_key:
        raise HTTPException(
            status_code=404,
            detail="No audio available for this article",
        )

    # Fetch audio from R2
    audio_obj = await r2.get(audio_key)
    if audio_obj is None:
        raise HTTPException(status_code=404, detail="Audio file not found")

    # Read entire audio body from R2.
    # IMPORTANT: Cannot use StreamingResponse with async generators — the
    # Cloudflare Workers ASGI adapter only consumes the FIRST yielded chunk
    # from the generator, silently truncating the response.  Instead, read
    # the full body via consume_readable_stream and return as a single Response.
    from wrappers import consume_readable_stream

    body = getattr(audio_obj, "body", audio_obj)
    audio_bytes = await consume_readable_stream(body)

    # Detect format from magic bytes and serve with correct MIME type.
    if audio_bytes[:4] == b"OggS":
        media_type = "audio/ogg"
    elif audio_bytes[:4] == b"RIFF":
        media_type = "audio/wav"
    else:
        media_type = "audio/mpeg"

    return Response(
        content=audio_bytes,
        media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
            "Content-Length": str(len(audio_bytes)),
        },
    )


@router.get("/{article_id}/audio-timing")
async def get_audio_timing(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    """Return the timing manifest for TTS sentence highlighting.

    Returns the JSON timing data from R2, or 404 if no timing
    data exists (legacy audio generated before immersive reading).
    """
    env = request.scope["env"]
    db = env.DB
    r2 = env.CONTENT
    user_id = user["user_id"]

    article = await _get_user_article(db, article_id, user_id, fields="id, audio_status")

    if article.get("audio_status") != "ready":
        raise HTTPException(status_code=404, detail="No audio timing available")

    from articles.storage import article_key

    timing_key = article_key(article_id, "audio-timing.json")
    timing_obj = await r2.get(timing_key)

    if timing_obj is None:
        raise HTTPException(status_code=404, detail="No audio timing available")

    from wrappers import consume_readable_stream

    body = getattr(timing_obj, "body", timing_obj)
    timing_bytes = await consume_readable_stream(body)

    return Response(
        content=timing_bytes,
        media_type="application/json",
        headers={
            "Cache-Control": "public, max-age=86400, immutable",
        },
    )

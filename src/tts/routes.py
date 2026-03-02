"""TTS (Listen Later) routes for Tasche.

Provides endpoints for requesting TTS generation and streaming the resulting
audio.  All endpoints require authentication via ``get_current_user``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from articles.routes import _enqueue_or_fail, _get_user_article
from articles.storage import article_key, get_content
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

    Returns 202 Accepted with the article ID and audio status.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    # Verify article exists and belongs to user
    article = await _get_user_article(db, article_id, user_id, fields="id, audio_status, audio_key")

    # Idempotency check: don't enqueue if already in progress or ready
    audio_status = article.get("audio_status")
    if audio_status == "pending":
        raise HTTPException(
            status_code=409,
            detail="Audio generation is already in progress",
        )
    # Allow re-queue from 'generating' — this state can become stuck
    # if the queue consumer fails without resetting the status.
    if audio_status == "ready":
        return JSONResponse(
            content={
                "id": article_id,
                "audio_status": "ready",
                "audio_key": article.get("audio_key"),
            },
            status_code=200,
        )

    # Only enqueue if audio_status is NULL or 'failed'
    now = now_iso()

    # Update D1: set audio_status
    await (
        db.prepare(
            "UPDATE articles SET audio_status = 'pending', "
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

    Returns the audio as ``audio/mpeg`` via a ``StreamingResponse``.
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

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
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
) -> JSONResponse:
    """Return the sentence timing map for an article's TTS audio.

    The timing JSON is generated during TTS processing and stored in R2
    at ``articles/{article_id}/audio-timing.json``.  Returns 404 if no
    timing data is available (e.g. audio was generated before timing
    support was added, or audio has not been generated at all).
    """
    import json as _json

    env = request.scope["env"]
    db = env.DB
    r2 = env.CONTENT
    user_id = user["user_id"]

    # Verify article exists and belongs to user
    await _get_user_article(db, article_id, user_id, fields="id")

    timing_key = article_key(article_id, "audio-timing.json")
    content = await get_content(r2, timing_key)
    if content is None:
        raise HTTPException(
            status_code=404,
            detail="No audio timing data available",
        )

    return JSONResponse(
        content=_json.loads(content),
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )

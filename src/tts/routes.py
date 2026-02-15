"""TTS (Listen Later) routes for Tasche.

Provides endpoints for requesting TTS generation and streaming the resulting
audio.  All endpoints require authentication via ``get_current_user``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from articles.routes import _get_user_article
from auth.dependencies import get_current_user
from wrappers import _to_js_value

router = APIRouter()


@router.post("/{article_id}/listen-later", status_code=202)
async def listen_later(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Queue a TTS generation job for an article.

    Sets ``listen_later = 1`` and ``audio_status = 'pending'`` in D1, then
    enqueues a ``tts_generation`` message to ``ARTICLE_QUEUE``.

    Returns 202 Accepted with the article ID and audio status.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    # Verify article exists and belongs to user
    await _get_user_article(db, article_id, user_id)

    now = datetime.now(UTC).isoformat()

    # Update D1: set listen_later and audio_status
    await (
        db.prepare(
            "UPDATE articles SET listen_later = 1, audio_status = 'pending', "
            "updated_at = ? WHERE id = ? AND user_id = ?"
        )
        .bind(now, article_id, user_id)
        .run()
    )

    # Enqueue TTS generation job
    message = _to_js_value({
        "type": "tts_generation",
        "article_id": article_id,
        "user_id": user_id,
    })
    await env.ARTICLE_QUEUE.send(message)

    return {"id": article_id, "audio_status": "pending"}


@router.get("/{article_id}/audio")
async def get_audio(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> StreamingResponse:
    """Stream the audio file for an article from R2.

    Returns the audio as ``audio/mpeg`` via a ``StreamingResponse``.
    Returns 404 if no audio is available for the article.
    """
    env = request.scope["env"]
    db = env.DB
    r2 = env.CONTENT
    user_id = user["user_id"]

    # Fetch article to get audio_key and check audio_status
    article = await _get_user_article(db, article_id, user_id)

    audio_status = article.get("audio_status")
    if audio_status != "ready":
        if audio_status in ("pending", "generating"):
            raise HTTPException(
                status_code=409,
                detail="Audio is still being generated",
            )
        raise HTTPException(
            status_code=404, detail="No audio available for this article",
        )

    audio_key = article.get("audio_key")
    if not audio_key:
        raise HTTPException(
            status_code=404, detail="No audio available for this article",
        )

    # Fetch audio from R2
    audio_obj = await r2.get(audio_key)
    if audio_obj is None:
        raise HTTPException(status_code=404, detail="Audio file not found")

    audio_bytes = await audio_obj.arrayBuffer()

    async def _stream():
        yield audio_bytes

    return StreamingResponse(
        _stream(),
        media_type="audio/mpeg",
        headers={"Content-Length": str(len(audio_bytes))},
    )

"""TTS (Listen Later) routes for Tasche.

Provides endpoints for requesting TTS generation and streaming the resulting
audio.  All endpoints require authentication via ``get_current_user``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from articles.routes import _get_user_article
from articles.storage import article_key, get_content
from auth.dependencies import get_current_user
from wrappers import get_r2_size

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
    now = datetime.now(UTC).isoformat()

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
    try:
        await env.ARTICLE_QUEUE.send(
            {
                "type": "tts_generation",
                "article_id": article_id,
                "user_id": user_id,
            }
        )
    except Exception:
        # Roll back D1 status on queue failure
        await (
            db.prepare(
                "UPDATE articles SET audio_status = NULL, updated_at = ? "
                "WHERE id = ? AND user_id = ?"
            )
            .bind(now, article_id, user_id)
            .run()
        )
        raise HTTPException(status_code=503, detail="Failed to enqueue TTS job")

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
    # the full body via body.getReader() and return as a single Response.
    from wrappers import to_py_bytes

    body = getattr(audio_obj, "body", None)
    if body is not None and hasattr(body, "getReader"):
        # Pyodide path: R2 object has a ReadableStream body
        reader = body.getReader()
        parts: list[bytes] = []
        try:
            while True:
                result = await reader.read()
                if bool(getattr(result, "done", True)):
                    break
                chunk = getattr(result, "value", None)
                if chunk is not None:
                    parts.append(to_py_bytes(chunk))
        finally:
            reader.releaseLock()
        audio_bytes = b"".join(parts)
    elif isinstance(body, (bytes, bytearray)):
        # Test/mock path: MockR2Object has .body as raw bytes
        audio_bytes = bytes(body)
    elif isinstance(audio_obj, (bytes, bytearray)):
        audio_bytes = bytes(audio_obj)
    else:
        audio_bytes = b""

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


@router.get("/{article_id}/tts-diagnostics")
async def get_tts_diagnostics(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> JSONResponse:
    """Return stored TTS diagnostics from R2 (written during TTS processing)."""
    import json as _json

    env = request.scope["env"]
    db = env.DB
    r2 = env.CONTENT
    user_id = user["user_id"]

    await _get_user_article(db, article_id, user_id, fields="id")

    diag_key = article_key(article_id, "tts-diagnostics.json")
    content = await get_content(r2, diag_key)
    if content is None:
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(
            status_code=404,
            detail="No TTS diagnostics available (article may predate diagnostic logging)",
        )
    # Also check actual audio file size for diagnostic comparison
    audio_key_str = article_key(article_id, "audio.mp3")
    audio_obj = await r2.get(audio_key_str)
    audio_size = get_r2_size(audio_obj) if audio_obj is not None else None

    diag_data = _json.loads(content)
    diag_data["_live_audio_r2_size"] = audio_size

    return JSONResponse(content=diag_data)


@router.post("/{article_id}/tts-probe")
async def tts_probe(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Single-call TTS diagnostic probe.

    Makes ONE Workers AI TTS call with short text and reports detailed
    type information about the response and byte conversion at each stage.
    Fast enough to complete within the request timeout.
    """
    import traceback

    from wrappers import consume_readable_stream, to_py_bytes

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    await _get_user_article(db, article_id, user_id, fields="id")

    try:
        ai = env.AI
        test_text = "Hello world. This is a diagnostic probe for text to speech audio generation."

        # Call Workers AI directly (through SafeAI wrapper)
        raw_result = await ai.run("@cf/deepgram/aura-2-en", {"text": test_text})

        diag: dict[str, Any] = {
            "text_len": len(test_text),
            "ai_result_type": type(raw_result).__name__,
            "ai_result_repr": repr(raw_result)[:300],
        }

        # Probe available attributes
        probe_attrs = [
            "getReader", "arrayBuffer", "body", "to_py", "to_bytes",
            "byteLength", "buffer", "locked", "tee", "pipeTo",
            "text", "json", "blob", "ok", "status", "headers",
        ]
        diag["attrs"] = [a for a in probe_attrs if hasattr(raw_result, a)]

        # Try getReader path
        if hasattr(raw_result, "getReader"):
            try:
                reader = raw_result.getReader()
                diag["reader_type"] = type(reader).__name__

                chunks_info = []
                reader_parts = []
                chunk_idx = 0
                while True:
                    read_result = await reader.read()
                    done = getattr(read_result, "done", True)
                    value = getattr(read_result, "value", None)
                    chunk_info = {
                        "index": chunk_idx,
                        "done": bool(done),
                        "value_type": type(value).__name__ if value is not None else "None",
                    }
                    if value is not None:
                        if hasattr(value, "byteLength"):
                            chunk_info["js_byteLength"] = int(value.byteLength)
                        py_bytes = to_py_bytes(value)
                        chunk_info["py_bytes_len"] = len(py_bytes)
                        reader_parts.append(py_bytes)
                    chunks_info.append(chunk_info)
                    chunk_idx += 1
                    if done:
                        break

                reader.releaseLock()
                diag["reader_chunks"] = chunks_info
                diag["reader_total_bytes"] = sum(len(p) for p in reader_parts)
            except Exception as exc:
                diag["reader_error"] = f"{type(exc).__name__}: {exc}"

        # Test SEQUENTIAL AI calls (simulating multi-chunk TTS)
        sequential_results = []
        test_sentences = [
            "Hello world. This is the first chunk of text for speech.",
            "Here is the second chunk with different words entirely.",
            "And a third chunk to check for rate limiting or degradation.",
        ]
        for idx, sentence in enumerate(test_sentences):
            raw = await ai.run("@cf/deepgram/aura-2-en", {"text": sentence})
            consumed_chunk = await consume_readable_stream(raw)
            sequential_results.append({
                "index": idx,
                "text_len": len(sentence),
                "audio_bytes": len(consumed_chunk),
            })
        diag["sequential_calls"] = sequential_results
        diag["sequential_total"] = sum(r["audio_bytes"] for r in sequential_results)

        # Use the last call's consumed bytes for R2 round-trip
        consumed = consumed_chunk

        # R2 round-trip test: write consumed bytes, read back, compare
        r2 = env.CONTENT
        probe_key = article_key(article_id, "probe-audio.bin")
        try:
            await r2.put(probe_key, consumed)
            diag["r2_write_input_len"] = len(consumed)
            diag["r2_write_input_type"] = type(consumed).__name__

            # Read back
            r2_obj = await r2.get(probe_key)
            if r2_obj is not None:
                diag["r2_obj_size"] = get_r2_size(r2_obj)

                readback = await consume_readable_stream(r2_obj)
                diag["r2_readback_len"] = len(readback)
                diag["r2_roundtrip_match"] = len(readback) == len(consumed)
            else:
                diag["r2_read_result"] = "None (key not found)"

            # Clean up probe file
            await r2.delete(probe_key)
        except Exception as exc:
            diag["r2_roundtrip_error"] = f"{type(exc).__name__}: {exc}"

        return {"result": "ok", "diagnostics": diag}

    except Exception as exc:
        return {
            "result": "error",
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }


@router.post("/{article_id}/tts-now")
async def tts_now(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Process TTS inline (bypasses queue) for debugging.

    Runs the full TTS pipeline in the request handler so errors
    are returned directly instead of being lost in queue handler logs.
    """
    import traceback

    from tts.processing import process_tts

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    await _get_user_article(db, article_id, user_id, fields="id")

    try:
        diag = await process_tts(
            article_id, env, user_id=user_id, raise_on_error=True
        )
        article = await _get_user_article(
            db, article_id, user_id,
            fields="id, audio_status, audio_key, audio_duration_seconds",
        )
        actual_status = article.get("audio_status", "unknown")
        result = "success" if actual_status == "ready" else "error"
        resp: dict[str, Any] = {
            "id": article_id, "result": result, "article": article,
        }
        if diag:
            resp["diagnostics"] = diag
        return resp
    except Exception as exc:
        return {
            "id": article_id,
            "result": "error",
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }

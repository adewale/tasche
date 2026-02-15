"""TTS generation queue consumer for Tasche.

Processes ``tts_generation`` queue messages by fetching markdown content,
calling Workers AI for text-to-speech conversion, and storing the resulting
audio in R2.

Steps:
 1. Update ``audio_status`` to ``'generating'`` in D1
 2. Fetch markdown content from R2 (using the article's ``markdown_key``)
 3. If no markdown in R2, fall back to D1 ``markdown_content`` field
 4. Call Workers AI ``@cf/deepgram/aura-2-en`` with the text
 5. Store the audio result to R2 at ``articles/{article_id}/audio.mp3``
 6. Update D1: ``audio_key``, ``audio_duration_seconds``, ``audio_status = 'ready'``
 7. On failure: set ``audio_status = 'failed'``
"""

from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime

from articles.storage import article_key, get_content
from wrappers import d1_first

# TTS model identifier
_TTS_MODEL = "@cf/deepgram/aura-2-en"

# Approximate speech rate: ~150 words per minute for TTS
_WORDS_PER_MINUTE = 150


def _estimate_duration(text: str) -> int:
    """Estimate audio duration in seconds from text length.

    Uses a rough approximation of ~150 words per minute for TTS output.
    Returns at least 1 second.
    """
    word_count = len(text.split())
    seconds = max(1, int((word_count / _WORDS_PER_MINUTE) * 60))
    return seconds


def _now() -> str:
    """Return the current UTC timestamp as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


async def process_tts(article_id: str, env: object) -> None:
    """Process a TTS generation job for a single article.

    This is the main entry point called by the queue handler in ``entry.py``.
    On success, ``audio_status`` is set to ``'ready'``.  On any failure,
    ``audio_status`` is set to ``'failed'``.

    Parameters
    ----------
    article_id:
        The D1 article row ID.
    env:
        Worker environment object with ``DB`` (D1), ``CONTENT`` (R2),
        and ``AI`` (Workers AI).
    """
    db = env.DB  # type: ignore[attr-defined]
    r2 = env.CONTENT  # type: ignore[attr-defined]
    ai = env.AI  # type: ignore[attr-defined]

    try:
        # Step 1: Update audio_status to 'generating'
        await db.prepare(
            "UPDATE articles SET audio_status = ?, updated_at = ? WHERE id = ?"
        ).bind("generating", _now(), article_id).run()

        # Step 2: Fetch markdown content from R2
        article = d1_first(
            await db.prepare(
                "SELECT markdown_key, markdown_content FROM articles WHERE id = ?"
            ).bind(article_id).first()
        )

        markdown_text = None

        if article and article.get("markdown_key"):
            markdown_text = await get_content(r2, article["markdown_key"])

        # Step 3: Fall back to D1 markdown_content if R2 didn't have it
        if not markdown_text and article:
            markdown_text = article.get("markdown_content")

        if not markdown_text:
            raise ValueError(f"No markdown content found for article {article_id}")

        # Step 4: Call Workers AI for TTS
        audio_data = await ai.run(_TTS_MODEL, text=markdown_text)

        # Step 5: Store audio in R2
        audio_r2_key = article_key(article_id, "audio.mp3")
        await r2.put(audio_r2_key, audio_data)

        # Step 6: Update D1 with audio metadata
        duration = _estimate_duration(markdown_text)

        await db.prepare(
            "UPDATE articles SET audio_key = ?, audio_duration_seconds = ?, "
            "audio_status = ?, updated_at = ? WHERE id = ?"
        ).bind(audio_r2_key, duration, "ready", _now(), article_id).run()

        print(
            json.dumps({
                "event": "tts_processed",
                "article_id": article_id,
                "status": "ready",
                "audio_key": audio_r2_key,
                "audio_duration_seconds": duration,
            })
        )

    except Exception:
        # Step 7: On failure, set audio_status to 'failed'
        print(
            json.dumps({
                "event": "tts_processing_failed",
                "article_id": article_id,
                "error": traceback.format_exc(),
            })
        )
        try:
            await db.prepare(
                "UPDATE articles SET audio_status = ?, updated_at = ? WHERE id = ?"
            ).bind("failed", _now(), article_id).run()
        except Exception:
            print(
                json.dumps({
                    "event": "tts_status_update_failed",
                    "article_id": article_id,
                    "error": traceback.format_exc(),
                })
            )

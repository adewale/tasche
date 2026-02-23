"""TTS generation queue consumer for Tasche.

Processes ``tts_generation`` queue messages by fetching markdown content,
calling Workers AI for text-to-speech conversion, and storing the resulting
audio in R2.

Steps:
 1. Update ``audio_status`` to ``'generating'`` in D1
 2. Fetch ``markdown_content`` from D1
 3. Call Workers AI ``@cf/deepgram/aura-2-en`` with the text
 4. Store the audio result to R2 at ``articles/{article_id}/audio.mp3``
 5. Update D1: ``audio_key``, ``audio_duration_seconds``, ``audio_status = 'ready'``
 6. On failure: set ``audio_status = 'failed'``
"""

from __future__ import annotations

import json
import re
import traceback
from datetime import UTC, datetime

from articles.storage import article_key
from wrappers import consume_readable_stream

# TTS model identifier
_TTS_MODEL = "@cf/deepgram/aura-2-en"

# Approximate speech rate: ~150 words per minute for TTS
_WORDS_PER_MINUTE = 150

# Maximum text length to send to TTS (characters)
_MAX_TTS_TEXT_LENGTH = 100_000


def _estimate_duration(text: str) -> int:
    """Estimate audio duration in seconds from text length.

    Uses a rough approximation of ~150 words per minute for TTS output.
    Returns at least 1 second.
    """
    word_count = len(text.split())
    seconds = max(1, int((word_count / _WORDS_PER_MINUTE) * 60))
    return seconds


# Sentence boundary regex: split on .!? followed by whitespace or end of string.
# Handles common abbreviations by requiring a capital letter or end of string after
# the boundary, and avoids splitting on decimal numbers.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    """Split text into sentences using punctuation boundaries.

    Uses a simple regex that splits on sentence-ending punctuation (.!?)
    followed by whitespace. Filters out empty strings.

    Parameters
    ----------
    text:
        The plain text to split (should already have markdown stripped).

    Returns
    -------
    list[str]
        A list of sentence strings. Returns an empty list for empty input.
    """
    if not text or not text.strip():
        return []
    parts = _SENTENCE_SPLIT_RE.split(text.strip())
    return [s.strip() for s in parts if s.strip()]


def generate_sentence_timing(
    text: str,
    words_per_minute: int = _WORDS_PER_MINUTE,
) -> dict:
    """Generate a sentence timing map from plain text.

    Splits the text into sentences, then computes estimated start and end
    times for each sentence based on cumulative word count and the given
    speaking rate.

    Parameters
    ----------
    text:
        Plain text (markdown already stripped).
    words_per_minute:
        Estimated TTS speaking rate. Defaults to 150 WPM.

    Returns
    -------
    dict
        A timing map with ``sentences`` (list of dicts with ``text``,
        ``start``, ``end``, ``word_count``), ``words_per_minute``, and
        ``total_duration_seconds``.
    """
    sentences = split_sentences(text)
    if not sentences:
        return {
            "sentences": [],
            "words_per_minute": words_per_minute,
            "total_duration_seconds": 0,
        }

    words_per_second = words_per_minute / 60.0
    timing_entries = []
    cumulative_time = 0.0

    for sentence in sentences:
        wc = len(sentence.split())
        duration = wc / words_per_second if words_per_second > 0 else 0
        timing_entries.append({
            "text": sentence,
            "start": round(cumulative_time, 2),
            "end": round(cumulative_time + duration, 2),
            "word_count": wc,
        })
        cumulative_time += duration

    return {
        "sentences": timing_entries,
        "words_per_minute": words_per_minute,
        "total_duration_seconds": round(cumulative_time, 2),
    }


def strip_markdown(text: str) -> str:
    """Remove markdown syntax from text for cleaner TTS output.

    Strips: headings (#), bold/italic (**, *, __, _), links ([text](url) -> text),
    images (![alt](url) -> removed), code blocks (``` and inline `code`),
    horizontal rules (---), blockquotes (>), HTML tags, and list markers.
    """
    if not text:
        return text

    lines = text.split("\n")
    result_lines: list[str] = []
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        # Handle code blocks
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        # Skip horizontal rules
        if stripped in ("---", "***", "___"):
            continue

        # Remove heading markers
        if stripped.startswith("#"):
            # Count the heading level and strip the markers
            heading_text = stripped.lstrip("#").strip()
            result_lines.append(heading_text)
            continue

        # Remove blockquote markers
        if stripped.startswith(">"):
            stripped = stripped.lstrip(">").strip()

        # Remove list markers (-, *, 1.)
        if len(stripped) >= 2:
            if stripped[0] in ("-", "*") and stripped[1] == " ":
                stripped = stripped[2:]
            elif len(stripped) >= 3 and stripped[0].isdigit():
                dot_pos = stripped.find(". ")
                if dot_pos > 0 and dot_pos <= 3 and stripped[:dot_pos].isdigit():
                    stripped = stripped[dot_pos + 2 :]

        result_lines.append(stripped)

    text = "\n".join(result_lines)

    # Remove images entirely: ![alt](url)
    i = 0
    out_chars: list[str] = []
    while i < len(text):
        if text[i : i + 2] == "![":
            # Find closing ]
            close_bracket = text.find("]", i + 2)
            has_paren = (
                close_bracket != -1
                and close_bracket + 1 < len(text)
                and text[close_bracket + 1] == "("
            )
            if has_paren:
                close_paren = text.find(")", close_bracket + 2)
                if close_paren != -1:
                    i = close_paren + 1
                    continue
        out_chars.append(text[i])
        i += 1
    text = "".join(out_chars)

    # Replace links [text](url) with just text
    i = 0
    out_chars = []
    while i < len(text):
        if text[i] == "[":
            close_bracket = text.find("]", i + 1)
            has_paren = (
                close_bracket != -1
                and close_bracket + 1 < len(text)
                and text[close_bracket + 1] == "("
            )
            if has_paren:
                close_paren = text.find(")", close_bracket + 2)
                if close_paren != -1:
                    link_text = text[i + 1 : close_bracket]
                    out_chars.append(link_text)
                    i = close_paren + 1
                    continue
        out_chars.append(text[i])
        i += 1
    text = "".join(out_chars)

    # Remove inline code
    i = 0
    out_chars = []
    while i < len(text):
        if text[i] == "`":
            end = text.find("`", i + 1)
            if end != -1:
                out_chars.append(text[i + 1 : end])
                i = end + 1
                continue
        out_chars.append(text[i])
        i += 1
    text = "".join(out_chars)

    # Remove bold/italic markers: ***text***, **text**, *text*, ___text___, __text__, _text_
    # Uses regex to only remove formatting markers around words, preserving
    # standalone * and _ in normal text (e.g., "2 * 3", "my_variable").
    text = re.sub(r"(\*{1,3}|_{1,3})(?=\S)(.+?)(?<=\S)\1", r"\2", text)

    # Remove HTML tags
    i = 0
    out_chars = []
    while i < len(text):
        if text[i] == "<":
            end = text.find(">", i + 1)
            if end != -1:
                i = end + 1
                continue
        out_chars.append(text[i])
        i += 1
    text = "".join(out_chars)

    # Clean up excess whitespace
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    text = text.strip()

    return text


def _now() -> str:
    """Return the current UTC timestamp as an ISO 8601 string."""
    return datetime.now(UTC).isoformat()


async def process_tts(article_id: str, env: object, *, user_id: str) -> None:
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
    user_id:
        The owner's user ID.  The query always verifies ownership.
    """
    db = env.DB  # type: ignore[attr-defined]
    r2 = env.CONTENT  # type: ignore[attr-defined]
    ai = env.AI  # type: ignore[attr-defined]

    try:
        # Idempotency check: skip if audio is already ready
        existing = await (
            db.prepare(
                "SELECT audio_status FROM articles WHERE id = ? AND user_id = ?"
            ).bind(article_id, user_id).first()
        )
        if existing and existing.get("audio_status") == "ready":
            print(json.dumps({
                "event": "tts_skipped",
                "article_id": article_id,
                "reason": "audio_already_ready",
            }))
            return

        # Step 1: Update audio_status to 'generating'
        await db.prepare(
            "UPDATE articles SET audio_status = ?, updated_at = ? WHERE id = ? AND user_id = ?"
        ).bind("generating", _now(), article_id, user_id).run()

        # Step 2: Fetch markdown content from D1
        article = await (
            db.prepare(
                "SELECT markdown_content FROM articles"
                " WHERE id = ? AND user_id = ?"
            ).bind(article_id, user_id).first()
        )

        markdown_text = article.get("markdown_content") if article else None

        if not markdown_text:
            raise ValueError(f"No markdown content found for article {article_id}")

        # Strip markdown syntax for cleaner speech output
        tts_text = strip_markdown(markdown_text)

        # Truncate to maximum length if needed
        if len(tts_text) > _MAX_TTS_TEXT_LENGTH:
            tts_text = tts_text[:_MAX_TTS_TEXT_LENGTH] + "\n\n... Content has been truncated."
            print(
                json.dumps({
                    "event": "tts_text_truncated",
                    "article_id": article_id,
                    "original_length": len(markdown_text),
                    "truncated_to": _MAX_TTS_TEXT_LENGTH,
                })
            )

        # Step 4: Call Workers AI for TTS
        audio_data = await ai.run(_TTS_MODEL, {"text": tts_text})

        # Consume ReadableStream if the AI binding returns one
        audio_data = await consume_readable_stream(audio_data)
        if not audio_data:
            raise ValueError("Workers AI returned empty audio data")

        # Step 5: Store audio in R2
        audio_r2_key = article_key(article_id, "audio.mp3")
        await r2.put(audio_r2_key, audio_data)

        # Step 5b: Generate and store sentence timing map for highlight sync
        timing_data = generate_sentence_timing(tts_text)
        timing_r2_key = article_key(article_id, "audio-timing.json")
        await r2.put(timing_r2_key, json.dumps(timing_data))

        # Step 6: Update D1 with audio metadata
        duration = _estimate_duration(tts_text)

        await db.prepare(
            "UPDATE articles SET audio_key = ?, audio_duration_seconds = ?, "
            "audio_status = ?, updated_at = ? WHERE id = ? AND user_id = ?"
        ).bind(audio_r2_key, duration, "ready", _now(), article_id, user_id).run()

        print(
            json.dumps({
                "event": "tts_processed",
                "article_id": article_id,
                "status": "ready",
                "audio_key": audio_r2_key,
                "audio_duration_seconds": duration,
            })
        )

    except ValueError:
        # Permanent errors (missing content, invalid data) — mark as failed
        print(
            json.dumps({
                "event": "tts_processing_failed",
                "article_id": article_id,
                "error": traceback.format_exc(),
            })
        )
        try:
            await db.prepare(
                "UPDATE articles SET audio_status = ?, updated_at = ?"
                " WHERE id = ? AND user_id = ?"
            ).bind("failed", _now(), article_id, user_id).run()
        except Exception:
            print(
                json.dumps({
                    "event": "tts_status_update_failed",
                    "article_id": article_id,
                    "error": traceback.format_exc(),
                })
            )
    except Exception:
        # All other errors (network, JS, AI model) — let propagate for queue retry
        print(
            json.dumps({
                "event": "tts_processing_failed",
                "article_id": article_id,
                "error": traceback.format_exc(),
                "retryable": True,
            })
        )
        raise

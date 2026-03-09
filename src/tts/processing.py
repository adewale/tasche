"""TTS generation queue consumer for Tasche.

Processes ``tts_generation`` queue messages by fetching markdown content,
calling Workers AI for text-to-speech conversion, and storing the resulting
audio in R2.

The TTS model is configurable via the ``TTS_MODEL`` env var.  Supported
values: ``aura-2-en`` (default), ``melotts``, ``aura-2-es``, ``aura-1``.
A raw Workers AI model ID is also accepted.

Steps:
 1. Update ``audio_status`` to ``'generating'`` in D1
 2. Fetch ``markdown_content`` from D1
 3. Call Workers AI with the configured TTS model
 4. Store the audio result to R2 at ``articles/{article_id}/audio.mp3``
 5. Update D1: ``audio_key``, ``audio_duration_seconds``, ``audio_status = 'ready'``
 6. On failure: set ``audio_status = 'failed'``
"""

from __future__ import annotations

import base64
import json
import re
import struct
import traceback

from articles.storage import article_key
from utils import now_iso
from wide_event import current_event
from wrappers import consume_readable_stream


class _RetryableError(RuntimeError):
    """Raised when TTS should be retried without marking audio_status as failed.

    Used for transient conditions like the article still being processed —
    the content will be available on the next retry attempt.
    """


# Supported TTS models: short key → Workers AI model ID
_TTS_MODELS = {
    "melotts": "@cf/myshell-ai/melotts",
    "aura-2-en": "@cf/deepgram/aura-2-en",
    "aura-2-es": "@cf/deepgram/aura-2-es",
    "aura-1": "@cf/deepgram/aura-1",
}
_DEFAULT_TTS_MODEL = "aura-2-en"

# Deepgram aura-2 encoding parameters
_DEEPGRAM_ENCODING = "opus"
_DEEPGRAM_CONTAINER = "ogg"
_DEEPGRAM_BIT_RATE = 24000  # 24 kbps — excellent for speech
_DEEPGRAM_SAMPLE_RATE = 24000  # 24 kHz
_DEEPGRAM_DEFAULT_VOICE = "athena"
_COST_PER_1000_CHARS = 0.03


def _resolve_tts_model(env: object) -> tuple[str, str]:
    """Return ``(model_id, model_key)`` from the ``TTS_MODEL`` env var.

    Falls back to :data:`_DEFAULT_TTS_MODEL` when the var is unset.
    If the value isn't a known key, it's treated as a raw model ID.
    """
    key = getattr(env, "TTS_MODEL", None) or _DEFAULT_TTS_MODEL
    key = key.strip().lower()
    model_id = _TTS_MODELS.get(key, key)  # allow raw model ID too
    return model_id, key


# Approximate speech rate: ~150 words per minute for TTS
_WORDS_PER_MINUTE = 150

# Maximum text length to send to TTS (characters).
# Workers AI @cf/deepgram/aura-2-en allows 2000 chars per call.
_MAX_CHARS_PER_CHUNK = 1900  # Leave headroom below the 2000 limit
_MAX_TTS_TEXT_LENGTH = 100_000


def _estimate_duration(text: str) -> int:
    """Estimate audio duration in seconds from text length.

    Uses a rough approximation of ~150 words per minute for TTS output.
    Returns at least 1 second.
    """
    word_count = len(text.split())
    seconds = max(1, int((word_count / _WORDS_PER_MINUTE) * 60))
    return seconds


def _ogg_duration_seconds(data: bytes) -> float:
    """Extract duration from OGG Opus audio by reading page headers.

    Scans for OGG page sync patterns ('OggS') and reads
    granule_position from each page header. The final page's
    granule position gives total PCM samples at 48kHz.
    Pre-skip is read from the first page's Opus ID header.

    Returns 0.0 if the data is not valid OGG Opus.
    """
    if len(data) < 28:
        return 0.0

    pre_skip = 0
    last_granule = 0
    i = 0

    while i <= len(data) - 27:
        # Look for OGG page sync pattern
        if data[i : i + 4] != b"OggS":
            i += 1
            continue

        # Read granule_position: bytes 6-13, little-endian int64
        granule = int.from_bytes(data[i + 6 : i + 14], "little", signed=True)
        if granule >= 0:
            last_granule = granule

        # Number of page segments at byte 26
        if i + 27 > len(data):
            break
        num_segments = data[i + 26]
        header_size = 27 + num_segments

        # Read pre_skip from first Opus ID header
        if pre_skip == 0 and i + header_size < len(data):
            # Compute payload start
            payload_start = i + header_size
            # Check for 'OpusHead' magic in payload
            if (
                payload_start + 12 <= len(data)
                and data[payload_start : payload_start + 8] == b"OpusHead"
            ):
                pre_skip = int.from_bytes(data[payload_start + 10 : payload_start + 12], "little")

        # Advance past this page header + segment table
        i += header_size

    if last_granule <= pre_skip:
        return 0.0
    return (last_granule - pre_skip) / 48000.0


def _parse_ogg_pages(data: bytes) -> list[dict]:
    """Parse an OGG byte stream into a list of page dicts.

    Each page dict has: header_type, granule, serial, page_seq,
    num_segments, segment_table, payload.
    """
    pages: list[dict] = []
    i = 0

    while i <= len(data) - 27:
        if data[i : i + 4] != b"OggS":
            i += 1
            continue

        header_type = data[i + 5]
        granule = struct.unpack_from("<q", data, i + 6)[0]
        serial = struct.unpack_from("<I", data, i + 14)[0]
        page_seq = struct.unpack_from("<I", data, i + 18)[0]
        num_segments = data[i + 26]
        seg_table = data[i + 27 : i + 27 + num_segments]
        payload_size = sum(seg_table)
        payload_start = i + 27 + num_segments
        payload = data[payload_start : payload_start + payload_size]

        pages.append(
            {
                "header_type": header_type,
                "granule": granule,
                "serial": serial,
                "page_seq": page_seq,
                "num_segments": num_segments,
                "segment_table": bytes(seg_table),
                "payload": payload,
            }
        )

        i = payload_start + payload_size

    return pages


def _write_ogg_page(
    header_type: int,
    granule: int,
    serial: int,
    page_seq: int,
    segment_table: bytes,
    payload: bytes,
) -> bytes:
    """Write a single OGG page with a correct CRC-32 checksum."""
    # Build page without checksum (set to 0 for CRC calculation)
    header = b"OggS"
    header += struct.pack("<B", 0)  # version
    header += struct.pack("<B", header_type)
    header += struct.pack("<q", granule)
    header += struct.pack("<I", serial)
    header += struct.pack("<I", page_seq)
    header += struct.pack("<I", 0)  # checksum placeholder
    header += struct.pack("<B", len(segment_table))
    page = header + segment_table + payload

    # Calculate OGG CRC-32 and patch it in
    crc = _ogg_crc32(page)
    page = page[:22] + struct.pack("<I", crc) + page[26:]

    return page


# OGG CRC-32 lookup table (polynomial 0x04C11DB7, no bit reversal)
_OGG_CRC_TABLE: list[int] | None = None


def _ogg_crc32(data: bytes) -> int:
    """Compute the OGG-specific CRC-32 checksum.

    OGG uses a direct (non-reflected) CRC-32 with polynomial 0x04C11DB7,
    which differs from the standard zlib/gzip CRC-32.
    """
    global _OGG_CRC_TABLE
    if _OGG_CRC_TABLE is None:
        table = []
        for i in range(256):
            r = i << 24
            for _ in range(8):
                if r & 0x80000000:
                    r = ((r << 1) ^ 0x04C11DB7) & 0xFFFFFFFF
                else:
                    r = (r << 1) & 0xFFFFFFFF
            table.append(r)
        _OGG_CRC_TABLE = table

    crc = 0
    for byte in data:
        crc = (_OGG_CRC_TABLE[(crc >> 24) ^ byte] ^ (crc << 8)) & 0xFFFFFFFF
    return crc


def _remux_ogg_opus(audio_parts: list[bytes]) -> bytes:
    """Re-mux multiple complete OGG Opus files into a single logical stream.

    Each element of *audio_parts* is a self-contained OGG Opus file with
    its own BOS/EOS pages, headers, and granule positions.  Browsers only
    play the first logical stream (stopping at the first EOS page), so
    naive ``b"".join()`` causes audio truncation.

    This function:
    1. Keeps the ID header (OpusHead) and comment header (OpusTags) from
       the first chunk.
    2. Extracts audio data pages from every chunk, adjusting granule
       positions to be continuous across the single output stream.
    3. Writes all pages with a single serial number, sequential page
       sequence numbers, and correct CRC-32 checksums.

    For a single chunk, returns it unchanged (no re-mux needed).
    """
    if len(audio_parts) <= 1:
        return audio_parts[0] if audio_parts else b""

    serial = 1  # Arbitrary serial number for the output stream
    page_seq = 0
    output_pages: list[bytes] = []

    # Track cumulative granule offset so positions are continuous
    granule_offset = 0
    first_pre_skip = 0

    for chunk_idx, chunk_data in enumerate(audio_parts):
        pages = _parse_ogg_pages(chunk_data)
        if not pages:
            continue

        # Find this chunk's pre_skip from OpusHead
        chunk_pre_skip = 0
        for page in pages:
            if page["payload"][:8] == b"OpusHead" and len(page["payload"]) >= 12:
                chunk_pre_skip = struct.unpack_from("<H", page["payload"], 10)[0]
                break

        # Find the last granule in this chunk (from the EOS or last data page)
        chunk_last_granule = 0
        for page in reversed(pages):
            if page["granule"] > 0:
                chunk_last_granule = page["granule"]
                break

        for page in pages:
            is_header = page["payload"][:8] in (b"OpusHead", b"OpusTags")

            if chunk_idx == 0:
                # First chunk: keep headers as-is, keep data pages as-is
                if is_header:
                    # Preserve BOS flag only on the very first page
                    ht = page["header_type"]
                    output_pages.append(
                        _write_ogg_page(
                            ht & ~0x04,  # Clear EOS if somehow set on header
                            page["granule"],
                            serial,
                            page_seq,
                            page["segment_table"],
                            page["payload"],
                        )
                    )
                    page_seq += 1
                    if page["payload"][:8] == b"OpusHead":
                        first_pre_skip = chunk_pre_skip
                else:
                    # Data page from first chunk
                    ht = page["header_type"] & ~0x04  # Clear EOS
                    output_pages.append(
                        _write_ogg_page(
                            ht,
                            page["granule"],
                            serial,
                            page_seq,
                            page["segment_table"],
                            page["payload"],
                        )
                    )
                    page_seq += 1
            else:
                # Subsequent chunks: skip headers, re-map data page granules
                if is_header:
                    continue

                # Adjust granule: shift by the cumulative offset
                new_granule = page["granule"]
                if new_granule > 0:
                    # Remove this chunk's pre_skip contribution and add offset
                    new_granule = page["granule"] - chunk_pre_skip + granule_offset + first_pre_skip

                ht = page["header_type"] & ~0x06  # Clear BOS and EOS
                output_pages.append(
                    _write_ogg_page(
                        ht,
                        new_granule,
                        serial,
                        page_seq,
                        page["segment_table"],
                        page["payload"],
                    )
                )
                page_seq += 1

        # Update granule offset for the next chunk
        if chunk_last_granule > chunk_pre_skip:
            granule_offset += chunk_last_granule - chunk_pre_skip

    # Set EOS flag on the very last page
    if output_pages:
        last_page = output_pages[-1]
        # Parse the header_type byte (byte 5) and set EOS flag
        patched = last_page[:5] + bytes([last_page[5] | 0x04]) + last_page[6:]
        # Recompute CRC
        patched = patched[:22] + struct.pack("<I", 0) + patched[26:]
        crc = _ogg_crc32(patched)
        patched = patched[:22] + struct.pack("<I", crc) + patched[26:]
        output_pages[-1] = patched

    return b"".join(output_pages)


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


def chunk_text(text: str, max_chars: int = _MAX_CHARS_PER_CHUNK) -> list[str]:
    """Split text into chunks that fit within the TTS character limit.

    Splits on sentence boundaries so speech sounds natural. If a single
    sentence exceeds the limit, it is included as its own chunk (the TTS
    model will truncate or error, but we avoid infinite loops).
    """
    sentences = split_sentences(text)
    if not sentences:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        # +1 for the space between sentences
        added = len(sentence) + (1 if current else 0)
        if current and current_len + added > max_chars:
            chunks.append(" ".join(current))
            current = [sentence]
            current_len = len(sentence)
        else:
            current.append(sentence)
            current_len += added

    if current:
        chunks.append(" ".join(current))

    return chunks


def chunk_text_with_sentences(text: str, max_chars: int = _MAX_CHARS_PER_CHUNK) -> list[dict]:
    """Split text into chunks, preserving per-sentence boundaries.

    Like :func:`chunk_text` but returns richer metadata. Each entry
    has ``text`` (the full chunk) and ``sentences`` (list of individual
    sentences within the chunk).
    """
    sentences = split_sentences(text)
    if not sentences:
        return []

    chunks: list[dict] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        added = len(sentence) + (1 if current else 0)
        if current and current_len + added > max_chars:
            chunks.append(
                {
                    "text": " ".join(current),
                    "sentences": list(current),
                }
            )
            current = [sentence]
            current_len = len(sentence)
        else:
            current.append(sentence)
            current_len += added

    if current:
        chunks.append(
            {
                "text": " ".join(current),
                "sentences": list(current),
            }
        )

    return chunks


# Approximate pause durations in character-equivalents.
# A comma inserts a ~150ms breath; at ~15 chars/sec of speech that's ~2-3 chars.
_PAUSE_WEIGHTS = {
    ",": 3,  # ~150ms pause ≈ 3 chars of speech
    ";": 4,  # ~200ms
    ":": 4,
    "\u2014": 5,  # em-dash, ~250ms
    "\u2013": 4,  # en-dash
    "...": 6,  # ellipsis, ~300ms
    "\u2026": 6,  # unicode ellipsis
    "!": 2,  # sentence-final emphasis pause
    "?": 2,
    ".": 2,  # sentence-final pause
}

# Vowel clusters for syllable estimation.  English syllables are roughly
# "one vowel sound per syllable".  Consecutive vowels (diphthongs) count
# as one syllable.
_VOWELS = set("aeiouyAEIOUY")


def _estimate_syllables(word: str) -> int:
    """Estimate syllable count for an English word.

    Uses a simple vowel-cluster heuristic:
    1. Count groups of consecutive vowels as one syllable.
    2. If the word ends with a silent 'e' (and has >1 syllable), subtract one.
    3. Always return at least 1.

    This isn't perfect (e.g. "area" → 2 instead of 3) but it's good enough
    for proportional weighting — we only need relative accuracy, not absolute.
    """
    if not word:
        return 1

    # Strip non-alpha for counting
    w = "".join(ch for ch in word if ch.isalpha())
    if not w:
        return 1

    count = 0
    prev_vowel = False
    for ch in w:
        is_vowel = ch in _VOWELS
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel

    # Silent 'e' at end
    if w.lower().endswith("e") and count > 1:
        count -= 1

    return max(1, count)


def _speech_weight(sentence: str) -> float:
    """Estimate relative speech duration for a sentence.

    Combines three signals:
    1. **Syllable count** — "extraordinary" (5 syl) takes longer than "cat" (1).
       Syllables are weighted at 3.0 each (roughly 200ms per syllable at
       normal speech rate = ~3 characters worth of duration).
    2. **Punctuation pauses** — commas, semicolons, dashes insert breath pauses.
    3. **Base length** — character count as a floor to handle non-alphabetic text.

    Returns at least 1.0.
    """
    words = sentence.split()
    syllable_weight = sum(_estimate_syllables(w) for w in words) * 3.0
    pause_weight = 0.0
    for punct, bonus in _PAUSE_WEIGHTS.items():
        pause_weight += sentence.count(punct) * bonus
    # Use the larger of syllable-based or character-based weight, plus pauses.
    # This handles edge cases like all-punctuation or numeric text.
    base = max(syllable_weight, float(len(sentence)) * 0.5)
    return max(1.0, base + pause_weight)


def _build_timing_manifest(
    chunks_with_sentences: list[dict],
    audio_parts: list[bytes],
) -> dict:
    """Build a timing manifest from chunk audio and sentence metadata.

    Measures each audio chunk's duration from OGG page headers and
    distributes it proportionally across the sentences in that chunk
    by character count.
    """
    timing_sentences: list[dict] = []
    cumulative_ms = 0.0

    for chunk_info, chunk_audio in zip(chunks_with_sentences, audio_parts):
        chunk_duration_s = _ogg_duration_seconds(chunk_audio)
        chunk_duration_ms = chunk_duration_s * 1000

        # Fall back to estimate if OGG parsing fails
        if chunk_duration_ms <= 0:
            word_count = len(chunk_info["text"].split())
            chunk_duration_ms = max(100, (word_count / _WORDS_PER_MINUTE) * 60 * 1000)

        sentences = chunk_info["sentences"]
        total_weight = sum(_speech_weight(s) for s in sentences)

        for sentence in sentences:
            proportion = (
                _speech_weight(sentence) / total_weight
                if total_weight > 0
                else 1.0 / len(sentences)
            )
            sentence_duration_ms = chunk_duration_ms * proportion
            timing_sentences.append(
                {
                    "text": sentence,
                    "start_ms": round(cumulative_ms),
                    "end_ms": round(cumulative_ms + sentence_duration_ms),
                }
            )
            cumulative_ms += sentence_duration_ms

    return {
        "version": 1,
        "total_duration_ms": round(cumulative_ms),
        "sentences": timing_sentences,
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


async def process_tts(
    article_id: str,
    env: object,
    *,
    user_id: str,
    tts_voice: str | None = None,
    raise_on_error: bool = False,
) -> dict | None:
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
            db.prepare("SELECT audio_status FROM articles WHERE id = ? AND user_id = ?")
            .bind(article_id, user_id)
            .first()
        )
        if existing and existing.get("audio_status") == "ready":
            evt = current_event()
            if evt:
                evt.set("outcome", "skipped")
                evt.set("skip_reason", "audio_already_ready")
            return

        # Step 1: Update audio_status to 'generating'
        await (
            db.prepare(
                "UPDATE articles SET audio_status = ?, updated_at = ? WHERE id = ? AND user_id = ?"
            )
            .bind("generating", now_iso(), article_id, user_id)
            .run()
        )

        # Step 2: Fetch markdown content and article status from D1
        article = await (
            db.prepare("SELECT markdown_content, status FROM articles WHERE id = ? AND user_id = ?")
            .bind(article_id, user_id)
            .first()
        )

        markdown_text = article.get("markdown_content") if article else None

        if not markdown_text:
            # If the article is still being processed, the content isn't
            # available yet.  Raise a retryable error so the queue re-delivers
            # the message — by the time the retry runs, process_article will
            # likely have finished and populated markdown_content.
            article_status = article.get("status") if article else None
            if article_status in ("pending", "processing"):
                raise _RetryableError(
                    f"Article {article_id} is still {article_status} — "
                    f"markdown content not yet available, will retry"
                )
            raise ValueError(f"No markdown content found for article {article_id}")

        # Strip markdown syntax for cleaner speech output
        tts_text = strip_markdown(markdown_text)

        # Truncate to maximum length if needed
        if len(tts_text) > _MAX_TTS_TEXT_LENGTH:
            tts_text = tts_text[:_MAX_TTS_TEXT_LENGTH] + "\n\n... Content has been truncated."
            evt = current_event()
            if evt:
                evt.set("tts_text_truncated", True)
                evt.set("tts_original_length", len(markdown_text))

        # Step 4: Call Workers AI for TTS, chunking to stay within 2000-char limit
        chunks_meta = chunk_text_with_sentences(tts_text)
        if not chunks_meta:
            raise ValueError("No text to convert to speech after stripping markdown")
        chunks = [c["text"] for c in chunks_meta]

        model_id, model_key = _resolve_tts_model(env)
        is_melotts = model_key == "melotts" or "melotts" in model_id
        is_deepgram = not is_melotts and "deepgram" in model_id

        voice = tts_voice or _DEEPGRAM_DEFAULT_VOICE

        evt = current_event()
        if evt:
            evt.set("tts_chunks", len(chunks))
            evt.set("tts_model", model_id)
            evt.set("tts_voice", voice)
            if is_deepgram:
                evt.set("tts_encoding", _DEEPGRAM_ENCODING)

        audio_parts: list[bytes] = []
        total_chars = 0

        for i, chunk in enumerate(chunks):
            total_chars += len(chunk)
            if is_melotts:
                result = await ai.run(model_id, {"prompt": chunk, "lang": "en"})
                # MeloTTS returns {"audio": "<base64>"} — decode it
                audio_b64 = (
                    result.get("audio", "")
                    if hasattr(result, "get")
                    else getattr(result, "audio", "")
                )
                chunk_bytes = base64.b64decode(audio_b64) if audio_b64 else b""
            else:
                # Deepgram Aura models return a ReadableStream
                inputs = {"text": chunk}
                if is_deepgram:
                    deepgram_params = {
                        "speaker": voice,
                        "encoding": _DEEPGRAM_ENCODING,
                        "container": _DEEPGRAM_CONTAINER,
                        "bit_rate": _DEEPGRAM_BIT_RATE,
                    }
                    # Opus handles sample rate internally; the API
                    # rejects sample_rate when encoding=opus.
                    if _DEEPGRAM_ENCODING != "opus":
                        deepgram_params["sample_rate"] = _DEEPGRAM_SAMPLE_RATE
                    inputs.update(deepgram_params)
                chunk_audio = await ai.run(model_id, inputs)
                chunk_bytes = await consume_readable_stream(chunk_audio)

            if chunk_bytes:
                as_bytes = chunk_bytes if isinstance(chunk_bytes, bytes) else bytes(chunk_bytes)
                audio_parts.append(as_bytes)

        if not audio_parts:
            raise ValueError("Workers AI returned empty audio data")

        # Re-mux OGG Opus chunks into a single logical stream.
        # Naive b"".join() produces a chained OGG file with multiple
        # BOS/EOS pairs — browsers only play the first stream, silently
        # truncating all subsequent chunks.
        if len(audio_parts) > 1 and audio_parts[0][:4] == b"OggS":
            audio_data = _remux_ogg_opus(audio_parts)
        else:
            audio_data = b"".join(audio_parts)

        evt = current_event()
        if evt:
            evt.set("tts_chunk_sizes", [len(p) for p in audio_parts])
            evt.set("tts_total_audio_bytes", len(audio_data))

        # Step 5: Store audio in R2
        # Detect actual format via magic bytes
        if audio_data[:4] == b"OggS":
            ext = "ogg"
        elif audio_data[:4] == b"RIFF":
            ext = "wav"
        else:
            ext = "mp3"
        if ext == "wav":
            print(
                json.dumps(
                    {
                        "event": "tts_wav_warning",
                        "article_id": article_id,
                        "audio_bytes": len(audio_data),
                        "message": "TTS model returned WAV. "
                        "Consider switching to a model that outputs MP3.",
                    }
                )
            )
        audio_r2_key = article_key(article_id, f"audio.{ext}")
        audio_data_len = len(audio_data)
        await r2.put(audio_r2_key, audio_data)

        # Verify the R2 write by reading back the object size
        verify_obj = await r2.get(audio_r2_key)
        from wrappers import get_r2_size

        verify_size = get_r2_size(verify_obj) if verify_obj else -1
        r2_write_verified = verify_size == audio_data_len

        evt = current_event()
        if evt:
            evt.set("r2_audio_expected_bytes", audio_data_len)
            evt.set("r2_audio_actual_bytes", verify_size)
            evt.set("r2_write_verified", r2_write_verified)
        if not r2_write_verified:
            print(
                json.dumps(
                    {
                        "event": "r2_audio_write_mismatch",
                        "expected": audio_data_len,
                        "actual": verify_size,
                        "article_id": article_id,
                    }
                )
            )

        # Build and store timing manifest
        timing_manifest = _build_timing_manifest(chunks_meta, audio_parts)
        timing_key = article_key(article_id, "audio-timing.json")
        await r2.put(timing_key, json.dumps(timing_manifest))

        # Cost tracking for Deepgram models
        if is_deepgram:
            estimated_cost = round((total_chars / 1000) * _COST_PER_1000_CHARS, 6)
            evt = current_event()
            if evt:
                evt.set("tts_estimated_cost_usd", estimated_cost)
                evt.set("tts_total_chars", total_chars)

        # Step 6: Update D1 with audio metadata
        # Use measured duration from timing manifest when available
        if timing_manifest["total_duration_ms"] > 0:
            duration = round(timing_manifest["total_duration_ms"] / 1000)
        else:
            duration = _estimate_duration(tts_text)

        await (
            db.prepare(
                "UPDATE articles SET audio_key = ?, audio_duration_seconds = ?, "
                "audio_status = ?, updated_at = ? WHERE id = ? AND user_id = ?"
            )
            .bind(audio_r2_key, duration, "ready", now_iso(), article_id, user_id)
            .run()
        )

        evt = current_event()
        if evt:
            evt.set("tts_timing_sentences", len(timing_manifest["sentences"]))
            evt.set_many(
                {
                    "outcome": "success",
                    "audio_key": audio_r2_key,
                    "audio_duration_seconds": duration,
                }
            )

        return {
            "chunks": len(audio_parts),
            "chunk_sizes": [len(p) for p in audio_parts],
            "total_bytes": len(audio_data),
        }

    except ValueError:
        # Permanent errors (missing content, invalid data) — mark as failed
        evt = current_event()
        if evt:
            evt.set_many({"outcome": "error", "error.message": traceback.format_exc()[-500:]})
        try:
            await (
                db.prepare(
                    "UPDATE articles SET audio_status = ?, updated_at = ?"
                    " WHERE id = ? AND user_id = ?"
                )
                .bind("failed", now_iso(), article_id, user_id)
                .run()
            )
        except Exception:
            evt = current_event()
            if evt:
                evt.set("status_update_error", traceback.format_exc()[-500:])
        if raise_on_error:
            raise
    except _RetryableError:
        # Transient timing issue (article still processing) — re-raise for
        # queue retry without marking as failed.  The content will be
        # available on the next attempt.
        evt = current_event()
        if evt:
            evt.set_many(
                {
                    "outcome": "error",
                    "error.message": traceback.format_exc()[-500:],
                    "retryable": True,
                }
            )
        raise
    except Exception:
        # All other errors (network, JS, AI model) — mark as failed then
        # re-raise so the queue can retry.  If the queue retries, process_tts
        # will reset audio_status to 'generating' at the top.  If retries are
        # exhausted, the article correctly shows 'failed' instead of being
        # stuck at 'generating' forever.
        evt = current_event()
        if evt:
            evt.set_many(
                {
                    "outcome": "error",
                    "error.message": traceback.format_exc()[-500:],
                    "retryable": True,
                }
            )
        try:
            await (
                db.prepare(
                    "UPDATE articles SET audio_status = ?, updated_at = ?"
                    " WHERE id = ? AND user_id = ?"
                )
                .bind("failed", now_iso(), article_id, user_id)
                .run()
            )
        except Exception:
            pass  # Best-effort; the re-raise below triggers queue retry
        raise

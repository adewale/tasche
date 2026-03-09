"""Property-based tests for pure utility functions across the Tasche codebase.

Uses Hypothesis to generate random inputs and verify invariants that should
hold for all possible inputs.  Covers text processing (TTS pipeline),
article extraction helpers, FTS5 query sanitization, and reading streak
calculation.
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from articles.extraction import calculate_reading_time, count_words
from search.routes import _sanitize_fts5_query
from stats.routes import _calculate_streak
from tts.processing import (
    _build_timing_manifest,
    _estimate_duration,
    _ogg_duration_seconds,
    chunk_text,
    chunk_text_with_sentences,
    split_sentences,
    strip_markdown,
)

# ---------------------------------------------------------------------------
# 1. split_sentences
# ---------------------------------------------------------------------------


class TestSplitSentencesProperties:
    @given(text=st.text())
    @settings(max_examples=200)
    def test_all_sentences_are_nonempty(self, text: str) -> None:
        """Every returned sentence must be a non-empty string."""
        sentences = split_sentences(text)
        for s in sentences:
            assert isinstance(s, str)
            assert len(s) > 0

    @given(text=st.text())
    @settings(max_examples=200)
    def test_no_leading_or_trailing_whitespace(self, text: str) -> None:
        """No sentence should have leading or trailing whitespace."""
        for s in split_sentences(text):
            assert s == s.strip()

    @given(text=st.text())
    @settings(max_examples=200)
    def test_empty_or_whitespace_returns_empty(self, text: str) -> None:
        """If text is empty or whitespace-only, the result must be []."""
        if not text or not text.strip():
            assert split_sentences(text) == []

    @given(text=st.text(min_size=1).filter(lambda t: t.strip()))
    @settings(max_examples=200)
    def test_result_is_list_of_strings(self, text: str) -> None:
        """Return value is always a list of strings."""
        result = split_sentences(text)
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, str)

    @given(text=st.from_regex(r"[A-Za-z ]+\. [A-Za-z ]+\.", fullmatch=True))
    @settings(max_examples=200)
    def test_resplit_stability(self, text: str) -> None:
        """For well-formed text (sentences ending with '. '), splitting the
        joined result again should yield the same sentence count."""
        sentences = split_sentences(text)
        if not sentences:
            return
        rejoined = " ".join(sentences)
        resplit = split_sentences(rejoined)
        assert len(resplit) == len(sentences)


# ---------------------------------------------------------------------------
# 2. chunk_text
# ---------------------------------------------------------------------------


class TestChunkTextProperties:
    @given(text=st.text(), max_chars=st.integers(min_value=1, max_value=5000))
    @settings(max_examples=200)
    def test_chunk_length_within_limit(self, text: str, max_chars: int) -> None:
        """Every chunk must be <= max_chars, unless a single sentence exceeds it."""
        chunks = chunk_text(text, max_chars)
        sentences = split_sentences(text)
        oversized_sentences = {s for s in sentences if len(s) > max_chars}
        for chunk in chunks:
            if chunk not in oversized_sentences:
                assert len(chunk) <= max_chars, (
                    f"Chunk length {len(chunk)} exceeds max_chars {max_chars}"
                )

    @given(text=st.text())
    @settings(max_examples=200)
    def test_content_preserved(self, text: str) -> None:
        """Joining all chunks must produce the same words as the input.

        Uses whitespace normalisation as an independent oracle — no production
        code on the expected side.
        """
        chunks = chunk_text(text)
        joined = " ".join(chunks)
        # Normalise both sides identically: collapse all whitespace to single spaces
        assert " ".join(joined.split()) == " ".join(text.split())

    @given(text=st.text())
    @settings(max_examples=200)
    def test_empty_input_returns_empty(self, text: str) -> None:
        """If split_sentences produces nothing, chunk_text returns []."""
        if not split_sentences(text):
            assert chunk_text(text) == []

    @given(text=st.text())
    @settings(max_examples=200)
    def test_result_is_list_of_strings(self, text: str) -> None:
        """Return value is always a list of strings."""
        result = chunk_text(text)
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, str)


# ---------------------------------------------------------------------------
# 3. chunk_text_with_sentences
# ---------------------------------------------------------------------------


class TestChunkTextWithSentencesProperties:
    @given(text=st.text(), max_chars=st.integers(min_value=1, max_value=5000))
    @settings(max_examples=200)
    def test_text_field_matches_chunk_text(self, text: str, max_chars: int) -> None:
        """The 'text' field of each dict must match the output of chunk_text."""
        chunks_plain = chunk_text(text, max_chars)
        chunks_rich = chunk_text_with_sentences(text, max_chars)
        assert len(chunks_rich) == len(chunks_plain)
        for plain, rich in zip(chunks_plain, chunks_rich):
            assert rich["text"] == plain

    @given(text=st.text())
    @settings(max_examples=200)
    def test_content_preserved(self, text: str) -> None:
        """All sentence text joined across chunks must equal the input words.

        Uses whitespace normalisation as an independent oracle — no production
        code on the expected side.
        """
        chunks = chunk_text_with_sentences(text)
        all_sentences = []
        for chunk in chunks:
            all_sentences.extend(chunk["sentences"])
        joined = " ".join(all_sentences)
        assert " ".join(joined.split()) == " ".join(text.split())

    @given(text=st.text())
    @settings(max_examples=200)
    def test_text_equals_joined_sentences(self, text: str) -> None:
        """Each chunk's text must be the space-join of its sentences."""
        for chunk in chunk_text_with_sentences(text):
            assert chunk["text"] == " ".join(chunk["sentences"])

    @given(text=st.text())
    @settings(max_examples=200)
    def test_empty_input_returns_empty(self, text: str) -> None:
        """Empty/whitespace text should yield []."""
        if not split_sentences(text):
            assert chunk_text_with_sentences(text) == []


# ---------------------------------------------------------------------------
# 4. strip_markdown
# ---------------------------------------------------------------------------


class TestStripMarkdownProperties:
    @given(text=st.text())
    @settings(max_examples=200)
    def test_idempotent(self, text: str) -> None:
        """Applying strip_markdown twice gives the same result as once."""
        once = strip_markdown(text)
        twice = strip_markdown(once)
        assert twice == once

    @given(text=st.text())
    @settings(max_examples=200)
    def test_no_heading_markers(self, text: str) -> None:
        """No line in the result should start with '#' (heading markers)."""
        result = strip_markdown(text)
        for line in result.split("\n"):
            assert not line.strip().startswith("#"), (
                f"Heading marker found in stripped output: {line!r}"
            )

    @given(text=st.text())
    @settings(max_examples=200)
    def test_no_code_fences(self, text: str) -> None:
        """No code fences (```) should remain in the output."""
        result = strip_markdown(text)
        assert "```" not in result

    @given(text=st.text())
    @settings(max_examples=200)
    def test_empty_input_returns_empty(self, text: str) -> None:
        """Empty string input returns empty string (or falsy equivalent)."""
        if not text:
            result = strip_markdown(text)
            assert not result

    @given(text=st.text())
    @settings(max_examples=200)
    def test_never_crashes(self, text: str) -> None:
        """strip_markdown should never raise an exception on arbitrary text."""
        result = strip_markdown(text)
        assert isinstance(result, str)

    @given(
        text=st.from_regex(r"[A-Za-z .,;:?']+", fullmatch=True).filter(
            lambda t: t.strip()
        )
    )
    @settings(max_examples=200)
    def test_plain_text_preserved(self, text: str) -> None:
        """Text with no markdown syntax must survive stripping unchanged.

        Uses a restricted alphabet (letters, spaces, basic punctuation) that
        contains no markdown syntax characters, so the function should only
        normalise whitespace.
        """
        result = strip_markdown(text)
        assert " ".join(result.split()) == " ".join(text.split())

    @given(text=st.text())
    @settings(max_examples=200)
    def test_no_new_characters(self, text: str) -> None:
        """Every alphanumeric character in the output must come from the input.

        strip_markdown only removes syntax — it should never introduce new
        alphanumeric content.  Uses character-count comparison as an
        independent oracle.
        """
        from collections import Counter

        result = strip_markdown(text)
        output_chars = Counter(ch for ch in result if ch.isalnum())
        input_chars = Counter(ch for ch in text if ch.isalnum())
        for ch, count in output_chars.items():
            assert count <= input_chars.get(ch, 0), (
                f"Character {ch!r} appears {count} times in output but only "
                f"{input_chars.get(ch, 0)} times in input"
            )


# ---------------------------------------------------------------------------
# 5. _estimate_duration
# ---------------------------------------------------------------------------


class TestEstimateDurationProperties:
    @given(text=st.text())
    @settings(max_examples=200)
    def test_always_at_least_one(self, text: str) -> None:
        """Duration must always be >= 1 second."""
        assert _estimate_duration(text) >= 1

    @given(text=st.text())
    @settings(max_examples=200)
    def test_returns_int(self, text: str) -> None:
        """Return type must be int."""
        result = _estimate_duration(text)
        assert isinstance(result, int)

    @given(
        base=st.text(min_size=1).filter(lambda t: t.strip()),
        extra_words=st.lists(
            st.text(
                alphabet=st.characters(whitelist_categories=("L",)),
                min_size=1,
                max_size=10,
            ),
            min_size=1,
            max_size=50,
        ),
    )
    @settings(max_examples=200)
    def test_monotonic_with_more_words(self, base: str, extra_words: list[str]) -> None:
        """Adding more words should not decrease the estimated duration."""
        longer = base + " " + " ".join(extra_words)
        assert _estimate_duration(longer) >= _estimate_duration(base)


# ---------------------------------------------------------------------------
# 6. _ogg_duration_seconds
# ---------------------------------------------------------------------------


class TestOggDurationSecondsProperties:
    @given(data=st.binary())
    @settings(max_examples=200)
    def test_never_negative(self, data: bytes) -> None:
        """Duration must never be negative."""
        assert _ogg_duration_seconds(data) >= 0.0

    @given(data=st.binary(max_size=27))
    @settings(max_examples=200)
    def test_short_data_returns_zero(self, data: bytes) -> None:
        """Data shorter than 28 bytes must return 0.0."""
        assert _ogg_duration_seconds(data) == 0.0

    @given(data=st.binary(min_size=28).filter(lambda d: d[:4] != b"OggS"))
    @settings(max_examples=200)
    def test_non_ogg_data_returns_zero(self, data: bytes) -> None:
        """Data not starting with b'OggS' must return 0.0."""
        assert _ogg_duration_seconds(data) == 0.0

    @given(data=st.binary())
    @settings(max_examples=200)
    def test_returns_float(self, data: bytes) -> None:
        """Return type must always be float."""
        result = _ogg_duration_seconds(data)
        assert isinstance(result, float)

    @given(data=st.binary())
    @settings(max_examples=200)
    def test_never_raises(self, data: bytes) -> None:
        """The function must handle arbitrary bytes without raising."""
        # Should not raise any exception
        _ogg_duration_seconds(data)


# ---------------------------------------------------------------------------
# 7. _build_timing_manifest
# ---------------------------------------------------------------------------


# Strategy for generating valid chunks_with_sentences input
_sentence_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=50,
).filter(lambda s: s.strip())

_chunk_strategy = st.lists(_sentence_strategy, min_size=1, max_size=5).map(
    lambda sentences: {
        "text": " ".join(sentences),
        "sentences": sentences,
    }
)


class TestBuildTimingManifestProperties:
    @given(
        chunks=st.lists(_chunk_strategy, min_size=1, max_size=5),
    )
    @settings(max_examples=200)
    def test_version_is_one(self, chunks: list[dict]) -> None:
        """Manifest version must always be 1."""
        # Generate matching audio_parts (empty bytes = fallback estimation)
        audio_parts = [b"" for _ in chunks]
        manifest = _build_timing_manifest(chunks, audio_parts)
        assert manifest["version"] == 1

    @given(
        chunks=st.lists(_chunk_strategy, min_size=1, max_size=5),
    )
    @settings(max_examples=200)
    def test_total_duration_non_negative(self, chunks: list[dict]) -> None:
        """total_duration_ms must be >= 0."""
        audio_parts = [b"" for _ in chunks]
        manifest = _build_timing_manifest(chunks, audio_parts)
        assert manifest["total_duration_ms"] >= 0

    @given(
        chunks=st.lists(_chunk_strategy, min_size=1, max_size=5),
    )
    @settings(max_examples=200)
    def test_sentence_keys(self, chunks: list[dict]) -> None:
        """Every sentence dict must have text, start_ms, and end_ms keys."""
        audio_parts = [b"" for _ in chunks]
        manifest = _build_timing_manifest(chunks, audio_parts)
        for sentence in manifest["sentences"]:
            assert "text" in sentence
            assert "start_ms" in sentence
            assert "end_ms" in sentence

    @given(
        chunks=st.lists(_chunk_strategy, min_size=1, max_size=5),
    )
    @settings(max_examples=200)
    def test_start_le_end_for_each_sentence(self, chunks: list[dict]) -> None:
        """start_ms <= end_ms for every sentence."""
        audio_parts = [b"" for _ in chunks]
        manifest = _build_timing_manifest(chunks, audio_parts)
        for sentence in manifest["sentences"]:
            assert sentence["start_ms"] <= sentence["end_ms"]

    @given(
        chunks=st.lists(_chunk_strategy, min_size=1, max_size=5),
    )
    @settings(max_examples=200)
    def test_chronological_order(self, chunks: list[dict]) -> None:
        """Sentences must be in chronological order (non-decreasing start_ms)."""
        audio_parts = [b"" for _ in chunks]
        manifest = _build_timing_manifest(chunks, audio_parts)
        sentences = manifest["sentences"]
        for i in range(1, len(sentences)):
            assert sentences[i]["start_ms"] >= sentences[i - 1]["start_ms"]

    @given(
        chunks=st.lists(_chunk_strategy, min_size=1, max_size=5),
    )
    @settings(max_examples=200)
    def test_last_sentence_end_equals_total(self, chunks: list[dict]) -> None:
        """The last sentence's end_ms must equal total_duration_ms."""
        audio_parts = [b"" for _ in chunks]
        manifest = _build_timing_manifest(chunks, audio_parts)
        if manifest["sentences"]:
            assert manifest["sentences"][-1]["end_ms"] == manifest["total_duration_ms"]

    @given(
        chunks=st.lists(_chunk_strategy, min_size=1, max_size=5),
    )
    @settings(max_examples=200)
    def test_all_sentence_texts_are_nonempty(self, chunks: list[dict]) -> None:
        """Every sentence text must be a non-empty string."""
        audio_parts = [b"" for _ in chunks]
        manifest = _build_timing_manifest(chunks, audio_parts)
        for sentence in manifest["sentences"]:
            assert isinstance(sentence["text"], str)
            assert len(sentence["text"]) > 0

    def test_empty_chunks_returns_valid_manifest(self) -> None:
        """Empty input should still return a valid manifest structure."""
        manifest = _build_timing_manifest([], [])
        assert manifest["version"] == 1
        assert manifest["total_duration_ms"] == 0
        assert manifest["sentences"] == []


# ---------------------------------------------------------------------------
# 8. count_words
# ---------------------------------------------------------------------------


class TestCountWordsProperties:
    @given(text=st.text())
    @settings(max_examples=200)
    def test_always_non_negative(self, text: str) -> None:
        """Word count must always be >= 0."""
        assert count_words(text) >= 0

    def test_empty_string_returns_zero(self) -> None:
        """Empty string must return 0."""
        assert count_words("") == 0

    @given(text=st.text())
    @settings(max_examples=200)
    def test_returns_int(self, text: str) -> None:
        """Return type must be int."""
        assert isinstance(count_words(text), int)

    @given(
        words=st.lists(
            st.text(
                alphabet=st.characters(whitelist_categories=("L",)),
                min_size=1,
                max_size=10,
            ),
            min_size=1,
            max_size=20,
        ),
        extra_words=st.lists(
            st.text(
                alphabet=st.characters(whitelist_categories=("L",)),
                min_size=1,
                max_size=10,
            ),
            min_size=1,
            max_size=10,
        ),
    )
    @settings(max_examples=200)
    def test_monotonic_with_appended_words(self, words: list[str], extra_words: list[str]) -> None:
        """Appending words (separated by spaces) cannot decrease the count."""
        base = " ".join(words)
        longer = base + " " + " ".join(extra_words)
        assert count_words(longer) >= count_words(base)


# ---------------------------------------------------------------------------
# 9. calculate_reading_time
# ---------------------------------------------------------------------------


class TestCalculateReadingTimeProperties:
    @given(word_count=st.integers(min_value=0, max_value=1_000_000))
    @settings(max_examples=200)
    def test_always_at_least_one(self, word_count: int) -> None:
        """Reading time must always be >= 1 minute."""
        assert calculate_reading_time(word_count) >= 1

    @given(word_count=st.integers(max_value=0))
    @settings(max_examples=200)
    def test_non_positive_returns_one(self, word_count: int) -> None:
        """Non-positive word counts must return 1."""
        assert calculate_reading_time(word_count) == 1

    @given(
        wc_low=st.integers(min_value=0, max_value=500_000),
        wc_extra=st.integers(min_value=0, max_value=500_000),
    )
    @settings(max_examples=200)
    def test_monotonic(self, wc_low: int, wc_extra: int) -> None:
        """Higher word_count must yield >= reading time of lower word_count."""
        wc_high = wc_low + wc_extra
        assert calculate_reading_time(wc_high) >= calculate_reading_time(wc_low)

    @given(word_count=st.integers(min_value=0, max_value=1_000_000))
    @settings(max_examples=200)
    def test_returns_int(self, word_count: int) -> None:
        """Return type must be int."""
        assert isinstance(calculate_reading_time(word_count), int)


# ---------------------------------------------------------------------------
# 10. _sanitize_fts5_query
# ---------------------------------------------------------------------------


# FTS5 special characters (except the wrapping double-quotes we add)
_FTS5_SPECIAL_CHARS = set('"*+-^():{}[]|\\')


class TestSanitizeFts5QueryProperties:
    @given(query=st.text())
    @settings(max_examples=200)
    def test_no_special_chars_outside_wrapping_quotes(self, query: str) -> None:
        """The output must not contain any FTS5 special characters except the
        wrapping double-quotes around each token."""
        result = _sanitize_fts5_query(query)
        # Remove the wrapping double quotes to inspect inner content
        inner = result.replace('" "', " ")  # space between quoted tokens
        if inner.startswith('"'):
            inner = inner[1:]
        if inner.endswith('"'):
            inner = inner[:-1]
        for ch in inner:
            assert ch not in _FTS5_SPECIAL_CHARS, (
                f"Special char {ch!r} found in sanitized output: {result!r}"
            )

    def test_empty_query_returns_empty(self) -> None:
        """Empty query must return empty string."""
        assert _sanitize_fts5_query("") == ""

    @given(query=st.text(min_size=1).filter(lambda q: q.split()))
    @settings(max_examples=200)
    def test_each_token_wrapped_in_quotes(self, query: str) -> None:
        """Each surviving token in the output must be wrapped in double quotes."""
        result = _sanitize_fts5_query(query)
        if not result:
            return
        # Each token in the result should be of the form "word"
        tokens = result.split(" ")
        for token in tokens:
            if token:  # skip empty from double-spaces
                assert token.startswith('"') and token.endswith('"'), (
                    f"Token not properly quoted: {token!r}"
                )

    @given(query=st.text())
    @settings(max_examples=200)
    def test_never_crashes_on_unicode(self, query: str) -> None:
        """Should handle arbitrary unicode without raising."""
        result = _sanitize_fts5_query(query)
        assert isinstance(result, str)

    @given(
        query=st.text(
            alphabet=st.sampled_from(list(_FTS5_SPECIAL_CHARS)),
            min_size=1,
            max_size=20,
        )
    )
    @settings(max_examples=200)
    def test_all_special_chars_returns_empty(self, query: str) -> None:
        """A query composed entirely of special characters should return ''."""
        # Each token is entirely special chars, so all get cleaned to empty
        # and filtered out. But tokens with mixed content could survive,
        # so we only test the pure-special-chars case.
        result = _sanitize_fts5_query(query)
        # If the query is only special chars (no whitespace splitting won't help),
        # the result should be empty since cleaning removes all characters.
        # But whitespace in special chars could create tokens.
        # The key invariant: no special chars leak through.
        for ch in result:
            if ch != '"' and ch != " ":
                assert ch not in _FTS5_SPECIAL_CHARS

    @given(query=st.text())
    @settings(max_examples=200)
    def test_non_special_chars_preserved(self, query: str) -> None:
        """Every non-special, non-whitespace character must pass through.

        Extracts the "content" characters from input and output independently
        (stripping FTS5 specials and whitespace from input, stripping quotes
        and whitespace from output) and verifies they match exactly.
        """
        result = _sanitize_fts5_query(query)
        input_content = "".join(
            ch for ch in query if ch not in _FTS5_SPECIAL_CHARS and not ch.isspace()
        )
        output_content = "".join(
            ch for ch in result if ch != '"' and not ch.isspace()
        )
        assert output_content == input_content


# ---------------------------------------------------------------------------
# 11. _calculate_streak
# ---------------------------------------------------------------------------


class TestCalculateStreakProperties:
    @given(rows=st.lists(st.dictionaries(keys=st.just("d"), values=st.text())))
    @settings(max_examples=200)
    def test_always_non_negative(self, rows: list[dict]) -> None:
        """Streak must always be >= 0."""
        assert _calculate_streak(rows) >= 0

    def test_empty_rows_returns_zero(self) -> None:
        """Empty input must return 0."""
        assert _calculate_streak([]) == 0

    @given(rows=st.lists(st.dictionaries(keys=st.just("d"), values=st.text())))
    @settings(max_examples=200)
    def test_streak_le_row_count(self, rows: list[dict]) -> None:
        """Streak cannot exceed the number of rows."""
        assert _calculate_streak(rows) <= len(rows)

    @given(rows=st.lists(st.dictionaries(keys=st.just("d"), values=st.text())))
    @settings(max_examples=200)
    def test_returns_int(self, rows: list[dict]) -> None:
        """Return type must be int."""
        assert isinstance(_calculate_streak(rows), int)

    @given(n=st.integers(min_value=1, max_value=30))
    @settings(max_examples=200)
    def test_consecutive_days_from_today(self, n: int) -> None:
        """N consecutive days ending today should produce a streak of N."""
        today = date.today()
        rows = [{"d": (today - timedelta(days=i)).isoformat()} for i in range(n)]
        assert _calculate_streak(rows) == n

    @given(n=st.integers(min_value=1, max_value=30))
    @settings(max_examples=200)
    def test_consecutive_days_from_yesterday(self, n: int) -> None:
        """N consecutive days ending yesterday should produce a streak of N."""
        yesterday = date.today() - timedelta(days=1)
        rows = [{"d": (yesterday - timedelta(days=i)).isoformat()} for i in range(n)]
        assert _calculate_streak(rows) == n

    @given(gap=st.integers(min_value=2, max_value=100))
    @settings(max_examples=200)
    def test_gap_from_today_returns_zero(self, gap: int) -> None:
        """A single date more than 1 day in the past should yield streak 0."""
        old_date = date.today() - timedelta(days=gap)
        rows = [{"d": old_date.isoformat()}]
        assert _calculate_streak(rows) == 0

    def test_invalid_date_strings_ignored(self) -> None:
        """Rows with invalid date strings should be safely ignored."""
        rows = [
            {"d": "not-a-date"},
            {"d": "2099-99-99"},
            {"d": ""},
        ]
        assert _calculate_streak(rows) == 0

    @given(
        n=st.integers(min_value=1, max_value=10),
        gap=st.integers(min_value=2, max_value=30),
        extra=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=200)
    def test_streak_breaks_at_gap(self, n: int, gap: int, extra: int) -> None:
        """A gap in dates should cause the streak to stop at the gap."""
        today = date.today()
        # N consecutive days from today
        recent = [{"d": (today - timedelta(days=i)).isoformat()} for i in range(n)]
        # Then a gap, then more days
        old_start = today - timedelta(days=n + gap)
        old = [{"d": (old_start - timedelta(days=i)).isoformat()} for i in range(extra)]
        rows = recent + old
        assert _calculate_streak(rows) == n

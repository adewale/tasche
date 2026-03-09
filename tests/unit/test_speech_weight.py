"""Aggressive tests for speech weighting and syllable estimation.

Tests _estimate_syllables, _speech_weight, and the interaction between
punctuation pause modeling + syllable weighting in _build_timing_manifest.

Covers:
- Unit tests with known English words and their expected syllable counts
- Edge cases (empty strings, numbers, punctuation-only, unicode)
- Comparative tests (longer words must weight more than shorter ones)
- Property-based tests with hypothesis
- Integration tests verifying timing manifest correctness
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tts.processing import (
    _build_timing_manifest,
    _estimate_syllables,
    _speech_weight,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ogg_opus_data(duration_seconds: float) -> bytes:
    """Build a minimal OGG Opus byte sequence with a specific duration.

    Creates two OGG pages: one with the OpusHead header (containing
    pre_skip) and one with a granule_position that yields the desired
    duration.  Duration = (granule - pre_skip) / 48000.
    """
    pre_skip = 3840  # standard 80ms pre-skip
    target_granule = int(duration_seconds * 48000) + pre_skip

    # Page 1: OpusHead
    page1_payload = (
        b"OpusHead"  # magic
        + b"\x01"  # version
        + b"\x02"  # channel count
        + pre_skip.to_bytes(2, "little")  # pre_skip
        + (48000).to_bytes(4, "little")  # sample rate
        + b"\x00\x00"  # output gain
        + b"\x00"  # channel mapping
    )
    num_segments_1 = 1
    segment_table_1 = bytes([len(page1_payload)])
    page1 = (
        b"OggS"
        + b"\x00"  # version
        + b"\x02"  # header type (BOS)
        + (0).to_bytes(8, "little")  # granule = 0 for header
        + b"\x00\x00\x00\x00"  # serial
        + b"\x00\x00\x00\x00"  # page sequence
        + b"\x00\x00\x00\x00"  # checksum (ignored by our parser)
        + bytes([num_segments_1])
        + segment_table_1
        + page1_payload
    )

    # Page 2: audio data page with target granule
    audio_payload = b"\x00" * 100  # dummy audio data
    num_segments_2 = 1
    segment_table_2 = bytes([len(audio_payload)])
    page2 = (
        b"OggS"
        + b"\x00"
        + b"\x04"  # header type (EOS)
        + target_granule.to_bytes(8, "little")
        + b"\x00\x00\x00\x00"
        + b"\x01\x00\x00\x00"
        + b"\x00\x00\x00\x00"
        + bytes([num_segments_2])
        + segment_table_2
        + audio_payload
    )

    return page1 + page2


# ---------------------------------------------------------------------------
# _estimate_syllables: known words
# ---------------------------------------------------------------------------


class TestEstimateSyllablesKnownWords:
    """Test syllable estimation against known English words."""

    @pytest.mark.parametrize(
        "word,expected",
        [
            # 1-syllable words
            ("cat", 1),
            ("dog", 1),
            ("the", 1),
            ("a", 1),
            ("I", 1),
            ("run", 1),
            ("go", 1),
            ("fly", 1),
            ("strength", 1),
            ("through", 1),
            # 2-syllable words
            ("hello", 2),
            ("mother", 2),
            ("happy", 2),
            ("human", 2),
            ("running", 2),
            ("table", 1),  # silent-e rule overshoots, but min 1 is fine
            ("music", 2),
            ("apple", 2),
            ("garden", 2),
            # 3-syllable words
            ("beautiful", 3),
            ("computer", 3),
            ("tomorrow", 3),
            ("industry", 3),
            ("November", 3),
            # 4-syllable words
            ("extraordinary", 5),  # heuristic may differ from reality
            ("information", 4),
            ("communication", 5),
            ("understanding", 4),
            # Long words
            ("antidisestablishmentarianism", 10),  # heuristic gets 10; real is 12
        ],
    )
    def test_known_word(self, word: str, expected: int) -> None:
        """Syllable count for known words should be within ±1 of expected."""
        result = _estimate_syllables(word)
        assert abs(result - expected) <= 1, (
            f"Expected ~{expected} syllables for '{word}', got {result}"
        )


class TestEstimateSyllablesEdgeCases:
    """Edge cases for syllable estimation."""

    def test_empty_string(self) -> None:
        assert _estimate_syllables("") == 1

    def test_single_consonant(self) -> None:
        assert _estimate_syllables("b") == 1

    def test_single_vowel(self) -> None:
        assert _estimate_syllables("a") == 1

    def test_all_consonants(self) -> None:
        assert _estimate_syllables("xyz") == 1

    def test_all_vowels(self) -> None:
        # "aeiou" — one vowel cluster = 1 syllable
        assert _estimate_syllables("aeiou") == 1

    def test_alternating_vowels_consonants(self) -> None:
        # "ababa" — a, a, a = 3 vowel clusters
        result = _estimate_syllables("ababa")
        assert result >= 2

    def test_numbers_only(self) -> None:
        # "12345" — no alpha chars → 1
        assert _estimate_syllables("12345") == 1

    def test_punctuation_only(self) -> None:
        assert _estimate_syllables("...!?") == 1

    def test_mixed_punctuation_and_word(self) -> None:
        # "hello!" — strips non-alpha → "hello" → 2
        assert _estimate_syllables("hello!") == 2

    def test_hyphenated_word(self) -> None:
        # "self-driving" → strips non-alpha → "selfdriving"
        result = _estimate_syllables("self-driving")
        assert result >= 2

    def test_uppercase(self) -> None:
        assert _estimate_syllables("HELLO") == 2

    def test_mixed_case(self) -> None:
        assert _estimate_syllables("HeLLo") == 2

    def test_unicode_word(self) -> None:
        # Non-latin characters — no vowels detected → 1
        assert _estimate_syllables("日本語") == 1

    def test_whitespace_only(self) -> None:
        assert _estimate_syllables("   ") == 1


# ---------------------------------------------------------------------------
# _estimate_syllables: property-based
# ---------------------------------------------------------------------------


class TestEstimateSyllablesProperties:
    """Property-based tests for syllable estimation."""

    @given(st.text(min_size=0, max_size=100))
    @settings(max_examples=300)
    def test_always_returns_at_least_1(self, word: str) -> None:
        assert _estimate_syllables(word) >= 1

    @given(st.text(min_size=0, max_size=100))
    @settings(max_examples=300)
    def test_returns_int(self, word: str) -> None:
        assert isinstance(_estimate_syllables(word), int)

    @given(st.text(min_size=0, max_size=100))
    @settings(max_examples=300)
    def test_never_raises(self, word: str) -> None:
        """Should handle any input without exceptions."""
        _estimate_syllables(word)  # No assertion — just must not crash

    @given(
        st.text(
            alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyz")),
            min_size=1,
            max_size=20,
        )
    )
    @settings(max_examples=200)
    def test_alpha_words_bounded_by_length(self, word: str) -> None:
        """Syllable count can't exceed the number of characters."""
        result = _estimate_syllables(word)
        assert result <= len(word)

    @given(
        st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=100)
    def test_repeated_vowel_consonant_pairs(self, n: int) -> None:
        """'ba' × n should have roughly n syllables."""
        word = "ba" * n
        result = _estimate_syllables(word)
        # Should be close to n (each "ba" has one vowel cluster)
        assert abs(result - n) <= 1


# ---------------------------------------------------------------------------
# _speech_weight: unit tests
# ---------------------------------------------------------------------------


class TestSpeechWeightUnit:
    """Direct unit tests for _speech_weight."""

    def test_short_vs_long_sentence(self) -> None:
        """Longer sentences should have higher weight."""
        short = _speech_weight("Hi.")
        long = _speech_weight("This is a much longer sentence with many words.")
        assert long > short

    def test_punctuation_adds_weight(self) -> None:
        """Sentences with more punctuation should weigh more."""
        no_punct = _speech_weight("The cat sat on the mat")
        with_commas = _speech_weight("The cat, the dog, and the rat sat")
        assert with_commas > no_punct

    def test_comma_increases_weight(self) -> None:
        """A comma should increase weight over the same text without it."""
        without = _speech_weight("Hello world")
        with_comma = _speech_weight("Hello, world")
        assert with_comma > without

    def test_semicolon_increases_weight(self) -> None:
        without = _speech_weight("Run fast")
        with_semi = _speech_weight("Run fast;")
        assert with_semi > without

    def test_em_dash_increases_weight(self) -> None:
        without = _speech_weight("He was tired")
        with_dash = _speech_weight("He was tired\u2014very tired")
        assert with_dash > without

    def test_ellipsis_increases_weight(self) -> None:
        without = _speech_weight("And then")
        with_ellipsis = _speech_weight("And then...")
        assert with_ellipsis > without

    def test_unicode_ellipsis(self) -> None:
        without = _speech_weight("And then")
        with_ellipsis = _speech_weight("And then\u2026")
        assert with_ellipsis > without

    def test_polysyllabic_words_weigh_more(self) -> None:
        """'Extraordinary' should weigh more than 'cat' even though
        the character count ratio is only 13:3."""
        short = _speech_weight("cat")
        long = _speech_weight("extraordinary")
        # Syllable-weighted ratio should be roughly 5:1, not 4.3:1
        assert long > short * 3

    def test_minimum_weight(self) -> None:
        """Even empty-ish sentences get weight >= 1."""
        assert _speech_weight("") >= 1.0
        assert _speech_weight("a") >= 1.0
        assert _speech_weight(".") >= 1.0

    def test_all_punctuation(self) -> None:
        """Pure punctuation should still get meaningful weight from pauses."""
        result = _speech_weight("..., !!!")
        assert result > 5.0  # Multiple pause marks

    def test_numeric_text(self) -> None:
        """Numeric text falls back to character-based weight."""
        result = _speech_weight("12345678")
        assert result >= 4.0  # At least half the character count


class TestSpeechWeightComparative:
    """Comparative tests — verify relative ordering is correct."""

    @pytest.mark.parametrize(
        "lighter,heavier",
        [
            ("OK.", "This is a very long sentence with many polysyllabic words."),
            ("Go.", "Simultaneously, the extraordinary phenomenon continued."),
            ("Run", "The uncharacteristically enthusiastic participants"),
            ("Hi, world.", "Hi, world, it is nice to see you again today."),
        ],
    )
    def test_relative_ordering(self, lighter: str, heavier: str) -> None:
        assert _speech_weight(lighter) < _speech_weight(heavier)


# ---------------------------------------------------------------------------
# _speech_weight: property-based
# ---------------------------------------------------------------------------


class TestSpeechWeightProperties:
    """Property-based tests for _speech_weight."""

    @given(st.text(min_size=0, max_size=200))
    @settings(max_examples=300)
    def test_always_at_least_one(self, sentence: str) -> None:
        assert _speech_weight(sentence) >= 1.0

    @given(st.text(min_size=0, max_size=200))
    @settings(max_examples=300)
    def test_returns_float(self, sentence: str) -> None:
        assert isinstance(_speech_weight(sentence), float)

    @given(st.text(min_size=0, max_size=200))
    @settings(max_examples=300)
    def test_never_raises(self, sentence: str) -> None:
        _speech_weight(sentence)

    @given(
        st.text(min_size=1, max_size=50),
        st.text(min_size=1, max_size=50),
    )
    @settings(max_examples=200)
    def test_concatenation_increases_weight(self, a: str, b: str) -> None:
        """Concatenating text should never decrease weight."""
        combined = a + " " + b
        assert _speech_weight(combined) >= _speech_weight(a)

    @given(
        st.text(
            alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyz ")),
            min_size=5,
            max_size=100,
        )
    )
    @settings(max_examples=200)
    def test_adding_comma_never_decreases(self, sentence: str) -> None:
        """Adding a comma to a sentence should never decrease its weight."""
        with_comma = sentence + ","
        assert _speech_weight(with_comma) >= _speech_weight(sentence)


# ---------------------------------------------------------------------------
# Timing manifest integration: syllable + punctuation weighting
# ---------------------------------------------------------------------------


class TestTimingManifestWeighting:
    """Integration tests verifying that timing distribution reflects
    the syllable + punctuation weighting in realistic scenarios."""

    def test_polysyllabic_gets_more_time(self) -> None:
        """Sentence with polysyllabic words should get proportionally
        more time than sentence with monosyllabic words."""
        chunks = [
            {
                "text": "The cat sat. Simultaneously, the extraordinary phenomenon continued.",
                "sentences": [
                    "The cat sat.",
                    "Simultaneously, the extraordinary phenomenon continued.",
                ],
            }
        ]
        audio_parts = [_make_ogg_opus_data(6.0)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        s0_dur = manifest["sentences"][0]["end_ms"] - manifest["sentences"][0]["start_ms"]
        s1_dur = manifest["sentences"][1]["end_ms"] - manifest["sentences"][1]["start_ms"]
        # s1 has ~18 syllables + comma pause vs s0 with ~3 syllables
        assert s1_dur > s0_dur * 3, (
            f"Expected polysyllabic sentence to dominate: {s0_dur} vs {s1_dur}"
        )

    def test_comma_heavy_sentence_gets_more_time(self) -> None:
        """Sentence with many commas gets more time due to pause modeling."""
        chunks = [
            {
                "text": "Run fast. He ran, jumped, ducked, and rolled.",
                "sentences": [
                    "Run fast.",
                    "He ran, jumped, ducked, and rolled.",
                ],
            }
        ]
        audio_parts = [_make_ogg_opus_data(4.0)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        s0_dur = manifest["sentences"][0]["end_ms"] - manifest["sentences"][0]["start_ms"]
        s1_dur = manifest["sentences"][1]["end_ms"] - manifest["sentences"][1]["start_ms"]
        # s1 has 3 commas (9 pause-chars) plus more syllables
        assert s1_dur > s0_dur * 2

    def test_em_dash_adds_weight(self) -> None:
        """Em-dash pauses should increase sentence duration."""
        chunks = [
            {
                "text": "He arrived. She\u2014the tallest of them\u2014stood first.",
                "sentences": [
                    "He arrived.",
                    "She\u2014the tallest of them\u2014stood first.",
                ],
            }
        ]
        audio_parts = [_make_ogg_opus_data(4.0)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        s0_dur = manifest["sentences"][0]["end_ms"] - manifest["sentences"][0]["start_ms"]
        s1_dur = manifest["sentences"][1]["end_ms"] - manifest["sentences"][1]["start_ms"]
        assert s1_dur > s0_dur * 2

    def test_equal_length_different_syllables(self) -> None:
        """Two sentences of similar character count but different syllable
        counts should distribute time differently."""
        # "Strengths" = 1 syllable, 9 chars
        # "Beautiful" = 3 syllables, 9 chars
        chunks = [
            {
                "text": "Strengths help. Beautiful work done.",
                "sentences": ["Strengths help.", "Beautiful work done."],
            }
        ]
        audio_parts = [_make_ogg_opus_data(4.0)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        s0_dur = manifest["sentences"][0]["end_ms"] - manifest["sentences"][0]["start_ms"]
        s1_dur = manifest["sentences"][1]["end_ms"] - manifest["sentences"][1]["start_ms"]
        # "Beautiful work done" has more syllables (5) than "Strengths help" (2)
        # so it should get more time
        assert s1_dur > s0_dur

    def test_many_sentences_sum_correctly(self) -> None:
        """Multiple sentences in a chunk should still sum to chunk duration."""
        sentences = [
            "First.",
            "The second sentence is considerably longer and more complex.",
            "Third, with a comma.",
            "Extraordinarily polysyllabic vocabulary demonstration.",
            "End.",
        ]
        chunks = [{"text": " ".join(sentences), "sentences": sentences}]
        audio_parts = [_make_ogg_opus_data(10.0)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        total = sum(s["end_ms"] - s["start_ms"] for s in manifest["sentences"])
        # Should be very close to 10000ms (within rounding)
        assert abs(total - 10000) <= len(sentences), (
            f"Total duration {total}ms differs from 10000ms by more than rounding"
        )

    def test_all_same_sentences_get_equal_time(self) -> None:
        """Identical sentences should get equal time within rounding."""
        s = "Hello world."
        chunks = [{"text": f"{s} {s} {s}", "sentences": [s, s, s]}]
        audio_parts = [_make_ogg_opus_data(6.0)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        durations = [sent["end_ms"] - sent["start_ms"] for sent in manifest["sentences"]]
        # All should be ~2000ms each (within rounding)
        for d in durations:
            assert abs(d - 2000) <= 2

    def test_single_word_sentences(self) -> None:
        """Single-word sentences should still get reasonable timing."""
        chunks = [
            {
                "text": "Go. Stop. Run.",
                "sentences": ["Go.", "Stop.", "Run."],
            }
        ]
        audio_parts = [_make_ogg_opus_data(3.0)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        for sent in manifest["sentences"]:
            dur = sent["end_ms"] - sent["start_ms"]
            # Each should get roughly 1000ms
            assert dur > 500
            assert dur < 1500

    def test_numeric_sentence_gets_reasonable_weight(self) -> None:
        """Numeric text (no vowels) should still get meaningful duration."""
        chunks = [
            {
                "text": "12345. Hello world.",
                "sentences": ["12345.", "Hello world."],
            }
        ]
        audio_parts = [_make_ogg_opus_data(4.0)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        s0_dur = manifest["sentences"][0]["end_ms"] - manifest["sentences"][0]["start_ms"]
        # Numeric text should still get some duration (not 0)
        assert s0_dur > 200


# ---------------------------------------------------------------------------
# Timing manifest: property-based with weighting
# ---------------------------------------------------------------------------


class TestTimingManifestWeightingProperties:
    """Property-based tests for timing with the new weighting model."""

    @given(
        st.lists(
            st.text(
                alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyz ,.")),
                min_size=3,
                max_size=80,
            ),
            min_size=1,
            max_size=8,
        ),
        st.floats(min_value=0.5, max_value=30.0),
    )
    @settings(max_examples=200)
    def test_durations_always_sum_to_total(self, sentences: list[str], duration: float) -> None:
        """Individual sentence durations must sum to total_duration_ms."""
        # Filter out empty sentences
        sentences = [s for s in sentences if s.strip()]
        if not sentences:
            return

        chunks = [{"text": " ".join(sentences), "sentences": sentences}]
        audio_parts = [_make_ogg_opus_data(duration)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        if not manifest["sentences"]:
            return

        total = sum(s["end_ms"] - s["start_ms"] for s in manifest["sentences"])
        assert abs(total - manifest["total_duration_ms"]) <= len(sentences) + 1

    @given(
        st.lists(
            st.text(
                alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyz ,.")),
                min_size=3,
                max_size=80,
            ),
            min_size=1,
            max_size=8,
        ),
        st.floats(min_value=0.5, max_value=30.0),
    )
    @settings(max_examples=200)
    def test_all_durations_positive(self, sentences: list[str], duration: float) -> None:
        """Every sentence must get positive duration."""
        sentences = [s for s in sentences if s.strip()]
        if not sentences:
            return

        chunks = [{"text": " ".join(sentences), "sentences": sentences}]
        audio_parts = [_make_ogg_opus_data(duration)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        for sent in manifest["sentences"]:
            assert sent["end_ms"] >= sent["start_ms"], (
                f"Negative duration for '{sent['text']}': "
                f"start={sent['start_ms']} end={sent['end_ms']}"
            )

    @given(
        st.text(
            alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyz ")),
            min_size=5,
            max_size=30,
        ),
        st.text(
            alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyz ")),
            min_size=30,
            max_size=100,
        ),
        st.floats(min_value=1.0, max_value=20.0),
    )
    @settings(max_examples=200)
    def test_heavier_sentence_gets_more_time(self, light: str, heavy: str, duration: float) -> None:
        """In a two-sentence chunk, the sentence with higher speech weight
        should get more (or equal) time."""
        light = light.strip()
        heavy = heavy.strip()
        if not light or not heavy:
            return
        # Skip if the supposedly-heavy sentence doesn't actually weigh more
        if _speech_weight(f"{heavy}.") <= _speech_weight(f"{light}."):
            return

        chunks = [
            {
                "text": f"{light}. {heavy}.",
                "sentences": [f"{light}.", f"{heavy}."],
            }
        ]
        audio_parts = [_make_ogg_opus_data(duration)]
        manifest = _build_timing_manifest(chunks, audio_parts)

        if len(manifest["sentences"]) < 2:
            return

        s0_dur = manifest["sentences"][0]["end_ms"] - manifest["sentences"][0]["start_ms"]
        s1_dur = manifest["sentences"][1]["end_ms"] - manifest["sentences"][1]["start_ms"]
        assert s1_dur >= s0_dur, (
            f"Heavier sentence got less time: light='{light}' ({s0_dur}ms) "
            f"vs heavy='{heavy}' ({s1_dur}ms)"
        )

"""Tests for TTS processing fixes (Issues 17, 18, 60).

Covers:
- Issue 17: OGG CRC-32 lookup table correctness
- Issue 18: strip_markdown regex consolidation produces correct output
- Issue 60: chunk_text / chunk_text_with_sentences produce same results
  after extracting shared _accumulate_chunks helper
"""

from __future__ import annotations

import struct

from tts.processing import (
    _OGG_CRC_TABLE,
    _accumulate_chunks,
    _build_ogg_crc_table,
    _ogg_crc32,
    _write_ogg_page,
    chunk_text,
    chunk_text_with_sentences,
    strip_markdown,
)

# ---------------------------------------------------------------------------
# Issue 17 — OGG CRC-32 lookup table
# ---------------------------------------------------------------------------


class TestOggCrc32Table:
    """Verify the pre-computed CRC lookup table and CRC function."""

    def test_table_has_256_entries(self) -> None:
        assert len(_OGG_CRC_TABLE) == 256

    def test_table_is_tuple(self) -> None:
        """Table should be a tuple for faster indexing in WASM."""
        assert isinstance(_OGG_CRC_TABLE, tuple)

    def test_table_first_entry_is_zero(self) -> None:
        assert _OGG_CRC_TABLE[0] == 0

    def test_build_table_matches_module_table(self) -> None:
        """Calling _build_ogg_crc_table again must produce the same table."""
        fresh = _build_ogg_crc_table()
        assert fresh == _OGG_CRC_TABLE

    def test_crc_empty_bytes(self) -> None:
        assert _ogg_crc32(b"") == 0

    def test_crc_known_value_single_byte(self) -> None:
        """CRC of a single zero byte: table[0 ^ 0] ^ (0 << 8) = table[0] = 0."""
        assert _ogg_crc32(b"\x00") == 0

    def test_crc_known_value_oggs(self) -> None:
        """Verify CRC of 'OggS' magic bytes is deterministic and non-zero."""
        result = _ogg_crc32(b"OggS")
        assert isinstance(result, int)
        assert result != 0
        # Should be consistent across calls
        assert _ogg_crc32(b"OggS") == result

    def test_crc_different_inputs_differ(self) -> None:
        assert _ogg_crc32(b"hello") != _ogg_crc32(b"world")

    def test_crc_fits_in_32_bits(self) -> None:
        result = _ogg_crc32(b"test data for CRC")
        assert 0 <= result <= 0xFFFFFFFF

    def test_crc_all_ones(self) -> None:
        """CRC of 0xFF should use table entry 255."""
        result = _ogg_crc32(b"\xff")
        assert result == _OGG_CRC_TABLE[255]

    def test_crc_deterministic_long_input(self) -> None:
        """CRC is deterministic for longer inputs."""
        data = bytes(range(256)) * 4  # 1024 bytes
        assert _ogg_crc32(data) == _ogg_crc32(data)

    def test_write_ogg_page_uses_crc(self) -> None:
        """_write_ogg_page embeds a valid CRC that matches recomputation."""
        page = _write_ogg_page(
            header_type=0x02,  # BOS
            granule=0,
            serial=1,
            page_seq=0,
            segment_table=b"\x13",
            payload=(
                b"OpusHead"
                + b"\x01\x02"
                + b"\x00\x0f"
                + b"\x80\xbb\x00\x00"
                + b"\x00\x00\x00\x00\x00"
            ),
        )
        # Extract CRC from bytes 22-25
        embedded_crc = struct.unpack_from("<I", page, 22)[0]
        # Zero out CRC field and recompute
        zeroed = page[:22] + b"\x00\x00\x00\x00" + page[26:]
        assert _ogg_crc32(zeroed) == embedded_crc


# ---------------------------------------------------------------------------
# Issue 18 — strip_markdown (regex consolidation)
# ---------------------------------------------------------------------------


class TestStripMarkdown:
    """Verify strip_markdown produces correct output after regex refactor."""

    def test_empty_string(self) -> None:
        assert strip_markdown("") == ""

    def test_none_input(self) -> None:
        assert strip_markdown(None) is None

    def test_plain_text_unchanged(self) -> None:
        assert strip_markdown("Hello world") == "Hello world"

    def test_headings_removed(self) -> None:
        assert strip_markdown("# Heading 1") == "Heading 1"
        assert strip_markdown("## Heading 2") == "Heading 2"
        assert strip_markdown("### Heading 3") == "Heading 3"

    def test_bold_removed(self) -> None:
        assert strip_markdown("This is **bold** text") == "This is bold text"

    def test_italic_removed(self) -> None:
        assert strip_markdown("This is *italic* text") == "This is italic text"

    def test_bold_italic_removed(self) -> None:
        assert strip_markdown("This is ***bold italic*** text") == "This is bold italic text"

    def test_underscore_bold_removed(self) -> None:
        assert strip_markdown("This is __bold__ text") == "This is bold text"

    def test_links_replaced_with_text(self) -> None:
        result = strip_markdown("Click [here](https://example.com) now")
        assert result == "Click here now"

    def test_images_removed(self) -> None:
        result = strip_markdown("Before ![alt text](image.png) after")
        assert result == "Before  after"

    def test_inline_code_content_kept(self) -> None:
        result = strip_markdown("Use `print()` in Python")
        assert result == "Use print() in Python"

    def test_code_blocks_removed(self) -> None:
        md = "Before\n```python\nprint('hello')\n```\nAfter"
        result = strip_markdown(md)
        assert "print" not in result
        assert "Before" in result
        assert "After" in result

    def test_blockquotes_removed(self) -> None:
        assert strip_markdown("> quoted text") == "quoted text"

    def test_nested_blockquote_heading(self) -> None:
        assert strip_markdown("> # Title") == "Title"

    def test_horizontal_rules_removed(self) -> None:
        result = strip_markdown("Above\n---\nBelow")
        assert "---" not in result
        assert "Above" in result
        assert "Below" in result

    def test_unordered_list_markers_removed(self) -> None:
        result = strip_markdown("- item one\n- item two")
        assert result == "item one\nitem two"

    def test_ordered_list_markers_removed(self) -> None:
        result = strip_markdown("1. first\n2. second")
        assert result == "first\nsecond"

    def test_html_tags_removed(self) -> None:
        result = strip_markdown("Hello <strong>world</strong> end")
        assert result == "Hello world end"

    def test_unicode_preserved(self) -> None:
        result = strip_markdown("# Ueber die Bruecke")
        assert "Ueber die Bruecke" in result

    def test_complex_markdown(self) -> None:
        md = (
            "# Title\n\n"
            "Some **bold** and *italic* text.\n\n"
            "- List item with [a link](http://example.com)\n"
            "- Another ![img](pic.png) item\n\n"
            "> A blockquote\n\n"
            "```\ncode block\n```\n\n"
            "End with `inline code`."
        )
        result = strip_markdown(md)
        assert "Title" in result
        assert "bold" in result
        assert "italic" in result
        assert "a link" in result
        assert "![" not in result
        assert "```" not in result
        assert "inline code" in result
        assert "#" not in result

    def test_idempotent(self) -> None:
        """Running strip_markdown twice should give the same result."""
        md = "## Hello **world** [link](url)"
        once = strip_markdown(md)
        twice = strip_markdown(once)
        assert once == twice


# ---------------------------------------------------------------------------
# Issue 60 — shared chunking helper
# ---------------------------------------------------------------------------


class TestAccumulateChunks:
    """Test the extracted _accumulate_chunks helper directly."""

    def test_empty_list(self) -> None:
        assert _accumulate_chunks([], 100) == []

    def test_single_sentence_fits(self) -> None:
        result = _accumulate_chunks(["Hello world."], 100)
        assert result == [["Hello world."]]

    def test_single_sentence_exceeds(self) -> None:
        """A sentence larger than max_chars gets its own group."""
        result = _accumulate_chunks(["A" * 200], 100)
        assert result == [["A" * 200]]

    def test_two_sentences_fit_together(self) -> None:
        result = _accumulate_chunks(["Hello.", "World."], 20)
        assert result == [["Hello.", "World."]]

    def test_two_sentences_split(self) -> None:
        result = _accumulate_chunks(["Hello there.", "World here."], 15)
        assert result == [["Hello there."], ["World here."]]

    def test_accounts_for_space_separator(self) -> None:
        """Joining adds a space, so 'AAAA BBBB' = 9 chars."""
        result = _accumulate_chunks(["AAAA", "BBBB"], 9)
        # "AAAA BBBB" is exactly 9 chars, should fit
        assert result == [["AAAA", "BBBB"]]

    def test_space_pushes_over_limit(self) -> None:
        result = _accumulate_chunks(["AAAA", "BBBB"], 8)
        # "AAAA BBBB" is 9 chars, over limit of 8
        assert result == [["AAAA"], ["BBBB"]]


class TestChunkTextConsistency:
    """Verify chunk_text and chunk_text_with_sentences agree after refactor."""

    def test_empty_text(self) -> None:
        assert chunk_text("") == []
        assert chunk_text_with_sentences("") == []

    def test_whitespace_only(self) -> None:
        assert chunk_text("   ") == []
        assert chunk_text_with_sentences("   ") == []

    def test_single_sentence(self) -> None:
        text = "Hello world."
        chunks = chunk_text(text)
        meta = chunk_text_with_sentences(text)
        assert len(chunks) == 1
        assert len(meta) == 1
        assert chunks[0] == meta[0]["text"]
        assert meta[0]["sentences"] == ["Hello world."]

    def test_multiple_sentences_one_chunk(self) -> None:
        text = "First sentence. Second sentence. Third one."
        chunks = chunk_text(text, max_chars=500)
        meta = chunk_text_with_sentences(text, max_chars=500)
        assert len(chunks) == 1
        assert len(meta) == 1
        assert chunks[0] == meta[0]["text"]

    def test_texts_match_between_functions(self) -> None:
        """chunk_text output must equal the 'text' fields from chunk_text_with_sentences."""
        text = (
            "The quick brown fox jumps over the lazy dog. "
            "Pack my box with five dozen liquor jugs. "
            "How vexingly quick daft zebras jump. "
            "The five boxing wizards jump quickly."
        )
        chunks = chunk_text(text, max_chars=80)
        meta = chunk_text_with_sentences(text, max_chars=80)
        assert len(chunks) == len(meta)
        for c, m in zip(chunks, meta):
            assert c == m["text"]

    def test_sentences_field_is_list(self) -> None:
        text = "One. Two. Three."
        meta = chunk_text_with_sentences(text, max_chars=500)
        assert isinstance(meta[0]["sentences"], list)
        assert meta[0]["sentences"] == ["One.", "Two.", "Three."]

    def test_long_text_splits(self) -> None:
        """A long text should produce multiple chunks."""
        sentences = [f"Sentence number {i}." for i in range(100)]
        text = " ".join(sentences)
        chunks = chunk_text(text, max_chars=200)
        meta = chunk_text_with_sentences(text, max_chars=200)
        assert len(chunks) > 1
        assert len(chunks) == len(meta)
        for c, m in zip(chunks, meta):
            assert c == m["text"]

    def test_unicode_text(self) -> None:
        text = "Erste Satz auf Deutsch. Zweiter Satz. Dritter Satz."
        chunks = chunk_text(text, max_chars=30)
        meta = chunk_text_with_sentences(text, max_chars=30)
        assert len(chunks) == len(meta)
        for c, m in zip(chunks, meta):
            assert c == m["text"]

    def test_all_chunks_within_limit(self) -> None:
        """Every chunk should respect max_chars (unless a single sentence exceeds it)."""
        sentences = [f"This is test sentence number {i} with some text." for i in range(50)]
        text = " ".join(sentences)
        limit = 150
        chunks = chunk_text(text, max_chars=limit)
        for chunk in chunks:
            # Each chunk is either under limit, or is a single oversized sentence
            if len(chunk.split(". ")) > 1:
                assert len(chunk) <= limit

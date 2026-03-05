"""Tests for R2 storage operations (src/articles/storage.py).

Covers article_key() path construction and validation, store/get round-trips
for content and metadata, delete_article_content() with single-page and
paginated (truncated) listings, and missing-key behaviour.
"""

from __future__ import annotations

from typing import Any

import pytest

from articles.storage import (
    article_key,
    delete_article_content,
    delete_non_audio_content,
    get_content,
    get_metadata,
    store_content,
    store_metadata,
)
from tests.conftest import MockR2

# ---------------------------------------------------------------------------
# article_key() — path construction
# ---------------------------------------------------------------------------


class TestArticleKey:
    def test_basic_key_construction(self) -> None:
        """article_key returns articles/{id}/{filename} format."""
        key = article_key("abc123", "content.html")
        assert key == "articles/abc123/content.html"

    def test_markdown_key(self) -> None:
        """article_key works for markdown filenames."""
        key = article_key("xyz", "content.md")
        assert key == "articles/xyz/content.md"

    def test_metadata_key(self) -> None:
        """article_key works for metadata.json."""
        key = article_key("id_99", "metadata.json")
        assert key == "articles/id_99/metadata.json"

    def test_audio_key(self) -> None:
        """article_key works for audio files."""
        key = article_key("art_tts", "audio.mp3")
        assert key == "articles/art_tts/audio.mp3"

    def test_rejects_slash_in_filename(self) -> None:
        """article_key raises ValueError when filename contains a slash."""
        with pytest.raises(ValueError, match="Invalid filename"):
            article_key("abc", "sub/dir.html")

    def test_rejects_dot_dot_in_filename(self) -> None:
        """article_key raises ValueError when filename contains '..'."""
        with pytest.raises(ValueError, match="Invalid filename"):
            article_key("abc", "../etc/passwd")

    def test_rejects_double_dot_only(self) -> None:
        """article_key raises ValueError for '..' as the entire filename."""
        with pytest.raises(ValueError, match="Invalid filename"):
            article_key("abc", "..")

    def test_allows_dots_in_normal_filename(self) -> None:
        """article_key allows a single dot (extension separator)."""
        key = article_key("abc", "file.name.txt")
        assert key == "articles/abc/file.name.txt"


# ---------------------------------------------------------------------------
# store_content() / get_content() round-trip
# ---------------------------------------------------------------------------


class TestStoreGetContent:
    async def test_round_trip_html(self) -> None:
        """store_content stores HTML in R2 and get_content retrieves it."""
        r2 = MockR2()
        html = "<h1>Hello</h1><p>World</p>"

        keys = await store_content(r2, "art_001", html)

        assert "html_key" in keys
        assert keys["html_key"] == "articles/art_001/content.html"

        retrieved = await get_content(r2, keys["html_key"])
        assert retrieved == html

    async def test_stores_unicode_content(self) -> None:
        """store_content preserves Unicode characters."""
        r2 = MockR2()
        html = "<p>Hola mundo. Caf\u00e9. \u65e5\u672c\u8a9e.</p>"

        keys = await store_content(r2, "art_uni", html)
        retrieved = await get_content(r2, keys["html_key"])
        assert retrieved == html

    async def test_get_content_missing_key_returns_none(self) -> None:
        """get_content returns None when the R2 key does not exist."""
        r2 = MockR2()
        result = await get_content(r2, "articles/nonexistent/content.html")
        assert result is None


# ---------------------------------------------------------------------------
# store_metadata() / get_metadata() round-trip
# ---------------------------------------------------------------------------


class TestStoreGetMetadata:
    async def test_round_trip_metadata(self) -> None:
        """store_metadata stores JSON and get_metadata retrieves it."""
        r2 = MockR2()
        metadata = {
            "article_id": "art_m1",
            "original_url": "https://example.com/page",
            "word_count": 1500,
            "reading_time_minutes": 7,
        }

        key = await store_metadata(r2, "art_m1", metadata)
        assert key == "articles/art_m1/metadata.json"

        retrieved = await get_metadata(r2, "art_m1")
        assert retrieved == metadata

    async def test_metadata_with_nested_structures(self) -> None:
        """store_metadata handles nested dicts and lists."""
        r2 = MockR2()
        metadata = {
            "article_id": "art_m2",
            "tags": ["python", "cloudflare"],
            "images": {"count": 3, "total_size": 500000},
        }

        await store_metadata(r2, "art_m2", metadata)
        retrieved = await get_metadata(r2, "art_m2")
        assert retrieved == metadata

    async def test_get_metadata_missing_returns_none(self) -> None:
        """get_metadata returns None when no metadata exists for the article."""
        r2 = MockR2()
        result = await get_metadata(r2, "nonexistent_article")
        assert result is None


# ---------------------------------------------------------------------------
# delete_article_content() — single page
# ---------------------------------------------------------------------------


class TestDeleteArticleContent:
    async def test_deletes_all_objects_for_article(self) -> None:
        """delete_article_content removes all R2 objects under the article prefix."""
        r2 = MockR2()

        # Pre-populate R2 with several objects for the article
        await r2.put("articles/art_del/content.html", b"<p>html</p>")
        await r2.put("articles/art_del/metadata.json", b'{"key": "val"}')
        await r2.put("articles/art_del/thumbnail.webp", b"WEBP")
        await r2.put("articles/art_del/audio.mp3", b"MP3DATA")

        # Also add an object for a DIFFERENT article to ensure it is not deleted
        await r2.put("articles/other_art/content.html", b"<p>other</p>")

        await delete_article_content(r2, "art_del")

        # All objects for art_del should be gone
        assert await r2.get("articles/art_del/content.html") is None
        assert await r2.get("articles/art_del/metadata.json") is None
        assert await r2.get("articles/art_del/thumbnail.webp") is None
        assert await r2.get("articles/art_del/audio.mp3") is None

        # Other article's content should be untouched
        other = await r2.get("articles/other_art/content.html")
        assert other is not None

    async def test_deletes_image_subdirectory(self) -> None:
        """delete_article_content removes images stored under the article prefix."""
        r2 = MockR2()
        await r2.put("articles/art_img/content.html", b"<p>html</p>")
        await r2.put("articles/art_img/images/abc123.webp", b"IMG1")
        await r2.put("articles/art_img/images/def456.webp", b"IMG2")

        await delete_article_content(r2, "art_img")

        assert await r2.get("articles/art_img/content.html") is None
        assert await r2.get("articles/art_img/images/abc123.webp") is None
        assert await r2.get("articles/art_img/images/def456.webp") is None

    async def test_no_objects_is_noop(self) -> None:
        """delete_article_content on an article with no R2 objects does not raise."""
        r2 = MockR2()
        # Should not raise
        await delete_article_content(r2, "nonexistent_article")


# ---------------------------------------------------------------------------
# delete_article_content() — paginated listing (truncated=True)
# ---------------------------------------------------------------------------


class TestDeleteArticleContentPaginated:
    async def test_handles_truncated_listing(self) -> None:
        """delete_article_content follows pagination when R2 list is truncated."""
        r2 = MockR2()

        # Populate enough objects to trigger pagination with a small page size.
        # The MockR2.list() uses limit=1000 by default, so we need to test with
        # a custom R2 that returns truncated results.
        keys_to_store = [f"articles/art_pag/file_{i:03d}.txt" for i in range(5)]
        for k in keys_to_store:
            await r2.put(k, b"data")

        # Override R2 list to simulate pagination with page_size=2
        original_list = r2.list

        call_count = 0

        async def _paginated_list(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            # Use a small effective page size by modifying kwargs
            kwargs["limit"] = 2
            return await original_list(**kwargs)

        r2.list = _paginated_list

        await delete_article_content(r2, "art_pag")

        # All objects should be deleted
        for k in keys_to_store:
            assert await r2.get(k) is None

        # list() should have been called multiple times (pagination)
        assert call_count >= 2


# ---------------------------------------------------------------------------
# Missing key returns None
# ---------------------------------------------------------------------------


class TestMissingKeyReturnsNone:
    async def test_get_content_on_empty_r2(self) -> None:
        """get_content returns None from a completely empty R2 bucket."""
        r2 = MockR2()
        assert await get_content(r2, "articles/nope/content.html") is None

    async def test_get_metadata_on_empty_r2(self) -> None:
        """get_metadata returns None from a completely empty R2 bucket."""
        r2 = MockR2()
        assert await get_metadata(r2, "nope") is None

    async def test_get_content_after_deletion(self) -> None:
        """get_content returns None after the object has been deleted."""
        r2 = MockR2()
        await r2.put("articles/art_x/content.html", b"<p>hi</p>")
        await delete_article_content(r2, "art_x")
        assert await get_content(r2, "articles/art_x/content.html") is None


# ---------------------------------------------------------------------------
# delete_non_audio_content() — selective cleanup preserving audio
# ---------------------------------------------------------------------------


class TestDeleteNonAudioContent:
    async def test_preserves_audio_files(self) -> None:
        """delete_non_audio_content keeps audio.ogg, audio.mp3, audio.wav, and timing."""
        r2 = MockR2()
        await r2.put("articles/art_re/content.html", b"<p>html</p>")
        await r2.put("articles/art_re/metadata.json", b"{}")
        await r2.put("articles/art_re/thumbnail.webp", b"WEBP")
        await r2.put("articles/art_re/images/abc.webp", b"IMG")
        await r2.put("articles/art_re/audio.ogg", b"AUDIO_OGG")
        await r2.put("articles/art_re/audio.mp3", b"AUDIO_MP3")
        await r2.put("articles/art_re/audio.wav", b"AUDIO_WAV")
        await r2.put("articles/art_re/audio-timing.json", b"TIMING")

        await delete_non_audio_content(r2, "art_re")

        # Non-audio content should be deleted
        assert await r2.get("articles/art_re/content.html") is None
        assert await r2.get("articles/art_re/metadata.json") is None
        assert await r2.get("articles/art_re/thumbnail.webp") is None
        assert await r2.get("articles/art_re/images/abc.webp") is None

        # Audio files should be preserved
        assert await r2.get("articles/art_re/audio.ogg") is not None
        assert await r2.get("articles/art_re/audio.mp3") is not None
        assert await r2.get("articles/art_re/audio.wav") is not None
        assert await r2.get("articles/art_re/audio-timing.json") is not None

    async def test_does_not_affect_other_articles(self) -> None:
        """delete_non_audio_content only touches the specified article."""
        r2 = MockR2()
        await r2.put("articles/art_a/content.html", b"A")
        await r2.put("articles/art_b/content.html", b"B")

        await delete_non_audio_content(r2, "art_a")

        assert await r2.get("articles/art_a/content.html") is None
        assert await r2.get("articles/art_b/content.html") is not None

    async def test_noop_on_empty_prefix(self) -> None:
        """delete_non_audio_content does not raise when no objects exist."""
        r2 = MockR2()
        await delete_non_audio_content(r2, "nonexistent")

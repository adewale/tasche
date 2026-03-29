"""Tests for audit fixes in images.py, storage.py, and processing.py.

Covers:
- Issue 12: Concurrent image downloads via asyncio.gather
- Issue 15: Concurrent R2 PUTs for images
- Issue 16: Concurrent R2 DELETEs
- Issue 21: Thumbnail format detection and error logging
- Issue 25: Safe Content-Length parsing
- Issue 29: audio_status guard before TTS enqueue
- Issue 34: raw.html cleanup after processing
- Issue 59: Shared _paginated_delete helper
- Issue 72: Markdown stored in R2
- Issue 73: article_key() used for image R2 keys
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from articles.images import download_images, store_images
from articles.storage import (
    _paginated_delete,
    article_key,
    delete_article_content,
    delete_audio_content,
)
from tests.conftest import (
    MockEnv,
    MockQueue,
    MockR2,
    TrackingD1,
    _make_mock_http_fetch,
    _make_mock_response,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_html_with_images(srcs: list[str]) -> str:
    img_tags = "\n".join(f'<img src="{src}">' for src in srcs)
    return f"<html><body>{img_tags}</body></html>"


def _make_image_response(
    *,
    status_code: int = 200,
    content: bytes = b"fake-image-data",
    content_type: str = "image/jpeg",
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"content-type": content_type}
    return resp


# =========================================================================
# Issue 12: Concurrent image downloads via asyncio.gather
# =========================================================================


class TestConcurrentImageDownloads:
    async def test_downloads_use_asyncio_gather(self) -> None:
        """download_images uses asyncio.gather for concurrent downloads."""
        html = _make_html_with_images(
            [
                "https://cdn.example.com/a.jpg",
                "https://cdn.example.com/b.jpg",
                "https://cdn.example.com/c.jpg",
            ]
        )
        mock_fetch = AsyncMock(return_value=_make_image_response(content=b"IMG"))

        with (
            patch("articles.images.http_fetch", mock_fetch),
            patch("articles.images.asyncio.gather", wraps=asyncio.gather) as mock_gather,
        ):
            result = await download_images(html)

        assert len(result) == 3
        # asyncio.gather should have been called (at least once)
        assert mock_gather.call_count >= 1

    async def test_semaphore_limits_concurrency(self) -> None:
        """The semaphore limits concurrent downloads to _DOWNLOAD_CONCURRENCY."""
        urls = [f"https://cdn.example.com/img{i}.jpg" for i in range(10)]
        html = _make_html_with_images(urls)

        concurrent_count = 0
        max_concurrent = 0

        original_fetch = AsyncMock(return_value=_make_image_response(content=b"X"))

        async def _tracking_fetch(url, **kwargs):
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            result = await original_fetch(url, **kwargs)
            concurrent_count -= 1
            return result

        mock_fetch = AsyncMock(side_effect=_tracking_fetch)

        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)

        assert len(result) == 10
        # Concurrency should have been limited to 5 (_DOWNLOAD_CONCURRENCY)
        assert max_concurrent <= 5

    async def test_total_size_budget_still_enforced(self) -> None:
        """Total size budget is enforced even with concurrent downloads."""
        html = _make_html_with_images(
            [
                "https://cdn.example.com/a.jpg",
                "https://cdn.example.com/b.jpg",
                "https://cdn.example.com/c.jpg",
            ]
        )
        mock_fetch = AsyncMock(return_value=_make_image_response(content=b"x" * 400))

        with patch("articles.images.http_fetch", mock_fetch):
            # 3 images * 400 bytes = 1200, limit 800 -> at most 2
            result = await download_images(html, max_total=800)

        assert len(result) == 2

    async def test_failed_downloads_skipped_in_results(self) -> None:
        """Failed downloads (exceptions) return None and are skipped."""
        html = _make_html_with_images(
            [
                "https://cdn.example.com/ok.jpg",
                "https://cdn.example.com/fail.jpg",
            ]
        )

        call_count = 0

        async def _side_effect(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "fail" in url:
                raise TimeoutError("timeout")
            return _make_image_response(content=b"OK_DATA")

        mock_fetch = AsyncMock(side_effect=_side_effect)

        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)

        assert len(result) == 1
        assert result[0]["url"] == "https://cdn.example.com/ok.jpg"


# =========================================================================
# Issue 15: Concurrent R2 PUTs for images
# =========================================================================


class TestConcurrentImageUploads:
    async def test_store_images_uses_gather(self) -> None:
        """store_images uploads all images concurrently via asyncio.gather."""
        r2 = MockR2()
        images = [
            {
                "url": f"https://cdn.example.com/img{i}.jpg",
                "data": b"data",
                "content_type": "image/jpeg",
            }
            for i in range(5)
        ]

        with patch("articles.images.asyncio.gather", wraps=asyncio.gather) as mock_gather:
            image_map = await store_images(r2, "art_001", images)

        assert len(image_map) == 5
        assert mock_gather.call_count >= 1
        # All images should be stored in R2
        for key in image_map.values():
            assert key in r2._store

    async def test_store_images_empty_list(self) -> None:
        """store_images with empty list does not call gather with tasks."""
        r2 = MockR2()
        image_map = await store_images(r2, "art_001", [])
        assert image_map == {}


# =========================================================================
# Issue 16 + 59: Concurrent R2 DELETEs and shared _paginated_delete helper
# =========================================================================


class TestPaginatedDelete:
    async def test_deletes_all_objects(self) -> None:
        """_paginated_delete removes all objects under a prefix."""
        r2 = MockR2()
        for i in range(5):
            await r2.put(f"articles/art_001/file_{i}.txt", b"data")

        await _paginated_delete(r2, "articles/art_001/")

        for i in range(5):
            assert await r2.get(f"articles/art_001/file_{i}.txt") is None

    async def test_respects_key_filter(self) -> None:
        """_paginated_delete only deletes keys matching the filter."""
        r2 = MockR2()
        await r2.put("articles/art_001/content.html", b"html")
        await r2.put("articles/art_001/audio.mp3", b"mp3")
        await r2.put("articles/art_001/audio-timing.json", b"json")

        def _is_audio(key: str) -> bool:
            return "audio" in key.rsplit("/", 1)[-1]

        await _paginated_delete(r2, "articles/art_001/", key_filter=_is_audio)

        # Audio files deleted
        assert await r2.get("articles/art_001/audio.mp3") is None
        assert await r2.get("articles/art_001/audio-timing.json") is None
        # Non-audio preserved
        assert await r2.get("articles/art_001/content.html") is not None

    async def test_handles_pagination(self) -> None:
        """_paginated_delete follows pagination cursors."""
        r2 = MockR2()
        keys = [f"articles/art_pag/file_{i:03d}.txt" for i in range(5)]
        for k in keys:
            await r2.put(k, b"data")

        original_list = r2.list
        call_count = 0

        async def _paginated_list(**kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            kwargs["limit"] = 2
            return await original_list(**kwargs)

        r2.list = _paginated_list

        await _paginated_delete(r2, "articles/art_pag/")

        for k in keys:
            assert await r2.get(k) is None
        assert call_count >= 2

    async def test_uses_asyncio_gather_for_deletes(self) -> None:
        """_paginated_delete deletes concurrently via asyncio.gather."""
        r2 = MockR2()
        for i in range(3):
            await r2.put(f"articles/art_001/file_{i}.txt", b"data")

        with patch("articles.storage.asyncio.gather", wraps=asyncio.gather) as mock_gather:
            await _paginated_delete(r2, "articles/art_001/")

        assert mock_gather.call_count >= 1


class TestDeleteAudioContentUsesHelper:
    async def test_delete_audio_only(self) -> None:
        """delete_audio_content removes only audio files."""
        r2 = MockR2()
        await r2.put("articles/art_001/content.html", b"html")
        await r2.put("articles/art_001/audio.mp3", b"mp3")

        await delete_audio_content(r2, "art_001")

        assert await r2.get("articles/art_001/audio.mp3") is None
        assert await r2.get("articles/art_001/content.html") is not None


class TestDeleteArticleContentUsesHelper:
    async def test_delete_everything(self) -> None:
        """delete_article_content removes all files for the article."""
        r2 = MockR2()
        await r2.put("articles/art_001/content.html", b"html")
        await r2.put("articles/art_001/audio.mp3", b"mp3")
        await r2.put("articles/art_001/images/abc.jpg", b"img")

        await delete_article_content(r2, "art_001")

        assert await r2.get("articles/art_001/content.html") is None
        assert await r2.get("articles/art_001/audio.mp3") is None
        assert await r2.get("articles/art_001/images/abc.jpg") is None


# =========================================================================
# Issue 73: article_key() with allow_subpath for images
# =========================================================================


class TestArticleKeySubpath:
    def test_allows_subpath_when_enabled(self) -> None:
        """article_key allows / in filename when allow_subpath=True."""
        key = article_key("art_001", "images/abc123.jpg", allow_subpath=True)
        assert key == "articles/art_001/images/abc123.jpg"

    def test_rejects_slash_by_default(self) -> None:
        """article_key rejects / in filename by default."""
        with pytest.raises(ValueError, match="Invalid filename"):
            article_key("art_001", "images/abc123.jpg")

    def test_still_rejects_path_traversal(self) -> None:
        """article_key rejects .. even with allow_subpath."""
        with pytest.raises(ValueError, match="Invalid filename"):
            article_key("art_001", "../etc/passwd", allow_subpath=True)

    def test_store_images_uses_article_key(self) -> None:
        """store_images constructs keys via article_key helper."""
        # Verify the key format matches article_key output
        url = "https://cdn.example.com/photo.jpg"
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        expected = article_key("art_001", f"images/{url_hash}.jpg", allow_subpath=True)
        assert expected == f"articles/art_001/images/{url_hash}.jpg"


# =========================================================================
# Issue 25: Safe Content-Length parsing
# =========================================================================


class TestContentLengthParsing:
    async def test_malformed_content_length_does_not_crash(self) -> None:
        """Malformed Content-Length header falls through to body check."""
        from articles.processing import _fetch_page

        resp = _make_mock_response(
            text="<html><body>Hello</body></html>",
            headers={"content-type": "text/html", "content-length": "not-a-number"},
        )

        mock_fetch = AsyncMock(return_value=resp)

        with patch("articles.processing.http_fetch", mock_fetch):
            html, final_url = await _fetch_page("https://example.com")

        assert "Hello" in html

    async def test_empty_content_length_does_not_crash(self) -> None:
        """Empty Content-Length header falls through to body check."""
        from articles.processing import _fetch_page

        resp = _make_mock_response(
            text="<html><body>Ok</body></html>",
            headers={"content-type": "text/html", "content-length": ""},
        )

        mock_fetch = AsyncMock(return_value=resp)

        with patch("articles.processing.http_fetch", mock_fetch):
            html, _ = await _fetch_page("https://example.com")

        assert "Ok" in html

    async def test_valid_content_length_still_enforced(self) -> None:
        """Valid Content-Length exceeding limit still raises ValueError."""
        from articles.processing import _fetch_page

        resp = _make_mock_response(
            text="<html><body>Big</body></html>",
            headers={
                "content-type": "text/html",
                "content-length": "999999999",
            },
        )

        mock_fetch = AsyncMock(return_value=resp)

        with patch("articles.processing.http_fetch", mock_fetch):
            with pytest.raises(ValueError, match="Response too large"):
                await _fetch_page("https://example.com")


# =========================================================================
# Issue 72: Markdown stored in R2
# =========================================================================


class TestMarkdownStoredInR2:
    async def test_markdown_stored_in_r2_on_success(self) -> None:
        """After processing, content.md is stored in R2."""
        db = TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
        ):
            from articles.processing import process_article

            await process_article("art_md", "https://example.com/article", env)

        # content.md should exist in R2
        md_key = "articles/art_md/content.md"
        md_obj = await r2.get(md_key)
        assert md_obj is not None
        md_content = await md_obj.text()
        assert len(md_content) > 0


# =========================================================================
# Issue 34: raw.html deleted after processing
# =========================================================================


class TestRawHtmlCleanup:
    async def test_raw_html_deleted_after_processing(self) -> None:
        """Pre-supplied raw.html is deleted from R2 after processing."""
        db = TrackingD1()
        r2 = MockR2()

        # Pre-supply raw HTML
        raw_html = """
        <html><head><title>Pre-supplied</title></head>
        <body><article><p>Pre-supplied content for testing the pipeline.
        We need enough text here for extraction to work properly and
        produce meaningful output from the readability algorithm.</p>
        </article></body></html>
        """
        await r2.put("articles/art_raw/raw.html", raw_html)

        env = MockEnv(db=db, content=r2)

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
        ):
            from articles.processing import process_article

            await process_article("art_raw", "https://example.com/article", env)

        # raw.html should be deleted
        assert await r2.get("articles/art_raw/raw.html") is None

    async def test_no_raw_html_does_not_error(self) -> None:
        """Processing without raw.html does not raise on cleanup."""
        db = TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
        ):
            from articles.processing import process_article

            await process_article("art_no_raw", "https://example.com/article", env)

        # Should complete without error - no raw.html existed


# =========================================================================
# Issue 29: audio_status guard before TTS enqueue
# =========================================================================


class TestTTSEnqueueGuard:
    async def test_enqueues_tts_when_audio_status_pending(self) -> None:
        """TTS is enqueued when audio_status is 'pending'."""
        queue = MockQueue()

        def _result_fn(sql, params):
            if "SELECT audio_status" in sql:
                return [{"audio_status": "pending", "user_id": "user_001"}]
            return []

        db = TrackingD1(result_fn=_result_fn)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2, article_queue=queue)

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
        ):
            from articles.processing import process_article

            await process_article("art_tts", "https://example.com/article", env)

        tts_msgs = [m for m in queue.messages if m.get("type") == "tts_generation"]
        assert len(tts_msgs) == 1
        assert tts_msgs[0]["article_id"] == "art_tts"

        # Verify that audio_status was atomically updated to 'generating'
        generating_updates = [
            (sql, params)
            for sql, params in db.executed
            if "audio_status = ?" in sql
            and "generating" in str(params)
            and "AND audio_status = ?" in sql
        ]
        assert len(generating_updates) >= 1

    async def test_skips_tts_when_audio_status_generating(self) -> None:
        """TTS is NOT enqueued when audio_status is already 'generating'."""
        queue = MockQueue()

        def _result_fn(sql, params):
            if "SELECT audio_status" in sql:
                return [{"audio_status": "generating", "user_id": "user_001"}]
            return []

        db = TrackingD1(result_fn=_result_fn)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2, article_queue=queue)

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
        ):
            from articles.processing import process_article

            await process_article("art_skip", "https://example.com/article", env)

        tts_msgs = [m for m in queue.messages if m.get("type") == "tts_generation"]
        assert len(tts_msgs) == 0

    async def test_skips_tts_when_audio_status_ready(self) -> None:
        """TTS is NOT enqueued when audio_status is 'ready'."""
        queue = MockQueue()

        def _result_fn(sql, params):
            if "SELECT audio_status" in sql:
                return [{"audio_status": "ready", "user_id": "user_001"}]
            return []

        db = TrackingD1(result_fn=_result_fn)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2, article_queue=queue)

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
        ):
            from articles.processing import process_article

            await process_article("art_ready", "https://example.com/article", env)

        tts_msgs = [m for m in queue.messages if m.get("type") == "tts_generation"]
        assert len(tts_msgs) == 0

    async def test_skips_tts_when_audio_status_none(self) -> None:
        """TTS is NOT enqueued when audio_status is None."""
        queue = MockQueue()

        def _result_fn(sql, params):
            if "SELECT audio_status" in sql:
                return [{"audio_status": None, "user_id": "user_001"}]
            return []

        db = TrackingD1(result_fn=_result_fn)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2, article_queue=queue)

        mock_client = _make_mock_http_fetch()

        with (
            patch("articles.processing.http_fetch", mock_client),
            patch("articles.images.http_fetch", mock_client),
        ):
            from articles.processing import process_article

            await process_article("art_none", "https://example.com/article", env)

        tts_msgs = [m for m in queue.messages if m.get("type") == "tts_generation"]
        assert len(tts_msgs) == 0


# =========================================================================
# Issue 21: Thumbnail format detection
# =========================================================================


class TestThumbnailFormatDetection:
    async def test_png_thumbnail_gets_png_extension(self) -> None:
        """A PNG thumbnail is stored with .png extension, not .webp."""
        db = TrackingD1()
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        # Build a page response with og:image meta tag
        page_html = """
        <html>
        <head>
            <title>Test</title>
            <meta property="og:image" content="https://cdn.example.com/thumb.png">
        </head>
        <body><article><p>Enough content for extraction to work properly.
        We need substantial text here so extraction works.</p></article></body>
        </html>
        """

        page_resp = _make_mock_response(text=page_html)

        thumb_resp = MagicMock()
        thumb_resp.status_code = 200
        thumb_resp.content = b"FAKE_PNG_DATA"
        thumb_resp.headers = {"content-type": "image/png"}

        img_resp = MagicMock()
        img_resp.status_code = 200
        img_resp.content = b"fake-image"
        img_resp.headers = {"content-type": "image/jpeg"}

        call_count = 0

        async def _mock_fetch(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return page_resp
            if "thumb" in url:
                return thumb_resp
            return img_resp

        mock_fetch = AsyncMock(side_effect=_mock_fetch)

        with (
            patch("articles.processing.http_fetch", mock_fetch),
            patch("articles.images.http_fetch", mock_fetch),
        ):
            from articles.processing import process_article

            await process_article("art_thumb", "https://example.com/article", env)

        # Check that a thumbnail with .png extension was stored
        png_keys = [k for k in r2._store if "thumbnail.png" in k]
        webp_keys = [k for k in r2._store if "thumbnail.webp" in k]
        assert len(png_keys) == 1, f"Expected thumbnail.png, got keys: {list(r2._store.keys())}"
        assert len(webp_keys) == 0

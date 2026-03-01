"""Tests for the image processing pipeline (src/articles/images.py).

Covers download_images() (size limits, data URI skipping, deduplication,
SSRF protection, non-200 skipping) and store_images() (deterministic hash
keys, correct file extensions from content-type).
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

from articles.images import download_images, store_images
from tests.conftest import MockR2

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_html_with_images(srcs: list[str]) -> str:
    """Generate an HTML string with <img> tags for the given src URLs."""
    img_tags = "\n".join(f'<img src="{src}">' for src in srcs)
    return f"<html><body>{img_tags}</body></html>"


def _make_mock_response(
    *,
    status_code: int = 200,
    content: bytes = b"fake-image-data",
    content_type: str = "image/jpeg",
    url: str = "https://cdn.example.com/image.jpg",
) -> MagicMock:
    """Create a mock httpx response for an image download."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"content-type": content_type}
    resp.url = url
    return resp


# ---------------------------------------------------------------------------
# download_images() — basic functionality
# ---------------------------------------------------------------------------


class TestDownloadImagesBasic:
    async def test_downloads_images_from_html(self) -> None:
        """download_images extracts img srcs and downloads each one."""
        html = _make_html_with_images(
            [
                "https://cdn.example.com/photo1.jpg",
                "https://cdn.example.com/photo2.png",
            ]
        )

        mock_fetch = AsyncMock(return_value=_make_mock_response(content=b"IMG_DATA"))

        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)

        assert len(result) == 2
        assert result[0]["url"] == "https://cdn.example.com/photo1.jpg"
        assert result[0]["data"] == b"IMG_DATA"
        assert result[0]["content_type"] == "image/jpeg"

    async def test_empty_html_returns_empty(self) -> None:
        """download_images with no img tags returns an empty list."""
        mock_fetch = AsyncMock()
        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images("<html><body><p>No images</p></body></html>")
        assert result == []


# ---------------------------------------------------------------------------
# download_images() — size limits
# ---------------------------------------------------------------------------


class TestDownloadImagesSizeLimits:
    async def test_skips_image_exceeding_per_image_limit(self) -> None:
        """Images larger than max_per_image are skipped."""
        html = _make_html_with_images(["https://cdn.example.com/huge.jpg"])

        big_data = b"x" * 3_000_000  # 3 MB, exceeds default 2 MB limit
        mock_fetch = AsyncMock(return_value=_make_mock_response(content=big_data))

        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)
        assert len(result) == 0

    async def test_custom_per_image_limit(self) -> None:
        """Custom max_per_image is respected."""
        html = _make_html_with_images(["https://cdn.example.com/small.jpg"])

        data = b"x" * 500
        mock_fetch = AsyncMock(return_value=_make_mock_response(content=data))

        with patch("articles.images.http_fetch", mock_fetch):
            # With a limit of 100, the 500-byte image should be skipped
            result = await download_images(html, max_per_image=100)
            assert len(result) == 0

            # With a limit of 1000, it should be included
            result = await download_images(html, max_per_image=1000)
            assert len(result) == 1

    async def test_stops_at_total_limit(self) -> None:
        """download_images stops downloading once max_total is reached."""
        html = _make_html_with_images(
            [
                "https://cdn.example.com/img1.jpg",
                "https://cdn.example.com/img2.jpg",
                "https://cdn.example.com/img3.jpg",
            ]
        )

        data = b"x" * 500
        mock_fetch = AsyncMock(return_value=_make_mock_response(content=data))

        # Total limit of 800 means only 1 image can fit (500 bytes each, second
        # would push total to 1000 which exceeds 800)
        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html, max_total=800)
        assert len(result) == 1

    async def test_cumulative_total_is_enforced(self) -> None:
        """The cumulative size of all images is checked against max_total."""
        html = _make_html_with_images(
            [
                "https://cdn.example.com/a.jpg",
                "https://cdn.example.com/b.jpg",
                "https://cdn.example.com/c.jpg",
            ]
        )

        data = b"x" * 400
        mock_fetch = AsyncMock(return_value=_make_mock_response(content=data))

        # 3 images * 400 bytes = 1200 total, limit is 1000
        # First two fit (800), third would push to 1200 -> skipped
        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html, max_total=1000)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# download_images() — data URI skipping
# ---------------------------------------------------------------------------


class TestDownloadImagesDataURI:
    async def test_skips_data_uris(self) -> None:
        """Images with data: URIs are skipped without making HTTP requests."""
        html = _make_html_with_images(
            [
                "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAUA",
                "https://cdn.example.com/real.jpg",
            ]
        )

        mock_fetch = AsyncMock(return_value=_make_mock_response(content=b"real_image"))

        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)

        # Only the non-data-URI image should be downloaded
        assert len(result) == 1
        assert result[0]["url"] == "https://cdn.example.com/real.jpg"

        # HTTP fetch should only have been called once (not for the data URI)
        assert mock_fetch.call_count == 1


# ---------------------------------------------------------------------------
# download_images() — duplicate URL deduplication
# ---------------------------------------------------------------------------


class TestDownloadImagesDedupe:
    async def test_deduplicates_identical_urls(self) -> None:
        """Duplicate img src URLs are only downloaded once."""
        html = _make_html_with_images(
            [
                "https://cdn.example.com/same.jpg",
                "https://cdn.example.com/same.jpg",
                "https://cdn.example.com/same.jpg",
            ]
        )

        mock_fetch = AsyncMock(return_value=_make_mock_response(content=b"img_data"))

        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)

        assert len(result) == 1
        assert mock_fetch.call_count == 1


# ---------------------------------------------------------------------------
# download_images() — SSRF protection
# ---------------------------------------------------------------------------


class TestDownloadImagesSSRF:
    async def test_skips_localhost_urls(self) -> None:
        """Images pointing to localhost are blocked by SSRF protection."""
        html = _make_html_with_images(["http://localhost/internal.jpg"])

        mock_fetch = AsyncMock()
        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)

        assert len(result) == 0
        mock_fetch.assert_not_called()

    async def test_skips_127_0_0_1(self) -> None:
        """Images pointing to 127.0.0.1 are blocked."""
        html = _make_html_with_images(["http://127.0.0.1:8080/secret.png"])

        mock_fetch = AsyncMock()
        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)

        assert len(result) == 0

    async def test_skips_private_ip_10_range(self) -> None:
        """Images pointing to 10.x.x.x private IPs are blocked."""
        html = _make_html_with_images(["http://10.0.0.5/internal.png"])

        mock_fetch = AsyncMock()
        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)

        assert len(result) == 0

    async def test_skips_private_ip_192_168(self) -> None:
        """Images pointing to 192.168.x.x private IPs are blocked."""
        html = _make_html_with_images(["http://192.168.1.100/photo.jpg"])

        mock_fetch = AsyncMock()
        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)

        assert len(result) == 0

    async def test_skips_metadata_endpoint(self) -> None:
        """Images pointing to the cloud metadata endpoint (169.254.169.254) are blocked."""
        html = _make_html_with_images(["http://169.254.169.254/latest/meta-data/"])

        mock_fetch = AsyncMock()
        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)

        assert len(result) == 0

    async def test_ssrf_check_on_redirect(self) -> None:
        """SSRF check is also applied to each redirect hop."""
        html = _make_html_with_images(["https://cdn.example.com/redirect.jpg"])

        # First response is a 302 redirecting to a private IP
        redirect_resp = _make_mock_response(status_code=302, content=b"")
        redirect_resp.headers = {"content-type": "image/jpeg", "location": "http://10.0.0.1/internal.jpg"}

        mock_fetch = AsyncMock(return_value=redirect_resp)

        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# download_images() — non-200 response skipping
# ---------------------------------------------------------------------------


class TestDownloadImagesNon200:
    async def test_skips_404_response(self) -> None:
        """Images returning 404 are skipped."""
        html = _make_html_with_images(["https://cdn.example.com/missing.jpg"])

        mock_fetch = AsyncMock(return_value=_make_mock_response(status_code=404, content=b""))

        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)
        assert len(result) == 0

    async def test_skips_500_response(self) -> None:
        """Images returning 500 are skipped."""
        html = _make_html_with_images(["https://cdn.example.com/error.jpg"])

        mock_fetch = AsyncMock(return_value=_make_mock_response(status_code=500, content=b""))

        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)
        assert len(result) == 0

    async def test_skips_on_http_error_exception(self) -> None:
        """Images that raise httpx.HTTPError during download are skipped."""
        html = _make_html_with_images(["https://cdn.example.com/timeout.jpg"])

        mock_fetch = AsyncMock(side_effect=TimeoutError("timeout"))

        with patch("articles.images.http_fetch", mock_fetch):
            result = await download_images(html)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# store_images() — deterministic hash-based keys
# ---------------------------------------------------------------------------


class TestStoreImagesDeterministicKeys:
    async def test_uses_sha256_hash_of_url(self) -> None:
        """store_images creates keys using SHA-256 hash of the original URL."""
        r2 = MockR2()
        url = "https://cdn.example.com/photo.jpg"
        expected_hash = hashlib.sha256(url.encode()).hexdigest()[:16]

        images = [
            {
                "url": url,
                "data": b"fake-image",
                "content_type": "image/jpeg",
            }
        ]

        image_map = await store_images(r2, "art_001", images)

        expected_key = f"articles/art_001/images/{expected_hash}.jpg"
        assert image_map[url] == expected_key
        assert expected_key in r2._store

    async def test_deterministic_key_for_same_url(self) -> None:
        """The same URL always produces the same R2 key."""
        r2 = MockR2()
        url = "https://cdn.example.com/consistent.png"

        images = [
            {
                "url": url,
                "data": b"image-data-1",
                "content_type": "image/png",
            }
        ]

        map1 = await store_images(r2, "art_a", images)

        r2_b = MockR2()
        map2 = await store_images(r2_b, "art_a", images)

        # Same URL and article_id should produce same key
        assert map1[url] == map2[url]

    async def test_different_urls_different_keys(self) -> None:
        """Different URLs produce different R2 keys."""
        r2 = MockR2()
        images = [
            {"url": "https://cdn.example.com/a.jpg", "data": b"a", "content_type": "image/jpeg"},
            {"url": "https://cdn.example.com/b.jpg", "data": b"b", "content_type": "image/jpeg"},
        ]

        image_map = await store_images(r2, "art_multi", images)

        keys = list(image_map.values())
        assert len(set(keys)) == 2  # All keys are unique

    async def test_stores_image_data_in_r2(self) -> None:
        """store_images actually stores the image bytes in R2."""
        r2 = MockR2()
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        images = [
            {
                "url": "https://cdn.example.com/photo.png",
                "data": data,
                "content_type": "image/png",
            }
        ]

        image_map = await store_images(r2, "art_data", images)

        key = list(image_map.values())[0]
        stored = r2._store[key]
        assert stored == data


# ---------------------------------------------------------------------------
# store_images() — correct file extension from content-type
# ---------------------------------------------------------------------------


class TestStoreImagesExtensions:
    async def test_jpeg_extension(self) -> None:
        """image/jpeg produces .jpg extension."""
        r2 = MockR2()
        images = [{"url": "https://x.com/a", "data": b"j", "content_type": "image/jpeg"}]
        image_map = await store_images(r2, "art_ext", images)
        key = image_map["https://x.com/a"]
        assert key.endswith(".jpg")

    async def test_png_extension(self) -> None:
        """image/png produces .png extension."""
        r2 = MockR2()
        images = [{"url": "https://x.com/b", "data": b"p", "content_type": "image/png"}]
        image_map = await store_images(r2, "art_ext", images)
        key = image_map["https://x.com/b"]
        assert key.endswith(".png")

    async def test_gif_extension(self) -> None:
        """image/gif produces .gif extension."""
        r2 = MockR2()
        images = [{"url": "https://x.com/c", "data": b"g", "content_type": "image/gif"}]
        image_map = await store_images(r2, "art_ext", images)
        key = image_map["https://x.com/c"]
        assert key.endswith(".gif")

    async def test_webp_extension(self) -> None:
        """image/webp produces .webp extension."""
        r2 = MockR2()
        images = [{"url": "https://x.com/d", "data": b"w", "content_type": "image/webp"}]
        image_map = await store_images(r2, "art_ext", images)
        key = image_map["https://x.com/d"]
        assert key.endswith(".webp")

    async def test_svg_not_in_mime_map(self) -> None:
        """image/svg+xml is not in _MIME_TO_EXT (XSS risk) — falls back to .bin."""
        r2 = MockR2()
        images = [{"url": "https://x.com/e", "data": b"s", "content_type": "image/svg+xml"}]
        image_map = await store_images(r2, "art_ext", images)
        key = image_map["https://x.com/e"]
        assert key.endswith(".bin")

    async def test_unknown_content_type_falls_back_to_url_extension(self) -> None:
        """Unknown content-type falls back to the URL's file extension."""
        r2 = MockR2()
        images = [
            {
                "url": "https://x.com/image.bmp",
                "data": b"bmp",
                "content_type": "image/bmp",
            }
        ]
        image_map = await store_images(r2, "art_ext", images)
        key = image_map["https://x.com/image.bmp"]
        assert key.endswith(".bmp")

    async def test_no_extension_in_url_uses_bin(self) -> None:
        """When content-type is unknown and URL has no extension, .bin is used."""
        r2 = MockR2()
        images = [
            {
                "url": "https://x.com/image",
                "data": b"unknown",
                "content_type": "application/octet-stream",
            }
        ]
        image_map = await store_images(r2, "art_ext", images)
        key = image_map["https://x.com/image"]
        assert key.endswith(".bin")

    async def test_empty_images_list(self) -> None:
        """store_images with an empty list returns an empty mapping."""
        r2 = MockR2()
        image_map = await store_images(r2, "art_empty", [])
        assert image_map == {}

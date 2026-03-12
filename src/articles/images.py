"""Image processing for the article processing pipeline.

Downloads images referenced in article HTML, stores them to R2, and
returns a mapping of original URLs to R2 keys for path rewriting.

Note: Actual WebP conversion would require Pillow (available in Pyodide).
For now, images are stored as-is with a .webp extension since runtime
conversion requires testing in the actual Pyodide environment.
"""

from __future__ import annotations

import asyncio
import hashlib
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from articles.storage import article_key
from articles.urls import _is_private_hostname
from wrappers import http_fetch

# Maximum number of concurrent image downloads
_DOWNLOAD_CONCURRENCY = 5

# Mapping of MIME types to file extensions for stored images
_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


async def download_images(
    html: str,
    *,
    max_per_image: int = 2_000_000,
    max_total: int = 10_000_000,
) -> list[dict]:
    """Extract ``<img>`` sources from HTML and download each image.

    Skips images that exceed *max_per_image* bytes or would push the
    cumulative total past *max_total*.  Also skips data URIs and images
    that fail to download.

    Parameters
    ----------
    html:
        HTML string containing ``<img>`` tags.
    max_per_image:
        Maximum allowed size per image in bytes (default 2 MB).
    max_total:
        Maximum total size for all images combined (default 10 MB).

    Returns
    -------
    list[dict]
        Each dict has keys: ``url`` (original src), ``data`` (bytes),
        ``content_type`` (str from response header or ``"image/jpeg"`` default).
    """
    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()

    for img in soup.find_all("img"):
        src = img.get("src", "").strip()
        if not src or src.startswith("data:") or src in seen:
            continue
        seen.add(src)
        urls.append(src)

    # Filter URLs for SSRF before downloading
    safe_urls: list[str] = []
    for url in urls:
        try:
            parsed = urlparse(url)
            if parsed.scheme and parsed.scheme not in ("http", "https"):
                continue
            if parsed.hostname and _is_private_hostname(parsed.hostname):
                continue
        except Exception:
            continue
        safe_urls.append(url)

    # Download images concurrently with a semaphore to limit parallelism
    semaphore = asyncio.Semaphore(_DOWNLOAD_CONCURRENCY)

    async def _download_one(url: str) -> dict | None:
        async with semaphore:
            try:
                resp = await http_fetch(url, timeout=15.0, follow_redirects=False)
                # Follow redirects manually, checking each hop for SSRF
                hops = 0
                while resp.status_code in (301, 302, 303, 307, 308) and hops < 5:
                    location = resp.headers.get("location")
                    if not location:
                        break
                    redirect_parsed = urlparse(location)
                    if redirect_parsed.hostname and _is_private_hostname(
                        redirect_parsed.hostname
                    ):
                        break
                    resp = await http_fetch(location, timeout=15.0, follow_redirects=False)
                    hops += 1
                if resp.status_code != 200:
                    return None
            except Exception:
                return None

            data = resp.content
            if len(data) > max_per_image:
                return None

            content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            if not content_type.startswith("image/"):
                return None

            return {
                "url": url,
                "data": data,
                "content_type": content_type,
            }

    downloaded = await asyncio.gather(*[_download_one(u) for u in safe_urls])

    # Apply total size budget sequentially (order-preserving)
    results: list[dict] = []
    total_size = 0
    for item in downloaded:
        if item is None:
            continue
        if total_size >= max_total:
            break
        if total_size + len(item["data"]) > max_total:
            continue
        total_size += len(item["data"])
        results.append(item)

    return results


async def store_images(
    r2: object,
    article_id: str,
    images: list[dict],
) -> dict[str, str]:
    """Store downloaded images to R2 and return the URL-to-key mapping.

    Each image is stored at ``articles/{article_id}/images/{hash}.webp``
    where the hash is derived from the original URL for deterministic
    key generation.

    Parameters
    ----------
    r2:
        R2 bucket binding (``env.CONTENT``).
    article_id:
        The article ID for the R2 key prefix.
    images:
        List of image dicts from ``download_images``.

    Returns
    -------
    dict[str, str]
        Mapping of original URL -> R2 key.
    """
    image_map: dict[str, str] = {}
    upload_tasks: list[tuple[str, str, bytes]] = []

    for img in images:
        url = img["url"]
        data = img["data"]
        content_type = img.get("content_type", "")

        # Determine file extension from Content-Type
        ext = _MIME_TO_EXT.get(content_type, "")
        if not ext:
            # Fall back to the original URL extension, or .bin as last resort
            path = urlparse(url).path
            dot_pos = path.rfind(".")
            ext = path[dot_pos:].lower() if dot_pos != -1 else ".bin"
            # Sanitise: keep only short, alphanumeric extensions
            if len(ext) > 5 or not ext[1:].isalnum():
                ext = ".bin"

        # Deterministic hash from original URL
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        r2_key = article_key(article_id, f"images/{url_hash}{ext}", allow_subpath=True)

        image_map[url] = r2_key
        upload_tasks.append((url, r2_key, data))

    # Upload all images concurrently
    await asyncio.gather(*[r2.put(key, data) for _, key, data in upload_tasks])

    return image_map

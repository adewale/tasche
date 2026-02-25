"""Image processing for the article processing pipeline.

Downloads images referenced in article HTML, stores them to R2, and
returns a mapping of original URLs to R2 keys for path rewriting.

Note: Actual WebP conversion would require Pillow (available in Pyodide).
For now, images are stored as-is with a .webp extension since runtime
conversion requires testing in the actual Pyodide environment.
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from articles.urls import _is_private_hostname
from wrappers import http_fetch

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

    results: list[dict] = []
    total_size = 0

    for url in urls:
        if total_size >= max_total:
            break

        # SSRF protection: skip images pointing to private/internal URLs
        try:
            parsed = urlparse(url)
            if parsed.scheme and parsed.scheme not in ("http", "https"):
                continue
            if parsed.hostname and _is_private_hostname(parsed.hostname):
                continue
        except Exception:
            continue

        try:
            resp = await http_fetch(url, timeout=15.0, follow_redirects=True)
            if resp.status_code != 200:
                continue

            # SSRF protection: check final URL after redirects
            resp_parsed = urlparse(str(resp.url))
            if resp_parsed.hostname and _is_private_hostname(resp_parsed.hostname):
                continue
        except Exception:
            continue

        data = resp.content
        if len(data) > max_per_image:
            continue
        if total_size + len(data) > max_total:
            continue

        content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        if not content_type.startswith("image/"):
            continue
        total_size += len(data)

        results.append(
            {
                "url": url,
                "data": data,
                "content_type": content_type,
            }
        )

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
        r2_key = f"articles/{article_id}/images/{url_hash}{ext}"

        await r2.put(r2_key, data)
        image_map[url] = r2_key

    return image_map

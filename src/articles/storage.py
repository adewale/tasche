"""R2 storage operations for article content.

Provides helpers to store and retrieve HTML, Markdown, and metadata files
associated with a saved article.  All R2 keys follow the convention
``articles/{article_id}/{filename}``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any


def article_key(article_id: str, filename: str, *, allow_subpath: bool = False) -> str:
    """Generate an R2 object key for an article file.

    Parameters
    ----------
    article_id:
        The article's unique identifier.  Must not contain ``/`` or ``..``.
    filename:
        The filename (or subpath when *allow_subpath* is ``True``).
    allow_subpath:
        When ``True``, allows a single ``/`` in *filename* to support
        subdirectory paths like ``images/hash.ext``.  Path traversal
        (``..``) is still rejected.

    Returns
    -------
    str
        Key in the format ``articles/{article_id}/{filename}``.

    Raises
    ------
    ValueError
        If *filename* contains path traversal characters.
    """
    if "/" in article_id or ".." in article_id:
        raise ValueError(f"Invalid article_id: {article_id}")
    if ".." in filename:
        raise ValueError(f"Invalid filename: {filename}")
    if "/" in filename and not allow_subpath:
        raise ValueError(f"Invalid filename: {filename}")
    return f"articles/{article_id}/{filename}"


async def store_content(r2: Any, article_id: str, html: str) -> dict[str, str]:
    """Store HTML content in R2.

    Parameters
    ----------
    r2:
        The R2 bucket binding (``env.CONTENT``).
    article_id:
        The article's unique identifier.
    html:
        The article's HTML content.

    Returns
    -------
    dict
        A dict with ``html_key`` entry.
    """
    html_k = article_key(article_id, "content.html")

    await r2.put(html_k, html)

    return {"html_key": html_k}


async def get_content(r2: Any, key: str) -> str | None:
    """Retrieve text content from R2 by key.

    Parameters
    ----------
    r2:
        The R2 bucket binding.
    key:
        The R2 object key.

    Returns
    -------
    str or None
        The stored text content, or ``None`` if the object does not exist.
    """
    obj = await r2.get(key)
    if obj is None:
        return None
    return await obj.text()


async def store_metadata(r2: Any, article_id: str, metadata: dict[str, Any]) -> str:
    """Store article metadata as JSON in R2.

    Parameters
    ----------
    r2:
        The R2 bucket binding.
    article_id:
        The article's unique identifier.
    metadata:
        A dict of metadata to serialise and store.

    Returns
    -------
    str
        The R2 key where the metadata was stored.
    """
    key = article_key(article_id, "metadata.json")
    await r2.put(key, json.dumps(metadata))
    return key


async def get_metadata(r2: Any, article_id: str) -> dict[str, Any] | None:
    """Retrieve article metadata JSON from R2.

    Returns
    -------
    dict or None
        The stored metadata dict, or ``None`` if no metadata exists.
    """
    key = article_key(article_id, "metadata.json")
    obj = await r2.get(key)
    if obj is None:
        return None
    raw = await obj.text()
    return json.loads(raw)


async def _paginated_delete(
    r2: Any,
    prefix: str,
    *,
    key_filter: Any | None = None,
) -> None:
    """List R2 objects under *prefix* and delete them concurrently.

    Parameters
    ----------
    r2:
        The R2 bucket binding.
    prefix:
        The R2 key prefix to list under.
    key_filter:
        Optional callable ``(key: str) -> bool``.  When provided, only
        keys for which *key_filter* returns ``True`` are deleted.
        When ``None``, all keys under the prefix are deleted.
    """
    cursor = None

    while True:
        list_kwargs: dict[str, Any] = {"prefix": prefix}
        if cursor is not None:
            list_kwargs["cursor"] = cursor

        converted = await r2.list(**list_kwargs)

        objects = converted.get("objects", []) if isinstance(converted, dict) else []
        keys_to_delete: list[str] = []
        for obj in objects:
            key = obj.get("key", "") if isinstance(obj, dict) else getattr(obj, "key", "")
            if key and (key_filter is None or key_filter(key)):
                keys_to_delete.append(key)

        if keys_to_delete:
            await asyncio.gather(*[r2.delete(k) for k in keys_to_delete])

        truncated = converted.get("truncated", False) if isinstance(converted, dict) else False
        if not truncated:
            break
        cursor = converted.get("cursor") if isinstance(converted, dict) else None
        if not cursor:
            break


async def delete_audio_content(r2: Any, article_id: str) -> None:
    """Delete audio-related R2 objects for an article.

    Uses R2 ``list(prefix=...)`` to discover and delete objects whose keys
    contain ``audio`` (e.g. ``audio.ogg``, ``audio.mp3``, ``audio-timing.json``).
    Text content (HTML, images, thumbnails, metadata) is preserved.
    """
    prefix = f"articles/{article_id}/"

    def _is_audio_key(key: str) -> bool:
        filename = key.rsplit("/", 1)[-1]
        return "audio" in filename

    await _paginated_delete(r2, prefix, key_filter=_is_audio_key)


async def delete_article_content(r2: Any, article_id: str) -> None:
    """Delete all R2 objects associated with an article.

    Uses R2 ``list(prefix=...)`` to discover and delete all objects under the
    article's prefix, including content files, images, thumbnails, and audio.

    Parameters
    ----------
    r2:
        The R2 bucket binding.
    article_id:
        The article's unique identifier.
    """
    prefix = f"articles/{article_id}/"
    await _paginated_delete(r2, prefix)

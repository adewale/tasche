"""Main queue consumer pipeline for article processing.

Called by ``_handle_article_processing`` in ``entry.py`` when an
``article_processing`` queue message is consumed.

Steps:
 1. Update article status to 'processing'
 2. Fetch page via http_fetch (follow redirects, capture final_url)
 3. Extract canonical_url from HTML
 4. Extract thumbnail from og:image meta tag
 5. Extract article content (Readability Service Binding with BS4 fallback)
 6. Download and store images
 7. Rewrite HTML image paths to local R2 paths
 8. Convert to Markdown
 9. Store content.html to R2
10. Store metadata.json to R2
11. Update D1 with all metadata
12. FTS5 indexing (handled automatically by D1 triggers)
"""

from __future__ import annotations

import hashlib
import json  # noqa: F401 — kept for other callers / queue handler
import traceback

from articles.extraction import (
    calculate_reading_time,
    count_words,
    extract_article,
    extract_canonical_url,
    extract_thumbnail_url,
    html_to_markdown,
    rewrite_image_paths,
)
from articles.images import download_images, store_images
from articles.storage import article_key, store_content, store_metadata
from articles.urls import _is_private_hostname, extract_domain
from utils import now_iso
from wide_event import current_event
from wrappers import HttpError, SafeEnv, http_fetch


async def _mark_failed(db: object, article_id: str) -> None:
    """Log the error and set article status to 'failed' in D1."""
    evt = current_event()
    if evt:
        evt.set_many(
            {
                "outcome": "error",
                "error.message": traceback.format_exc()[-500:],
            }
        )
    try:
        await (
            db.prepare("UPDATE articles SET status = ?, updated_at = ? WHERE id = ?")
            .bind("failed", now_iso(), article_id)
            .run()
        )
    except Exception:
        evt = current_event()
        if evt:
            evt.set("status_update_error", traceback.format_exc()[-500:])


async def process_article(article_id: str, original_url: str, env: object) -> None:
    """Process a single article through the full content pipeline.

    This is the main entry point called by the queue handler.  On success,
    the article status is set to ``'ready'``.  On any failure, the status
    is set to ``'failed'``.

    Parameters
    ----------
    article_id:
        The D1 article row ID.
    original_url:
        The URL originally submitted by the user.
    env:
        Worker environment object with ``DB`` (D1), ``CONTENT`` (R2),
        and optional ``READABILITY`` service binding.
    """
    db = env.DB  # type: ignore[attr-defined]
    r2 = env.CONTENT  # type: ignore[attr-defined]

    # Pre-initialise variables that are assigned inside the HTTP block but
    # referenced after it.  This prevents UnboundLocalError when an exception
    # occurs before these variables are assigned.
    image_map: dict[str, str] = {}
    clean_html: str = ""
    final_url: str = original_url
    canonical_url: str = ""
    domain: str = ""
    title: str = ""
    excerpt: str = ""
    author: str | None = None
    thumbnail_key: str | None = None
    original_key: str | None = None
    markdown: str = ""

    # Track processing phase: errors during storage (R2 writes, D1 updates)
    # are transient and should propagate for queue retry, while errors during
    # content extraction are permanent (the content won't change on retry).
    in_storage_phase = False

    try:
        # Step 1: Update status to 'processing'
        await (
            db.prepare("UPDATE articles SET status = ?, updated_at = ? WHERE id = ?")
            .bind("processing", now_iso(), article_id)
            .run()
        )

        # Check if raw HTML was pre-supplied (e.g. bookmarklet content capture).
        # If so, skip the HTTP fetch and use the pre-supplied content.
        raw_key = article_key(article_id, "raw.html")
        pre_supplied_html: str | None = None
        raw_obj = await r2.get(raw_key)
        if raw_obj is not None:
            pre_supplied_html = await raw_obj.text()

        # Step 2: Fetch page, following redirects (skipped when content pre-supplied)
        if pre_supplied_html is not None:
            html = pre_supplied_html
            final_url = original_url
            evt = current_event()
            if evt:
                evt.set("content_source", "pre_supplied")
                evt.set("content_length", len(html))
        else:
            html, final_url = await _fetch_page(original_url)

            # SSRF check: validate the final URL after redirects
            from urllib.parse import urlparse

            parsed_final = urlparse(final_url)
            if parsed_final.hostname and _is_private_hostname(parsed_final.hostname):
                raise ValueError(f"Redirect to private/internal URL blocked: {final_url}")

        # Step 3: Extract canonical URL
        canonical_url = extract_canonical_url(html) or final_url
        domain = extract_domain(final_url)

        # Step 4: Extract thumbnail from og:image meta tag
        thumbnail_key = None
        original_key = None

        thumbnail_url = extract_thumbnail_url(html)
        if thumbnail_url:
            try:
                from urllib.parse import urlparse

                parsed = urlparse(thumbnail_url)
                if parsed.hostname and not _is_private_hostname(parsed.hostname):
                    resp = await http_fetch(thumbnail_url, timeout=15.0)
                    if resp.status_code == 200:
                        content_type = resp.headers.get("content-type", "").split(";")[0].strip()
                        max_thumb = 2 * 1024 * 1024
                        if content_type.startswith("image/") and len(resp.content) <= max_thumb:
                            thumbnail_key = article_key(article_id, "thumbnail.webp")
                            await r2.put(thumbnail_key, resp.content)
            except Exception:
                pass  # Thumbnail extraction is non-fatal

        # Step 5: Extract article content
        # Prefer the Readability Service Binding (100% Mozilla fidelity)
        # with BS4 heuristic fallback when the binding is unavailable.
        safe_env = SafeEnv(env)
        readability = safe_env.READABILITY
        extraction_method = "bs4"
        if readability is not None:
            try:
                article = await readability.parse(html, final_url)
                if article.get("html"):
                    extraction_method = "readability"
                else:
                    article = extract_article(html)
            except Exception as exc:
                evt = current_event()
                if evt:
                    evt.set("extraction_fallback", True)
                    evt.set("extraction_fallback_error", str(exc)[:200])
                article = extract_article(html)
        else:
            article = extract_article(html)
        clean_html = article["html"]
        title = article["title"]
        excerpt = article["excerpt"]
        author = article["byline"]

        # Step 7: Download and store images
        images = await download_images(clean_html)
        image_map = await store_images(r2, article_id, images)

        # Preserve user-supplied title if one was provided at creation time
        existing = await (
            db.prepare("SELECT title FROM articles WHERE id = ?").bind(article_id).first()
        )
        if existing and existing.get("title"):
            title = existing["title"]

        # Step 8: Rewrite HTML image paths to API-served URLs
        if image_map:
            api_image_map = {url: f"/api/{r2_key}" for url, r2_key in image_map.items()}
            clean_html = rewrite_image_paths(clean_html, api_image_map)

        # Step 9: Convert to Markdown
        markdown = html_to_markdown(clean_html)
        word_count = count_words(markdown)
        reading_time = calculate_reading_time(word_count)

        # --- Storage phase: errors from here are transient (R2/D1) ---
        in_storage_phase = True

        # Step 10: Store content.html to R2
        keys = await store_content(r2, article_id, clean_html)
        html_key = keys["html_key"]

        # Step 12: Store metadata.json to R2
        content_hash = hashlib.sha256(clean_html.encode("utf-8")).hexdigest()
        await store_metadata(
            r2,
            article_id,
            {
                "article_id": article_id,
                "archived_at": now_iso(),
                "original_url": original_url,
                "final_url": final_url,
                "canonical_url": canonical_url,
                "domain": domain,
                "title": title,
                "author": author,
                "word_count": word_count,
                "reading_time_minutes": reading_time,
                "image_count": len(image_map),
                "extraction_method": extraction_method,
                "content_hash": content_hash,
            },
        )

        # Step 13: Update D1 with all metadata
        await (
            db.prepare(
                """UPDATE articles SET
                title = ?,
                excerpt = ?,
                author = ?,
                word_count = ?,
                reading_time_minutes = ?,
                domain = ?,
                final_url = ?,
                canonical_url = ?,
                html_key = ?,
                thumbnail_key = ?,
                original_key = ?,
                image_count = ?,
                markdown_content = ?,
                status = ?,
                updated_at = ?
            WHERE id = ?"""
            )
            .bind(
                title,
                excerpt,
                author,
                word_count,
                reading_time,
                domain,
                final_url,
                canonical_url,
                html_key,
                thumbnail_key,
                original_key,
                len(image_map),
                markdown,
                "ready",
                now_iso(),
                article_id,
            )
            .run()
        )

        # Step 14: FTS5 indexing is handled by D1 triggers automatically.

        evt = current_event()
        if evt:
            evt.set_many(
                {
                    "outcome": "success",
                    "extraction_method": extraction_method,
                    "word_count": word_count,
                    "image_count": len(image_map),
                }
            )

        # Auto-enqueue TTS if listen_later was requested at save time
        try:
            art_row = await (
                db.prepare("SELECT audio_status, user_id FROM articles WHERE id = ?")
                .bind(article_id)
                .first()
            )
            if art_row and art_row.get("audio_status") == "pending":
                await env.ARTICLE_QUEUE.send(
                    {
                        "type": "tts_generation",
                        "article_id": article_id,
                        "user_id": art_row.get("user_id", ""),
                    }
                )
                evt = current_event()
                if evt:
                    evt.set("tts_auto_enqueued", True)
        except Exception:
            # Non-fatal: TTS can be requested manually later
            evt = current_event()
            if evt:
                evt.set("tts_auto_enqueue_error", traceback.format_exc()[-500:])

    except (ConnectionError, TimeoutError):
        # Transient network errors — let propagate for queue retry
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
    except HttpError as exc:
        if exc.status_code >= 500:
            # Server errors are transient — let propagate for queue retry
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
        # Client errors (4xx) are permanent — mark as failed
        await _mark_failed(db, article_id)
    except Exception:
        if in_storage_phase:
            # R2/D1 errors are transient — let propagate for queue retry
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
        # Content extraction errors are permanent — mark as failed
        await _mark_failed(db, article_id)


async def _fetch_page(
    url: str,
) -> tuple[str, str]:
    """Fetch a page with redirect following.

    Returns
    -------
    tuple[str, str]
        (html_content, final_url_after_redirects)

    Raises
    ------
    HttpError
        When the response status is 4xx or 5xx.
    """
    _MAX_CONTENT_LENGTH = 10_485_760  # 10 MB

    resp = await http_fetch(
        url,
        timeout=30.0,
        headers={
            "User-Agent": ("Mozilla/5.0 (compatible; Tasche/1.0; +https://github.com/tasche)"),
        },
    )
    resp.raise_for_status()

    # Validate Content-Length before reading body into memory
    content_length = resp.headers.get("content-length")
    if content_length is not None and int(content_length) > _MAX_CONTENT_LENGTH:
        raise ValueError(
            f"Response too large: Content-Length {content_length} exceeds "
            f"limit of {_MAX_CONTENT_LENGTH} bytes"
        )

    # Validate Content-Type before reading body into memory
    content_type = resp.headers.get("content-type", "")
    mime = content_type.split(";")[0].strip().lower()
    if mime and mime not in ("text/html", "application/xhtml+xml"):
        raise ValueError(
            f"Unexpected Content-Type '{mime}': expected text/html or application/xhtml+xml"
        )

    body_bytes = resp.content
    if len(body_bytes) > _MAX_CONTENT_LENGTH:
        raise ValueError(
            f"Response body too large: {len(body_bytes)} bytes exceeds "
            f"limit of {_MAX_CONTENT_LENGTH} bytes"
        )

    # Detect charset from Content-Type header, fall back to UTF-8
    charset = "utf-8"
    for part in content_type.split(";")[1:]:
        part = part.strip().lower()
        if part.startswith("charset="):
            charset = part[len("charset=") :].strip().strip("'\"")
            break

    return body_bytes.decode(charset, errors="replace"), str(resp.url)

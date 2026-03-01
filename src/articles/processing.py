"""Main queue consumer pipeline for article processing.

Implements the 14-step content processing pipeline described in spec
section 3.1.  Called by ``_handle_article_processing`` in ``entry.py``
when an ``article_processing`` queue message is consumed.

Steps:
 1. Update article status to 'processing'
 2. Fetch page via http_fetch (follow redirects, capture final_url)
 3. Try Browser Rendering scrape if content looks JS-heavy
 4. Extract canonical_url from HTML
 5. Screenshots via Browser Rendering (thumbnail + full-page archival)
 6. Extract article content (Readability Service Binding with BS4 fallback)
 7. Download and store images
 8. Rewrite HTML image paths to local R2 paths
 9. Convert to Markdown
10. Store content.html to R2
11. Store metadata.json to R2
12. Update D1 with all metadata
13. FTS5 indexing (handled automatically by D1 triggers)
"""

from __future__ import annotations

import hashlib
import json  # noqa: F401 — kept for other callers / queue handler
import traceback

from articles.browser_rendering import BrowserRenderingError, scrape, screenshot
from articles.extraction import (
    calculate_reading_time,
    count_words,
    extract_article,
    extract_canonical_url,
    html_to_markdown,
    rewrite_image_paths,
)
from articles.images import download_images, store_images
from articles.storage import article_key, store_content, store_metadata
from articles.urls import _is_private_hostname, extract_domain
from utils import now_iso
from wide_event import current_event
from wrappers import HttpError, SafeEnv, http_fetch

# Minimum content length (characters) to consider HTML as "real" content.
# Below this threshold, the page is likely JS-rendered and needs Browser Rendering.
_MIN_CONTENT_LENGTH = 500


async def apply_auto_tags(
    env: object,
    article_id: str,
    domain: str,
    title: str,
    url: str,
) -> int:
    """Apply tag rules to an article based on its metadata.

    Fetches all tag_rules from D1, evaluates each against the article's
    domain, title, and URL, and creates article-tag associations for
    matching rules.

    Parameters
    ----------
    env:
        Worker environment with ``DB`` binding.
    article_id:
        The article to tag.
    domain:
        The article's domain (e.g. ``example.com``).
    title:
        The article's title.
    url:
        The article's final URL after redirects.

    Returns
    -------
    int
        Number of tags applied.
    """
    import fnmatch

    db = env.DB  # type: ignore[attr-defined]

    rules = await db.prepare("SELECT tag_id, match_type, pattern FROM tag_rules").all()

    if not rules:
        return 0

    matched_tag_ids: set[str] = set()
    title_lower = (title or "").lower()
    url_lower = (url or "").lower()
    domain_lower = (domain or "").lower()

    for rule in rules:
        match_type = rule.get("match_type", "")
        pattern = rule.get("pattern", "")
        tag_id = rule.get("tag_id", "")

        if not pattern or not tag_id:
            continue

        matched = False
        if match_type == "domain":
            # Exact match or glob match (supports wildcards like *.example.com)
            pattern_lower = pattern.lower()
            if domain_lower == pattern_lower:
                matched = True
            elif fnmatch.fnmatch(domain_lower, pattern_lower):
                matched = True
        elif match_type == "title_contains":
            if pattern.lower() in title_lower:
                matched = True
        elif match_type == "url_contains":
            if pattern.lower() in url_lower:
                matched = True

        if matched:
            matched_tag_ids.add(tag_id)

    applied = 0
    for tag_id in matched_tag_ids:
        try:
            await (
                db.prepare("INSERT OR IGNORE INTO article_tags (article_id, tag_id) VALUES (?, ?)")
                .bind(article_id, tag_id)
                .run()
            )
            applied += 1
        except Exception:
            # Non-fatal: skip this tag association if it fails
            pass

    if applied > 0:
        evt = current_event()
        if evt:
            evt.set("auto_tags_applied", applied)

    return applied


def _is_js_heavy(html: str) -> bool:
    """Heuristic: detect if HTML is mostly a JS shell with minimal content.

    Returns ``True`` if the body text (excluding scripts) is shorter than
    ``_MIN_CONTENT_LENGTH``, suggesting the real content is rendered by
    JavaScript.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    # Remove script and style tags
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(strip=True)
    return len(text) < _MIN_CONTENT_LENGTH


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
        ``CF_ACCOUNT_ID``, and ``CF_API_TOKEN`` for Browser Rendering.
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

        # Step 3: If content looks JS-heavy, try Browser Rendering
        # (skip when content was pre-supplied — the user's browser already
        # rendered any JS)
        safe_env = SafeEnv(env)
        account_id = safe_env.get("CF_ACCOUNT_ID")
        api_token = safe_env.get("CF_API_TOKEN")
        has_browser_rendering = bool(account_id and api_token)

        if not has_browser_rendering:
            evt = current_event()
            if evt:
                evt.set("browser_rendering", "not_configured")

        if has_browser_rendering and pre_supplied_html is None and _is_js_heavy(html):
            try:
                html = await scrape(final_url, account_id, api_token)
            except BrowserRenderingError:
                pass  # Fall back to the original HTML

        # Step 4: Extract canonical URL
        canonical_url = extract_canonical_url(html) or final_url
        domain = extract_domain(final_url)

        # Step 5: Screenshots via Browser Rendering
        thumbnail_key = None
        original_key = None

        # 5a: Thumbnail (above-the-fold crop)
        if has_browser_rendering:
            try:
                thumb_data = await screenshot(
                    final_url,
                    account_id,
                    api_token,
                    viewport_width=1200,
                    viewport_height=630,
                )
                thumbnail_key = article_key(article_id, "thumbnail.webp")
                await r2.put(thumbnail_key, thumb_data)
            except BrowserRenderingError:
                pass  # Per-URL failures are non-fatal

        # 5b: Full-page archival screenshot
        if has_browser_rendering:
            try:
                full_data = await screenshot(
                    final_url,
                    account_id,
                    api_token,
                    viewport_width=1200,
                    viewport_height=800,
                    full_page=True,
                )
                original_key = article_key(article_id, "original.webp")
                await r2.put(original_key, full_data)
            except BrowserRenderingError:
                pass  # Per-URL failures are non-fatal

        # Step 6: Extract article content
        # Prefer the Readability Service Binding (100% Mozilla fidelity)
        # with BS4 heuristic fallback when the binding is unavailable.
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

        # Step 15: Apply auto-tagging rules
        try:
            await apply_auto_tags(env, article_id, domain, title, final_url)
        except Exception:
            # Non-fatal: auto-tagging failure should not block processing
            evt = current_event()
            if evt:
                evt.set("auto_tag_error", traceback.format_exc()[-500:])

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
    except Exception:
        # Other permanent errors (invalid content, etc.) — mark as failed
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
    return body_bytes.decode("utf-8", errors="replace"), str(resp.url)

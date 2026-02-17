"""Main queue consumer pipeline for article processing.

Implements the 14-step content processing pipeline described in spec
section 3.1.  Called by ``_handle_article_processing`` in ``entry.py``
when an ``article_processing`` queue message is consumed.

Steps:
 1. Update article status to 'processing'
 2. Fetch page via httpx (follow redirects, capture final_url)
 3. Try Browser Rendering scrape if content looks JS-heavy
 4. Extract canonical_url from HTML
 5. Generate thumbnail via Browser Rendering screenshot API
 6. Extract article content via readability
 7. Download and store images
 8. Rewrite HTML image paths to local R2 paths
 9. Convert to Markdown
10. Store content.html to R2
11. Store content.md to R2
12. Store metadata.json to R2
13. Update D1 with all metadata
14. FTS5 indexing (handled automatically by D1 triggers)
"""

from __future__ import annotations

import hashlib
import json
import traceback
from datetime import UTC, datetime

import httpx

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

# Minimum content length (characters) to consider HTML as "real" content.
# Below this threshold, the page is likely JS-rendered and needs Browser Rendering.
_MIN_CONTENT_LENGTH = 500


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
        and optionally ``CF_ACCOUNT_ID`` / ``CF_API_TOKEN`` for Browser
        Rendering.
    """
    db = env.DB  # type: ignore[attr-defined]
    r2 = env.CONTENT  # type: ignore[attr-defined]

    try:
        # Step 1: Update status to 'processing'
        await (
            db.prepare("UPDATE articles SET status = ?, updated_at = ? WHERE id = ?")
            .bind("processing", _now(), article_id)
            .run()
        )

        # Step 2: Fetch page via httpx, following redirects
        async with httpx.AsyncClient(follow_redirects=True) as client:
            html, final_url = await _fetch_page(client, original_url)

            # SSRF check: validate the final URL after redirects
            from urllib.parse import urlparse

            parsed_final = urlparse(final_url)
            if parsed_final.hostname and _is_private_hostname(parsed_final.hostname):
                raise ValueError(f"Redirect to private/internal URL blocked: {final_url}")

            # Step 3: If content looks JS-heavy, try Browser Rendering
            account_id = getattr(env, "CF_ACCOUNT_ID", None)
            api_token = getattr(env, "CF_API_TOKEN", None)

            if _is_js_heavy(html) and account_id and api_token:
                try:
                    html = await scrape(client, final_url, account_id, api_token)
                except BrowserRenderingError:
                    pass  # Fall back to the original HTML

            # Step 4: Extract canonical URL
            canonical_url = extract_canonical_url(html) or final_url
            domain = extract_domain(final_url)

            # Step 5: Thumbnail via Browser Rendering screenshot
            thumbnail_key = None
            if account_id and api_token:
                try:
                    thumb_data = await screenshot(
                        client,
                        final_url,
                        account_id,
                        api_token,
                        viewport_width=1200,
                        viewport_height=630,
                    )
                    thumbnail_key = article_key(article_id, "thumbnail.webp")
                    await r2.put(thumbnail_key, thumb_data)
                except BrowserRenderingError:
                    pass  # Thumbnail is optional

            # Step 6: Extract article content via readability
            article = extract_article(html)
            clean_html = article["html"]
            title = article["title"]
            excerpt = article["excerpt"]
            author = article["byline"]

            # Step 7: Download and store images
            images = await download_images(client, clean_html)
            image_map = await store_images(r2, article_id, images)

        # Step 8: Rewrite HTML image paths
        if image_map:
            clean_html = rewrite_image_paths(clean_html, image_map)

        # Step 9: Convert to Markdown
        markdown = html_to_markdown(clean_html)
        word_count = count_words(markdown)
        reading_time = calculate_reading_time(word_count)

        # Steps 10-11: Store content.html and content.md to R2
        keys = await store_content(r2, article_id, clean_html, markdown)
        html_key = keys["html_key"]
        markdown_key = keys["markdown_key"]

        # Step 12: Store metadata.json to R2
        content_hash = hashlib.sha256(clean_html.encode("utf-8")).hexdigest()
        extraction_method = "readability"
        await store_metadata(
            r2,
            article_id,
            {
                "article_id": article_id,
                "archived_at": _now(),
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
                markdown_key = ?,
                thumbnail_key = ?,
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
                markdown_key,
                thumbnail_key,
                len(image_map),
                markdown,
                "ready",
                _now(),
                article_id,
            )
            .run()
        )

        # Step 14: FTS5 indexing is handled by D1 triggers automatically.

        print(
            json.dumps(
                {
                    "event": "article_processed",
                    "article_id": article_id,
                    "status": "ready",
                    "word_count": word_count,
                    "image_count": len(image_map),
                }
            )
        )

    except (
        ConnectionError,
        TimeoutError,
        httpx.ConnectError,
        httpx.TimeoutException,
    ):
        # Transient network errors — let propagate for queue retry
        print(
            json.dumps(
                {
                    "event": "article_processing_failed",
                    "article_id": article_id,
                    "error": traceback.format_exc(),
                    "retryable": True,
                }
            )
        )
        raise
    except Exception:
        # Permanent errors (HTTP 4xx, invalid content, etc.) — mark as failed
        print(
            json.dumps(
                {
                    "event": "article_processing_failed",
                    "article_id": article_id,
                    "error": traceback.format_exc(),
                }
            )
        )
        try:
            await (
                db.prepare("UPDATE articles SET status = ?, updated_at = ? WHERE id = ?")
                .bind("failed", _now(), article_id)
                .run()
            )
        except Exception:
            print(
                json.dumps(
                    {
                        "event": "article_status_update_failed",
                        "article_id": article_id,
                        "error": traceback.format_exc(),
                    }
                )
            )


async def _fetch_page(
    client: httpx.AsyncClient,
    url: str,
) -> tuple[str, str]:
    """Fetch a page via httpx with redirect following.

    Returns
    -------
    tuple[str, str]
        (html_content, final_url_after_redirects)

    Raises
    ------
    httpx.HTTPStatusError
        When the response status is 4xx or 5xx.
    """
    resp = await client.get(
        url,
        timeout=30.0,
        headers={
            "User-Agent": ("Mozilla/5.0 (compatible; Tasche/1.0; +https://github.com/tasche)"),
        },
    )
    resp.raise_for_status()
    return resp.text, str(resp.url)


def _now() -> str:
    """Return the current UTC timestamp as an ISO 8601 string for D1."""
    return datetime.now(UTC).isoformat()

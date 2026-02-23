"""Feed refresh logic for Tasche.

Fetches feed XML, parses entries, deduplicates against existing articles,
and enqueues new entries for article processing.
"""

from __future__ import annotations

import traceback
from typing import Any

from articles.urls import check_duplicate, extract_domain, validate_url
from feeds.parser import parse_feed
from utils import generate_id, now_iso
from wide_event import current_event
from wrappers import HttpClient, HttpError


async def refresh_feed(env: Any, feed: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Refresh a single feed and enqueue new articles.

    Parameters
    ----------
    env:
        Worker environment with DB and ARTICLE_QUEUE bindings.
    feed:
        The feed row from D1 (must have id, url, last_entry_published).
    user_id:
        The user ID to associate new articles with.

    Returns
    -------
    dict
        Summary with ``new_articles`` count and ``errors`` list.
    """
    db = env.DB
    feed_id = feed["id"]
    feed_url = feed["url"]
    last_published = feed.get("last_entry_published") or ""

    new_count = 0
    errors: list[str] = []
    latest_published = last_published

    try:
        async with HttpClient() as client:
            resp = await client.get(
                feed_url,
                timeout=30.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; Tasche/1.0; +https://github.com/tasche)",
                },
            )
            resp.raise_for_status()

        parsed = parse_feed(resp.text)

        # Update feed title/site_url if we got better data
        if parsed.title and parsed.title != feed.get("title", ""):
            await (
                db.prepare("UPDATE feeds SET title = ?, updated_at = ? WHERE id = ?")
                .bind(parsed.title, now_iso(), feed_id)
                .run()
            )

        if parsed.site_url and parsed.site_url != feed.get("site_url", ""):
            await (
                db.prepare("UPDATE feeds SET site_url = ?, updated_at = ? WHERE id = ?")
                .bind(parsed.site_url, now_iso(), feed_id)
                .run()
            )

        for entry in parsed.entries:
            if not entry.link:
                continue

            # Skip entries older than or equal to last_entry_published
            if last_published and entry.published and entry.published <= last_published:
                continue

            # Track the latest published date
            if entry.published and entry.published > latest_published:
                latest_published = entry.published

            # Validate URL
            try:
                url = validate_url(entry.link)
            except ValueError:
                errors.append(f"Invalid URL: {entry.link}")
                continue

            # Check for duplicate articles
            existing = await check_duplicate(db, user_id, url)
            if existing is not None:
                continue

            # Create new article
            article_id = generate_id()
            domain = extract_domain(url)
            title = entry.title or None
            now = now_iso()

            try:
                await (
                    db.prepare(
                        "INSERT INTO articles (id, user_id, original_url, domain, title, "
                        "status, reading_status, is_favorite, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, 'pending', 'unread', 0, ?, ?)"
                    )
                    .bind(article_id, user_id, url, domain, title, now, now)
                    .run()
                )
            except Exception as exc:
                exc_msg = str(exc).lower()
                if "unique" in exc_msg or "constraint" in exc_msg:
                    continue  # Already exists
                errors.append(f"Insert failed for {url}: {exc}")
                continue

            # Enqueue for processing
            try:
                await env.ARTICLE_QUEUE.send(
                    {
                        "type": "article_processing",
                        "article_id": article_id,
                        "url": url,
                        "user_id": user_id,
                    }
                )
                new_count += 1
            except Exception as exc:
                errors.append(f"Enqueue failed for {url}: {exc}")
                # Mark as failed since we cannot process it
                try:
                    await (
                        db.prepare(
                            "UPDATE articles SET status = 'failed', updated_at = ? WHERE id = ?"
                        )
                        .bind(now_iso(), article_id)
                        .run()
                    )
                except Exception:
                    pass

    except (HttpError, ConnectionError, TimeoutError, ValueError) as exc:
        errors.append(f"Fetch failed: {exc}")
    except Exception:
        errors.append(f"Unexpected error: {traceback.format_exc()[-500:]}")

    # Update feed timestamps
    now = now_iso()
    if latest_published and latest_published != last_published:
        await (
            db.prepare(
                "UPDATE feeds SET last_fetched_at = ?, last_entry_published = ?, "
                "updated_at = ? WHERE id = ?"
            )
            .bind(now, latest_published, now, feed_id)
            .run()
        )
    else:
        await (
            db.prepare("UPDATE feeds SET last_fetched_at = ?, updated_at = ? WHERE id = ?")
            .bind(now, now, feed_id)
            .run()
        )

    evt = current_event()
    if evt:
        evt.set_many({
            "feed_id": feed_id,
            "feed_url": feed_url,
            "new_articles": new_count,
            "feed_errors": len(errors),
        })

    return {"new_articles": new_count, "errors": errors}


async def refresh_all_feeds(env: Any, user_id: str) -> dict[str, Any]:
    """Refresh all active feeds for a user.

    Parameters
    ----------
    env:
        Worker environment with DB and ARTICLE_QUEUE bindings.
    user_id:
        The user ID whose feeds to refresh.

    Returns
    -------
    dict
        Summary with ``feeds_checked`` and ``total_new_articles``.
    """
    db = env.DB

    feeds = await (
        db.prepare(
            "SELECT * FROM feeds WHERE is_active = 1 AND user_id = ? "
            "ORDER BY last_fetched_at ASC NULLS FIRST"
        )
        .bind(user_id)
        .all()
    )

    total_new = 0
    feeds_checked = 0

    for feed in feeds:
        try:
            result = await refresh_feed(env, feed, user_id)
            total_new += result["new_articles"]
            feeds_checked += 1
        except Exception:
            evt = current_event()
            if evt:
                evt.set("feed_refresh_error", traceback.format_exc()[-500:])

    evt = current_event()
    if evt:
        evt.set_many({"feeds_checked": feeds_checked, "total_new_articles": total_new})

    return {"feeds_checked": feeds_checked, "total_new_articles": total_new}

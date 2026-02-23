"""Feed CRUD and refresh routes for Tasche.

Provides endpoints for subscribing to RSS/Atom feeds, listing them,
refreshing them, and importing from OPML files.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.dependencies import get_current_user
from feeds.parser import parse_feed, parse_opml
from feeds.processing import refresh_all_feeds, refresh_feed
from utils import generate_id, now_iso
from wrappers import HttpClient

router = APIRouter()


@router.get("")
async def list_feeds(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List all feeds for the authenticated user."""
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    return await (
        db.prepare("SELECT * FROM feeds WHERE user_id = ? ORDER BY created_at DESC")
        .bind(user_id)
        .all()
    )


@router.post("", status_code=201)
async def add_feed(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Add a new feed subscription.

    Accepts a JSON body with ``url`` (required). Fetches the feed immediately
    to validate it and extract title/site_url.
    """
    body = await request.json()
    url = body.get("url", "").strip()

    if not url:
        raise HTTPException(status_code=422, detail="url is required")

    if len(url) > 2048:
        raise HTTPException(status_code=400, detail="URL must not exceed 2048 characters")

    # Basic URL validation
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="URL must use http or https scheme")

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    # Check for duplicate feed URL
    existing = await (
        db.prepare("SELECT id FROM feeds WHERE url = ? AND user_id = ?").bind(url, user_id).first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Feed with this URL already exists")

    # Fetch and validate the feed
    try:
        async with HttpClient() as client:
            resp = await client.get(
                url,
                timeout=30.0,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; Tasche/1.0; +https://github.com/tasche)",
                },
            )
            resp.raise_for_status()

        parsed = parse_feed(resp.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid feed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch feed: {exc}") from exc

    feed_id = generate_id()
    now = now_iso()

    feed = {
        "id": feed_id,
        "user_id": user_id,
        "url": url,
        "title": parsed.title or "",
        "site_url": parsed.site_url or "",
        "last_fetched_at": now,
        "last_entry_published": None,
        "fetch_interval_minutes": 60,
        "is_active": 1,
        "created_at": now,
        "updated_at": now,
    }

    await (
        db.prepare(
            "INSERT INTO feeds (id, user_id, url, title, site_url, last_fetched_at, "
            "last_entry_published, fetch_interval_minutes, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        .bind(
            feed_id,
            user_id,
            url,
            feed["title"],
            feed["site_url"],
            now,
            None,
            60,
            1,
            now,
            now,
        )
        .run()
    )

    return feed


@router.delete("/{feed_id}", status_code=204)
async def delete_feed(
    request: Request,
    feed_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> None:
    """Delete a feed subscription."""
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    existing = await (
        db.prepare("SELECT id FROM feeds WHERE id = ? AND user_id = ?")
        .bind(feed_id, user_id)
        .first()
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="Feed not found")

    await db.prepare("DELETE FROM feeds WHERE id = ? AND user_id = ?").bind(feed_id, user_id).run()


@router.post("/{feed_id}/refresh")
async def refresh_single_feed(
    request: Request,
    feed_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Manually trigger a refresh for a single feed."""
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    feed = await (
        db.prepare("SELECT * FROM feeds WHERE id = ? AND user_id = ?")
        .bind(feed_id, user_id)
        .first()
    )
    if feed is None:
        raise HTTPException(status_code=404, detail="Feed not found")

    result = await refresh_feed(env, feed, user_id)
    return result


@router.post("/refresh-all")
async def refresh_all(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Refresh all active feeds for the authenticated user."""
    env = request.scope["env"]
    user_id = user["user_id"]

    result = await refresh_all_feeds(env, user_id)
    return result


@router.post("/import-opml")
async def import_opml(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Import feeds from an OPML file.

    Accepts a JSON body with ``opml`` (the raw XML string).
    Creates feeds for each valid outline, skipping duplicates.
    """
    body = await request.json()
    opml_text = body.get("opml", "")

    if not opml_text:
        raise HTTPException(status_code=422, detail="opml field is required")

    try:
        outlines = parse_opml(opml_text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not outlines:
        return {"imported": 0, "skipped": 0, "errors": []}

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    imported = 0
    skipped = 0
    errors: list[str] = []

    for outline in outlines:
        url = outline["url"].strip()
        if not url:
            continue

        # Check for duplicate
        existing = await (
            db.prepare("SELECT id FROM feeds WHERE url = ? AND user_id = ?")
            .bind(url, user_id)
            .first()
        )
        if existing is not None:
            skipped += 1
            continue

        feed_id = generate_id()
        now = now_iso()

        try:
            await (
                db.prepare(
                    "INSERT INTO feeds (id, user_id, url, title, site_url, "
                    "fetch_interval_minutes, is_active, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                )
                .bind(
                    feed_id,
                    user_id,
                    url,
                    outline.get("title", ""),
                    outline.get("site_url", ""),
                    60,
                    1,
                    now,
                    now,
                )
                .run()
            )
            imported += 1
        except Exception as exc:
            errors.append(f"Failed to import {url}: {exc}")

    return {"imported": imported, "skipped": skipped, "errors": errors}

"""Article CRUD routes for Tasche.

Provides endpoints for creating, listing, retrieving, updating, and deleting
saved articles.  All endpoints require authentication via the
``get_current_user`` dependency.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse

from articles.health import check_original_url
from articles.storage import (
    article_key,
    delete_article_content,
    get_content,
    get_metadata,
)
from articles.urls import check_duplicate, extract_domain, validate_url
from auth.dependencies import get_current_user
from src.boundary import consume_readable_stream, get_r2_size
from utils import generate_id, get_user_entity, now_iso

router = APIRouter()


async def _enqueue_or_fail(
    env: Any,
    db: Any,
    message: dict,
    article_id: str,
    status_field: str = "status",
    rollback_value: str | None = "failed",
) -> None:
    """Send a message to ARTICLE_QUEUE, rolling back on failure."""
    try:
        await env.ARTICLE_QUEUE.send(message)
    except Exception:
        now = now_iso()
        await (
            db.prepare(f"UPDATE articles SET {status_field} = ?, updated_at = ? WHERE id = ?")
            .bind(rollback_value, now, article_id)
            .run()
        )
        raise HTTPException(status_code=503, detail="Failed to enqueue processing job")


async def _serve_r2_object(
    r2_obj: Any,
    media_type: str,
    cache_control: str = "public, max-age=86400",
) -> Response:
    """Serve an R2 object as an HTTP response.

    Reads the full body via :func:`src.boundary.consume_readable_stream` and
    returns a plain ``Response``.  ``StreamingResponse`` with async generators
    is broken in the Python Workers ASGI adapter (it truncates after the
    first chunk), so we must read the full body into memory.

    These are bounded-size objects (thumbnails <= 2 MB, images <= 2 MB)
    so reading into memory is safe.
    """
    body = await consume_readable_stream(r2_obj)
    headers: dict[str, str] = {"Cache-Control": cache_control}
    size = get_r2_size(r2_obj)
    if size is not None:
        headers["Content-Length"] = str(size)
    return Response(content=body, media_type=media_type, headers=headers)


# Column list for the list endpoint — excludes large fields like markdown_content.
_LIST_COLUMNS = (
    "id, user_id, original_url, final_url, canonical_url, domain, title, "
    "excerpt, author, word_count, reading_time_minutes, image_count, status, "
    "reading_status, is_favorite, audio_key, audio_duration_seconds, "
    "audio_status, html_key, thumbnail_key, original_key, original_status, "
    "scroll_position, reading_progress, created_at, updated_at, last_checked_at"
)

# Pre-computed "articles."-prefixed column string for use in JOINs (avoids
# recomputing on every request).
_LIST_COLUMNS_PREFIXED = ", ".join(f"articles.{c.strip()}" for c in _LIST_COLUMNS.split(","))

_VALID_READING_STATUSES = {"unread", "archived"}
_VALID_STATUSES = {"pending", "processing", "ready", "failed"}
_VALID_AUDIO_STATUSES = {"pending", "generating", "ready", "failed"}

# Allowlist of safe sort options — maps user-facing key to SQL ORDER BY clause.
# Prevents SQL injection by never interpolating user input directly.
_VALID_SORT_OPTIONS: dict[str, str] = {
    "newest": "created_at DESC",
    "oldest": "created_at ASC",
    "shortest": "reading_time_minutes ASC NULLS LAST",
    "longest": "reading_time_minutes DESC NULLS LAST",
    "title_asc": "title ASC",
}

# Characters that have special meaning in FTS5 query syntax.
_FTS5_SPECIAL_CHARS = set('"*+-^():{}[]|\\')


def _sanitize_fts5_query(query: str) -> str:
    """Sanitize a search query for safe use with FTS5 MATCH.

    Wraps each word in double quotes so FTS5 treats them as literals,
    stripping any FTS5 operator characters. Example:
    ``hello world`` -> ``"hello" "world"``.
    ``OR AND NOT`` -> ``"OR" "AND" "NOT"``
    """
    tokens = query.split()
    safe_tokens = []
    for token in tokens:
        cleaned = "".join(ch for ch in token if ch not in _FTS5_SPECIAL_CHARS)
        if cleaned:
            safe_tokens.append(f'"{cleaned}"')
    return " ".join(safe_tokens)


def _validate_reading_status(value: str) -> None:
    """Raise 422 if *value* is not a valid reading_status."""
    if value not in _VALID_READING_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"reading_status must be one of: {', '.join(sorted(_VALID_READING_STATUSES))}",
        )


def _validate_batch_ids(
    ids: Any,
    *,
    label: str = "article_ids",
    max_count: int = 100,
) -> None:
    """Raise 422 if *ids* is not a non-empty list within *max_count*."""
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=422, detail=f"{label} must be a non-empty list")
    if len(ids) > max_count:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot process more than {max_count} articles at once",
        )


async def _get_user_article(
    db: Any,
    article_id: str,
    user_id: str,
    fields: str = "*",
) -> dict[str, Any]:
    """Fetch an article by ID for a user, or raise 404."""
    return await get_user_entity(
        db,
        table="articles",
        entity_id=article_id,
        user_id=user_id,
        fields=fields,
        not_found="Article not found",
    )


def _parse_tags_json(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse and filter the ``tags_json`` field from a row.

    Pops the ``tags_json`` key, deserialises the JSON string, and filters
    out ``null`` entries produced by ``json_group_array`` for zero tags.
    """
    raw = row.pop("tags_json", "[]")
    parsed = json.loads(raw) if raw else []
    return [t for t in parsed if t is not None]


@router.post("", status_code=201)
async def create_article(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Save a new article, or re-process if the URL already exists.

    Accepts a JSON body with ``url`` (required), ``title`` (optional), and
    ``content`` (optional HTML string).  When ``content`` is provided, the
    raw HTML is stored directly in R2 so the processing pipeline can skip
    the HTTP fetch step -- useful for paywalled pages where the user's
    browser can see the content but the server cannot.

    If the URL already exists (across ``original_url``, ``final_url``, and
    ``canonical_url``), resets the existing article to ``pending`` and
    re-enqueues it for processing.  The response includes ``updated: true``
    and the original ``created_at`` so the frontend can show an appropriate
    toast.
    """
    body = await request.json()
    url = body.get("url", "")
    title = body.get("title")
    content = body.get("content")
    listen_later = bool(body.get("listen_later", False))

    # Validate field lengths
    if isinstance(url, str) and len(url) > 2048:
        raise HTTPException(status_code=400, detail="URL must not exceed 2048 characters")
    if title is not None and isinstance(title, str) and len(title) > 500:
        raise HTTPException(status_code=400, detail="Title must not exceed 500 characters")
    # Validate content: must be a string and not exceed 5 MB
    _MAX_CONTENT_SIZE = 5_242_880  # 5 MB
    if content is not None:
        if not isinstance(content, str):
            raise HTTPException(status_code=400, detail="Content must be a string")
        if len(content) > _MAX_CONTENT_SIZE:
            raise HTTPException(
                status_code=400,
                detail="Content must not exceed 5 MB",
            )

    # Validate URL
    try:
        url = validate_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    # Check for duplicates — if found, re-process rather than reject
    existing = await check_duplicate(db, user_id, url)
    is_update = existing is not None

    now = now_iso()

    if is_update:
        article_id = existing["id"]
        had_audio = existing.get("audio_status") is not None
        # Clean up ALL old R2 content (text + audio) to prevent orphans
        r2 = env.CONTENT
        await delete_article_content(r2, article_id)
        # Reset status so the pipeline re-processes the article.
        # Re-queue TTS if the article had audio or listen_later is requested.
        requeue_tts = listen_later or had_audio
        if requeue_tts:
            update_sql = (
                "UPDATE articles SET status = 'pending', "
                "audio_status = 'pending', audio_key = NULL, "
                "audio_duration_seconds = NULL"
            )
        else:
            update_sql = (
                "UPDATE articles SET status = 'pending', "
                "audio_status = NULL, audio_key = NULL, "
                "audio_duration_seconds = NULL"
            )
        update_sql += ", updated_at = ? WHERE id = ?"
        await db.prepare(update_sql).bind(now, article_id).run()
    else:
        article_id = generate_id()
        domain = extract_domain(url)

        columns = "id, user_id, original_url, domain, title, status, reading_status, is_favorite"
        values = "?, ?, ?, ?, ?, 'pending', 'unread', 0"
        bind_params = [article_id, user_id, url, domain, title]

        if listen_later:
            columns += ", audio_status"
            values += ", 'pending'"

        columns += ", created_at, updated_at"
        values += ", ?, ?"
        bind_params.extend([now, now])

        try:
            await (
                db.prepare(f"INSERT INTO articles ({columns}) VALUES ({values})")
                .bind(*bind_params)
                .run()
            )
        except Exception as exc:
            # Handle unique constraint violation (race condition)
            exc_msg = str(exc).lower()
            if "unique" in exc_msg or "constraint" in exc_msg:
                raise HTTPException(
                    status_code=409,
                    detail="Article with this URL already exists",
                ) from exc
            raise

    # If content was provided (e.g. from the bookmarklet capturing page HTML),
    # store the raw HTML in R2 under the article's content key so the
    # processing pipeline can skip the HTTP fetch step.
    if content:
        r2 = env.CONTENT
        raw_key = article_key(article_id, "raw.html")
        await r2.put(raw_key, content)

    # Apply tags if provided (from bookmarklet popup)
    tag_ids = body.get("tag_ids")
    if tag_ids and isinstance(tag_ids, list):
        valid_tids = [tid for tid in tag_ids[:20] if isinstance(tid, str) and tid]
        if valid_tids:
            # Batch-validate tag ownership in a single query
            placeholders = ", ".join("?" for _ in valid_tids)
            owned_rows = await (
                db.prepare(f"SELECT id FROM tags WHERE id IN ({placeholders}) AND user_id = ?")
                .bind(*valid_tids, user_id)
                .all()
            )
            owned_ids = {r["id"] for r in owned_rows}
            # Batch-insert all valid tag associations concurrently
            insert_coros = [
                db.prepare("INSERT OR IGNORE INTO article_tags (article_id, tag_id) VALUES (?, ?)")
                .bind(article_id, tid)
                .run()
                for tid in valid_tids
                if tid in owned_ids
            ]
            if insert_coros:
                await asyncio.gather(*insert_coros)

    # Enqueue processing job
    message: dict[str, Any] = {
        "type": "article_processing",
        "article_id": article_id,
        "url": url,
        "user_id": user_id,
    }

    # Chain TTS after text processing when article had/wants audio
    if is_update and requeue_tts:
        pref = await (
            db.prepare("SELECT tts_voice FROM user_preferences WHERE user_id = ?")
            .bind(user_id)
            .first()
        )
        message["requeue_tts"] = True
        message["tts_voice"] = pref.get("tts_voice") if pref else "athena"

    await _enqueue_or_fail(env, db, message, article_id)

    result: dict[str, Any] = {"id": article_id, "status": "pending", "created_at": now}
    if is_update:
        result["updated"] = True
        result["created_at"] = existing.get("created_at", "")
    return result


@router.get("")
async def list_articles(
    request: Request,
    response: Response,
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    reading_status: str | None = Query(default=None),
    is_favorite: bool | None = Query(default=None),
    audio_status: str | None = Query(default=None),
    tag: list[str] | None = Query(default=None),
    sort: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List the authenticated user's articles.

    Supports optional filtering by ``q`` (full-text search), ``status``,
    ``reading_status``, ``is_favorite``, ``audio_status``, and ``tag``.
    All filters compose naturally — e.g. ``?q=python&tag=abc&reading_status=unread``
    searches for "python" within unread articles tagged "abc".

    Multiple ``tag`` parameters may be passed for intersection filtering
    (articles must have *all* specified tags).  Up to 4 tags are allowed;
    more than 4 returns 400.

    When ``q`` is provided, results are ordered by FTS5 relevance unless
    ``sort`` is explicitly set.  Results are paginated via ``limit`` and
    ``offset``.

    Valid ``sort`` values: ``newest``, ``oldest``, ``shortest``,
    ``longest``, ``title_asc``.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    # Sanitize and validate search query if provided
    safe_q: str | None = None
    if q is not None:
        q = q.strip()
        if q:
            safe_q = _sanitize_fts5_query(q)
            if not safe_q:
                safe_q = None

    # Validate enum query parameters
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of: {', '.join(sorted(_VALID_STATUSES))}",
        )
    if reading_status is not None:
        _validate_reading_status(reading_status)
    if audio_status is not None and audio_status not in _VALID_AUDIO_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"audio_status must be one of: {', '.join(sorted(_VALID_AUDIO_STATUSES))}",
        )
    if sort is not None and sort not in _VALID_SORT_OPTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"sort must be one of: {', '.join(sorted(_VALID_SORT_OPTIONS))}",
        )

    where_clauses = ["articles.user_id = ?"]
    params: list[Any] = [user_id]

    # FTS5 search — join articles_fts when a search query is present
    fts_join = ""
    if safe_q:
        fts_join = "INNER JOIN articles_fts ON articles.rowid = articles_fts.rowid"
        where_clauses.append("articles_fts MATCH ?")
        params.append(safe_q)

    if status is not None:
        where_clauses.append("articles.status = ?")
        params.append(status)

    if reading_status is not None:
        where_clauses.append("articles.reading_status = ?")
        params.append(reading_status)

    if is_favorite is not None:
        where_clauses.append("articles.is_favorite = ?")
        params.append(1 if is_favorite else 0)

    if audio_status is not None:
        where_clauses.append("articles.audio_status = ?")
        params.append(audio_status)

    if tag is not None:
        if len(tag) > 4:
            raise HTTPException(status_code=400, detail="At most 4 tag filters are allowed")
        if len(tag) == 1:
            where_clauses.append(
                "articles.id IN (SELECT article_id FROM article_tags WHERE tag_id = ?)"
            )
            params.append(tag[0])
        else:
            placeholders = ", ".join("?" for _ in tag)
            where_clauses.append(
                f"articles.id IN ("
                f"SELECT article_id FROM article_tags "
                f"WHERE tag_id IN ({placeholders}) "
                f"GROUP BY article_id "
                f"HAVING COUNT(DISTINCT tag_id) = ?)"
            )
            params.extend(tag)
            params.append(len(tag))

    where = " AND ".join(where_clauses)

    # When searching, default to FTS5 relevance ordering; otherwise newest first.
    if sort is not None:
        order_by = _VALID_SORT_OPTIONS[sort]
    elif safe_q:
        order_by = "bm25(articles_fts, 10.0, 5.0, 1.0)"
    else:
        order_by = _VALID_SORT_OPTIONS["newest"]

    # Include tags inline via correlated subquery to avoid N+1 fetches.
    tags_sub = (
        "COALESCE("
        "(SELECT json_group_array(json_object("
        "'id', t.id, 'name', t.name"
        ")) FROM article_tags at2 "
        "INNER JOIN tags t ON t.id = at2.tag_id "
        "WHERE at2.article_id = articles.id), '[]')"
    )
    sql = (
        f"SELECT {_LIST_COLUMNS_PREFIXED}, {tags_sub} AS tags_json "
        f"FROM articles {fts_join} WHERE {where} ORDER BY {order_by} LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    rows = await db.prepare(sql).bind(*params).all()

    # Parse the JSON-encoded tags into actual lists.
    for row in rows:
        row["tags"] = _parse_tags_json(row)

    response.headers["Cache-Control"] = "private, max-age=30"
    return rows


@router.post("/batch-check-originals")
async def batch_check_originals(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Check original URLs for articles that haven't been checked recently.

    Selects up to 10 articles where ``original_status = 'unknown'`` or
    ``last_checked_at`` is older than 30 days, performs a HEAD request
    against each original URL, and updates the ``original_status`` and
    ``last_checked_at`` fields in D1.

    Returns a summary with the number of articles checked and individual
    results.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    results_list = await (
        db.prepare(
            "SELECT id, original_url FROM articles WHERE user_id = ? "
            "AND (original_status = 'unknown' "
            "OR last_checked_at IS NULL "
            "OR last_checked_at < datetime('now', '-30 days')) "
            "ORDER BY last_checked_at ASC NULLS FIRST "
            "LIMIT 10"
        )
        .bind(user_id)
        .all()
    )

    checked: list[dict[str, str]] = []
    now = now_iso()

    for row in results_list:
        article_id = row["id"]
        original_url = row["original_url"]

        try:
            new_status = await check_original_url(original_url)
        except Exception:
            new_status = "unknown"

        await (
            db.prepare(
                "UPDATE articles SET original_status = ?, last_checked_at = ?, "
                "updated_at = ? WHERE id = ? AND user_id = ?"
            )
            .bind(new_status, now, now, article_id, user_id)
            .run()
        )

        checked.append(
            {
                "article_id": article_id,
                "original_url": original_url,
                "original_status": new_status,
            }
        )

    return {"checked": len(checked), "results": checked}


@router.post("/batch-update")
async def batch_update_articles(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Apply updates to multiple articles at once.

    Accepts a JSON body with ``article_ids`` (list of article ID strings)
    and ``updates`` (dict of fields to update).  Only fields in the
    standard updatable set are allowed: ``reading_status``, ``is_favorite``.

    Returns the count of updated articles.
    """
    body = await request.json()
    article_ids = body.get("article_ids", [])
    updates = body.get("updates", {})

    _validate_batch_ids(article_ids)
    if not isinstance(updates, dict) or not updates:
        raise HTTPException(status_code=422, detail="updates must be a non-empty object")

    allowed_fields = {"reading_status", "is_favorite"}
    invalid_fields = set(updates.keys()) - allowed_fields
    if invalid_fields:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid update fields: {', '.join(sorted(invalid_fields))}",
        )

    if "reading_status" in updates:
        _validate_reading_status(updates["reading_status"])
    if "is_favorite" in updates and updates["is_favorite"] not in (0, 1, True, False):
        raise HTTPException(status_code=422, detail="is_favorite must be 0 or 1")

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    now = now_iso()
    set_clauses: list[str] = []
    params: list[Any] = []

    for field_name in allowed_fields:
        if field_name in updates:
            value = updates[field_name]
            if field_name == "is_favorite":
                value = 1 if value else 0
            set_clauses.append(f"{field_name} = ?")
            params.append(value)

    set_clauses.append("updated_at = ?")
    params.append(now)

    sql = f"UPDATE articles SET {', '.join(set_clauses)} WHERE id = ? AND user_id = ?"
    valid_ids = [aid for aid in article_ids if isinstance(aid, str)]

    async def _update_one(article_id: str) -> int:
        bind_params = params + [article_id, user_id]
        result = await db.prepare(sql).bind(*bind_params).run()
        return 1 if result.get("meta", {}).get("changes", 0) > 0 else 0

    results = await asyncio.gather(*[_update_one(aid) for aid in valid_ids])
    return {"updated": sum(results)}


@router.post("/batch-delete")
async def batch_delete_articles(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete multiple articles at once.

    Accepts a JSON body with ``article_ids`` (list of article ID strings).
    Deletes each article's R2 content and D1 row.

    Returns the count of deleted articles.
    """
    body = await request.json()
    article_ids = body.get("article_ids", [])

    _validate_batch_ids(article_ids)

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    valid_ids = [aid for aid in article_ids if isinstance(aid, str)]

    # Batch-verify ownership in a single query
    if not valid_ids:
        return {"deleted": 0}
    placeholders = ", ".join("?" for _ in valid_ids)
    owned_rows = await (
        db.prepare(f"SELECT id FROM articles WHERE id IN ({placeholders}) AND user_id = ?")
        .bind(*valid_ids, user_id)
        .all()
    )
    owned_ids = [r["id"] for r in owned_rows]

    if not owned_ids:
        return {"deleted": 0}

    async def _delete_one(article_id: str) -> None:
        # Delete R2 content first
        await delete_article_content(env.CONTENT, article_id)
        # Delete from D1
        await (
            db.prepare("DELETE FROM articles WHERE id = ? AND user_id = ?")
            .bind(article_id, user_id)
            .run()
        )

    await asyncio.gather(*[_delete_one(aid) for aid in owned_ids])
    return {"deleted": len(owned_ids)}


@router.get("/{article_id}")
async def get_article(
    request: Request,
    response: Response,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Retrieve a single article by ID.

    Returns the article metadata from D1 with tags inline (consistent with
    ``list_articles``).  Only articles belonging to the authenticated user
    are returned.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    # Include tags inline via correlated subquery (same as list_articles).
    tags_sub = (
        "COALESCE("
        "(SELECT json_group_array(json_object("
        "'id', t.id, 'name', t.name"
        ")) FROM article_tags at2 "
        "INNER JOIN tags t ON t.id = at2.tag_id "
        "WHERE at2.article_id = articles.id), '[]')"
    )
    sql = (
        f"SELECT {_LIST_COLUMNS}, markdown_content, {tags_sub} AS tags_json "
        f"FROM articles WHERE id = ? AND user_id = ?"
    )
    row = await db.prepare(sql).bind(article_id, user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Article not found")

    row["tags"] = _parse_tags_json(row)

    response.headers["Cache-Control"] = "private, max-age=60"
    return row


@router.get("/{article_id}/content")
async def get_article_content(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> HTMLResponse:
    """Serve the article's HTML content from R2.

    Returns the clean HTML stored during article processing.  Falls back to
    404 if no HTML content is available in R2.
    """
    env = request.scope["env"]
    db = env.DB
    r2 = env.CONTENT
    user_id = user["user_id"]

    article = await _get_user_article(db, article_id, user_id, fields="id, html_key")

    html_key = article.get("html_key")
    if not html_key:
        raise HTTPException(status_code=404, detail="No content available")

    html_content = await get_content(r2, html_key)
    if html_content is None:
        raise HTTPException(status_code=404, detail="Content not found in storage")

    response = HTMLResponse(content=html_content)
    # Restrictive CSP prevents scripts in fetched article HTML from executing,
    # even if DOMPurify is bypassed.
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; img-src * data:; style-src 'unsafe-inline'"
    )
    # Immutable: article content never changes after processing completes.
    # Re-processing deletes R2 content and resets status, so the old cache
    # entry becomes unreachable (404 on next request).
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response


@router.get("/{article_id}/markdown")
async def get_article_markdown(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    """Serve the article's raw Markdown content.

    Returns the Markdown stored in D1's ``markdown_content`` column as
    ``text/markdown; charset=utf-8``.  Falls back to 404 if no Markdown
    content is available.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    article = await _get_user_article(db, article_id, user_id, fields="id, markdown_content")

    markdown_content = article.get("markdown_content")
    if not markdown_content:
        raise HTTPException(status_code=404, detail="No markdown content available")

    return Response(
        content=markdown_content,
        media_type="text/markdown; charset=utf-8",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/{article_id}/metadata")
async def get_article_metadata(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Retrieve the article's processing metadata from R2.

    Returns archive timestamp, image count, word count, content hash, and
    other provenance information stored during article processing.
    """
    env = request.scope["env"]
    db = env.DB
    r2 = env.CONTENT
    user_id = user["user_id"]

    await _get_user_article(db, article_id, user_id, fields="id")

    metadata = await get_metadata(r2, article_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="No metadata available")

    from fastapi.responses import JSONResponse

    return JSONResponse(
        content=metadata,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.get("/{article_id}/thumbnail")
async def get_article_thumbnail(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    """Serve the article's thumbnail image from R2.

    Returns the thumbnail WebP image stored during article processing.
    Falls back to 404 if no thumbnail is available.
    """
    env = request.scope["env"]
    db = env.DB
    r2 = env.CONTENT
    user_id = user["user_id"]

    article = await _get_user_article(
        db,
        article_id,
        user_id,
        fields="id, thumbnail_key",
    )

    thumbnail_key = article.get("thumbnail_key")
    if not thumbnail_key:
        raise HTTPException(status_code=404, detail="No thumbnail available")

    obj = await r2.get(thumbnail_key)
    if obj is None:
        raise HTTPException(
            status_code=404,
            detail="Thumbnail not found in storage",
        )

    return await _serve_r2_object(obj, media_type="image/webp")


# Extension-to-media-type mapping for article images.
_IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}

# Images are content-addressed by hash — filenames must match this pattern.
_IMAGE_FILENAME_RE = re.compile(r"^[a-f0-9]+\.(webp|jpg|jpeg|png|gif|svg|bin)$")


@router.get("/{article_id}/images/{filename}")
async def get_article_image(
    request: Request,
    article_id: str,
    filename: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    """Serve an article's archived image from R2.

    Images are content-addressed by hash, so they are immutable once stored.
    Returns an aggressive ``Cache-Control`` header accordingly.
    """
    env = request.scope["env"]
    db = env.DB
    r2 = env.CONTENT
    user_id = user["user_id"]

    if not _IMAGE_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid image filename")

    await _get_user_article(db, article_id, user_id, fields="id")

    r2_key = f"articles/{article_id}/images/{filename}"
    obj = await r2.get(r2_key)
    if obj is None:
        raise HTTPException(status_code=404, detail="Image not found in storage")

    # Derive media type from file extension
    ext = ""
    dot_pos = filename.rfind(".")
    if dot_pos != -1:
        ext = filename[dot_pos:].lower()
    media_type = _IMAGE_MEDIA_TYPES.get(ext, "application/octet-stream")

    response = await _serve_r2_object(
        obj,
        media_type=media_type,
        cache_control="public, max-age=31536000, immutable",
    )

    # SVG files can contain embedded JavaScript — serve with protective
    # headers to prevent XSS.
    if media_type == "image/svg+xml":
        response.headers["Content-Disposition"] = "attachment"
        response.headers["Content-Security-Policy"] = "sandbox"

    return response


@router.patch("/{article_id}")
async def update_article(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Update an article's mutable fields.

    Accepts a JSON body with any of: ``reading_status``, ``is_favorite``,
    ``scroll_position``, ``reading_progress``, ``title``.  Only the provided
    fields are updated.  ``updated_at`` is always set to the current time.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    await _get_user_article(db, article_id, user_id, fields="id")

    body = await request.json()
    updatable_fields = {
        "reading_status",
        "is_favorite",
        "scroll_position",
        "reading_progress",
        "title",
    }

    # Validate field lengths
    if "title" in body and isinstance(body["title"], str) and len(body["title"]) > 500:
        raise HTTPException(status_code=400, detail="Title must not exceed 500 characters")
    # Validate enum fields
    if "reading_status" in body:
        _validate_reading_status(body["reading_status"])
    if "is_favorite" in body and body["is_favorite"] not in (0, 1, True, False):
        raise HTTPException(status_code=422, detail="is_favorite must be 0 or 1")
    if "reading_progress" in body:
        rp = body["reading_progress"]
        if not isinstance(rp, (int, float)):
            raise HTTPException(
                status_code=422,
                detail="reading_progress must be a number",
            )
        if not (0.0 <= rp <= 1.0):
            raise HTTPException(
                status_code=422,
                detail="reading_progress must be between 0.0 and 1.0",
            )
    if "scroll_position" in body:
        sp = body["scroll_position"]
        if not isinstance(sp, (int, float)):
            raise HTTPException(
                status_code=422,
                detail="scroll_position must be a number",
            )
        if sp < 0:
            raise HTTPException(
                status_code=422,
                detail="scroll_position must be >= 0",
            )

    set_clauses: list[str] = []
    params: list[Any] = []

    for field_name in updatable_fields:
        if field_name in body:
            value = body[field_name]
            if field_name == "is_favorite":
                value = 1 if value else 0
            set_clauses.append(f"{field_name} = ?")
            params.append(value)

    if not set_clauses:
        raise HTTPException(status_code=422, detail="No updatable fields provided")

    now = now_iso()
    set_clauses.append("updated_at = ?")
    params.append(now)

    params.extend([article_id, user_id])
    sql = f"UPDATE articles SET {', '.join(set_clauses)} WHERE id = ? AND user_id = ?"

    await db.prepare(sql).bind(*params).run()

    # Return the updated article
    updated = await _get_user_article(db, article_id, user_id)
    return updated


@router.delete("/{article_id}", status_code=204)
async def delete_article(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> None:
    """Delete an article.

    Removes the article row from D1 and deletes all associated content from
    R2 (HTML, Markdown, metadata).
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    await _get_user_article(db, article_id, user_id, fields="id")

    # Delete R2 content first — if this fails, D1 row still exists for retry
    await delete_article_content(env.CONTENT, article_id)

    # Delete from D1
    await (
        db.prepare("DELETE FROM articles WHERE id = ? AND user_id = ?")
        .bind(article_id, user_id)
        .run()
    )


@router.post("/{article_id}/retry", status_code=202)
async def retry_article(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Re-queue an article for processing.

    Resets the status to ``pending`` and enqueues a new processing job.
    Deletes all R2 content (text AND audio) so nothing is orphaned.
    If the article had audio in any state, also re-queues TTS generation
    so audio and timing are rebuilt from the new text.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    article = await _get_user_article(
        db,
        article_id,
        user_id,
        fields="id, original_url, status, audio_status",
    )

    # Clean up ALL R2 content (text + audio) before re-processing
    await delete_article_content(env.CONTENT, article_id)

    had_audio = article.get("audio_status") is not None
    now = now_iso()

    if had_audio:
        await (
            db.prepare(
                "UPDATE articles SET status = 'pending', "
                "audio_status = 'pending', audio_key = NULL, "
                "audio_duration_seconds = NULL, updated_at = ? "
                "WHERE id = ?"
            )
            .bind(now, article_id)
            .run()
        )
    else:
        await (
            db.prepare("UPDATE articles SET status = 'pending', updated_at = ? WHERE id = ?")
            .bind(now, article_id)
            .run()
        )

    message: dict[str, Any] = {
        "type": "article_processing",
        "article_id": article_id,
        "url": article["original_url"],
        "user_id": user_id,
    }

    # Chain TTS after text processing so markdown is available first
    if had_audio:
        pref = await (
            db.prepare("SELECT tts_voice FROM user_preferences WHERE user_id = ?")
            .bind(user_id)
            .first()
        )
        message["requeue_tts"] = True
        message["tts_voice"] = pref.get("tts_voice") if pref else "athena"

    await _enqueue_or_fail(env, db, message, article_id)

    return {"id": article_id, "status": "pending"}


@router.post("/{article_id}/process-now")
async def process_now(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Process an article inline (bypasses queue) for debugging.

    Runs the full processing pipeline in the request handler so errors
    are returned directly instead of being lost in queue handler logs.
    """
    from articles.processing import process_article

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    article = await _get_user_article(db, article_id, user_id, fields="id, original_url, status")

    try:
        await process_article(article_id, article["original_url"], env)
        fields = "id, status, title, word_count"
        updated = await _get_user_article(db, article_id, user_id, fields=fields)
        # process_article catches exceptions internally and sets status to
        # 'failed' without re-raising — check the actual status.
        actual_status = updated.get("status", "unknown")
        result = "success" if actual_status == "ready" else "error"
        return {"id": article_id, "result": result, "article": updated}
    except Exception:
        import traceback

        tb = traceback.format_exc()[-500:]
        print(
            json.dumps(
                {
                    "event": "process_now_error",
                    "article_id": article_id,
                    "error": tb,
                }
            )
        )
        return {
            "id": article_id,
            "result": "error",
            "error": "An internal error occurred during processing",
        }


@router.post("/{article_id}/check-original")
async def check_original(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Check if the original URL is still accessible and update original_status.

    Performs an HTTP HEAD request against the article's ``original_url``,
    classifies the result, and updates the ``original_status`` and
    ``last_checked_at`` fields in D1.

    Returns the article ID and the new ``original_status``.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    article = await _get_user_article(
        db,
        article_id,
        user_id,
        fields="id, original_url",
    )

    original_url = article.get("original_url", "")
    new_status = await check_original_url(original_url)
    now = now_iso()

    await (
        db.prepare(
            "UPDATE articles SET original_status = ?, last_checked_at = ?, "
            "updated_at = ? WHERE id = ? AND user_id = ?"
        )
        .bind(new_status, now, now, article_id, user_id)
        .run()
    )

    return {
        "article_id": article_id,
        "original_status": new_status,
        "last_checked_at": now,
    }

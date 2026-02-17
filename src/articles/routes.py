"""Article CRUD routes for Tasche.

Provides endpoints for creating, listing, retrieving, updating, and deleting
saved articles.  All endpoints require authentication via the
``get_current_user`` dependency.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from articles.health import check_original_url
from articles.storage import delete_article_content, get_content, get_metadata
from articles.urls import check_duplicate, extract_domain, validate_url
from auth.dependencies import get_current_user
from wrappers import _to_js_value, d1_first, d1_rows

router = APIRouter()

# Column list for the list endpoint — excludes large fields like markdown_content.
_LIST_COLUMNS = (
    "id, user_id, original_url, final_url, canonical_url, domain, title, "
    "excerpt, author, word_count, reading_time_minutes, image_count, status, "
    "reading_status, is_favorite, listen_later, audio_key, audio_duration_seconds, "
    "audio_status, html_key, markdown_key, thumbnail_key, original_status, "
    "scroll_position, reading_progress, notes, created_at, updated_at"
)

_VALID_READING_STATUSES = {"unread", "reading", "archived"}


async def _get_user_article(
    db: Any,
    article_id: str,
    user_id: str,
    fields: str = "*",
) -> dict[str, Any]:
    """Fetch an article by ID for a user, or raise 404."""
    article = d1_first(
        await db.prepare(f"SELECT {fields} FROM articles WHERE id = ? AND user_id = ?")
        .bind(article_id, user_id)
        .first()
    )
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


@router.post("", status_code=201)
async def create_article(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Save a new article.

    Accepts a JSON body with ``url`` (required) and ``title`` (optional).
    Validates the URL, checks for duplicates across all three URL columns,
    inserts the article into D1 with ``status='pending'``, and enqueues a
    processing job to ``ARTICLE_QUEUE``.
    """
    body = await request.json()
    url = body.get("url", "")
    title = body.get("title")

    # Validate field lengths
    if isinstance(url, str) and len(url) > 2048:
        raise HTTPException(status_code=400, detail="URL must not exceed 2048 characters")
    if title is not None and isinstance(title, str) and len(title) > 500:
        raise HTTPException(status_code=400, detail="Title must not exceed 500 characters")

    # Validate URL
    try:
        url = validate_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    # Check for duplicates
    existing = await check_duplicate(db, user_id, url)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="Article with this URL already exists",
        )

    # Generate ID and insert
    article_id = secrets.token_urlsafe(16)
    domain = extract_domain(url)
    now = datetime.now(UTC).isoformat()

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
        # Handle unique constraint violation (race condition)
        exc_msg = str(exc).lower()
        if "unique" in exc_msg or "constraint" in exc_msg:
            raise HTTPException(
                status_code=409,
                detail="Article with this URL already exists",
            ) from exc
        raise

    # Enqueue processing job
    message = _to_js_value(
        {
            "type": "article_processing",
            "article_id": article_id,
            "url": url,
            "user_id": user_id,
        }
    )
    await env.ARTICLE_QUEUE.send(message)

    return {"id": article_id, "status": "pending"}


@router.get("")
async def list_articles(
    request: Request,
    status: str | None = Query(default=None),
    reading_status: str | None = Query(default=None),
    is_favorite: bool | None = Query(default=None),
    audio_status: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List the authenticated user's articles.

    Supports optional filtering by ``status``, ``reading_status``,
    ``is_favorite``, ``audio_status``, and ``tag``.  Results are ordered by
    ``created_at DESC`` and paginated via ``limit`` and ``offset``.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    where_clauses = ["user_id = ?"]
    params: list[Any] = [user_id]

    if status is not None:
        where_clauses.append("status = ?")
        params.append(status)

    if reading_status is not None:
        where_clauses.append("reading_status = ?")
        params.append(reading_status)

    if is_favorite is not None:
        where_clauses.append("is_favorite = ?")
        params.append(1 if is_favorite else 0)

    if audio_status is not None:
        where_clauses.append("audio_status = ?")
        params.append(audio_status)

    if tag is not None:
        where_clauses.append("id IN (SELECT article_id FROM article_tags WHERE tag_id = ?)")
        params.append(tag)

    where = " AND ".join(where_clauses)
    sql = (
        f"SELECT {_LIST_COLUMNS} FROM articles WHERE {where} "
        "ORDER BY created_at DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    results = await db.prepare(sql).bind(*params).all()
    return d1_rows(results)


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

    results_list = d1_rows(
        await db.prepare(
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
    now = datetime.now(UTC).isoformat()

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


@router.get("/{article_id}")
async def get_article(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Retrieve a single article by ID.

    Returns the article metadata from D1.  Only articles belonging to the
    authenticated user are returned.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    article = await _get_user_article(db, article_id, user_id)
    return article


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

    return HTMLResponse(content=html_content)


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

    return metadata


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

    # Stream from R2 body if available, otherwise fall back to arrayBuffer
    body = getattr(obj, "body", None)

    if body is not None and hasattr(body, "getReader"):
        async def _stream_thumbnail():
            reader = body.getReader()
            try:
                while True:
                    result = await reader.read()
                    done = getattr(result, "done", True)
                    if done:
                        break
                    chunk = getattr(result, "value", None)
                    if chunk is not None:
                        if hasattr(chunk, "to_py"):
                            yield bytes(chunk.to_py())
                        else:
                            yield bytes(chunk)
            finally:
                reader.releaseLock()

        return StreamingResponse(
            _stream_thumbnail(),
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    # Fallback: load entire buffer (for mocks / non-streaming environments)
    image_data = await obj.arrayBuffer()
    return Response(
        content=bytes(image_data) if not isinstance(image_data, bytes) else image_data,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=86400"},
    )


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
        "notes",
    }

    # Validate field lengths
    if "title" in body and isinstance(body["title"], str) and len(body["title"]) > 500:
        raise HTTPException(status_code=400, detail="Title must not exceed 500 characters")
    if "notes" in body and isinstance(body["notes"], str) and len(body["notes"]) > 10000:
        raise HTTPException(status_code=400, detail="Notes must not exceed 10000 characters")

    # Validate enum fields
    if "reading_status" in body and body["reading_status"] not in _VALID_READING_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"reading_status must be one of: {', '.join(sorted(_VALID_READING_STATUSES))}",
        )
    if "is_favorite" in body and body["is_favorite"] not in (0, 1, True, False):
        raise HTTPException(status_code=422, detail="is_favorite must be 0 or 1")

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

    now = datetime.now(UTC).isoformat()
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
    now = datetime.now(UTC).isoformat()

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

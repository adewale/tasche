"""Article CRUD routes for Tasche.

Provides endpoints for creating, listing, retrieving, updating, and deleting
saved articles.  All endpoints require authentication via the
``get_current_user`` dependency.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from articles.health import check_original_url
from articles.storage import (
    article_key,
    delete_article_content,
    get_content,
    get_metadata,
)
from articles.urls import check_duplicate, extract_domain, validate_url
from auth.dependencies import get_current_user
from utils import generate_id, now_iso
from wrappers import stream_r2_body

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


async def _stream_r2_object(
    r2_obj: Any,
    media_type: str,
    cache_control: str = "public, max-age=86400",
) -> Response:
    """Stream an R2 object as an HTTP response.

    Uses :func:`wrappers.stream_r2_body` for all JS ReadableStream
    interaction.  Falls back gracefully in mock / non-streaming environments.
    """
    return StreamingResponse(
        stream_r2_body(r2_obj),
        media_type=media_type,
        headers={"Cache-Control": cache_control},
    )


# Column list for the list endpoint — excludes large fields like markdown_content.
_LIST_COLUMNS = (
    "id, user_id, original_url, final_url, canonical_url, domain, title, "
    "excerpt, author, word_count, reading_time_minutes, image_count, status, "
    "reading_status, is_favorite, audio_key, audio_duration_seconds, "
    "audio_status, html_key, thumbnail_key, original_key, original_status, "
    "scroll_position, reading_progress, created_at, updated_at"
)

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


def _validate_reading_status(value: str) -> None:
    """Raise 422 if *value* is not a valid reading_status."""
    if value not in _VALID_READING_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"reading_status must be one of: {', '.join(sorted(_VALID_READING_STATUSES))}",
        )


async def _get_user_article(
    db: Any,
    article_id: str,
    user_id: str,
    fields: str = "*",
) -> dict[str, Any]:
    """Fetch an article by ID for a user, or raise 404.

    .. warning::

        The *fields* parameter is interpolated directly into the SQL query.
        It must **never** contain user-supplied input.  Only pass hard-coded
        column lists defined in this module.
    """
    article = await (
        db.prepare(f"SELECT {fields} FROM articles WHERE id = ? AND user_id = ?")
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
        # Reset status so the pipeline re-processes the article
        update_sql = "UPDATE articles SET status = 'pending'"
        if listen_later:
            update_sql += ", audio_status = 'pending'"
        update_sql += ", updated_at = ? WHERE id = ?"
        await db.prepare(update_sql).bind(now, article_id).run()
    else:
        article_id = generate_id()
        domain = extract_domain(url)

        columns = (
            "id, user_id, original_url, domain, title, "
            "status, reading_status, is_favorite"
        )
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

    # Enqueue processing job
    await _enqueue_or_fail(
        env,
        db,
        {
            "type": "article_processing",
            "article_id": article_id,
            "url": url,
            "user_id": user_id,
        },
        article_id,
    )

    result: dict[str, Any] = {"id": article_id, "status": "pending"}
    if is_update:
        result["updated"] = True
        result["created_at"] = existing.get("created_at", "")
    return result


@router.get("")
async def list_articles(
    request: Request,
    status: str | None = Query(default=None),
    reading_status: str | None = Query(default=None),
    is_favorite: bool | None = Query(default=None),
    audio_status: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List the authenticated user's articles.

    Supports optional filtering by ``status``, ``reading_status``,
    ``is_favorite``, ``audio_status``, and ``tag``.  Results are ordered
    according to ``sort`` (default ``newest`` / ``created_at DESC``) and
    paginated via ``limit`` and ``offset``.

    Valid ``sort`` values: ``newest``, ``oldest``, ``shortest``,
    ``longest``, ``title_asc``.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

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
    order_by = _VALID_SORT_OPTIONS.get(sort, _VALID_SORT_OPTIONS["newest"])
    sql = f"SELECT {_LIST_COLUMNS} FROM articles WHERE {where} ORDER BY {order_by} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    return await db.prepare(sql).bind(*params).all()


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

    if not isinstance(article_ids, list) or not article_ids:
        raise HTTPException(status_code=422, detail="article_ids must be a non-empty list")
    if len(article_ids) > 100:
        raise HTTPException(status_code=422, detail="Cannot update more than 100 articles at once")
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

    updated_count = 0
    for article_id in article_ids:
        if not isinstance(article_id, str):
            continue
        bind_params = params + [article_id, user_id]
        sql = f"UPDATE articles SET {', '.join(set_clauses)} WHERE id = ? AND user_id = ?"
        result = await db.prepare(sql).bind(*bind_params).run()
        if result.get("meta", {}).get("changes", 0) > 0:
            updated_count += 1

    return {"updated": updated_count}


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

    if not isinstance(article_ids, list) or not article_ids:
        raise HTTPException(status_code=422, detail="article_ids must be a non-empty list")
    if len(article_ids) > 100:
        raise HTTPException(status_code=422, detail="Cannot delete more than 100 articles at once")

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    deleted_count = 0
    for article_id in article_ids:
        if not isinstance(article_id, str):
            continue
        # Verify ownership before deleting
        article = await (
            db.prepare("SELECT id FROM articles WHERE id = ? AND user_id = ?")
            .bind(article_id, user_id)
            .first()
        )
        if article is None:
            continue
        # Delete R2 content first
        await delete_article_content(env.CONTENT, article_id)
        # Delete from D1
        await (
            db.prepare("DELETE FROM articles WHERE id = ? AND user_id = ?")
            .bind(article_id, user_id)
            .run()
        )
        deleted_count += 1

    return {"deleted": deleted_count}


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

    article = await _get_user_article(
        db,
        article_id,
        user_id,
        fields=_LIST_COLUMNS + ", markdown_content",
    )
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

    response = HTMLResponse(content=html_content)
    # Restrictive CSP prevents scripts in fetched article HTML from executing,
    # even if DOMPurify is bypassed.
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; img-src * data:; style-src 'unsafe-inline'"
    )
    # Ensure the SW's network-first strategy always revalidates — content
    # can change when a user retries processing (#10).
    response.headers["Cache-Control"] = "private, no-cache"
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
        headers={"Cache-Control": "private, max-age=300"},
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

    return await _stream_r2_object(obj, media_type="image/webp")


@router.get("/{article_id}/screenshot")
async def get_article_screenshot(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    """Serve the article's full-page archival screenshot from R2.

    Returns the full-page WebP screenshot stored during article processing.
    Falls back to 404 if no screenshot is available.
    """
    env = request.scope["env"]
    db = env.DB
    r2 = env.CONTENT
    user_id = user["user_id"]

    article = await _get_user_article(
        db,
        article_id,
        user_id,
        fields="id, original_key",
    )

    original_key = article.get("original_key")
    if not original_key:
        raise HTTPException(status_code=404, detail="No screenshot available")

    obj = await r2.get(original_key)
    if obj is None:
        raise HTTPException(
            status_code=404,
            detail="Screenshot not found in storage",
        )

    return await _stream_r2_object(obj, media_type="image/webp")


# Extension-to-media-type mapping for article images.
_IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}


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

    return await _stream_r2_object(
        obj,
        media_type=media_type,
        cache_control="public, max-age=31536000, immutable",
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
    Works for any article status — useful for re-extracting content with
    an updated pipeline.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    article = await _get_user_article(db, article_id, user_id, fields="id, original_url, status")

    now = now_iso()
    await (
        db.prepare("UPDATE articles SET status = 'pending', updated_at = ? WHERE id = ?")
        .bind(now, article_id)
        .run()
    )

    await _enqueue_or_fail(
        env,
        db,
        {
            "type": "article_processing",
            "article_id": article_id,
            "url": article["original_url"],
            "user_id": user_id,
        },
        article_id,
    )

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
    import traceback

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
    except Exception as exc:
        return {
            "id": article_id,
            "result": "error",
            "error": str(exc),
            "traceback": traceback.format_exc(),
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

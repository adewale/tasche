"""Tag management routes for Tasche.

Provides endpoints for creating, listing, and deleting tags, as well as
adding and removing tags from articles.  All endpoints require authentication
via the ``get_current_user`` dependency.

Two routers are exported:

* ``router`` — tag CRUD, mounted at ``/api/tags``
* ``article_tags_router`` — article-tag associations, mounted at ``/api/articles``
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.dependencies import get_current_user
from wrappers import d1_first, d1_rows

router = APIRouter()
article_tags_router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_user_tag(
    db: Any, tag_id: str, user_id: str,
) -> dict[str, Any]:
    """Fetch a tag by ID for a user, or raise 404."""
    tag = d1_first(
        await db.prepare(
            "SELECT id, user_id, name, created_at FROM tags "
            "WHERE id = ? AND user_id = ?"
        )
        .bind(tag_id, user_id)
        .first()
    )
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")
    return tag


async def _get_user_article_id(
    db: Any, article_id: str, user_id: str,
) -> dict[str, Any]:
    """Verify an article belongs to a user, or raise 404."""
    article = d1_first(
        await db.prepare(
            "SELECT id FROM articles WHERE id = ? AND user_id = ?"
        )
        .bind(article_id, user_id)
        .first()
    )
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


# ---------------------------------------------------------------------------
# Tag CRUD
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_tag(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Create a new tag.

    Accepts a JSON body with ``name`` (required).  Tag names must be unique
    per user.
    """
    body = await request.json()
    name = body.get("name", "").strip()

    if not name:
        raise HTTPException(status_code=422, detail="Tag name is required")

    if len(name) > 100:
        raise HTTPException(status_code=400, detail="Tag name must not exceed 100 characters")

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    # Check for duplicate tag name for this user
    existing = d1_first(
        await db.prepare(
            "SELECT id FROM tags WHERE user_id = ? AND name = ?"
        )
        .bind(user_id, name)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="Tag with this name already exists",
        )

    tag_id = secrets.token_urlsafe(16)
    now = datetime.now(UTC).isoformat()

    await (
        db.prepare(
            "INSERT INTO tags (id, user_id, name, created_at) "
            "VALUES (?, ?, ?, ?)"
        )
        .bind(tag_id, user_id, name, now)
        .run()
    )

    return {"id": tag_id, "user_id": user_id, "name": name, "created_at": now}


@router.get("")
async def list_tags(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List the authenticated user's tags.

    Returns all tags ordered by name.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    results = await (
        db.prepare(
            "SELECT t.id, t.user_id, t.name, t.created_at, "
            "COUNT(at.article_id) as article_count "
            "FROM tags t LEFT JOIN article_tags at ON t.id = at.tag_id "
            "WHERE t.user_id = ? "
            "GROUP BY t.id "
            "ORDER BY t.name"
        )
        .bind(user_id)
        .all()
    )
    return d1_rows(results)


@router.delete("/{tag_id}", status_code=204)
async def delete_tag(
    request: Request,
    tag_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> None:
    """Delete a tag.

    Removes the tag and all article-tag associations (via ON DELETE CASCADE).
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    await _get_user_tag(db, tag_id, user_id)

    await (
        db.prepare("DELETE FROM tags WHERE id = ? AND user_id = ?")
        .bind(tag_id, user_id)
        .run()
    )


# ---------------------------------------------------------------------------
# Article-tag associations (mounted at /api/articles)
# ---------------------------------------------------------------------------


@article_tags_router.post("/{article_id}/tags", status_code=201)
async def add_tag_to_article(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Add a tag to an article.

    Accepts a JSON body with ``tag_id`` (required).  Both the article and the
    tag must belong to the authenticated user.
    """
    body = await request.json()
    tag_id = body.get("tag_id", "")

    if not tag_id:
        raise HTTPException(status_code=422, detail="tag_id is required")

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    await _get_user_article_id(db, article_id, user_id)
    await _get_user_tag(db, tag_id, user_id)

    # Check if association already exists
    existing = d1_first(
        await db.prepare(
            "SELECT article_id FROM article_tags "
            "WHERE article_id = ? AND tag_id = ?"
        )
        .bind(article_id, tag_id)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="Tag already applied to this article",
        )

    await (
        db.prepare(
            "INSERT INTO article_tags (article_id, tag_id) VALUES (?, ?)"
        )
        .bind(article_id, tag_id)
        .run()
    )

    return {"article_id": article_id, "tag_id": tag_id}


@article_tags_router.delete(
    "/{article_id}/tags/{tag_id}", status_code=204,
)
async def remove_tag_from_article(
    request: Request,
    article_id: str,
    tag_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> None:
    """Remove a tag from an article.

    Both the article and the tag must belong to the authenticated user.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    await _get_user_article_id(db, article_id, user_id)
    await _get_user_tag(db, tag_id, user_id)

    await (
        db.prepare(
            "DELETE FROM article_tags WHERE article_id = ? AND tag_id = ?"
        )
        .bind(article_id, tag_id)
        .run()
    )


@article_tags_router.get("/{article_id}/tags")
async def get_article_tags(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List all tags for a specific article.

    The article must belong to the authenticated user.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    await _get_user_article_id(db, article_id, user_id)

    results = await (
        db.prepare(
            "SELECT t.id, t.user_id, t.name, t.created_at "
            "FROM tags t INNER JOIN article_tags at ON t.id = at.tag_id "
            "WHERE at.article_id = ? AND t.user_id = ? "
            "ORDER BY t.name"
        )
        .bind(article_id, user_id)
        .all()
    )
    return d1_rows(results)

"""Highlight CRUD routes for Tasche.

Provides endpoints for creating, listing, retrieving, updating, and deleting
highlights on articles, plus a random highlight endpoint for spaced repetition.
All endpoints require authentication via the ``get_current_user`` dependency.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth.dependencies import get_current_user
from utils import generate_id, now_iso

router = APIRouter()

# Separate router for article-scoped highlight endpoints
article_highlights_router = APIRouter()

_VALID_COLORS = {"yellow", "green", "blue", "pink"}


async def _get_user_article(db: Any, article_id: str, user_id: str) -> dict[str, Any]:
    """Verify the article belongs to the user, or raise 404."""
    article = await (
        db.prepare("SELECT id, title FROM articles WHERE id = ? AND user_id = ?")
        .bind(article_id, user_id)
        .first()
    )
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return article


async def _get_highlight_for_user(db: Any, highlight_id: str, user_id: str) -> dict[str, Any]:
    """Fetch a highlight by ID, verifying it belongs to the user's article."""
    highlight = await (
        db.prepare(
            "SELECT h.* FROM highlights h "
            "INNER JOIN articles a ON h.article_id = a.id "
            "WHERE h.id = ? AND a.user_id = ?"
        )
        .bind(highlight_id, user_id)
        .first()
    )
    if highlight is None:
        raise HTTPException(status_code=404, detail="Highlight not found")
    return highlight


# ---------------------------------------------------------------------------
# Article-scoped endpoints (mounted under /api/articles)
# ---------------------------------------------------------------------------


@article_highlights_router.get("/{article_id}/highlights")
async def list_article_highlights(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List all highlights for a specific article."""
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    await _get_user_article(db, article_id, user_id)

    return await (
        db.prepare("SELECT * FROM highlights WHERE article_id = ? ORDER BY created_at ASC")
        .bind(article_id)
        .all()
    )


@article_highlights_router.post("/{article_id}/highlights", status_code=201)
async def create_highlight(
    request: Request,
    article_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Create a new highlight on an article.

    Accepts a JSON body with ``text`` (required), and optional ``note``,
    ``prefix``, ``suffix``, and ``color``.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    await _get_user_article(db, article_id, user_id)

    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="text is required")
    if len(text) > 10000:
        raise HTTPException(status_code=400, detail="text must not exceed 10000 characters")

    note = body.get("note", "")
    if isinstance(note, str) and len(note) > 5000:
        raise HTTPException(status_code=400, detail="note must not exceed 5000 characters")

    prefix = body.get("prefix", "")
    suffix = body.get("suffix", "")
    color = body.get("color", "yellow")

    if color not in _VALID_COLORS:
        raise HTTPException(
            status_code=422,
            detail=f"color must be one of: {', '.join(sorted(_VALID_COLORS))}",
        )

    highlight_id = generate_id()
    now = now_iso()

    await (
        db.prepare(
            "INSERT INTO highlights "
            "(id, article_id, text, note, prefix, suffix, color, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        .bind(highlight_id, article_id, text, note, prefix, suffix, color, now, now)
        .run()
    )

    return {
        "id": highlight_id,
        "article_id": article_id,
        "text": text,
        "note": note,
        "prefix": prefix,
        "suffix": suffix,
        "color": color,
        "created_at": now,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# Highlight-scoped endpoints (mounted under /api/highlights)
# ---------------------------------------------------------------------------


@router.get("")
async def list_all_highlights(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List all highlights across all articles, with article title.

    Results are paginated via ``limit`` and ``offset`` and ordered by
    creation date (newest first).
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    return await (
        db.prepare(
            "SELECT h.*, a.title AS article_title "
            "FROM highlights h "
            "INNER JOIN articles a ON h.article_id = a.id "
            "WHERE a.user_id = ? "
            "ORDER BY h.created_at DESC "
            "LIMIT ? OFFSET ?"
        )
        .bind(user_id, limit, offset)
        .all()
    )


@router.get("/random")
async def random_highlight(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return a random highlight for spaced repetition review.

    Joins with articles to include the article title and provides
    surrounding context (prefix/suffix) for the reveal step.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    highlight = await (
        db.prepare(
            "SELECT h.*, a.title AS article_title "
            "FROM highlights h "
            "INNER JOIN articles a ON h.article_id = a.id "
            "WHERE a.user_id = ? "
            "ORDER BY RANDOM() LIMIT 1"
        )
        .bind(user_id)
        .first()
    )

    if highlight is None:
        raise HTTPException(status_code=404, detail="No highlights found")

    return highlight


@router.patch("/{highlight_id}")
async def update_highlight(
    request: Request,
    highlight_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Update a highlight's note or color.

    Accepts a JSON body with ``note`` and/or ``color``.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    await _get_highlight_for_user(db, highlight_id, user_id)

    body = await request.json()
    set_clauses: list[str] = []
    params: list[Any] = []

    if "note" in body:
        note = body["note"]
        if isinstance(note, str) and len(note) > 5000:
            raise HTTPException(status_code=400, detail="note must not exceed 5000 characters")
        set_clauses.append("note = ?")
        params.append(note)

    if "color" in body:
        color = body["color"]
        if color not in _VALID_COLORS:
            raise HTTPException(
                status_code=422,
                detail=f"color must be one of: {', '.join(sorted(_VALID_COLORS))}",
            )
        set_clauses.append("color = ?")
        params.append(color)

    if not set_clauses:
        raise HTTPException(status_code=422, detail="No updatable fields provided")

    now = now_iso()
    set_clauses.append("updated_at = ?")
    params.append(now)

    params.append(highlight_id)
    sql = f"UPDATE highlights SET {', '.join(set_clauses)} WHERE id = ?"
    await db.prepare(sql).bind(*params).run()

    # Return the updated highlight
    updated = await (
        db.prepare(
            "SELECT h.*, a.title AS article_title "
            "FROM highlights h "
            "INNER JOIN articles a ON h.article_id = a.id "
            "WHERE h.id = ?"
        )
        .bind(highlight_id)
        .first()
    )
    return updated


@router.delete("/{highlight_id}", status_code=204)
async def delete_highlight(
    request: Request,
    highlight_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> None:
    """Delete a highlight."""
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    await _get_highlight_for_user(db, highlight_id, user_id)

    await db.prepare("DELETE FROM highlights WHERE id = ?").bind(highlight_id).run()

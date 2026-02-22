"""Full-text search routes for Tasche.

Provides the ``GET /api/search?q=...`` endpoint that searches across article
title, excerpt, and markdown_content via the ``articles_fts`` FTS5 virtual
table.  Results are ordered by relevance and filtered by the authenticated
user's ``user_id``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from auth.dependencies import get_current_user

router = APIRouter()

# Characters that have special meaning in FTS5 query syntax.
_FTS5_SPECIAL_CHARS = set('"*+-^():{}[]|\\')


def _sanitize_fts5_query(query: str) -> str:
    """Sanitize a search query for safe use with FTS5 MATCH.

    Wraps each word in double quotes so FTS5 treats them as literals,
    stripping any FTS5 operator characters. Example:
    ``hello world`` -> ``"hello" "world"``.
    ``OR AND NOT`` -> ``"OR" "AND" "NOT"``
    """
    # Split on whitespace and process each token
    tokens = query.split()
    safe_tokens = []
    for token in tokens:
        # Remove any FTS5 special characters
        cleaned = "".join(ch for ch in token if ch not in _FTS5_SPECIAL_CHARS)
        if cleaned:
            # Wrap in double quotes to treat as a literal
            safe_tokens.append(f'"{cleaned}"')
    return " ".join(safe_tokens)


# Column list for search results — same as the articles list endpoint.
_SEARCH_COLUMNS = (
    "id, user_id, original_url, final_url, canonical_url, domain, title, "
    "excerpt, author, word_count, reading_time_minutes, image_count, status, "
    "reading_status, is_favorite, audio_key, audio_duration_seconds, "
    "audio_status, html_key, thumbnail_key, original_key, original_status, "
    "scroll_position, reading_progress, created_at, updated_at"
)


@router.get("")
async def search_articles(
    request: Request,
    q: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Search articles using FTS5 full-text search.

    Searches across title, excerpt, and markdown_content in the
    ``articles_fts`` virtual table.  Results are filtered by the
    authenticated user's ``user_id`` and ordered by FTS5 relevance.

    Parameters
    ----------
    q:
        The search query string.  Empty queries return an empty list.
    limit:
        Maximum number of results to return (1-100, default 20).
    offset:
        Number of results to skip for pagination (default 0).
    """
    q = q.strip()
    if not q:
        raise HTTPException(status_code=422, detail="Search query is required")

    # Sanitize the query to prevent FTS5 syntax injection
    safe_q = _sanitize_fts5_query(q)
    if not safe_q:
        raise HTTPException(status_code=422, detail="Search query is required")

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    # Prefix columns with "articles." to avoid ambiguity with FTS5 table columns.
    prefixed = ", ".join(f"articles.{c.strip()}" for c in _SEARCH_COLUMNS.split(","))
    sql = (
        f"SELECT {prefixed} FROM articles "
        "INNER JOIN articles_fts ON articles.rowid = articles_fts.rowid "
        "WHERE articles_fts MATCH ? AND articles.user_id = ? "
        "ORDER BY articles_fts.rank "
        "LIMIT ? OFFSET ?"
    )

    return await db.prepare(sql).bind(safe_q, user_id, limit, offset).all()

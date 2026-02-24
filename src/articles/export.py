"""Data export routes for Tasche.

Provides endpoints for exporting the user's articles in JSON and Netscape
bookmark HTML formats.  All endpoints require authentication via the
``get_current_user`` dependency.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from auth.dependencies import get_current_user

router = APIRouter()


async def _get_all_articles_with_tags(
    db: Any,
    user_id: str,
) -> list[dict[str, Any]]:
    """Fetch all articles for a user with their associated tags.

    Returns a list of article dicts, each augmented with a ``tags`` key
    containing a list of tag name strings.
    """
    articles = await (
        db.prepare("SELECT * FROM articles WHERE user_id = ? ORDER BY created_at DESC")
        .bind(user_id)
        .all()
    )

    if not articles:
        return []

    # Fetch all tags for this user's articles in a single query
    tag_rows = await (
        db.prepare(
            "SELECT at.article_id, t.name "
            "FROM article_tags at "
            "INNER JOIN tags t ON at.tag_id = t.id "
            "WHERE t.user_id = ? "
            "ORDER BY t.name"
        )
        .bind(user_id)
        .all()
    )

    # Build a mapping of article_id -> list of tag names
    tags_by_article: dict[str, list[str]] = {}
    for row in tag_rows:
        article_id = row["article_id"]
        if article_id not in tags_by_article:
            tags_by_article[article_id] = []
        tags_by_article[article_id].append(row["name"])

    # Augment each article with its tags
    for article in articles:
        article["tags"] = tags_by_article.get(article["id"], [])

    return articles


def _iso_to_unix(iso_str: str | None) -> int:
    """Convert an ISO 8601 timestamp string to a Unix timestamp.

    Returns 0 if the input is ``None`` or cannot be parsed.
    """
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return 0


def _escape_html(text: str) -> str:
    """Escape HTML special characters in text."""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


@router.get("/json")
async def export_json(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    """Export all articles as a JSON array.

    Each article includes all D1 columns plus a ``tags`` key containing
    a list of tag name strings.  The response includes a
    ``Content-Disposition`` header to trigger a browser download.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    articles = await _get_all_articles_with_tags(db, user_id)

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    filename = f"tasche-export-{date_str}.json"

    content = json.dumps(articles, indent=2, ensure_ascii=False)

    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/html")
async def export_html(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    """Export all articles as Netscape bookmark format HTML.

    This is the standard format used by browsers and every read-it-later
    app for import/export.  The response includes a ``Content-Disposition``
    header to trigger a browser download.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    articles = await _get_all_articles_with_tags(db, user_id)

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    filename = f"tasche-export-{date_str}.html"

    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Tasche Export</TITLE>",
        "<H1>Tasche Export</H1>",
        "<DL><p>",
    ]

    for article in articles:
        url = article.get("original_url", "")
        title = article.get("title") or url
        excerpt = article.get("excerpt") or ""
        add_date = _iso_to_unix(article.get("created_at"))
        tags = article.get("tags", [])

        tag_attr = ""
        if tags:
            tag_attr = f' TAGS="{_escape_html(",".join(tags))}"'

        lines.append(
            f'  <DT><A HREF="{_escape_html(url)}" ADD_DATE="{add_date}"{tag_attr}>'
            f"{_escape_html(title)}</A>"
        )
        if excerpt:
            lines.append(f"  <DD>{_escape_html(excerpt)}")

    lines.append("</DL><p>")

    content = "\n".join(lines)

    return Response(
        content=content,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )

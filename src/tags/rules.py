"""Tag rules routes for auto-tagging articles.

Provides endpoints for creating, listing, and deleting tag rules.
Rules are evaluated during article processing to automatically apply
tags based on domain, title, or URL patterns.

Exported router is mounted at ``/api/tag-rules`` in ``entry.py``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.dependencies import get_current_user
from utils import generate_id, now_iso

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/tag-rules — List all rules
# ---------------------------------------------------------------------------


@router.get("")
async def list_tag_rules(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List all tag rules for the authenticated user.

    Joins with tags to include the tag name in each rule.
    Only returns rules whose tags belong to the current user.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    return await (
        db.prepare(
            "SELECT tr.id, tr.tag_id, tr.match_type, tr.pattern, tr.created_at, "
            "t.name as tag_name "
            "FROM tag_rules tr "
            "INNER JOIN tags t ON tr.tag_id = t.id "
            "WHERE t.user_id = ? "
            "ORDER BY t.name, tr.match_type"
        )
        .bind(user_id)
        .all()
    )


# ---------------------------------------------------------------------------
# POST /api/tag-rules — Create a rule
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_tag_rule(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Create a new tag rule.

    Accepts a JSON body with ``tag_id``, ``match_type``, and ``pattern``.
    The tag must belong to the authenticated user.
    """
    body = await request.json()
    tag_id = body.get("tag_id", "").strip()
    match_type = body.get("match_type", "").strip()
    pattern = body.get("pattern", "").strip()

    if not tag_id:
        raise HTTPException(status_code=422, detail="tag_id is required")
    if not match_type:
        raise HTTPException(status_code=422, detail="match_type is required")
    if match_type not in ("domain", "title_contains", "url_contains"):
        raise HTTPException(
            status_code=400,
            detail="match_type must be one of: domain, title_contains, url_contains",
        )
    if not pattern:
        raise HTTPException(status_code=422, detail="pattern is required")
    if len(pattern) > 500:
        raise HTTPException(
            status_code=400,
            detail="pattern must not exceed 500 characters",
        )

    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    # Verify the tag belongs to the current user
    tag = await (
        db.prepare("SELECT id, name FROM tags WHERE id = ? AND user_id = ?")
        .bind(tag_id, user_id)
        .first()
    )
    if tag is None:
        raise HTTPException(status_code=404, detail="Tag not found")

    # Check for duplicate rule (same tag, match_type, pattern)
    existing = await (
        db.prepare("SELECT id FROM tag_rules WHERE tag_id = ? AND match_type = ? AND pattern = ?")
        .bind(tag_id, match_type, pattern)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="A rule with this tag, type, and pattern already exists",
        )

    rule_id = generate_id()
    now = now_iso()

    await (
        db.prepare(
            "INSERT INTO tag_rules (id, tag_id, match_type, pattern, created_at) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        .bind(rule_id, tag_id, match_type, pattern, now)
        .run()
    )

    return {
        "id": rule_id,
        "tag_id": tag_id,
        "tag_name": tag["name"],
        "match_type": match_type,
        "pattern": pattern,
        "created_at": now,
    }


# ---------------------------------------------------------------------------
# DELETE /api/tag-rules/{rule_id} — Delete a rule
# ---------------------------------------------------------------------------


@router.delete("/{rule_id}", status_code=204)
async def delete_tag_rule(
    request: Request,
    rule_id: str,
    user: dict[str, Any] = Depends(get_current_user),
) -> None:
    """Delete a tag rule.

    The rule's tag must belong to the authenticated user.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    # Verify the rule exists and belongs to the user (via tag ownership)
    rule = await (
        db.prepare(
            "SELECT tr.id FROM tag_rules tr "
            "INNER JOIN tags t ON tr.tag_id = t.id "
            "WHERE tr.id = ? AND t.user_id = ?"
        )
        .bind(rule_id, user_id)
        .first()
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Tag rule not found")

    await db.prepare("DELETE FROM tag_rules WHERE id = ?").bind(rule_id).run()

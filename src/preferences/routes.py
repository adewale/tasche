"""User preferences routes for Tasche.

Provides endpoints for reading and updating user preferences such as
TTS voice selection.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.dependencies import get_current_user
from utils import now_iso

router = APIRouter()

_VALID_VOICES = ("athena", "orion")


@router.get("")
async def get_preferences(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the current user's preferences.

    Defaults to ``{"tts_voice": "athena"}`` if no preferences row exists.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    row = await (
        db.prepare("SELECT tts_voice FROM user_preferences WHERE user_id = ?").bind(user_id).first()
    )

    tts_voice = row.get("tts_voice") if row else "athena"
    return {"tts_voice": tts_voice}


@router.patch("")
async def update_preferences(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Update the current user's preferences.

    Accepts ``{"tts_voice": "athena"|"orion"}``.  Returns 422 for invalid values.
    Uses INSERT OR REPLACE (UPSERT) to create or update the row.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    body = await request.json()
    tts_voice = body.get("tts_voice")

    if tts_voice is not None and tts_voice not in _VALID_VOICES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid tts_voice: must be one of {_VALID_VOICES}",
        )

    if tts_voice is None:
        raise HTTPException(status_code=422, detail="No valid fields to update")

    now = now_iso()
    await (
        db.prepare(
            "INSERT INTO user_preferences (user_id, tts_voice, created_at, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET tts_voice = ?, updated_at = ?"
        )
        .bind(user_id, tts_voice, now, now, tts_voice, now)
        .run()
    )

    return {"tts_voice": tts_voice}

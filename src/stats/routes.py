"""Reading statistics endpoint for Tasche.

Provides aggregated reading statistics computed from existing D1 data.
All queries are scoped to the authenticated user.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from auth.dependencies import get_current_user

router = APIRouter()


@router.get("")
async def get_stats(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return aggregated reading statistics for the authenticated user.

    Computes totals, breakdowns by status, weekly/monthly activity,
    top domains, reading streak, average reading time, and monthly trends
    from the articles table in D1.
    """
    env = request.scope["env"]
    db = env.DB
    user_id = user["user_id"]

    # 1. Total articles
    total_row = await (
        db.prepare("SELECT COUNT(*) AS cnt FROM articles WHERE user_id = ?").bind(user_id).first()
    )
    total_articles = (total_row or {}).get("cnt", 0)

    # 2. Total words read (archived articles only)
    words_row = await (
        db.prepare(
            "SELECT COALESCE(SUM(word_count), 0) AS total "
            "FROM articles WHERE user_id = ? AND reading_status = 'archived'"
        )
        .bind(user_id)
        .first()
    )
    total_words_read = (words_row or {}).get("total", 0)

    # 3. Articles by reading status
    status_rows = await (
        db.prepare(
            "SELECT reading_status, COUNT(*) AS cnt "
            "FROM articles WHERE user_id = ? "
            "GROUP BY reading_status"
        )
        .bind(user_id)
        .all()
    )
    articles_by_status = {"unread": 0, "archived": 0}
    for row in status_rows:
        status = row.get("reading_status", "")
        if status in articles_by_status:
            articles_by_status[status] = row.get("cnt", 0)

    # 4-7. Weekly/monthly activity (saved + archived) in a single query
    activity_row = await (
        db.prepare(
            "SELECT "
            "COUNT(CASE WHEN created_at >= datetime('now', '-7 days') THEN 1 END) AS saved_week, "
            "COUNT(CASE WHEN created_at >= datetime('now', '-30 days') THEN 1 END) AS saved_month, "
            "COUNT(CASE WHEN reading_status = 'archived' "
            "AND updated_at >= datetime('now', '-7 days') THEN 1 END) AS archived_week, "
            "COUNT(CASE WHEN reading_status = 'archived' "
            "AND updated_at >= datetime('now', '-30 days') THEN 1 END) AS archived_month "
            "FROM articles WHERE user_id = ?"
        )
        .bind(user_id)
        .first()
    )
    activity = activity_row or {}
    articles_this_week = activity.get("saved_week", 0)
    articles_this_month = activity.get("saved_month", 0)
    archived_this_week = activity.get("archived_week", 0)
    archived_this_month = activity.get("archived_month", 0)

    # 8. Top domains (top 10)
    domain_rows = await (
        db.prepare(
            "SELECT domain, COUNT(*) AS cnt "
            "FROM articles WHERE user_id = ? AND domain IS NOT NULL "
            "GROUP BY domain ORDER BY cnt DESC LIMIT 10"
        )
        .bind(user_id)
        .all()
    )
    top_domains = [
        {"domain": row.get("domain", ""), "count": row.get("cnt", 0)} for row in domain_rows
    ]

    # 9. Reading streak (consecutive days with at least one archived article)
    streak_rows = await (
        db.prepare(
            "SELECT DISTINCT date(updated_at) AS d "
            "FROM articles "
            "WHERE user_id = ? AND reading_status = 'archived' "
            "ORDER BY d DESC"
        )
        .bind(user_id)
        .all()
    )
    reading_streak_days = _calculate_streak(streak_rows)

    # 10. Average reading time
    avg_row = await (
        db.prepare(
            "SELECT AVG(reading_time_minutes) AS avg_rt "
            "FROM articles "
            "WHERE user_id = ? AND reading_time_minutes IS NOT NULL"
        )
        .bind(user_id)
        .first()
    )
    avg_val = (avg_row or {}).get("avg_rt")
    avg_reading_time_minutes = round(avg_val, 1) if avg_val is not None else 0

    # 11. Articles by month (last 12 months)
    monthly_saved_rows = await (
        db.prepare(
            "SELECT strftime('%Y-%m', created_at) AS month, COUNT(*) AS cnt "
            "FROM articles WHERE user_id = ? "
            "AND created_at >= datetime('now', '-12 months') "
            "GROUP BY month ORDER BY month"
        )
        .bind(user_id)
        .all()
    )
    monthly_archived_rows = await (
        db.prepare(
            "SELECT strftime('%Y-%m', updated_at) AS month, COUNT(*) AS cnt "
            "FROM articles WHERE user_id = ? AND reading_status = 'archived' "
            "AND updated_at >= datetime('now', '-12 months') "
            "GROUP BY month ORDER BY month"
        )
        .bind(user_id)
        .all()
    )

    # Merge saved and archived into a single list
    saved_map: dict[str, int] = {}
    for row in monthly_saved_rows:
        m = row.get("month", "")
        if m:
            saved_map[m] = row.get("cnt", 0)

    archived_map: dict[str, int] = {}
    for row in monthly_archived_rows:
        m = row.get("month", "")
        if m:
            archived_map[m] = row.get("cnt", 0)

    all_months = sorted(set(list(saved_map.keys()) + list(archived_map.keys())))
    articles_by_month = [
        {
            "month": m,
            "saved": saved_map.get(m, 0),
            "archived": archived_map.get(m, 0),
        }
        for m in all_months
    ]

    return {
        "total_articles": total_articles,
        "total_words_read": total_words_read,
        "articles_by_status": articles_by_status,
        "articles_this_week": articles_this_week,
        "articles_this_month": articles_this_month,
        "archived_this_week": archived_this_week,
        "archived_this_month": archived_this_month,
        "top_domains": top_domains,
        "reading_streak_days": reading_streak_days,
        "avg_reading_time_minutes": avg_reading_time_minutes,
        "articles_by_month": articles_by_month,
    }


def _calculate_streak(rows: list[dict[str, Any]]) -> int:
    """Count consecutive days from today backwards with archived articles.

    Parameters
    ----------
    rows:
        List of dicts with a ``"d"`` key containing date strings (YYYY-MM-DD),
        sorted descending.  Produced by a ``SELECT DISTINCT date(updated_at)``
        query.

    Returns
    -------
    int
        The number of consecutive days (ending today or yesterday) where the
        user archived at least one article.
    """
    from datetime import date, timedelta

    if not rows:
        return 0

    dates = set()
    for row in rows:
        d = row.get("d")
        if d:
            try:
                dates.add(date.fromisoformat(d))
            except (ValueError, TypeError):
                continue

    if not dates:
        return 0

    today = date.today()
    streak = 0
    # Allow the streak to start from today or yesterday
    check = today
    if check not in dates:
        check = today - timedelta(days=1)
        if check not in dates:
            return 0

    while check in dates:
        streak += 1
        check -= timedelta(days=1)

    return streak

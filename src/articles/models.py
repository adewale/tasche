"""TypedDicts for article data structures.

Provides typed representations of article rows from D1, input payloads for
creating articles, and query parameters for listing articles.
"""

from __future__ import annotations

from typing import TypedDict


class ArticleRow(TypedDict, total=False):
    """All fields from the D1 articles table."""

    id: str
    user_id: str
    original_url: str
    final_url: str | None
    canonical_url: str | None
    domain: str | None
    title: str | None
    excerpt: str | None
    author: str | None
    word_count: int | None
    reading_time_minutes: int | None
    image_count: int
    status: str
    reading_status: str
    is_favorite: int
    listen_later: int
    audio_key: str | None
    audio_duration_seconds: int | None
    audio_status: str | None
    html_key: str | None
    markdown_key: str | None
    thumbnail_key: str | None
    markdown_content: str | None
    original_status: str
    scroll_position: float
    reading_progress: float
    created_at: str
    updated_at: str


class ArticleCreate(TypedDict, total=False):
    """Input for creating an article.  ``url`` is required; ``title`` is optional."""

    url: str
    title: str | None


class ArticleListParams(TypedDict, total=False):
    """Query parameters for listing articles."""

    status: str | None
    reading_status: str | None
    is_favorite: bool | None
    tag: str | None
    limit: int
    offset: int
    sort: str | None

"""TypedDicts for tag data structures.

Provides typed representations of tag rows and article-tag associations
from D1.
"""

from __future__ import annotations

from typing import TypedDict


class TagRow(TypedDict, total=False):
    """All fields from the D1 tags table."""

    id: str
    user_id: str
    name: str
    created_at: str


class ArticleTagRow(TypedDict):
    """A row from the D1 article_tags join table."""

    article_id: str
    tag_id: str

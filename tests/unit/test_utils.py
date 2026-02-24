"""Tests for shared utility functions (src/utils.py)."""

from __future__ import annotations

import re

from src.utils import generate_id, now_iso


class TestNowIso:
    def test_returns_iso_format(self) -> None:
        """now_iso() returns a valid ISO 8601 timestamp."""
        result = now_iso()
        # ISO 8601 format: 2025-01-01T00:00:00+00:00
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", result)

    def test_includes_utc_offset(self) -> None:
        """now_iso() includes UTC timezone offset."""
        result = now_iso()
        assert "+00:00" in result or "Z" in result


class TestGenerateId:
    def test_default_length(self) -> None:
        """generate_id() with default 16 bytes produces a 22-char ID."""
        result = generate_id()
        assert len(result) == 22

    def test_session_length(self) -> None:
        """generate_id(32) for sessions produces a 43-char ID."""
        result = generate_id(32)
        assert len(result) == 43

    def test_url_safe_characters(self) -> None:
        """IDs contain only URL-safe characters (alphanumeric, -, _)."""
        for _ in range(20):
            result = generate_id()
            assert re.match(r"^[A-Za-z0-9_-]+$", result)

    def test_uniqueness(self) -> None:
        """Successive calls produce different IDs."""
        ids = {generate_id() for _ in range(100)}
        assert len(ids) == 100

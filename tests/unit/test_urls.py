"""Tests for article URL utilities (src/articles/urls.py).

Covers URL validation, domain extraction, and duplicate checking across all
three URL columns (original_url, final_url, canonical_url).
"""

from __future__ import annotations

import pytest

from src.articles.urls import check_duplicate, extract_domain, validate_url
from tests.conftest import MockD1

# =========================================================================
# validate_url
# =========================================================================


class TestValidateUrl:
    def test_accepts_https_url(self) -> None:
        """A valid https URL is returned normalised."""
        result = validate_url("https://example.com/article")
        assert result == "https://example.com/article"

    def test_accepts_http_url(self) -> None:
        """A valid http URL is accepted."""
        result = validate_url("http://example.com/page")
        assert result == "http://example.com/page"

    def test_strips_whitespace(self) -> None:
        """Leading/trailing whitespace is stripped before validation."""
        result = validate_url("  https://example.com/  ")
        assert result == "https://example.com/"

    def test_rejects_ftp_url(self) -> None:
        """ftp scheme is not allowed."""
        with pytest.raises(ValueError, match="http or https"):
            validate_url("ftp://files.example.com/doc.pdf")

    def test_rejects_javascript_url(self) -> None:
        """javascript scheme is not allowed."""
        with pytest.raises(ValueError, match="http or https"):
            validate_url("javascript:alert(1)")

    def test_rejects_data_url(self) -> None:
        """data scheme is not allowed."""
        with pytest.raises(ValueError, match="http or https"):
            validate_url("data:text/html,<h1>Hi</h1>")

    def test_rejects_empty_string(self) -> None:
        """Empty string raises ValueError."""
        with pytest.raises(ValueError, match="non-empty string"):
            validate_url("")

    def test_rejects_missing_hostname(self) -> None:
        """A URL without a hostname is rejected."""
        with pytest.raises(ValueError):
            validate_url("https://")

    def test_rejects_bare_path(self) -> None:
        """A bare path without scheme is rejected."""
        with pytest.raises(ValueError):
            validate_url("/just/a/path")


# =========================================================================
# extract_domain
# =========================================================================


class TestExtractDomain:
    def test_extracts_hostname(self) -> None:
        """Extracts the hostname from a valid URL."""
        assert extract_domain("https://example.com/path") == "example.com"

    def test_extracts_subdomain(self) -> None:
        """Preserves subdomains in the hostname."""
        assert extract_domain("https://blog.example.com/post") == "blog.example.com"

    def test_extracts_hostname_with_port(self) -> None:
        """Hostname is extracted without the port."""
        assert extract_domain("https://example.com:8080/page") == "example.com"

    def test_returns_empty_for_invalid_url(self) -> None:
        """Returns empty string when hostname cannot be extracted."""
        assert extract_domain("") == ""


# =========================================================================
# check_duplicate
# =========================================================================


class TestCheckDuplicate:
    async def test_finds_match_on_original_url(self) -> None:
        """Finds a duplicate when original_url matches."""
        article = {"id": "a1", "original_url": "https://example.com/article"}

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql and params[1] == "https://example.com/article":
                return [article]
            return []

        db = MockD1(execute=execute)
        result = await check_duplicate(db, "user_001", "https://example.com/article")
        assert result is not None
        assert result["id"] == "a1"

    async def test_finds_match_on_final_url(self) -> None:
        """Finds a duplicate when final_url matches."""
        article = {"id": "a2", "final_url": "https://example.com/final"}

        def execute(sql: str, params: list) -> list:
            if "final_url = ?" in sql and params[2] == "https://example.com/final":
                return [article]
            return []

        db = MockD1(execute=execute)
        result = await check_duplicate(db, "user_001", "https://example.com/final")
        assert result is not None
        assert result["id"] == "a2"

    async def test_finds_match_on_canonical_url(self) -> None:
        """Finds a duplicate when canonical_url matches."""
        article = {"id": "a3", "canonical_url": "https://example.com/canonical"}

        def execute(sql: str, params: list) -> list:
            if "canonical_url = ?" in sql and params[3] == "https://example.com/canonical":
                return [article]
            return []

        db = MockD1(execute=execute)
        result = await check_duplicate(db, "user_001", "https://example.com/canonical")
        assert result is not None
        assert result["id"] == "a3"

    async def test_returns_none_when_no_match(self) -> None:
        """Returns None when no duplicate exists."""
        db = MockD1(execute=lambda sql, params: [])
        result = await check_duplicate(db, "user_001", "https://example.com/new")
        assert result is None

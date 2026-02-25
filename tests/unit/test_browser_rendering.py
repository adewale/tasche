"""Tests for Browser Rendering API client.

Verifies that screenshot and scrape functions construct correct API requests,
handle success responses, and raise errors on failures.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.articles.browser_rendering import (
    BrowserRenderingError,
    scrape,
    screenshot,
)


def _mock_response(
    status_code: int = 200,
    content: bytes = b"image-data",
    text: str = "",
    json_data: dict | None = None,
) -> MagicMock:
    """Create a mock HTTP response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = content
    resp.text = text or "response body"
    resp.json = MagicMock(return_value=json_data or {})
    return resp


class TestScreenshot:
    @pytest.mark.asyncio
    async def test_returns_image_bytes(self) -> None:
        """Successful screenshot returns raw image bytes."""
        mock_fetch = AsyncMock(return_value=_mock_response(content=b"PNG-IMAGE-DATA"))
        with patch("src.articles.browser_rendering.http_fetch", mock_fetch):
            result = await screenshot("https://example.com", "acct-123", "token-abc")
        assert result == b"PNG-IMAGE-DATA"

    @pytest.mark.asyncio
    async def test_sends_correct_endpoint(self) -> None:
        """Screenshot POSTs to the correct API endpoint."""
        mock_fetch = AsyncMock(return_value=_mock_response())
        with patch("src.articles.browser_rendering.http_fetch", mock_fetch):
            await screenshot("https://example.com", "acct-123", "token-abc")
        url = mock_fetch.call_args[0][0]
        assert "acct-123" in url
        assert url.endswith("/screenshot")

    @pytest.mark.asyncio
    async def test_sends_auth_header(self) -> None:
        """Screenshot sends Bearer token in Authorization header."""
        mock_fetch = AsyncMock(return_value=_mock_response())
        with patch("src.articles.browser_rendering.http_fetch", mock_fetch):
            await screenshot("https://example.com", "acct-123", "token-abc")
        headers = mock_fetch.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer token-abc"

    @pytest.mark.asyncio
    async def test_sends_viewport_config(self) -> None:
        """Screenshot sends viewport dimensions in the payload."""
        mock_fetch = AsyncMock(return_value=_mock_response())
        with patch("src.articles.browser_rendering.http_fetch", mock_fetch):
            await screenshot(
                "https://example.com",
                "acct-123",
                "token-abc",
                viewport_width=800,
                viewport_height=600,
            )
        payload = mock_fetch.call_args[1]["json_data"]
        assert payload["viewport"]["width"] == 800
        assert payload["viewport"]["height"] == 600

    @pytest.mark.asyncio
    async def test_sends_url_in_payload(self) -> None:
        """Screenshot sends the target URL in the payload."""
        mock_fetch = AsyncMock(return_value=_mock_response())
        with patch("src.articles.browser_rendering.http_fetch", mock_fetch):
            await screenshot("https://example.com/page", "acct-123", "token-abc")
        payload = mock_fetch.call_args[1]["json_data"]
        assert payload["url"] == "https://example.com/page"

    @pytest.mark.asyncio
    async def test_raises_on_error_status(self) -> None:
        """Screenshot raises BrowserRenderingError on non-200 status."""
        mock_fetch = AsyncMock(
            return_value=_mock_response(status_code=500, text="Internal Server Error")
        )
        with patch("src.articles.browser_rendering.http_fetch", mock_fetch):
            with pytest.raises(BrowserRenderingError, match="HTTP 500"):
                await screenshot("https://example.com", "acct-123", "token-abc")

    @pytest.mark.asyncio
    async def test_raises_on_403(self) -> None:
        """Screenshot raises on 403 (invalid API token)."""
        mock_fetch = AsyncMock(return_value=_mock_response(status_code=403, text="Forbidden"))
        with patch("src.articles.browser_rendering.http_fetch", mock_fetch):
            with pytest.raises(BrowserRenderingError, match="HTTP 403"):
                await screenshot("https://example.com", "acct-123", "bad-token")


class TestScrape:
    @pytest.mark.asyncio
    async def test_returns_html_from_result_field(self) -> None:
        """Scrape returns the 'result' field from the JSON response."""
        resp = _mock_response(json_data={"result": "<html><body>Rendered</body></html>"})
        mock_fetch = AsyncMock(return_value=resp)
        with patch("src.articles.browser_rendering.http_fetch", mock_fetch):
            result = await scrape("https://example.com", "acct-123", "token-abc")
        assert result == "<html><body>Rendered</body></html>"

    @pytest.mark.asyncio
    async def test_sends_correct_endpoint(self) -> None:
        """Scrape POSTs to the correct API endpoint."""
        resp = _mock_response(json_data={"result": "<html></html>"})
        mock_fetch = AsyncMock(return_value=resp)
        with patch("src.articles.browser_rendering.http_fetch", mock_fetch):
            await scrape("https://example.com", "acct-123", "token-abc")
        url = mock_fetch.call_args[0][0]
        assert "acct-123" in url
        assert url.endswith("/scrape")

    @pytest.mark.asyncio
    async def test_sends_auth_header(self) -> None:
        """Scrape sends Bearer token in Authorization header."""
        resp = _mock_response(json_data={"result": ""})
        mock_fetch = AsyncMock(return_value=resp)
        with patch("src.articles.browser_rendering.http_fetch", mock_fetch):
            await scrape("https://example.com", "acct-123", "token-abc")
        headers = mock_fetch.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer token-abc"

    @pytest.mark.asyncio
    async def test_raises_on_error_status(self) -> None:
        """Scrape raises BrowserRenderingError on non-200 status."""
        mock_fetch = AsyncMock(return_value=_mock_response(status_code=502, text="Bad Gateway"))
        with patch("src.articles.browser_rendering.http_fetch", mock_fetch):
            with pytest.raises(BrowserRenderingError, match="HTTP 502"):
                await scrape("https://example.com", "acct-123", "token-abc")

    @pytest.mark.asyncio
    async def test_fallback_to_text_when_no_result_field(self) -> None:
        """Scrape falls back to resp.text when JSON has no 'result' key."""
        resp = _mock_response(text="<html>Fallback</html>", json_data={"other": "data"})
        mock_fetch = AsyncMock(return_value=resp)
        with patch("src.articles.browser_rendering.http_fetch", mock_fetch):
            result = await scrape("https://example.com", "acct-123", "token-abc")
        assert result == "<html>Fallback</html>"

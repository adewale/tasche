"""Tests for email HTML cleanup utilities.

Covers tracking pixel removal, script/style stripping, footer removal,
hidden element removal, and first URL extraction.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from src.articles.email_cleanup import (
    _is_footer_section,
    _is_tracking_pixel,
    clean_email_html,
    extract_first_url,
)


def _make_img(attrs: dict) -> object:
    """Create a BeautifulSoup <img> Tag with the given attributes."""
    html = "<img"
    for k, v in attrs.items():
        html += f' {k}="{v}"'
    html += ">"
    soup = BeautifulSoup(html, "html.parser")
    return soup.find("img")


# =========================================================================
# _is_tracking_pixel
# =========================================================================


class TestIsTrackingPixel:
    def test_detects_1x1_pixel(self) -> None:
        """Image with width=1 height=1 is a tracking pixel."""
        img = _make_img({"src": "https://example.com/img.gif", "width": "1", "height": "1"})
        assert _is_tracking_pixel(img) is True

    def test_detects_0x0_pixel(self) -> None:
        """Image with width=0 height=0 is a tracking pixel."""
        img = _make_img({"src": "https://example.com/pixel.gif", "width": "0", "height": "0"})
        assert _is_tracking_pixel(img) is True

    def test_detects_tracking_domain(self) -> None:
        """Image from a known tracking domain is a tracking pixel."""
        img = _make_img({"src": "https://pixel.mailchimp.com/open.gif"})
        assert _is_tracking_pixel(img) is True

    def test_detects_tracking_url_pattern(self) -> None:
        """Image URL containing tracking patterns is a tracking pixel."""
        img = _make_img({"src": "https://example.com/track/open/12345"})
        assert _is_tracking_pixel(img) is True

    def test_detects_hidden_display_none(self) -> None:
        """Image with display:none is a tracking pixel."""
        img = _make_img({"src": "https://example.com/img.gif", "style": "display:none"})
        assert _is_tracking_pixel(img) is True

    def test_detects_hidden_visibility_hidden(self) -> None:
        """Image with visibility:hidden is a tracking pixel."""
        img = _make_img({"src": "https://example.com/img.gif", "style": "visibility:hidden"})
        assert _is_tracking_pixel(img) is True

    def test_detects_1px_style(self) -> None:
        """Image with 1px dimensions in style is a tracking pixel."""
        img = _make_img(
            {
                "src": "https://example.com/img.gif",
                "style": "width:1px; height:0px",
            }
        )
        assert _is_tracking_pixel(img) is True

    def test_normal_image_not_tracking(self) -> None:
        """Normal content image is not a tracking pixel."""
        img = _make_img(
            {
                "src": "https://cdn.example.com/newsletter-banner.jpg",
                "width": "600",
                "height": "300",
            }
        )
        assert _is_tracking_pixel(img) is False

    def test_image_without_src_not_tracking(self) -> None:
        """Image without src attribute is not a tracking pixel."""
        img = _make_img({"alt": "placeholder"})
        assert _is_tracking_pixel(img) is False

    def test_open_substack_tracking(self) -> None:
        """Image from open.substack.com is a tracking pixel."""
        img = _make_img({"src": "https://open.substack.com/api/v1/track/open"})
        assert _is_tracking_pixel(img) is True

    def test_sendgrid_tracking(self) -> None:
        """Image from ct.sendgrid.net is a tracking pixel."""
        img = _make_img({"src": "https://ct.sendgrid.net/wf/open?u=abc123"})
        assert _is_tracking_pixel(img) is True


# =========================================================================
# _is_footer_section
# =========================================================================


class TestIsFooterSection:
    def test_detects_unsubscribe_text(self) -> None:
        """Section with 'unsubscribe' text is a footer."""
        html = "<div>Click here to unsubscribe from this newsletter.</div>"
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("div")
        assert _is_footer_section(tag) is True

    def test_detects_manage_preferences(self) -> None:
        """Section with 'manage preferences' text is a footer."""
        html = "<p>Manage your email preferences or opt out.</p>"
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("p")
        assert _is_footer_section(tag) is True

    def test_detects_view_in_browser(self) -> None:
        """Section with 'view in browser' text is a footer."""
        html = "<td>View this email in your browser</td>"
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("td")
        assert _is_footer_section(tag) is True

    def test_normal_content_not_footer(self) -> None:
        """Normal article content is not a footer."""
        html = "<p>This is interesting article content about technology.</p>"
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("p")
        assert _is_footer_section(tag) is False

    def test_large_section_with_unsubscribe_not_footer(self) -> None:
        """Large content section mentioning unsubscribe is not treated as footer."""
        text = "x" * 1001 + " unsubscribe"
        html = f"<div>{text}</div>"
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("div")
        assert _is_footer_section(tag) is False


# =========================================================================
# clean_email_html
# =========================================================================


class TestCleanEmailHtml:
    def test_removes_script_tags(self) -> None:
        """Script tags are completely removed."""
        html = "<html><body><p>Content</p><script>alert('xss')</script></body></html>"
        result = clean_email_html(html)
        assert "<script>" not in result
        assert "alert" not in result
        assert "Content" in result

    def test_removes_style_tags(self) -> None:
        """Style tags are completely removed."""
        html = "<html><body><style>.foo { color: red; }</style><p>Content</p></body></html>"
        result = clean_email_html(html)
        assert "<style>" not in result
        assert "color: red" not in result
        assert "Content" in result

    def test_removes_noscript_tags(self) -> None:
        """Noscript tags are completely removed."""
        html = "<html><body><noscript>Enable JS</noscript><p>Content</p></body></html>"
        result = clean_email_html(html)
        assert "<noscript>" not in result
        assert "Enable JS" not in result

    def test_removes_tracking_pixels(self) -> None:
        """Tracking pixel images are removed."""
        html = """
        <html><body>
            <p>Newsletter content here.</p>
            <img src="https://pixel.mailchimp.com/open.gif" width="1" height="1">
            <img src="https://cdn.example.com/banner.jpg" width="600" height="300">
        </body></html>
        """
        result = clean_email_html(html)
        assert "pixel.mailchimp.com" not in result
        assert "banner.jpg" in result
        assert "Newsletter content" in result

    def test_removes_hidden_elements(self) -> None:
        """Elements with display:none are removed."""
        html = """
        <html><body>
            <p>Visible content</p>
            <div style="display:none">Hidden tracking stuff</div>
        </body></html>
        """
        result = clean_email_html(html)
        assert "Visible content" in result
        assert "Hidden tracking" not in result

    def test_removes_footer_boilerplate(self) -> None:
        """Unsubscribe / footer sections are removed."""
        html = """
        <html><body>
            <p>Great newsletter content about technology and science.</p>
            <div>Click here to unsubscribe from this mailing list.</div>
        </body></html>
        """
        result = clean_email_html(html)
        assert "Great newsletter" in result
        assert "unsubscribe" not in result.lower()

    def test_preserves_content_images(self) -> None:
        """Normal content images are preserved."""
        html = """
        <html><body>
            <p>Article with images</p>
            <img src="https://cdn.example.com/photo.jpg" width="600" height="400">
        </body></html>
        """
        result = clean_email_html(html)
        assert "photo.jpg" in result

    def test_preserves_formatting(self) -> None:
        """Bold, italic, and other formatting is preserved."""
        html = """
        <html><body>
            <p>This has <strong>bold</strong> and <em>italic</em> text.</p>
        </body></html>
        """
        result = clean_email_html(html)
        assert "<strong>bold</strong>" in result
        assert "<em>italic</em>" in result

    def test_preserves_links(self) -> None:
        """Hyperlinks are preserved."""
        html = """
        <html><body>
            <p>Click <a href="https://example.com/article">here</a> to read more.</p>
        </body></html>
        """
        result = clean_email_html(html)
        assert 'href="https://example.com/article"' in result

    def test_empty_input_returns_empty(self) -> None:
        """Empty string input returns empty string."""
        assert clean_email_html("") == ""

    def test_none_input_returns_empty(self) -> None:
        """None-ish input returns empty string."""
        assert clean_email_html("") == ""

    def test_extracts_body_content(self) -> None:
        """When a <body> tag exists, only its contents are returned."""
        html = """
        <html>
        <head><title>Newsletter</title></head>
        <body>
            <p>Body content only</p>
        </body>
        </html>
        """
        result = clean_email_html(html)
        assert "Body content only" in result
        assert "<title>" not in result
        assert "<head>" not in result

    def test_full_newsletter_cleanup(self) -> None:
        """Integration test: a realistic newsletter email gets properly cleaned."""
        html = """
        <html>
        <head>
            <style>.header { font-size: 24px; }</style>
        </head>
        <body>
            <div style="display:none">Preheader text for email clients</div>
            <h1>Weekly Tech Digest</h1>
            <p>Here are this week's top stories in technology and science.</p>
            <p>First, <a href="https://example.com/ai-news">AI makes breakthrough</a>
            in natural language processing.</p>
            <img src="https://cdn.example.com/ai-photo.jpg" width="600" height="400">
            <p>Second, quantum computing reaches new milestone.</p>
            <img src="https://open.substack.com/api/v1/track/open" width="1" height="1">
            <script>window.track('open')</script>
            <div>
                <p>You are receiving this because you subscribed.</p>
                <p><a href="https://example.com/unsubscribe">Unsubscribe</a> |
                <a href="https://example.com/preferences">Manage preferences</a></p>
            </div>
        </body>
        </html>
        """
        result = clean_email_html(html)

        # Content should be preserved
        assert "Weekly Tech Digest" in result
        assert "top stories" in result
        assert "AI makes breakthrough" in result
        assert "quantum computing" in result
        assert "ai-photo.jpg" in result

        # Junk should be removed
        assert "<script>" not in result
        assert "<style>" not in result
        assert "Preheader text" not in result
        assert "open.substack.com" not in result
        assert "window.track" not in result


# =========================================================================
# extract_first_url
# =========================================================================


class TestExtractFirstUrl:
    def test_extracts_first_http_url(self) -> None:
        """Returns the first http/https URL from anchor tags."""
        html = """
        <p>Read <a href="https://example.com/article">this article</a> today.</p>
        """
        assert extract_first_url(html) == "https://example.com/article"

    def test_skips_mailto_links(self) -> None:
        """Skips mailto: links."""
        html = """
        <a href="mailto:test@example.com">Email us</a>
        <a href="https://example.com/article">Read more</a>
        """
        assert extract_first_url(html) == "https://example.com/article"

    def test_skips_unsubscribe_links(self) -> None:
        """Skips unsubscribe links."""
        html = """
        <a href="https://example.com/unsubscribe?token=abc">Unsubscribe</a>
        <a href="https://example.com/real-article">Read more</a>
        """
        assert extract_first_url(html) == "https://example.com/real-article"

    def test_skips_tracking_domain_links(self) -> None:
        """Skips links to known tracking domains."""
        html = """
        <a href="https://ct.sendgrid.net/redirect/abc">Track</a>
        <a href="https://example.com/article">Read</a>
        """
        assert extract_first_url(html) == "https://example.com/article"

    def test_returns_none_for_no_urls(self) -> None:
        """Returns None when no suitable URLs exist."""
        html = "<p>Just plain text, no links.</p>"
        assert extract_first_url(html) is None

    def test_returns_none_for_empty_input(self) -> None:
        """Returns None for empty string."""
        assert extract_first_url("") is None

    def test_returns_none_for_only_unsubscribe_links(self) -> None:
        """Returns None when all links are unsubscribe links."""
        html = """
        <a href="https://example.com/unsubscribe">Unsubscribe</a>
        <a href="https://example.com/manage-preferences">Preferences</a>
        """
        assert extract_first_url(html) is None

    def test_skips_fragment_links(self) -> None:
        """Skips anchor fragment links."""
        html = """
        <a href="#section1">Jump to section</a>
        <a href="https://example.com/article">Real link</a>
        """
        assert extract_first_url(html) == "https://example.com/article"

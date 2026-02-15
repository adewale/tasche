"""Tests for Phase 4 — Content extraction utilities.

Covers canonical URL extraction, article extraction via python-readability,
HTML-to-Markdown conversion, word counting, reading time calculation,
and image path rewriting.
"""

from __future__ import annotations

from src.articles.extraction import (
    calculate_reading_time,
    count_words,
    extract_article,
    extract_canonical_url,
    html_to_markdown,
    rewrite_image_paths,
)
from src.articles.urls import extract_domain

# =========================================================================
# extract_canonical_url
# =========================================================================


class TestExtractCanonicalUrl:
    def test_extracts_canonical_url(self) -> None:
        """Returns the href from <link rel="canonical">."""
        html = """
        <html>
        <head>
            <link rel="canonical" href="https://example.com/article/123">
        </head>
        <body><p>Hello</p></body>
        </html>
        """
        assert extract_canonical_url(html) == "https://example.com/article/123"

    def test_returns_none_without_canonical(self) -> None:
        """Returns None when no canonical link is present."""
        html = """
        <html>
        <head><title>Test</title></head>
        <body><p>Hello</p></body>
        </html>
        """
        assert extract_canonical_url(html) is None

    def test_returns_none_for_empty_href(self) -> None:
        """Returns None when canonical link has empty href."""
        html = '<html><head><link rel="canonical" href=""></head><body></body></html>'
        assert extract_canonical_url(html) is None

    def test_strips_whitespace_from_href(self) -> None:
        """Strips leading/trailing whitespace from the canonical href."""
        html = '<html><head><link rel="canonical" href="  https://example.com/  "></head></html>'
        assert extract_canonical_url(html) == "https://example.com/"


# =========================================================================
# extract_article
# =========================================================================


class TestExtractArticle:
    def test_returns_title_and_content(self) -> None:
        """extract_article returns a dict with title and html keys."""
        html = """
        <html>
        <head><title>My Great Article</title></head>
        <body>
            <article>
                <h1>My Great Article</h1>
                <p>This is the first paragraph of a long article that contains
                enough text to be considered real content by the readability
                algorithm. We need to make it substantial enough.</p>
                <p>Second paragraph with more content to ensure extraction works
                properly. The readability algorithm needs sufficient text to
                identify the main content area of the page.</p>
                <p>Third paragraph adding even more content to pass the minimum
                threshold for content extraction to work reliably.</p>
            </article>
        </body>
        </html>
        """
        result = extract_article(html)

        assert "title" in result
        assert "html" in result
        assert "excerpt" in result
        assert result["title"]  # non-empty
        assert result["html"]  # non-empty

    def test_excerpt_is_plain_text(self) -> None:
        """The excerpt should be plain text without HTML tags."""
        html = """
        <html>
        <head><title>Test</title></head>
        <body>
            <article>
                <p>This is a <strong>bold</strong> paragraph with enough content
                to be extracted by readability as the main article body.</p>
                <p>More text here to ensure extraction works properly.</p>
                <p>Even more text to make the content substantial.</p>
            </article>
        </body>
        </html>
        """
        result = extract_article(html)
        assert "<strong>" not in result["excerpt"]
        assert "<p>" not in result["excerpt"]


# =========================================================================
# html_to_markdown
# =========================================================================


class TestHtmlToMarkdown:
    def test_converts_basic_html(self) -> None:
        """Converts basic HTML elements to Markdown."""
        html = "<h1>Title</h1><p>A paragraph.</p>"
        md = html_to_markdown(html)
        assert "# Title" in md
        assert "A paragraph." in md

    def test_converts_links(self) -> None:
        """Converts anchor tags to Markdown links."""
        html = '<p>Click <a href="https://example.com">here</a>.</p>'
        md = html_to_markdown(html)
        assert "[here]" in md
        assert "https://example.com" in md

    def test_converts_emphasis(self) -> None:
        """Converts bold and italic tags."""
        html = "<p><strong>bold</strong> and <em>italic</em></p>"
        md = html_to_markdown(html)
        assert "**bold**" in md
        assert "*italic*" in md

    def test_strips_script_tags(self) -> None:
        """Script tags are removed during conversion."""
        html = "<p>Text</p><script>alert('xss')</script>"
        md = html_to_markdown(html)
        assert "alert" not in md
        assert "Text" in md


# =========================================================================
# count_words
# =========================================================================


class TestCountWords:
    def test_counts_simple_text(self) -> None:
        assert count_words("hello world foo bar") == 4

    def test_counts_empty_string(self) -> None:
        assert count_words("") == 0

    def test_counts_single_word(self) -> None:
        assert count_words("hello") == 1

    def test_handles_multiple_spaces(self) -> None:
        """Multiple spaces between words do not inflate the count."""
        assert count_words("hello   world") == 2

    def test_handles_newlines(self) -> None:
        assert count_words("hello\nworld\nfoo") == 3


# =========================================================================
# calculate_reading_time
# =========================================================================


class TestCalculateReadingTime:
    def test_200_words_is_1_minute(self) -> None:
        assert calculate_reading_time(200) == 1

    def test_400_words_is_2_minutes(self) -> None:
        assert calculate_reading_time(400) == 2

    def test_zero_words_returns_1(self) -> None:
        """Minimum reading time is 1 minute."""
        assert calculate_reading_time(0) == 1

    def test_250_words_rounds_up(self) -> None:
        """250 words / 200 wpm = 1.25 -> rounds up to 2."""
        assert calculate_reading_time(250) == 2

    def test_custom_wpm(self) -> None:
        assert calculate_reading_time(300, wpm=300) == 1


# =========================================================================
# rewrite_image_paths
# =========================================================================


class TestRewriteImagePaths:
    def test_substitutes_urls(self) -> None:
        """Replaces image src attributes with R2 paths."""
        html = '<p><img src="https://cdn.example.com/photo.jpg"></p>'
        image_map = {
            "https://cdn.example.com/photo.jpg": "articles/abc/images/deadbeef.webp",
        }
        result = rewrite_image_paths(html, image_map)
        assert "articles/abc/images/deadbeef.webp" in result
        assert "https://cdn.example.com/photo.jpg" not in result

    def test_leaves_unmapped_images_unchanged(self) -> None:
        """Images not in the map are left with their original src."""
        html = '<img src="https://other.com/img.png">'
        image_map = {
            "https://cdn.example.com/photo.jpg": "articles/abc/images/deadbeef.webp",
        }
        result = rewrite_image_paths(html, image_map)
        assert "https://other.com/img.png" in result

    def test_handles_multiple_images(self) -> None:
        """Rewrites multiple images in a single HTML string."""
        html = (
            '<img src="https://a.com/1.jpg">'
            '<img src="https://b.com/2.jpg">'
        )
        image_map = {
            "https://a.com/1.jpg": "articles/x/images/aaa.webp",
            "https://b.com/2.jpg": "articles/x/images/bbb.webp",
        }
        result = rewrite_image_paths(html, image_map)
        assert "articles/x/images/aaa.webp" in result
        assert "articles/x/images/bbb.webp" in result

    def test_empty_image_map_returns_original(self) -> None:
        """Empty image map returns HTML unchanged."""
        html = '<img src="https://a.com/1.jpg">'
        result = rewrite_image_paths(html, {})
        assert result == html


# =========================================================================
# extract_domain
# =========================================================================


class TestExtractDomain:
    def test_extracts_hostname(self) -> None:
        assert extract_domain("https://example.com/path") == "example.com"

    def test_extracts_subdomain(self) -> None:
        assert extract_domain("https://blog.example.com/article") == "blog.example.com"

    def test_empty_url_returns_empty(self) -> None:
        assert extract_domain("") == ""

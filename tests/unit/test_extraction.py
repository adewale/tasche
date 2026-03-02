"""Tests for Phase 4 — Content extraction utilities.

Covers canonical URL extraction, article extraction via BeautifulSoup,
HTML-to-Markdown conversion, word counting, reading time calculation,
and image path rewriting.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from src.articles.extraction import (
    _unwrap_layout_tables,
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
        # Phase E: verify extracted title matches expected value
        assert result["title"] == "My Great Article"

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
# extract_article (fallback extractor)
# =========================================================================


class TestExtractArticleBs4:
    def test_extracts_from_article_tag(self) -> None:
        """Finds content inside <article> tags."""
        html = """
        <html>
        <head><title>Page Title</title></head>
        <body>
            <nav><a href="/">Home</a></nav>
            <article>
                <h1>Article Title</h1>
                <p>First paragraph of the article content.</p>
                <p>Second paragraph with more details.</p>
            </article>
            <footer>Copyright 2026</footer>
        </body>
        </html>
        """
        result = extract_article(html)
        assert result["title"] == "Article Title"
        assert "First paragraph" in result["html"]
        assert "<nav>" not in result["html"]
        assert "Copyright" not in result["html"]

    def test_extracts_from_main_tag(self) -> None:
        """Falls back to <main> when no <article> exists."""
        html = """
        <html>
        <head><title>Page</title></head>
        <body>
            <header><h1>Site Name</h1></header>
            <main>
                <h1>Main Content Title</h1>
                <p>The actual content goes here.</p>
            </main>
            <aside>Sidebar stuff</aside>
        </body>
        </html>
        """
        result = extract_article(html)
        assert result["title"] == "Main Content Title"
        assert "actual content" in result["html"]

    def test_extracts_from_largest_div(self) -> None:
        """Falls back to largest div when no semantic containers exist."""
        html = """
        <html>
        <head><title>Div Page</title></head>
        <body>
            <div class="sidebar"><p>Short.</p></div>
            <div class="content">
                <h1>Blog Post</h1>
                <p>This is a much longer paragraph that contains the actual
                article content that we want to extract from the page.</p>
                <p>Another paragraph with even more content to ensure this
                div scores higher than the sidebar.</p>
            </div>
        </body>
        </html>
        """
        result = extract_article(html)
        assert result["title"] == "Blog Post"
        assert "article content" in result["html"]

    def test_extracts_author_from_meta(self) -> None:
        """Extracts author from meta tags."""
        html = """
        <html>
        <head>
            <meta name="author" content="Jane Doe">
            <title>Test</title>
        </head>
        <body><article><p>Content here.</p></article></body>
        </html>
        """
        result = extract_article(html)
        assert result["byline"] == "Jane Doe"

    def test_removes_nav_and_footer(self) -> None:
        """Strips navigation and footer boilerplate."""
        html = """
        <html><body>
            <nav><ul><li>Link 1</li><li>Link 2</li></ul></nav>
            <article><p>Real content.</p></article>
            <footer><p>Footer text</p></footer>
        </body></html>
        """
        result = extract_article(html)
        assert "Real content" in result["html"]
        assert "Link 1" not in result["html"]
        assert "Footer text" not in result["html"]

    def test_excerpt_is_plain_text(self) -> None:
        """Excerpt has no HTML tags."""
        html = """
        <html><body>
            <article><p>This is a <strong>bold</strong> paragraph.</p></article>
        </body></html>
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

    def test_layout_tables_produce_clean_markdown(self) -> None:
        """Layout tables should not appear as markdown table syntax."""
        html = """
        <table><tr><td>
            <table><tr><td></td><td>
                <h1>My Article</h1>
                <p>Some real content here.</p>
            </td></tr></table>
        </td></tr></table>
        """
        md = html_to_markdown(html)
        assert "# My Article" in md
        assert "Some real content here." in md
        # No table pipes should remain
        assert "|" not in md

    def test_data_tables_preserved_in_markdown(self) -> None:
        """Real data tables (multi-cell, mostly filled) are kept."""
        html = """
        <p>Here is a comparison:</p>
        <table>
            <tr><th>Name</th><th>Value</th></tr>
            <tr><td>Alpha</td><td>100</td></tr>
            <tr><td>Beta</td><td>200</td></tr>
        </table>
        """
        md = html_to_markdown(html)
        assert "Alpha" in md
        assert "Beta" in md
        # Data tables should produce pipe-delimited markdown
        assert "|" in md

    def test_paulgraham_style_nested_layout_tables(self) -> None:
        """Deeply nested single-cell layout tables produce prose, not table syntax."""
        html = """
        <table border="0" cellpadding="0" cellspacing="0"><tr><td>
            <table border="0"><tr><td></td><td>
                <table><tr><td>
                    <h2>Taste for Makers</h2>
                    <p>February 2002</p>
                    <p>I was talking recently to a friend who teaches at MIT.</p>
                    <p>His students were not able to distinguish good design from bad.</p>
                </td></tr></table>
            </td></tr></table>
        </td></tr></table>
        """
        md = html_to_markdown(html)
        assert "Taste for Makers" in md
        assert "talking recently" in md
        # Should have no table pipe characters at all
        assert "|" not in md

    def test_code_block_preserves_language(self) -> None:
        """Code blocks with language class retain the language annotation."""
        html = '<pre><code class="language-python">x = 1</code></pre>'
        md = html_to_markdown(html)
        assert md.startswith("```python")

    def test_code_block_language_javascript(self) -> None:
        """Language annotation works for javascript too."""
        html = '<pre><code class="language-javascript">const x = 1;</code></pre>'
        md = html_to_markdown(html)
        assert md.startswith("```javascript")

    def test_code_block_no_language_class(self) -> None:
        """Code blocks without a language class produce plain fenced blocks."""
        html = "<pre><code>plain code</code></pre>"
        md = html_to_markdown(html)
        assert md.startswith("```\n")
        assert "plain code" in md

    def test_superscript_preserved(self) -> None:
        """Superscript is not silently dropped."""
        html = "<p>x<sup>2</sup> + y<sup>3</sup></p>"
        md = html_to_markdown(html)
        # The superscript content must appear distinctly from adjacent text
        assert "2" in md
        assert "x2 " not in md  # must not silently merge into surrounding text

    def test_subscript_preserved(self) -> None:
        """Subscript is not silently dropped."""
        html = "<p>H<sub>2</sub>O</p>"
        md = html_to_markdown(html)
        assert "2" in md
        assert "H2O" not in md  # must not silently merge

    def test_ordered_list_numbering(self) -> None:
        """Ordered lists produce numbered items."""
        html = "<ol><li>First</li><li>Second</li><li>Third</li></ol>"
        md = html_to_markdown(html)
        assert "1." in md
        assert "2." in md
        assert "3." in md

    def test_nested_lists(self) -> None:
        """Nested lists produce indented sub-items."""
        html = "<ul><li>A<ul><li>A1</li></ul></li><li>B</li></ul>"
        md = html_to_markdown(html)
        assert "A" in md
        assert "A1" in md
        assert "B" in md
        # Sub-item should be indented
        lines = md.strip().split("\n")
        a1_line = next(line for line in lines if "A1" in line)
        assert a1_line.startswith(" ") or a1_line.startswith("\t")

    def test_blockquote_preserved(self) -> None:
        """Blockquotes produce > prefix."""
        html = "<blockquote><p>A wise quote.</p></blockquote>"
        md = html_to_markdown(html)
        assert md.startswith(">")
        assert "A wise quote." in md

    def test_inline_code_preserved(self) -> None:
        """Inline code produces backtick-wrapped text."""
        html = "<p>Use <code>git commit</code> to save.</p>"
        md = html_to_markdown(html)
        assert "`git commit`" in md

    def test_horizontal_rule(self) -> None:
        """Horizontal rules produce --- markers."""
        html = "<p>Above</p><hr><p>Below</p>"
        md = html_to_markdown(html)
        assert "---" in md
        assert "Above" in md
        assert "Below" in md

    def test_complex_article_structure(self) -> None:
        """A realistic article with mixed elements converts correctly."""
        html = """
        <h1>My Article</h1>
        <p>Intro with <a href="https://example.com">a link</a>.</p>
        <h2>Section</h2>
        <p>Text with <strong>bold</strong> and <em>italic</em>.</p>
        <ul><li>Point 1</li><li>Point 2</li></ul>
        <blockquote><p>A quote.</p></blockquote>
        <pre><code class="language-python">x = 1</code></pre>
        """
        md = html_to_markdown(html)
        assert "# My Article" in md
        assert "## Section" in md
        assert "[a link](https://example.com)" in md
        assert "**bold**" in md
        assert "*italic*" in md
        assert "Point 1" in md
        assert "> A quote." in md
        assert "```python" in md

    def test_markdown_no_excessive_whitespace(self) -> None:
        """Converted markdown should not have 3+ consecutive blank lines."""
        html = """
        <p>First paragraph.</p>
        <br><br><br><br>
        <p>Second paragraph.</p>
        """
        md = html_to_markdown(html)
        assert "\n\n\n" not in md


# =========================================================================
# _unwrap_layout_tables (direct unit tests)
# =========================================================================


class TestUnwrapLayoutTables:
    def test_unwraps_single_cell_table(self) -> None:
        """A table with exactly one cell is always a layout wrapper."""
        html = "<table><tr><td><p>Content</p></td></tr></table>"
        soup = BeautifulSoup(html, "html.parser")
        _unwrap_layout_tables(soup)
        assert soup.find("table") is None
        assert "Content" in soup.get_text()

    def test_unwraps_majority_empty_cells(self) -> None:
        """Tables where >50% of cells are empty are layout tables."""
        html = """
        <table>
            <tr><td></td><td></td><td>Content</td></tr>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        _unwrap_layout_tables(soup)
        assert soup.find("table") is None
        assert "Content" in soup.get_text()

    def test_preserves_data_table(self) -> None:
        """Tables where most cells have content are kept intact."""
        html = """
        <table>
            <tr><th>Name</th><th>Age</th></tr>
            <tr><td>Alice</td><td>30</td></tr>
            <tr><td>Bob</td><td>25</td></tr>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        _unwrap_layout_tables(soup)
        assert soup.find("table") is not None

    def test_unwraps_empty_table(self) -> None:
        """A table with no cells at all is unwrapped."""
        html = "<table></table>"
        soup = BeautifulSoup(html, "html.parser")
        _unwrap_layout_tables(soup)
        assert soup.find("table") is None

    def test_nested_layout_tables_unwrap_completely(self) -> None:
        """Nested single-cell layout tables all get removed."""
        html = """
        <table><tr><td>
            <table><tr><td>
                <p>Deep content</p>
            </td></tr></table>
        </td></tr></table>
        """
        soup = BeautifulSoup(html, "html.parser")
        _unwrap_layout_tables(soup)
        assert soup.find("table") is None
        assert "Deep content" in soup.get_text()

    def test_cleans_orphaned_row_tags(self) -> None:
        """After unwrapping tables, orphaned tr/td/th tags are also unwrapped."""
        html = "<table><tr><td><p>Text</p></td></tr></table>"
        soup = BeautifulSoup(html, "html.parser")
        _unwrap_layout_tables(soup)
        assert soup.find("tr") is None
        assert soup.find("td") is None
        assert "Text" in soup.get_text()

    def test_preserves_data_table_with_one_empty_cell(self) -> None:
        """A 2x2 table with only one empty cell (25%) is data, not layout."""
        html = """
        <table>
            <tr><td>A</td><td>B</td></tr>
            <tr><td>C</td><td></td></tr>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        _unwrap_layout_tables(soup)
        # 1 empty out of 4 = 25% < 50%, so table is preserved
        assert soup.find("table") is not None

    def test_handles_tbody_wrapped_rows(self) -> None:
        """Tables with <tbody> wrappers around rows are handled correctly."""
        html = """
        <table><tbody>
            <tr><td></td><td></td></tr>
            <tr><td>X</td><td></td></tr>
        </tbody></table>
        """
        soup = BeautifulSoup(html, "html.parser")
        _unwrap_layout_tables(soup)
        # 3 empty out of 4 = 75% > 50%, unwrapped
        assert soup.find("table") is None
        assert "X" in soup.get_text()

    def test_mixed_layout_and_data_tables(self) -> None:
        """A layout wrapper around a real data table: layout removed, data kept."""
        html = """
        <table><tr><td>
            <table>
                <tr><th>Col1</th><th>Col2</th></tr>
                <tr><td>Val1</td><td>Val2</td></tr>
            </table>
        </td></tr></table>
        """
        soup = BeautifulSoup(html, "html.parser")
        _unwrap_layout_tables(soup)
        # The outer single-cell table should be unwrapped
        # The inner data table (0 empty out of 4) should be kept
        tables = soup.find_all("table")
        assert len(tables) == 1
        assert "Val1" in tables[0].get_text()


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
        html = '<img src="https://a.com/1.jpg"><img src="https://b.com/2.jpg">'
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

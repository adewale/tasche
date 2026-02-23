"""Tests for EPUB export functionality.

Covers the pure-Python EPUB generator (src/articles/epub.py), the
single-article EPUB endpoint (GET /api/articles/{id}/epub), and the
batch EPUB endpoint (POST /api/export/epub).
"""

from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from src.articles.epub import (
    _sanitize_filename,
    _sanitize_xhtml,
    epub_filename,
    generate_epub,
    generate_multi_epub,
)
from src.articles.export import router as export_router
from src.articles.routes import router as articles_router
from src.auth.session import COOKIE_NAME
from tests.conftest import (
    ArticleFactory,
    MockD1,
    MockEnv,
    MockR2,
    _make_test_app,
)
from tests.conftest import (
    _authenticated_client as _authenticated_client_base,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ARTICLE_ROUTERS = ((articles_router, "/api/articles"),)
_EXPORT_ROUTERS = ((export_router, "/api/export"),)


def _make_article_app(env):
    return _make_test_app(env, *_ARTICLE_ROUTERS)


def _make_export_app(env):
    return _make_test_app(env, *_EXPORT_ROUTERS)


async def _article_authenticated_client(env: MockEnv) -> tuple[TestClient, str]:
    return await _authenticated_client_base(env, *_ARTICLE_ROUTERS)


async def _export_authenticated_client(env: MockEnv) -> tuple[TestClient, str]:
    return await _authenticated_client_base(env, *_EXPORT_ROUTERS)


def _parse_epub(epub_bytes: bytes) -> zipfile.ZipFile:
    """Open EPUB bytes as a ZipFile for inspection."""
    return zipfile.ZipFile(io.BytesIO(epub_bytes), "r")


# ---------------------------------------------------------------------------
# EPUB generator unit tests — generate_epub()
# ---------------------------------------------------------------------------


class TestGenerateEpub:
    def test_returns_bytes(self) -> None:
        """generate_epub() returns bytes."""
        result = generate_epub("Test Title", "Test Author", "<p>Hello world</p>")
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_is_valid_zip(self) -> None:
        """The generated EPUB is a valid ZIP file."""
        epub_bytes = generate_epub("Test", "Author", "<p>Content</p>")
        with _parse_epub(epub_bytes) as zf:
            assert zf.testzip() is None  # No corrupt files

    def test_mimetype_is_first_and_uncompressed(self) -> None:
        """The mimetype file must be the first entry and stored uncompressed."""
        epub_bytes = generate_epub("Test", "Author", "<p>Content</p>")
        with _parse_epub(epub_bytes) as zf:
            names = zf.namelist()
            assert names[0] == "mimetype"
            info = zf.getinfo("mimetype")
            assert info.compress_type == zipfile.ZIP_STORED
            assert zf.read("mimetype") == b"application/epub+zip"

    def test_contains_required_files(self) -> None:
        """The EPUB contains all required structural files."""
        epub_bytes = generate_epub("Test", "Author", "<p>Content</p>")
        with _parse_epub(epub_bytes) as zf:
            names = zf.namelist()
            assert "mimetype" in names
            assert "META-INF/container.xml" in names
            assert "OEBPS/content.opf" in names
            assert "OEBPS/toc.ncx" in names
            assert "OEBPS/content.xhtml" in names
            assert "OEBPS/style.css" in names

    def test_container_xml_points_to_opf(self) -> None:
        """container.xml references the correct OPF path."""
        epub_bytes = generate_epub("Test", "Author", "<p>Content</p>")
        with _parse_epub(epub_bytes) as zf:
            container = zf.read("META-INF/container.xml").decode("utf-8")
            assert "OEBPS/content.opf" in container

    def test_opf_contains_title_and_author(self) -> None:
        """content.opf includes the title and author metadata."""
        epub_bytes = generate_epub("My Article", "Jane Doe", "<p>Text</p>")
        with _parse_epub(epub_bytes) as zf:
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
            assert "My Article" in opf
            assert "Jane Doe" in opf

    def test_opf_contains_language(self) -> None:
        """content.opf includes the language metadata."""
        epub_bytes = generate_epub("Test", "Author", "<p>Text</p>", language="fr")
        with _parse_epub(epub_bytes) as zf:
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
            assert "fr" in opf

    def test_opf_references_content_xhtml(self) -> None:
        """content.opf manifest references the content XHTML file."""
        epub_bytes = generate_epub("Test", "Author", "<p>Text</p>")
        with _parse_epub(epub_bytes) as zf:
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
            assert "content.xhtml" in opf

    def test_ncx_contains_title(self) -> None:
        """toc.ncx includes the book title."""
        epub_bytes = generate_epub("Navigation Test", "Author", "<p>Text</p>")
        with _parse_epub(epub_bytes) as zf:
            ncx = zf.read("OEBPS/toc.ncx").decode("utf-8")
            assert "Navigation Test" in ncx

    def test_ncx_has_navpoint(self) -> None:
        """toc.ncx has at least one navPoint entry."""
        epub_bytes = generate_epub("Test", "Author", "<p>Text</p>")
        with _parse_epub(epub_bytes) as zf:
            ncx = zf.read("OEBPS/toc.ncx").decode("utf-8")
            assert "navPoint" in ncx
            assert "content.xhtml" in ncx

    def test_content_xhtml_includes_article_html(self) -> None:
        """content.xhtml wraps the article HTML in a valid XHTML document."""
        epub_bytes = generate_epub(
            "Test", "Author", "<p>Article paragraph here.</p>"
        )
        with _parse_epub(epub_bytes) as zf:
            xhtml = zf.read("OEBPS/content.xhtml").decode("utf-8")
            assert "Article paragraph here." in xhtml
            assert "<?xml version" in xhtml
            assert "xmlns" in xhtml

    def test_content_xhtml_includes_author_byline(self) -> None:
        """content.xhtml shows the author byline when provided."""
        epub_bytes = generate_epub("Test", "John Smith", "<p>Text</p>")
        with _parse_epub(epub_bytes) as zf:
            xhtml = zf.read("OEBPS/content.xhtml").decode("utf-8")
            assert "John Smith" in xhtml

    def test_content_xhtml_no_author_when_empty(self) -> None:
        """content.xhtml omits author section when author is empty."""
        epub_bytes = generate_epub("Test", "", "<p>Text</p>")
        with _parse_epub(epub_bytes) as zf:
            xhtml = zf.read("OEBPS/content.xhtml").decode("utf-8")
            assert "article-meta" not in xhtml

    def test_content_xhtml_links_stylesheet(self) -> None:
        """content.xhtml links to the stylesheet."""
        epub_bytes = generate_epub("Test", "Author", "<p>Text</p>")
        with _parse_epub(epub_bytes) as zf:
            xhtml = zf.read("OEBPS/content.xhtml").decode("utf-8")
            assert "style.css" in xhtml

    def test_stylesheet_has_body_rules(self) -> None:
        """style.css includes body styling for e-readers."""
        epub_bytes = generate_epub("Test", "Author", "<p>Text</p>")
        with _parse_epub(epub_bytes) as zf:
            css = zf.read("OEBPS/style.css").decode("utf-8")
            assert "body" in css
            assert "font-family" in css

    def test_escapes_special_characters_in_title(self) -> None:
        """Special characters in the title are escaped in XML."""
        epub_bytes = generate_epub(
            'Title with <script> & "quotes"', "Author", "<p>Text</p>"
        )
        with _parse_epub(epub_bytes) as zf:
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
            assert "&lt;script&gt;" in opf
            assert "&amp;" in opf


# ---------------------------------------------------------------------------
# EPUB generator unit tests — generate_multi_epub()
# ---------------------------------------------------------------------------


class TestGenerateMultiEpub:
    def test_returns_valid_epub(self) -> None:
        """generate_multi_epub() returns a valid EPUB ZIP."""
        articles = [
            {"title": "Chapter 1", "author": "Alice", "html_content": "<p>One</p>"},
            {"title": "Chapter 2", "author": "Bob", "html_content": "<p>Two</p>"},
        ]
        epub_bytes = generate_multi_epub("Collection", articles)
        assert isinstance(epub_bytes, bytes)
        with _parse_epub(epub_bytes) as zf:
            assert zf.testzip() is None

    def test_contains_multiple_chapters(self) -> None:
        """Multi-chapter EPUB contains separate XHTML files for each article."""
        articles = [
            {"title": "First", "author": "A", "html_content": "<p>First</p>"},
            {"title": "Second", "author": "B", "html_content": "<p>Second</p>"},
            {"title": "Third", "author": "C", "html_content": "<p>Third</p>"},
        ]
        epub_bytes = generate_multi_epub("Collection", articles)
        with _parse_epub(epub_bytes) as zf:
            names = zf.namelist()
            assert "OEBPS/chapter-1.xhtml" in names
            assert "OEBPS/chapter-2.xhtml" in names
            assert "OEBPS/chapter-3.xhtml" in names

    def test_opf_lists_all_chapters(self) -> None:
        """content.opf manifest includes all chapter files."""
        articles = [
            {"title": "Ch1", "author": "", "html_content": "<p>A</p>"},
            {"title": "Ch2", "author": "", "html_content": "<p>B</p>"},
        ]
        epub_bytes = generate_multi_epub("Collection", articles)
        with _parse_epub(epub_bytes) as zf:
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
            assert "chapter-1.xhtml" in opf
            assert "chapter-2.xhtml" in opf

    def test_ncx_has_navpoints_for_all_chapters(self) -> None:
        """toc.ncx has navPoint entries for each chapter."""
        articles = [
            {"title": "Alpha", "author": "", "html_content": "<p>A</p>"},
            {"title": "Beta", "author": "", "html_content": "<p>B</p>"},
        ]
        epub_bytes = generate_multi_epub("Collection", articles)
        with _parse_epub(epub_bytes) as zf:
            ncx = zf.read("OEBPS/toc.ncx").decode("utf-8")
            assert "Alpha" in ncx
            assert "Beta" in ncx

    def test_book_title_in_opf(self) -> None:
        """content.opf uses the collection title."""
        articles = [{"title": "Ch1", "author": "", "html_content": "<p>A</p>"}]
        epub_bytes = generate_multi_epub("My Reading List", articles)
        with _parse_epub(epub_bytes) as zf:
            opf = zf.read("OEBPS/content.opf").decode("utf-8")
            assert "My Reading List" in opf


# ---------------------------------------------------------------------------
# XHTML sanitization tests
# ---------------------------------------------------------------------------


class TestSanitizeXhtml:
    def test_closes_br_tags(self) -> None:
        """Bare <br> tags are self-closed for XHTML compliance."""
        result = _sanitize_xhtml("<p>Hello<br>World</p>")
        assert "<br />" in result

    def test_closes_img_tags(self) -> None:
        """Bare <img> tags are self-closed for XHTML compliance."""
        result = _sanitize_xhtml('<img src="test.jpg">')
        assert "/>" in result

    def test_closes_hr_tags(self) -> None:
        """Bare <hr> tags are self-closed."""
        result = _sanitize_xhtml("<hr>")
        assert "<hr />" in result

    def test_fixes_bare_ampersands(self) -> None:
        """Bare ampersands are escaped."""
        result = _sanitize_xhtml("<p>Tom & Jerry</p>")
        assert "&amp;" in result

    def test_preserves_entity_references(self) -> None:
        """Valid entity references are not double-escaped."""
        result = _sanitize_xhtml("<p>&amp; &lt; &gt;</p>")
        assert "&amp;" in result
        assert "&lt;" in result
        assert "&gt;" in result
        # Should not have &&amp;amp;
        assert "&amp;amp;" not in result


# ---------------------------------------------------------------------------
# Filename sanitization tests
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    def test_basic_title(self) -> None:
        """A simple title is used as-is."""
        assert _sanitize_filename("My Article") == "My Article"

    def test_removes_special_characters(self) -> None:
        """Characters unsafe for filenames are removed."""
        assert _sanitize_filename('Title: A "Test"') == "Title A Test"

    def test_truncates_long_titles(self) -> None:
        """Titles longer than 80 characters are truncated."""
        long_title = "A" * 100
        result = _sanitize_filename(long_title)
        assert len(result) <= 80

    def test_empty_title_fallback(self) -> None:
        """Empty titles fall back to 'article'."""
        assert _sanitize_filename("") == "article"

    def test_epub_filename_adds_extension(self) -> None:
        """epub_filename() appends .epub extension."""
        assert epub_filename("My Article") == "My Article.epub"


# ---------------------------------------------------------------------------
# GET /api/articles/{id}/epub — single article EPUB endpoint
# ---------------------------------------------------------------------------


class TestArticleEpubEndpoint:
    async def test_returns_epub_for_article_with_content(self) -> None:
        """GET /api/articles/{id}/epub returns an EPUB file."""
        article = ArticleFactory.create(
            id="epub_art_1",
            user_id="user_001",
            title="EPUB Test Article",
            author="Test Author",
            html_key="articles/epub_art_1/content.html",
        )

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql and "epub_art_1" in params:
                return [article]
            return []

        r2 = MockR2()

        db = MockD1(execute=execute)
        env = MockEnv(db=db, content=r2)

        # Store HTML content in R2
        await r2.put(
            "articles/epub_art_1/content.html",
            "<p>This is the article content for EPUB export.</p>",
        )

        client, session_id = await _article_authenticated_client(env)
        resp = client.get(
            "/api/articles/epub_art_1/epub",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/epub+zip"
        assert "attachment" in resp.headers["content-disposition"]
        assert ".epub" in resp.headers["content-disposition"]
        assert "EPUB Test Article" in resp.headers["content-disposition"]

        # Verify it is a valid EPUB
        with _parse_epub(resp.content) as zf:
            assert zf.testzip() is None
            names = zf.namelist()
            assert "mimetype" in names
            assert "OEBPS/content.xhtml" in names

    async def test_returns_404_when_no_html_key(self) -> None:
        """GET /api/articles/{id}/epub returns 404 when article has no html_key."""
        article = ArticleFactory.create(
            id="epub_no_html",
            user_id="user_001",
            html_key=None,
        )

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql and "epub_no_html" in params:
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _article_authenticated_client(env)
        resp = client.get(
            "/api/articles/epub_no_html/epub",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    async def test_returns_404_when_content_missing_in_r2(self) -> None:
        """GET /api/articles/{id}/epub returns 404 when R2 content is missing."""
        article = ArticleFactory.create(
            id="epub_no_r2",
            user_id="user_001",
            html_key="articles/epub_no_r2/content.html",
        )

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql and "epub_no_r2" in params:
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()  # Empty R2 -- no content stored
        env = MockEnv(db=db, content=r2)

        client, session_id = await _article_authenticated_client(env)
        resp = client.get(
            "/api/articles/epub_no_r2/epub",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    async def test_returns_404_for_nonexistent_article(self) -> None:
        """GET /api/articles/{id}/epub returns 404 for unknown article ID."""
        db = MockD1()  # Returns empty for all queries
        env = MockEnv(db=db)

        client, session_id = await _article_authenticated_client(env)
        resp = client.get(
            "/api/articles/nonexistent/epub",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 404

    def test_requires_auth(self) -> None:
        """GET /api/articles/{id}/epub returns 401 without auth."""
        env = MockEnv()
        app = _make_article_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles/some_id/epub")
        assert resp.status_code == 401

    async def test_handles_untitled_article(self) -> None:
        """GET /api/articles/{id}/epub handles articles without a title."""
        article = ArticleFactory.create(
            id="epub_untitled",
            user_id="user_001",
            title=None,
            author=None,
            html_key="articles/epub_untitled/content.html",
        )

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql and "epub_untitled" in params:
                return [article]
            return []

        r2 = MockR2()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, content=r2)

        await r2.put("articles/epub_untitled/content.html", "<p>Content</p>")

        client, session_id = await _article_authenticated_client(env)
        resp = client.get(
            "/api/articles/epub_untitled/epub",
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert ".epub" in resp.headers["content-disposition"]


# ---------------------------------------------------------------------------
# POST /api/export/epub — batch EPUB endpoint
# ---------------------------------------------------------------------------


class TestBatchEpubEndpoint:
    async def test_exports_multiple_articles(self) -> None:
        """POST /api/export/epub creates a multi-chapter EPUB."""
        articles = [
            ArticleFactory.create(
                id="batch_1",
                user_id="user_001",
                title="Batch Article 1",
                author="Author A",
                html_key="articles/batch_1/content.html",
            ),
            ArticleFactory.create(
                id="batch_2",
                user_id="user_001",
                title="Batch Article 2",
                author="Author B",
                html_key="articles/batch_2/content.html",
            ),
        ]

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql:
                for art in articles:
                    if art["id"] in params:
                        return [art]
            return []

        r2 = MockR2()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, content=r2)

        await r2.put("articles/batch_1/content.html", "<p>Content 1</p>")
        await r2.put("articles/batch_2/content.html", "<p>Content 2</p>")

        client, session_id = await _export_authenticated_client(env)
        resp = client.post(
            "/api/export/epub",
            json={"article_ids": ["batch_1", "batch_2"]},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/epub+zip"
        assert "attachment" in resp.headers["content-disposition"]
        assert ".epub" in resp.headers["content-disposition"]

        with _parse_epub(resp.content) as zf:
            assert zf.testzip() is None
            names = zf.namelist()
            assert "OEBPS/chapter-1.xhtml" in names
            assert "OEBPS/chapter-2.xhtml" in names

    async def test_returns_422_for_empty_article_ids(self) -> None:
        """POST /api/export/epub returns 422 when article_ids is empty."""
        env = MockEnv()
        client, session_id = await _export_authenticated_client(env)
        resp = client.post(
            "/api/export/epub",
            json={"article_ids": []},
            cookies={COOKIE_NAME: session_id},
        )
        assert resp.status_code == 422

    async def test_returns_422_for_too_many_articles(self) -> None:
        """POST /api/export/epub returns 422 when more than 50 IDs are given."""
        env = MockEnv()
        client, session_id = await _export_authenticated_client(env)
        resp = client.post(
            "/api/export/epub",
            json={"article_ids": [f"id_{i}" for i in range(51)]},
            cookies={COOKIE_NAME: session_id},
        )
        assert resp.status_code == 422

    async def test_returns_404_when_no_content_available(self) -> None:
        """POST /api/export/epub returns 404 when none of the articles have content."""
        article = ArticleFactory.create(
            id="batch_empty",
            user_id="user_001",
            html_key=None,
        )

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql and "batch_empty" in params:
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _export_authenticated_client(env)
        resp = client.post(
            "/api/export/epub",
            json={"article_ids": ["batch_empty"]},
            cookies={COOKIE_NAME: session_id},
        )
        assert resp.status_code == 404

    async def test_skips_articles_without_content(self) -> None:
        """POST /api/export/epub skips articles without HTML and includes the rest."""
        art_with = ArticleFactory.create(
            id="batch_has",
            user_id="user_001",
            title="Has Content",
            html_key="articles/batch_has/content.html",
        )
        art_without = ArticleFactory.create(
            id="batch_no",
            user_id="user_001",
            title="No Content",
            html_key=None,
        )

        def execute(sql: str, params: list) -> list:
            if "SELECT" in sql:
                if "batch_has" in params:
                    return [art_with]
                if "batch_no" in params:
                    return [art_without]
            return []

        r2 = MockR2()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, content=r2)

        await r2.put("articles/batch_has/content.html", "<p>Content</p>")

        client, session_id = await _export_authenticated_client(env)
        resp = client.post(
            "/api/export/epub",
            json={"article_ids": ["batch_has", "batch_no"]},
            cookies={COOKIE_NAME: session_id},
        )

        assert resp.status_code == 200
        with _parse_epub(resp.content) as zf:
            names = zf.namelist()
            # Only one chapter (the article with content)
            assert "OEBPS/chapter-1.xhtml" in names
            assert "OEBPS/chapter-2.xhtml" not in names

    def test_requires_auth(self) -> None:
        """POST /api/export/epub returns 401 without auth."""
        env = MockEnv()
        app = _make_export_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/export/epub",
            json={"article_ids": ["some_id"]},
        )
        assert resp.status_code == 401

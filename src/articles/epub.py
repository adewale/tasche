"""Pure-Python EPUB generator for Tasche.

Generates EPUB 2.0.1 files using only the standard library (zipfile + io).
EPUB is a ZIP container with a specific structure:

    mimetype                    (first entry, uncompressed)
    META-INF/container.xml      (points to content.opf)
    OEBPS/content.opf           (manifest + spine)
    OEBPS/toc.ncx               (table of contents)
    OEBPS/content.xhtml         (the article HTML)
    OEBPS/style.css             (reading stylesheet)

No external libraries are used, making this compatible with Pyodide.
"""

from __future__ import annotations

import io
import re
import uuid
import zipfile
from html import escape as html_escape
from typing import Any


def _sanitize_xhtml(html_content: str) -> str:
    """Convert HTML content into valid XHTML for EPUB.

    Performs minimal fixups to ensure the content is well-formed XHTML:
    - Closes void elements (img, br, hr, input, meta, link)
    - Escapes ampersands that are not part of entities
    """
    # Close void elements that are not self-closed
    void_elements = ["img", "br", "hr", "input", "meta", "link", "source"]
    for tag in void_elements:
        # Match tags like <br> or <br attr="val"> that are NOT already self-closed
        html_content = re.sub(
            rf"<({tag})(\s[^>]*)?>(?!\s*</{tag}>)",
            r"<\1\2 />",
            html_content,
            flags=re.IGNORECASE,
        )
        # Clean up double self-close: <br / /> -> <br />
        html_content = re.sub(
            rf"<({tag})(\s[^/]*?)\s*/\s*/>",
            r"<\1\2 />",
            html_content,
            flags=re.IGNORECASE,
        )

    # Fix bare ampersands (not part of a valid entity reference)
    html_content = re.sub(r"&(?!#?\w+;)", "&amp;", html_content)

    return html_content


def _sanitize_filename(title: str) -> str:
    """Create a safe filename from an article title.

    Removes or replaces characters that are not safe for filenames.
    Truncates to 80 characters to avoid overly long filenames.
    """
    # Replace common problematic characters
    safe = re.sub(r'[<>:"/\\|?*]', "", title)
    # Replace whitespace sequences with a single space
    safe = re.sub(r"\s+", " ", safe).strip()
    # Truncate
    if len(safe) > 80:
        safe = safe[:80].rstrip()
    return safe or "article"


_CONTAINER_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""


_STYLESHEET = """\
body {
  font-family: Georgia, "Times New Roman", serif;
  line-height: 1.6;
  margin: 1em;
  color: #1a1a1a;
}

h1, h2, h3, h4, h5, h6 {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  line-height: 1.3;
  margin-top: 1.5em;
  margin-bottom: 0.5em;
}

h1 {
  font-size: 1.6em;
  border-bottom: 1px solid #ccc;
  padding-bottom: 0.3em;
}

h2 {
  font-size: 1.3em;
}

h3 {
  font-size: 1.1em;
}

p {
  margin: 0.8em 0;
  text-align: justify;
}

img {
  max-width: 100%;
  height: auto;
}

blockquote {
  margin: 1em 0;
  padding: 0.5em 1em;
  border-left: 3px solid #ccc;
  color: #555;
  font-style: italic;
}

pre, code {
  font-family: "Courier New", Courier, monospace;
  font-size: 0.9em;
}

pre {
  background: #f4f4f4;
  padding: 1em;
  overflow-x: auto;
  border-radius: 3px;
}

code {
  background: #f4f4f4;
  padding: 0.15em 0.3em;
  border-radius: 2px;
}

pre code {
  background: none;
  padding: 0;
}

a {
  color: #1a5276;
  text-decoration: underline;
}

table {
  border-collapse: collapse;
  width: 100%;
  margin: 1em 0;
}

th, td {
  border: 1px solid #ddd;
  padding: 0.5em;
  text-align: left;
}

th {
  background: #f4f4f4;
  font-weight: bold;
}

ul, ol {
  margin: 0.8em 0;
  padding-left: 2em;
}

li {
  margin: 0.3em 0;
}

hr {
  border: none;
  border-top: 1px solid #ccc;
  margin: 2em 0;
}

figcaption {
  font-size: 0.85em;
  color: #666;
  text-align: center;
  margin-top: 0.3em;
}

.article-meta {
  color: #666;
  font-size: 0.9em;
  margin-bottom: 1.5em;
  border-bottom: 1px solid #eee;
  padding-bottom: 0.8em;
}
"""


def _build_content_opf(
    book_id: str,
    title: str,
    author: str,
    language: str,
    chapters: list[dict[str, str]],
) -> str:
    """Generate the OPF package document.

    Parameters
    ----------
    book_id:
        Unique identifier for the EPUB (UUID).
    title:
        Book title.
    author:
        Book author.
    language:
        BCP 47 language code.
    chapters:
        List of dicts with ``id`` and ``filename`` keys.
    """
    manifest_items = [
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '    <item id="css" href="style.css" media-type="text/css"/>',
    ]
    spine_refs = []

    for chapter in chapters:
        manifest_items.append(
            f'    <item id="{html_escape(chapter["id"])}" '
            f'href="{html_escape(chapter["filename"])}" '
            f'media-type="application/xhtml+xml"/>'
        )
        spine_refs.append(
            f'    <itemref idref="{html_escape(chapter["id"])}"/>'
        )

    manifest = "\n".join(manifest_items)
    spine = "\n".join(spine_refs)

    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{html_escape(title)}</dc:title>
    <dc:creator opf:role="aut">{html_escape(author)}</dc:creator>
    <dc:language>{html_escape(language)}</dc:language>
    <dc:identifier id="BookId">urn:uuid:{book_id}</dc:identifier>
  </metadata>
  <manifest>
{manifest}
  </manifest>
  <spine toc="ncx">
{spine}
  </spine>
</package>"""


def _build_toc_ncx(
    book_id: str,
    title: str,
    chapters: list[dict[str, str]],
) -> str:
    """Generate the NCX navigation document.

    Parameters
    ----------
    book_id:
        Unique identifier for the EPUB (UUID).
    title:
        Book title.
    chapters:
        List of dicts with ``id``, ``filename``, and ``title`` keys.
    """
    nav_points = []
    for i, chapter in enumerate(chapters, start=1):
        nav_points.append(f"""\
    <navPoint id="navPoint-{i}" playOrder="{i}">
      <navLabel>
        <text>{html_escape(chapter["title"])}</text>
      </navLabel>
      <content src="{html_escape(chapter['filename'])}"/>
    </navPoint>""")

    nav_content = "\n".join(nav_points)

    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:{book_id}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle>
    <text>{html_escape(title)}</text>
  </docTitle>
  <navMap>
{nav_content}
  </navMap>
</ncx>"""


def _build_xhtml(
    title: str,
    body_html: str,
    author: str = "",
    language: str = "en",
) -> str:
    """Wrap article HTML in a valid XHTML document.

    Parameters
    ----------
    title:
        Document title.
    body_html:
        The article's HTML content (will be sanitized for XHTML).
    author:
        Optional author byline.
    language:
        BCP 47 language code.
    """
    sanitized = _sanitize_xhtml(body_html)

    meta_section = ""
    if author:
        meta_section = f"""\
  <div class="article-meta">
    <p>By {html_escape(author)}</p>
  </div>
"""

    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{html_escape(language)}">
<head>
  <title>{html_escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <h1>{html_escape(title)}</h1>
{meta_section}  {sanitized}
</body>
</html>"""


def generate_epub(
    title: str,
    author: str,
    html_content: str,
    language: str = "en",
) -> bytes:
    """Generate a single-article EPUB file.

    Parameters
    ----------
    title:
        The article title.
    author:
        The article author (byline).
    html_content:
        The article's HTML content.
    language:
        BCP 47 language code (default: ``en``).

    Returns
    -------
    bytes
        The EPUB file as bytes, ready to be served as a download.
    """
    book_id = str(uuid.uuid4())

    chapters = [
        {"id": "content", "filename": "content.xhtml", "title": title or "Article"},
    ]

    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype MUST be the first file and MUST be uncompressed
        zf.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )

        # META-INF/container.xml
        zf.writestr("META-INF/container.xml", _CONTAINER_XML)

        # OEBPS/content.opf
        zf.writestr(
            "OEBPS/content.opf",
            _build_content_opf(book_id, title, author, language, chapters),
        )

        # OEBPS/toc.ncx
        zf.writestr(
            "OEBPS/toc.ncx",
            _build_toc_ncx(book_id, title, chapters),
        )

        # OEBPS/style.css
        zf.writestr("OEBPS/style.css", _STYLESHEET)

        # OEBPS/content.xhtml
        zf.writestr(
            "OEBPS/content.xhtml",
            _build_xhtml(title, html_content, author, language),
        )

    return buf.getvalue()


def generate_multi_epub(
    book_title: str,
    articles: list[dict[str, Any]],
    language: str = "en",
) -> bytes:
    """Generate a multi-chapter EPUB from a list of articles.

    Parameters
    ----------
    book_title:
        Title for the compiled EPUB.
    articles:
        List of article dicts, each with ``title``, ``author``, and
        ``html_content`` keys.
    language:
        BCP 47 language code (default: ``en``).

    Returns
    -------
    bytes
        The EPUB file as bytes.
    """
    book_id = str(uuid.uuid4())

    chapters = []
    for i, article in enumerate(articles):
        chapter_id = f"chapter-{i + 1}"
        filename = f"chapter-{i + 1}.xhtml"
        chapter_title = article.get("title") or f"Chapter {i + 1}"
        chapters.append({
            "id": chapter_id,
            "filename": filename,
            "title": chapter_title,
        })

    buf = io.BytesIO()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # mimetype MUST be first and uncompressed
        zf.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )

        # META-INF/container.xml
        zf.writestr("META-INF/container.xml", _CONTAINER_XML)

        # OEBPS/content.opf
        zf.writestr(
            "OEBPS/content.opf",
            _build_content_opf(book_id, book_title, "Tasche", language, chapters),
        )

        # OEBPS/toc.ncx
        zf.writestr(
            "OEBPS/toc.ncx",
            _build_toc_ncx(book_id, book_title, chapters),
        )

        # OEBPS/style.css
        zf.writestr("OEBPS/style.css", _STYLESHEET)

        # Chapter XHTML files
        for i, article in enumerate(articles):
            chapter_title = article.get("title") or f"Chapter {i + 1}"
            chapter_author = article.get("author") or ""
            chapter_html = article.get("html_content") or ""

            zf.writestr(
                f"OEBPS/chapter-{i + 1}.xhtml",
                _build_xhtml(chapter_title, chapter_html, chapter_author, language),
            )

    return buf.getvalue()


def epub_filename(title: str) -> str:
    """Generate a sanitized .epub filename from an article title.

    Parameters
    ----------
    title:
        The article title.

    Returns
    -------
    str
        A safe filename ending in ``.epub``.
    """
    return _sanitize_filename(title) + ".epub"

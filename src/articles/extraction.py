"""Content extraction utilities for the article processing pipeline.

Provides functions for extracting canonical URLs, article content,
markdown conversion, word counting, reading time estimation, and
image path rewriting.

Libraries used:
- beautifulsoup4 — HTML parsing, content extraction, canonical URL extraction
- markdownify — HTML to Markdown conversion
"""

from __future__ import annotations

import math
import re

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify


def parse_html(html: str) -> BeautifulSoup:
    """Parse an HTML string into a BeautifulSoup object.

    Use this to parse once at the top of a pipeline, then pass the
    resulting soup into the extraction functions that accept
    ``str | BeautifulSoup``.
    """
    return BeautifulSoup(html, "html.parser")


def _ensure_soup(html_or_soup: str | BeautifulSoup) -> BeautifulSoup:
    """Return a BeautifulSoup, parsing only if given a raw string."""
    if isinstance(html_or_soup, BeautifulSoup):
        return html_or_soup
    return BeautifulSoup(html_or_soup, "html.parser")


def extract_thumbnail_url(html: str | BeautifulSoup) -> str | None:
    """Extract a thumbnail URL from HTML meta tags.

    Checks og:image, twitter:image, and schema.org image in priority order.
    Returns the first valid URL found, or None.

    Accepts a raw HTML string or a pre-parsed BeautifulSoup object to
    avoid redundant parsing.
    """
    soup = _ensure_soup(html)

    for attr, value in [
        ("property", "og:image"),
        ("name", "twitter:image"),
        ("itemprop", "image"),
    ]:
        meta = soup.find("meta", attrs={attr: value})
        if meta and meta.get("content"):
            url = meta["content"].strip()
            if url.startswith(("http://", "https://")):
                return url

    return None


def extract_canonical_url(html: str | BeautifulSoup) -> str | None:
    """Extract the canonical URL from an HTML document.

    Looks for ``<link rel="canonical" href="...">`` in the page head.

    Parameters
    ----------
    html:
        Raw HTML string or pre-parsed BeautifulSoup object.

    Returns
    -------
    str or None
        The canonical URL if found, otherwise ``None``.
    """
    soup = _ensure_soup(html)
    link = soup.find("link", attrs={"rel": "canonical"})
    if link and link.get("href"):
        href = link["href"].strip()
        if href:
            return href
    return None


# ---------------------------------------------------------------------------
# BeautifulSoup content extractor
# ---------------------------------------------------------------------------

# Tags that are never article content.
_JUNK_TAGS = {
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "noscript",
    "iframe",
    "svg",
    "button",
    "input",
    "select",
    "textarea",
}

# Roles / classes / IDs that signal non-content regions.
_JUNK_PATTERNS = re.compile(
    r"nav|menu|sidebar|footer|header|comment|widget|advert|promo|social"
    r"|related|share|signup|subscribe|cookie|banner|popup|modal",
    re.IGNORECASE,
)


def _is_junk(tag: Tag) -> bool:
    """Return True if a tag is likely boilerplate rather than article content."""
    if tag.decomposed:
        return False
    if tag.name in _JUNK_TAGS:
        return True
    for attr in ("class", "id", "role"):
        val = tag.get(attr, "")
        text = " ".join(val) if isinstance(val, list) else str(val)
        if text and _JUNK_PATTERNS.search(text):
            return True
    return False


def _text_length(tag: Tag) -> int:
    """Return the length of visible text inside a tag."""
    return len(tag.get_text(strip=True))


def extract_article(html: str | BeautifulSoup) -> dict:
    """Extract article content using BeautifulSoup heuristics.

    Identifies the largest content-bearing block element (``<article>``,
    ``<main>``, or highest-scoring ``<div>``/``<section>``) and returns
    its inner HTML.

    Accepts a raw HTML string or a pre-parsed BeautifulSoup object.
    Note: this function mutates the soup (decomposes junk tags), so
    pass a copy if you need the original soup afterwards.
    """
    soup = _ensure_soup(html)

    # Extract fallback title from <title> tag
    fallback_title = ""
    title_tag = soup.find("title")
    if title_tag:
        fallback_title = title_tag.get_text(strip=True)

    # Extract author from common meta tags
    byline = None
    for meta_name in ("author", "article:author", "dc.creator"):
        meta = soup.find("meta", attrs={"name": meta_name}) or soup.find(
            "meta", attrs={"property": meta_name}
        )
        if meta and meta.get("content"):
            byline = meta["content"].strip()
            break

    # Remove junk elements
    for tag in soup.find_all(True):
        if _is_junk(tag):
            tag.decompose()

    # Try semantic containers first: <article>, then <main>
    content_tag = None
    for container_name in ("article", "main"):
        candidates = soup.find_all(container_name)
        if candidates:
            # Pick the one with the most text
            content_tag = max(candidates, key=_text_length)
            break

    # Fallback: score top-level block elements by text density
    if content_tag is None or _text_length(content_tag) < 100:
        best_score = 0
        for tag in soup.find_all(["div", "section"]):
            score = _text_length(tag)
            # Penalise deeply nested containers (they're likely wrappers)
            depth = len(list(tag.parents))
            score = score - (depth * 10)
            if score > best_score:
                best_score = score
                content_tag = tag

    # Last resort: use the <body>
    if content_tag is None:
        content_tag = soup.find("body") or soup

    # Extract title from <h1> inside the content container (preferred),
    # falling back to the page <title>.
    title = fallback_title
    if content_tag is not None:
        h1 = content_tag.find("h1")
        if h1:
            h1_text = h1.get_text(strip=True)
            if h1_text:
                title = h1_text

    content_html = content_tag.decode_contents()
    # Build excerpt directly from the already-parsed content tag to avoid
    # re-parsing the HTML string.
    excerpt = _make_excerpt_from_tag(content_tag)

    return {
        "title": title,
        "html": content_html,
        "excerpt": excerpt,
        "byline": byline,
    }


def _make_excerpt_from_tag(tag: Tag, max_length: int = 300) -> str:
    """Create a plain-text excerpt from a parsed tag.

    Extracts visible text and truncates to *max_length* characters at a
    word boundary, appending an ellipsis if truncated.  Avoids an extra
    BeautifulSoup parse by operating on the already-parsed tag.
    """
    text = tag.get_text(separator=" ", strip=True)
    if len(text) <= max_length:
        return text
    truncated = text[:max_length].rsplit(" ", 1)[0]
    return truncated + "..."


def _make_excerpt(html: str, max_length: int = 300) -> str:
    """Create a plain-text excerpt from HTML content.

    Strips all tags and truncates to *max_length* characters at a word
    boundary, appending an ellipsis if truncated.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    if len(text) <= max_length:
        return text
    # Truncate at a word boundary
    truncated = text[:max_length].rsplit(" ", 1)[0]
    return truncated + "..."


def _unwrap_layout_tables(soup: BeautifulSoup) -> None:
    """Replace layout tables with their cell contents in-place.

    Old-school HTML (e.g. paulgraham.com) uses nested ``<table>`` elements
    for page layout.  ``markdownify`` converts these into markdown table
    syntax, producing unreadable output.

    Heuristic: a table is *layout* if >=50% of its own (non-nested) cells
    are empty.  Only counts cells belonging directly to the table, not
    cells inside nested sub-tables.  Process innermost tables first so
    nested layout tables unwind correctly.
    """
    # Process innermost tables first (reverse avoids mutation issues)
    for table in reversed(soup.find_all("table")):
        # Collect only this table's own rows (not from nested tables)
        own_cells = []
        for tr in table.find_all("tr", recursive=False):
            own_cells.extend(tr.find_all(["td", "th"], recursive=False))
        # Also check rows inside direct thead/tbody/tfoot
        for section in table.find_all(["thead", "tbody", "tfoot"], recursive=False):
            for tr in section.find_all("tr", recursive=False):
                own_cells.extend(tr.find_all(["td", "th"], recursive=False))
        if not own_cells:
            table.unwrap()
            continue
        # Single-cell tables are always layout wrappers, never data
        if len(own_cells) <= 1:
            table.unwrap()
            continue
        empty = sum(1 for c in own_cells if not c.get_text(strip=True))
        if empty >= len(own_cells) * 0.5:
            table.unwrap()
    # Clean up any orphaned row/cell tags left after unwrapping
    for tag in soup.find_all(["tr", "td", "th", "thead", "tbody", "tfoot"]):
        if not tag.find_parent("table"):
            tag.unwrap()


def _get_code_language(el: Tag) -> str:
    """Extract the programming language from a ``<pre>`` element's ``<code>`` child."""
    code = el.find("code")
    if code:
        for cls in code.get("class", []):
            m = re.match(r"(?:language-|lang-)?(\w+)", cls)
            if m:
                return m.group(1)
    return ""


def html_to_markdown(html: str | BeautifulSoup) -> str:
    """Convert clean HTML to Markdown using markdownify.

    Parameters
    ----------
    html:
        Clean article HTML string or pre-parsed BeautifulSoup object
        (typically from ``extract_article``).

    Returns
    -------
    str
        Markdown representation of the HTML content.
    """
    soup = _ensure_soup(html)
    for tag in soup(["script", "style"]):
        tag.decompose()
    _unwrap_layout_tables(soup)
    cleaned_html = str(soup)

    md = markdownify(
        cleaned_html,
        heading_style="ATX",
        code_language_callback=_get_code_language,
        sub_symbol="<sub>",
        sup_symbol="<sup>",
    )
    # Clean up excessive whitespace while preserving paragraph breaks
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def count_words(text: str) -> int:
    """Count words in a text string.

    Splits on whitespace and counts non-empty tokens.

    Parameters
    ----------
    text:
        Plain text or Markdown string.

    Returns
    -------
    int
        Number of words.
    """
    return len(text.split())


def calculate_reading_time(word_count: int, wpm: int = 200) -> int:
    """Calculate estimated reading time in minutes.

    Parameters
    ----------
    word_count:
        Total number of words.
    wpm:
        Reading speed in words per minute (default 200).

    Returns
    -------
    int
        Reading time rounded up to the nearest minute, minimum 1.
    """
    if word_count <= 0:
        return 1
    return max(1, math.ceil(word_count / wpm))


def rewrite_image_paths(html: str | BeautifulSoup, image_map: dict[str, str]) -> str:
    """Replace original image URLs with local R2 paths in HTML.

    Parameters
    ----------
    html:
        HTML string or pre-parsed BeautifulSoup object containing
        ``<img>`` tags with original URLs.
    image_map:
        Mapping of original URL -> R2 key (e.g. ``articles/{id}/images/{hash}.webp``).

    Returns
    -------
    str
        HTML with image ``src`` attributes replaced by local R2 paths.
    """
    if not image_map:
        return str(html) if isinstance(html, BeautifulSoup) else html

    soup = _ensure_soup(html)
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src in image_map:
            img["src"] = image_map[src]
    return str(soup)

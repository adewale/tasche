"""Content extraction utilities for the article processing pipeline.

Provides functions for extracting canonical URLs, article content,
markdown conversion, word counting, reading time estimation, and
image path rewriting.

Libraries used:
- beautifulsoup4 — HTML parsing (canonical URL extraction, image src extraction)
- python-readability — article content extraction (Mozilla Readability algorithm)
- markdownify — HTML to Markdown conversion
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup
from markdownify import markdownify
from readability import parse as readability_parse


def extract_canonical_url(html: str) -> str | None:
    """Extract the canonical URL from an HTML document.

    Looks for ``<link rel="canonical" href="...">`` in the page head.

    Parameters
    ----------
    html:
        Raw HTML string.

    Returns
    -------
    str or None
        The canonical URL if found, otherwise ``None``.
    """
    soup = BeautifulSoup(html, "html.parser")
    link = soup.find("link", attrs={"rel": "canonical"})
    if link and link.get("href"):
        href = link["href"].strip()
        if href:
            return href
    return None


def extract_article(html: str) -> dict:
    """Extract article content from HTML using python-readability.

    Uses the Mozilla Readability algorithm to identify the main article
    content, stripping navigation, ads, and other boilerplate.

    Parameters
    ----------
    html:
        Raw HTML string of the full page.

    Returns
    -------
    dict
        Keys: ``title`` (str), ``html`` (str — clean article HTML),
        ``excerpt`` (str — short summary), ``byline`` (str | None — author).
    """
    article = readability_parse(html)
    content = article.content or ""
    excerpt = article.excerpt or _make_excerpt(content)
    return {
        "title": article.title or "",
        "html": content,
        "excerpt": excerpt,
        "byline": article.byline or None,
    }


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


def html_to_markdown(html: str) -> str:
    """Convert clean HTML to Markdown using markdownify.

    Parameters
    ----------
    html:
        Clean article HTML (typically from ``extract_article``).

    Returns
    -------
    str
        Markdown representation of the HTML content.
    """
    # Pre-strip script and style tags via BeautifulSoup since markdownify's
    # strip parameter may not handle them reliably.
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    cleaned_html = str(soup)

    md = markdownify(cleaned_html, heading_style="ATX")
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
    minutes = word_count / wpm
    return max(1, round(minutes + 0.49999))


def rewrite_image_paths(html: str, image_map: dict[str, str]) -> str:
    """Replace original image URLs with local R2 paths in HTML.

    Parameters
    ----------
    html:
        HTML string containing ``<img>`` tags with original URLs.
    image_map:
        Mapping of original URL -> R2 key (e.g. ``articles/{id}/images/{hash}.webp``).

    Returns
    -------
    str
        HTML with image ``src`` attributes replaced by local R2 paths.
    """
    if not image_map:
        return html

    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src in image_map:
            img["src"] = image_map[src]
    return str(soup)

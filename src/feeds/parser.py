"""Minimal RSS/Atom feed parser using only Python stdlib.

Handles RSS 2.0 and Atom 1.0 feeds using xml.etree.ElementTree.
No external dependencies (feedparser is not Pyodide-compatible).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# Atom namespace
_ATOM_NS = "http://www.w3.org/2005/Atom"


@dataclass
class FeedEntry:
    """A single entry/item from a feed."""

    title: str = ""
    link: str = ""
    published: str = ""
    summary: str = ""


@dataclass
class ParsedFeed:
    """Result of parsing a feed document."""

    title: str = ""
    site_url: str = ""
    entries: list[FeedEntry] = field(default_factory=list)


def parse_feed(xml_text: str) -> ParsedFeed:
    """Parse an RSS 2.0 or Atom 1.0 feed from XML text.

    Parameters
    ----------
    xml_text:
        The raw XML content of the feed.

    Returns
    -------
    ParsedFeed
        Parsed feed metadata and entries.

    Raises
    ------
    ValueError
        If the XML is not a recognised feed format.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc

    tag = _strip_ns(root.tag)

    if tag == "rss":
        return _parse_rss(root)
    elif tag == "feed":
        return _parse_atom(root)
    elif tag == "RDF":
        # RSS 1.0 (RDF) — treat channel/item similar to RSS 2.0
        return _parse_rdf(root)
    else:
        raise ValueError(f"Unrecognised feed format: root element is <{root.tag}>")


def _strip_ns(tag: str) -> str:
    """Remove the namespace prefix from an XML tag."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_text(element: ET.Element, path: str, namespaces: dict[str, str] | None = None) -> str:
    """Find a child element and return its text, or empty string."""
    child = element.find(path, namespaces)
    if child is not None and child.text:
        return child.text.strip()
    return ""


def _parse_rss(root: ET.Element) -> ParsedFeed:
    """Parse an RSS 2.0 feed."""
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS feed missing <channel> element")

    result = ParsedFeed(
        title=_find_text(channel, "title"),
        site_url=_find_text(channel, "link"),
    )

    for item in channel.findall("item"):
        entry = FeedEntry(
            title=_find_text(item, "title"),
            link=_find_text(item, "link"),
            published=_find_text(item, "pubDate"),
            summary=_find_text(item, "description"),
        )
        # Some feeds put link in guid when link is absent
        if not entry.link:
            guid = item.find("guid")
            if guid is not None and guid.text and guid.text.startswith("http"):
                entry.link = guid.text.strip()
        result.entries.append(entry)

    return result


def _parse_atom(root: ET.Element) -> ParsedFeed:
    """Parse an Atom 1.0 feed."""
    ns = {"atom": _ATOM_NS}

    result = ParsedFeed(
        title=_find_text(root, "atom:title", ns) or _find_text(root, "title"),
    )

    # Atom uses <link rel="alternate" href="..."/> for the site URL
    for link in root.findall("atom:link", ns) + root.findall("link"):
        rel = link.get("rel", "alternate")
        if rel == "alternate":
            result.site_url = link.get("href", "")
            break

    for entry_el in root.findall("atom:entry", ns) + root.findall("entry"):
        entry = FeedEntry(
            title=_find_text(entry_el, "atom:title", ns) or _find_text(entry_el, "title"),
            published=(
                _find_text(entry_el, "atom:published", ns)
                or _find_text(entry_el, "atom:updated", ns)
                or _find_text(entry_el, "published")
                or _find_text(entry_el, "updated")
            ),
            summary=(
                _find_text(entry_el, "atom:summary", ns)
                or _find_text(entry_el, "atom:content", ns)
                or _find_text(entry_el, "summary")
                or _find_text(entry_el, "content")
            ),
        )

        # Find the entry link
        for link in entry_el.findall("atom:link", ns) + entry_el.findall("link"):
            rel = link.get("rel", "alternate")
            if rel == "alternate" or rel == "":
                entry.link = link.get("href", "")
                break

        result.entries.append(entry)

    return result


def _parse_rdf(root: ET.Element) -> ParsedFeed:
    """Parse an RSS 1.0 (RDF) feed — basic support."""
    # RSS 1.0 uses RDF namespace; channel and items are siblings
    ns_rss = "http://purl.org/rss/1.0/"

    result = ParsedFeed()

    channel = root.find(f"{{{ns_rss}}}channel")
    if channel is not None:
        result.title = _find_text(channel, f"{{{ns_rss}}}title")
        result.site_url = _find_text(channel, f"{{{ns_rss}}}link")

    for item in root.findall(f"{{{ns_rss}}}item"):
        entry = FeedEntry(
            title=_find_text(item, f"{{{ns_rss}}}title"),
            link=_find_text(item, f"{{{ns_rss}}}link"),
            summary=_find_text(item, f"{{{ns_rss}}}description"),
        )
        result.entries.append(entry)

    return result


def parse_opml(xml_text: str) -> list[dict[str, str]]:
    """Parse an OPML file and return a list of feed outlines.

    Each result dict contains ``url`` (the feed URL), ``title`` (if present),
    and ``site_url`` (if present).

    Parameters
    ----------
    xml_text:
        The raw XML content of the OPML file.

    Returns
    -------
    list[dict[str, str]]
        List of feed outlines found in the OPML body.

    Raises
    ------
    ValueError
        If the XML is not valid OPML.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid OPML XML: {exc}") from exc

    if _strip_ns(root.tag) != "opml":
        raise ValueError(f"Not an OPML document: root element is <{root.tag}>")

    feeds: list[dict[str, str]] = []
    body = root.find("body")
    if body is None:
        return feeds

    _collect_outlines(body, feeds)
    return feeds


def _collect_outlines(element: ET.Element, feeds: list[dict[str, str]]) -> None:
    """Recursively collect feed outlines from an OPML body."""
    for outline in element.findall("outline"):
        xml_url = outline.get("xmlUrl", "").strip()
        if xml_url:
            feeds.append(
                {
                    "url": xml_url,
                    "title": outline.get("title", "").strip() or outline.get("text", "").strip(),
                    "site_url": outline.get("htmlUrl", "").strip(),
                }
            )
        # Recurse into nested outlines (OPML folders)
        _collect_outlines(outline, feeds)

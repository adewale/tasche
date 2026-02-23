"""Tests for the RSS/Atom feed parser (src/feeds/parser.py).

Covers RSS 2.0, Atom 1.0, and OPML parsing with sample XML.
"""

from __future__ import annotations

import pytest

from src.feeds.parser import parse_feed, parse_opml

# ---------------------------------------------------------------------------
# Sample RSS 2.0
# ---------------------------------------------------------------------------

_RSS_20 = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Blog</title>
    <link>https://example.com</link>
    <description>An example RSS feed</description>
    <item>
      <title>First Post</title>
      <link>https://example.com/first-post</link>
      <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
      <description>Summary of the first post.</description>
    </item>
    <item>
      <title>Second Post</title>
      <link>https://example.com/second-post</link>
      <pubDate>Tue, 02 Jan 2024 12:00:00 GMT</pubDate>
      <description>Summary of the second post.</description>
    </item>
  </channel>
</rss>
"""

# ---------------------------------------------------------------------------
# Sample Atom 1.0
# ---------------------------------------------------------------------------

_ATOM_10 = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Blog</title>
  <link href="https://atom-blog.example.com" rel="alternate" />
  <entry>
    <title>Alpha Entry</title>
    <link href="https://atom-blog.example.com/alpha" rel="alternate" />
    <published>2024-01-10T10:00:00Z</published>
    <summary>Alpha summary text.</summary>
  </entry>
  <entry>
    <title>Beta Entry</title>
    <link href="https://atom-blog.example.com/beta" rel="alternate" />
    <updated>2024-01-11T15:00:00Z</updated>
    <content>Beta content text.</content>
  </entry>
</feed>
"""

# ---------------------------------------------------------------------------
# Sample OPML
# ---------------------------------------------------------------------------

_OPML = """\
<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head>
    <title>My Feeds</title>
  </head>
  <body>
    <outline text="Tech" title="Tech">
      <outline type="rss" text="Blog A" title="Blog A"
               xmlUrl="https://a.example.com/feed.xml"
               htmlUrl="https://a.example.com" />
      <outline type="rss" text="Blog B" title="Blog B"
               xmlUrl="https://b.example.com/rss"
               htmlUrl="https://b.example.com" />
    </outline>
    <outline type="rss" text="Blog C" title="Blog C"
             xmlUrl="https://c.example.com/atom.xml"
             htmlUrl="https://c.example.com" />
  </body>
</opml>
"""


# ---------------------------------------------------------------------------
# RSS 2.0 tests
# ---------------------------------------------------------------------------


class TestParseRSS20:
    def test_parses_channel_metadata(self) -> None:
        result = parse_feed(_RSS_20)
        assert result.title == "Example Blog"
        assert result.site_url == "https://example.com"

    def test_parses_items(self) -> None:
        result = parse_feed(_RSS_20)
        assert len(result.entries) == 2

    def test_first_item_fields(self) -> None:
        result = parse_feed(_RSS_20)
        entry = result.entries[0]
        assert entry.title == "First Post"
        assert entry.link == "https://example.com/first-post"
        assert entry.published == "Mon, 01 Jan 2024 12:00:00 GMT"
        assert entry.summary == "Summary of the first post."

    def test_second_item_fields(self) -> None:
        result = parse_feed(_RSS_20)
        entry = result.entries[1]
        assert entry.title == "Second Post"
        assert entry.link == "https://example.com/second-post"

    def test_rss_with_guid_as_link(self) -> None:
        """When <link> is missing, fall back to <guid> if it looks like a URL."""
        xml = """\
<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test</title>
    <link>https://example.com</link>
    <item>
      <title>No Link Item</title>
      <guid>https://example.com/guid-item</guid>
    </item>
  </channel>
</rss>
"""
        result = parse_feed(xml)
        assert result.entries[0].link == "https://example.com/guid-item"


# ---------------------------------------------------------------------------
# Atom 1.0 tests
# ---------------------------------------------------------------------------


class TestParseAtom10:
    def test_parses_feed_metadata(self) -> None:
        result = parse_feed(_ATOM_10)
        assert result.title == "Atom Blog"
        assert result.site_url == "https://atom-blog.example.com"

    def test_parses_entries(self) -> None:
        result = parse_feed(_ATOM_10)
        assert len(result.entries) == 2

    def test_first_entry_fields(self) -> None:
        result = parse_feed(_ATOM_10)
        entry = result.entries[0]
        assert entry.title == "Alpha Entry"
        assert entry.link == "https://atom-blog.example.com/alpha"
        assert entry.published == "2024-01-10T10:00:00Z"
        assert entry.summary == "Alpha summary text."

    def test_second_entry_uses_updated_as_published(self) -> None:
        result = parse_feed(_ATOM_10)
        entry = result.entries[1]
        assert entry.published == "2024-01-11T15:00:00Z"

    def test_second_entry_uses_content_as_summary(self) -> None:
        result = parse_feed(_ATOM_10)
        entry = result.entries[1]
        assert entry.summary == "Beta content text."


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestParseFeedErrors:
    def test_invalid_xml_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid XML"):
            parse_feed("not xml at all <<<")

    def test_unknown_root_element_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unrecognised feed format"):
            parse_feed("<html><body>Not a feed</body></html>")

    def test_rss_missing_channel_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="missing <channel>"):
            parse_feed('<rss version="2.0"></rss>')


# ---------------------------------------------------------------------------
# OPML tests
# ---------------------------------------------------------------------------


class TestParseOPML:
    def test_parses_all_feeds(self) -> None:
        result = parse_opml(_OPML)
        assert len(result) == 3

    def test_first_feed(self) -> None:
        result = parse_opml(_OPML)
        assert result[0]["url"] == "https://a.example.com/feed.xml"
        assert result[0]["title"] == "Blog A"
        assert result[0]["site_url"] == "https://a.example.com"

    def test_nested_feed(self) -> None:
        result = parse_opml(_OPML)
        urls = [f["url"] for f in result]
        assert "https://b.example.com/rss" in urls

    def test_top_level_feed(self) -> None:
        result = parse_opml(_OPML)
        urls = [f["url"] for f in result]
        assert "https://c.example.com/atom.xml" in urls

    def test_invalid_opml_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid OPML XML"):
            parse_opml("not xml")

    def test_non_opml_document_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Not an OPML document"):
            parse_opml("<rss><channel></channel></rss>")

    def test_empty_body_returns_empty_list(self) -> None:
        opml = """\
<?xml version="1.0"?>
<opml version="2.0">
  <head><title>Empty</title></head>
  <body></body>
</opml>
"""
        result = parse_opml(opml)
        assert result == []

    def test_skips_outlines_without_xmlUrl(self) -> None:
        opml = """\
<?xml version="1.0"?>
<opml version="2.0">
  <head><title>Test</title></head>
  <body>
    <outline text="Folder" title="Folder" />
  </body>
</opml>
"""
        result = parse_opml(opml)
        assert result == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestParseFeedEdgeCases:
    def test_empty_rss_channel(self) -> None:
        xml = """\
<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Empty Feed</title>
    <link>https://example.com</link>
  </channel>
</rss>
"""
        result = parse_feed(xml)
        assert result.title == "Empty Feed"
        assert len(result.entries) == 0

    def test_atom_without_published_or_updated(self) -> None:
        xml = """\
<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>No Dates</title>
  <entry>
    <title>Entry Without Date</title>
    <link href="https://example.com/no-date" rel="alternate" />
  </entry>
</feed>
"""
        result = parse_feed(xml)
        assert result.entries[0].published == ""
        assert result.entries[0].link == "https://example.com/no-date"

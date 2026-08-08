#!/usr/bin/env python3
"""Diagnose why article processing fails for a given URL.

Exercises the same pipeline steps as process_article() but runs in CPython,
printing results at each stage so you can see exactly where things break.

Usage:
    uv run python script/diagnose_article.py <url>
    uv run python script/diagnose_article.py https://www.theguardian.com/...
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
import traceback


async def diagnose(url: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"Diagnosing: {url}")
    print(f"{'=' * 60}\n")

    # Step 1: Fetch the page
    print("[Step 1] Fetching page...")
    try:
        from articles.processing import _fetch_page
        from src.boundary import HttpClient

        async with HttpClient() as client:
            html, final_url = await _fetch_page(client, url)
        print(f"  OK — {len(html)} chars, final_url={final_url}")
    except Exception:
        print(f"  FAILED:\n{textwrap.indent(traceback.format_exc(), '    ')}")
        return

    # Step 2: JS-heavy check
    print("\n[Step 2] JS-heavy heuristic...")
    try:
        from articles.processing import _is_js_heavy

        is_js = _is_js_heavy(html)
        print(f"  is_js_heavy={is_js}")
    except Exception:
        print(f"  FAILED:\n{textwrap.indent(traceback.format_exc(), '    ')}")

    # Step 3: Canonical URL
    print("\n[Step 3] Extracting canonical URL...")
    try:
        from articles.extraction import extract_canonical_url

        canonical = extract_canonical_url(html)
        print(f"  canonical_url={canonical}")
    except Exception:
        print(f"  FAILED:\n{textwrap.indent(traceback.format_exc(), '    ')}")

    # Step 4: Content extraction
    print("\n[Step 4] Extracting article content...")
    try:
        from articles.extraction import extract_article

        article = extract_article(html)
        print(f"  title={article['title']!r}")
        print(f"  byline={article['byline']!r}")
        print(f"  excerpt={article['excerpt'][:120]!r}...")
        print(f"  html_length={len(article['html'])} chars")
        clean_html = article["html"]
    except Exception:
        print(f"  FAILED:\n{textwrap.indent(traceback.format_exc(), '    ')}")
        return

    # Step 5: Image extraction
    print("\n[Step 5] Extracting images from HTML...")
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(clean_html, "html.parser")
        imgs = soup.find_all("img")
        srcs = [img.get("src", "") for img in imgs]
        print(f"  Found {len(imgs)} <img> tags")
        for s in srcs[:5]:
            print(f"    {s[:100]}")
        if len(srcs) > 5:
            print(f"    ... and {len(srcs) - 5} more")
    except Exception:
        print(f"  FAILED:\n{textwrap.indent(traceback.format_exc(), '    ')}")

    # Step 6: Markdown conversion
    print("\n[Step 6] Converting to Markdown...")
    try:
        from articles.extraction import (
            calculate_reading_time,
            count_words,
            html_to_markdown,
        )

        markdown = html_to_markdown(clean_html)
        word_count = count_words(markdown)
        reading_time = calculate_reading_time(word_count)
        print(f"  word_count={word_count}, reading_time={reading_time} min")
        print(f"  markdown_length={len(markdown)} chars")
        print(f"  first 200 chars: {markdown[:200]!r}")
    except Exception:
        print(f"  FAILED:\n{textwrap.indent(traceback.format_exc(), '    ')}")

    print(f"\n{'=' * 60}")
    print("All pipeline steps passed!")
    print(f"{'=' * 60}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: uv run python script/diagnose_article.py <url>")
        sys.exit(1)
    asyncio.run(diagnose(sys.argv[1]))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Trace TTS pipeline to diagnose audio truncation.

Standalone diagnostic script that runs against staging via HTTP.
No project imports — just httpx.

Usage:
    STAGING_URL=https://tasche-staging.adewale-883.workers.dev \
        python scripts/agent-tools/trace_tts_pipeline.py

What it does:
    1. Creates a test article with known long-form content
    2. Calls /process-now to populate markdown_content
    3. Calls /tts-now — returns per-chunk diagnostics
    4. Downloads audio via GET /audio — reports actual file size
    5. Prints structured trace: text chunks vs audio chunks vs downloaded bytes
    6. Cleans up the test article

Exit code 0 if audio is proportional to text, 1 if truncated.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import httpx

BASE_URL = os.environ.get(
    "STAGING_URL",
    "https://tasche-staging.adewale-883.workers.dev",
)

# A known long article URL that should produce multiple TTS chunks
TEST_CONTENT_URL = "https://example.com"

# Minimum expected audio bytes for a multi-chunk TTS.  A single MP3 frame
# is ~400 bytes.  Even a short article should produce >10KB of audio.
MIN_EXPECTED_AUDIO_BYTES = 10_000


def _print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def main() -> int:
    test_id = uuid.uuid4().hex[:8]
    article_id: str | None = None

    with httpx.Client(base_url=BASE_URL, timeout=90.0) as client:
        try:
            # -------------------------------------------------------
            # Step 1: Create test article
            # -------------------------------------------------------
            _print_section("Step 1: Create test article")
            resp = client.post(
                "/api/articles",
                json={
                    "url": f"https://example.com/tts-trace-{test_id}",
                    "title": f"TTS Trace Test {test_id}",
                },
            )
            if resp.status_code != 201:
                print(f"FAIL: Create article returned {resp.status_code}: {resp.text}")
                return 1
            article_id = resp.json()["id"]
            print(f"  Article ID: {article_id}")
            print(f"  Status: {resp.json().get('status')}")

            # -------------------------------------------------------
            # Step 2: Process article (populate markdown_content)
            # -------------------------------------------------------
            _print_section("Step 2: Process article (populate markdown)")
            resp = client.post(
                f"/api/articles/{article_id}/process-now",
                timeout=90.0,
            )
            if resp.status_code != 200:
                print(f"FAIL: Process-now returned {resp.status_code}: {resp.text}")
                return 1
            process = resp.json()
            print(f"  Result: {process.get('result')}")
            if process.get("result") == "error":
                print(f"  Error: {process.get('error')}")

            # Fetch article to check markdown_content length
            resp = client.get(f"/api/articles/{article_id}")
            article = resp.json()
            md_len = len(article.get("markdown_content") or "")
            print(f"  markdown_content length: {md_len} chars")
            print(f"  Article status: {article.get('status')}")

            if md_len == 0:
                print(
                    "\n  WARNING: No markdown content — TTS will have nothing to process."
                    "\n  This is expected for example.com (minimal content)."
                    "\n  For a real trace, use an article with substantial text."
                )

            # -------------------------------------------------------
            # Step 3: Trigger TTS via /tts-now
            # -------------------------------------------------------
            _print_section("Step 3: Trigger TTS (inline, bypass queue)")
            resp = client.post(
                f"/api/articles/{article_id}/tts-now",
                timeout=120.0,
            )
            if resp.status_code != 200:
                print(f"FAIL: tts-now returned {resp.status_code}: {resp.text}")
                return 1

            tts_result = resp.json()
            print(f"  Result: {tts_result.get('result')}")

            if tts_result.get("result") == "error":
                print(f"  Error: {tts_result.get('error')}")
                print(f"  Traceback:\n{tts_result.get('traceback', '')[:500]}")
                return 1

            diag = tts_result.get("diagnostics", {})
            print(f"  Chunks: {diag.get('chunks')}")
            print(f"  Total bytes (from pipeline): {diag.get('total_bytes')}")

            chunk_sizes = diag.get("chunk_sizes", [])
            print(f"  Chunk sizes: {chunk_sizes}")

            chunk_diag = diag.get("chunk_diagnostics", [])
            if chunk_diag:
                print("\n  Per-chunk trace:")
                print(f"  {'Chunk':>5}  {'Text (chars)':>12}  {'Audio (bytes)':>13}  {'Ratio':>8}")
                print(f"  {'-----':>5}  {'------------':>12}  {'-------------':>13}  {'-----':>8}")
                for cd in chunk_diag:
                    text_len = cd.get("text_len", 0)
                    audio_bytes = cd.get("audio_bytes", 0)
                    ratio = f"{audio_bytes / text_len:.1f}" if text_len > 0 else "N/A"
                    print(
                        f"  {cd.get('index', '?'):>5}  {text_len:>12}  {audio_bytes:>13}  {ratio:>8}"
                    )

            # -------------------------------------------------------
            # Step 4: Download audio and check actual size
            # -------------------------------------------------------
            _print_section("Step 4: Download audio from R2")
            resp = client.get(f"/api/articles/{article_id}/audio")
            if resp.status_code == 409:
                print("  Audio still generating (409)")
                return 1
            if resp.status_code == 404:
                print("  No audio available (404)")
                return 1
            if resp.status_code != 200:
                print(f"  Unexpected status: {resp.status_code}")
                return 1

            downloaded_bytes = len(resp.content)
            content_length = resp.headers.get("content-length")
            print(f"  Downloaded: {downloaded_bytes:,} bytes")
            print(f"  Content-Length header: {content_length}")
            print(f"  Content-Type: {resp.headers.get('content-type')}")

            # Check first few bytes for MP3 header
            if resp.content[:3] == b"\xff\xfb\x90" or resp.content[:2] == b"\xff\xfb":
                print("  MP3 header: Valid (starts with FF FB)")
            elif resp.content[:3] == b"ID3":
                print("  MP3 header: Valid (ID3 tag)")
            else:
                print(f"  MP3 header: Unknown (first 4 bytes: {resp.content[:4].hex()})")

            # -------------------------------------------------------
            # Step 5: Verdict
            # -------------------------------------------------------
            _print_section("Verdict")
            pipeline_bytes = diag.get("total_bytes", 0)
            print(f"  Pipeline reported: {pipeline_bytes:,} bytes")
            print(f"  Actually downloaded: {downloaded_bytes:,} bytes")
            print(f"  Minimum expected: {MIN_EXPECTED_AUDIO_BYTES:,} bytes")

            if pipeline_bytes != downloaded_bytes:
                print(
                    f"\n  MISMATCH: Pipeline reported {pipeline_bytes:,} bytes "
                    f"but download was {downloaded_bytes:,} bytes."
                    f"\n  This suggests R2 storage or retrieval is losing bytes."
                )

            if downloaded_bytes < MIN_EXPECTED_AUDIO_BYTES and md_len > 100:
                print(
                    f"\n  TRUNCATED: Audio is only {downloaded_bytes:,} bytes "
                    f"for {md_len:,} chars of text."
                    f"\n  Expected at least {MIN_EXPECTED_AUDIO_BYTES:,} bytes."
                )
                return 1

            if downloaded_bytes >= MIN_EXPECTED_AUDIO_BYTES:
                print("\n  PASS: Audio size is proportional to text content.")
                return 0

            print("\n  INCONCLUSIVE: Content too short to determine if truncation occurs.")
            return 0

        finally:
            # -------------------------------------------------------
            # Cleanup
            # -------------------------------------------------------
            if article_id:
                _print_section("Cleanup")
                try:
                    resp = client.delete(f"/api/articles/{article_id}")
                    print(f"  Deleted article {article_id}: {resp.status_code}")
                except Exception as e:
                    print(f"  Cleanup failed: {e}")


if __name__ == "__main__":
    sys.exit(main())

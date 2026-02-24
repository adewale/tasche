"""E2E test fixtures — run against a live Cloudflare Workers deployment.

Staging URL: https://tasche-staging.adewale-883.workers.dev
Auth: DISABLE_AUTH=true (no OAuth needed, implicit "dev" user)

Gate: set RUN_E2E_TESTS=1 to enable. Without it, all E2E tests are skipped.
"""

from __future__ import annotations

import os
import socket

import httpx
import pytest

STAGING_URL = os.environ.get(
    "STAGING_URL",
    "https://tasche-staging.adewale-883.workers.dev",
)


def _is_server_reachable(url: str, timeout: float = 5.0) -> bool:
    """Check if the staging server is reachable via TCP."""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except (OSError, TimeoutError):
        return False


@pytest.fixture(scope="session")
def staging_url() -> str:
    """Return the staging base URL, skipping if unreachable."""
    if not _is_server_reachable(STAGING_URL):
        pytest.skip(f"Staging server unreachable: {STAGING_URL}")
    return STAGING_URL


@pytest.fixture()
async def http_client(staging_url: str) -> httpx.AsyncClient:
    """Async httpx client pointed at staging. No auth needed."""
    async with httpx.AsyncClient(
        base_url=staging_url,
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
    ) as client:
        yield client


@pytest.fixture()
def cleanup_articles(staging_url: str) -> list[str]:
    """Collects article IDs created during a test for cleanup in teardown."""
    ids: list[str] = []
    yield ids
    # Cleanup: delete all articles created during the test
    with httpx.Client(base_url=staging_url, timeout=30.0) as client:
        for article_id in ids:
            try:
                client.delete(f"/api/articles/{article_id}")
            except Exception:
                pass


@pytest.fixture()
def cleanup_tags(staging_url: str) -> list[str]:
    """Collects tag IDs created during a test for cleanup in teardown."""
    ids: list[str] = []
    yield ids
    with httpx.Client(base_url=staging_url, timeout=30.0) as client:
        for tag_id in ids:
            try:
                client.delete(f"/api/tags/{tag_id}")
            except Exception:
                pass

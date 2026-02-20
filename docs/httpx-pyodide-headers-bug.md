# httpx Does Not Transmit HTTP Headers Inside Cloudflare Python Workers (Pyodide/workerd)

## Summary

When using [httpx](https://www.python-httpx.org/) to make outbound HTTP requests from a Cloudflare Python Worker (Pyodide running inside workerd), **request headers are silently dropped**. The Python-side `headers` dict looks correct, but the receiving server never sees the headers. This causes failures on any API that enforces headers like `User-Agent` or `Authorization`.

The fix is to bypass httpx entirely in the Workers runtime and use `js.fetch()` -- the native Workers Fetch API -- via Pyodide's FFI bridge. httpx works fine in CPython (local development, tests), so the solution is a dual-path helper function that detects the runtime and dispatches accordingly.

---

## The Symptom

GitHub OAuth login broke at the `/api/auth/callback` endpoint. The flow has two outbound HTTP calls:

1. **Token exchange** -- POST to `https://github.com/login/oauth/access_token`
2. **User info** -- GET to `https://api.github.com/user`

The token exchange POST succeeded (HTTP 200), but the user info GET returned **HTTP 403** with:

```
Request forbidden by administrative rules.
Please make sure your request has a User-Agent header.
```

The code in `src/auth/routes.py` explicitly set `User-Agent: tasche/1.0` in the headers dict:

```python
auth_headers = {
    "Authorization": f"Bearer {access_token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "tasche/1.0",
}
user_resp = await http_fetch(GITHUB_USER_URL, headers=auth_headers)
```

The headers were present in the Python dict. They were passed correctly to httpx. But GitHub never received them.

---

## The Investigation

### Step 1: Add debug logging

We added `print(json.dumps(...))` calls in `src/auth/routes.py` to log the exact headers dict being passed and the response status/body from each GitHub API call. The logs confirmed:

- The Python-side headers dict contained `User-Agent`, `Authorization`, `Accept`, and `X-GitHub-Api-Version`
- The token exchange POST returned 200 (success)
- The user info GET returned 403 with the "User-Agent header" error message

### Step 2: Rule out our code

We tried setting headers at both the httpx client level (`httpx.AsyncClient(headers=...)`) and per-request (`client.get(url, headers=...)`). Neither worked. The headers dict was always correct on the Python side.

### Step 3: Understand why the token exchange succeeded

The token exchange POST to `github.com/login/oauth/access_token` succeeded because that endpoint does not enforce `User-Agent`. It accepts requests with or without it. The user info GET to `api.github.com/user` strictly requires `User-Agent` and returns 403 without it.

This is why the bug was intermittent in appearance -- one request "worked" and the next "failed" with the same headers.

### Step 4: Understand why tests never caught this

The unit tests in `tests/unit/test_auth.py` mock `http_fetch` entirely:

```python
with patch("src.auth.routes.http_fetch", mock_fetch):
    resp = client.get(
        f"/api/auth/callback?code=test_code&state={state}",
        follow_redirects=False,
    )
```

The mock returns canned responses. It never makes a real HTTP request. It never exercises httpx's transport layer. It runs in CPython, not Pyodide. The bug is invisible at this level.

### Step 5: Understand why curl could not reproduce this

`curl` runs as a native process, not inside Pyodide/WebAssembly. When you `curl -H "User-Agent: tasche/1.0" https://api.github.com/user`, it works because curl's HTTP stack transmits headers normally. The bug only manifests inside the workerd V8 isolate where Pyodide's networking stack intercepts httpx's transport.

---

## Root Cause

httpx running inside Pyodide (WebAssembly) within the workerd runtime does not reliably transmit request headers on outbound HTTP calls.

The issue is in how httpx's transport layer (AsyncHTTPTransport, backed by httpcore) interacts with the Pyodide/workerd networking stack. When httpx constructs an HTTP request and hands it off to the underlying transport, the headers are lost somewhere in the bridge between Python's socket-like abstractions and workerd's actual fetch implementation.

This is **not a bug in our code** -- it is a fundamental incompatibility between httpx's transport layer and the Pyodide/workerd runtime environment.

The [planet_cf reference project](https://github.com/adewale/planet_cf) for Cloudflare Python Workers avoids this entirely by using `js.fetch()` (the native Workers Fetch API via Pyodide FFI) for all production outbound HTTP.

---

## The Fix

### `http_fetch()` in `src/wrappers.py`

We added a cross-runtime HTTP helper that lives alongside the other FFI boundary functions (`d1_first`, `d1_rows`, `_to_js_value`, `_to_py_safe`):

```python
@dataclass
class HttpResponse:
    """Minimal HTTP response wrapper for cross-runtime compatibility."""

    status_code: int
    _body: str

    def json(self) -> Any:
        import json as _json
        return _json.loads(self._body)

    @property
    def text(self) -> str:
        return self._body


async def http_fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: str | None = None,
    form_data: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> HttpResponse:
    """Perform an HTTP request that works in both Pyodide and CPython.

    In the Workers runtime (Pyodide), uses the native JS fetch() API via
    the FFI.  httpx does not reliably transmit request headers when running
    inside workerd, which causes 403s from APIs that require User-Agent.

    In CPython (tests), falls back to httpx.
    """
    all_headers = dict(headers or {})

    if form_data:
        from urllib.parse import urlencode
        body = urlencode(form_data)
        all_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    if HAS_PYODIDE:
        opts: dict[str, Any] = {"method": method, "headers": all_headers}
        if body is not None:
            opts["body"] = body
        js_opts = _to_js_value(opts)
        response = await js.fetch(url, js_opts)
        response_text = await response.text()
        return HttpResponse(status_code=response.status, _body=response_text)
    else:
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            resp = await client.request(
                method, url, headers=all_headers, content=body
            )
            return HttpResponse(status_code=resp.status_code, _body=resp.text)
```

Key design decisions:

1. **`HAS_PYODIDE` guard** -- The same `try/except ImportError` pattern used throughout `src/wrappers.py`. When `js` is importable, we are in Pyodide.

2. **`js.fetch()` in Pyodide** -- Calls the native Workers Fetch API directly. The headers dict is converted to a JS object via `_to_js_value()` (which uses `Object.fromEntries` to produce a plain JS object, not a `Map`). This path reliably transmits all headers.

3. **httpx fallback in CPython** -- For tests and local development where Pyodide is not available. httpx works correctly in CPython.

4. **Unified `HttpResponse`** -- A minimal dataclass that normalizes the response interface across both runtimes. Supports `.status_code`, `.text`, and `.json()`.

5. **`form_data` parameter** -- Handles URL-encoded form bodies (used by the OAuth token exchange) without requiring the caller to manually encode.

### Usage in `src/auth/routes.py`

The auth routes import and call `http_fetch` instead of using httpx directly:

```python
from wrappers import SafeEnv, d1_first, http_fetch

# Token exchange
token_resp = await http_fetch(
    GITHUB_TOKEN_URL,
    method="POST",
    form_data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
    },
    headers={
        "Accept": "application/json",
        "User-Agent": "tasche/1.0",
    },
)

# User info
auth_headers = {
    "Authorization": f"Bearer {access_token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "tasche/1.0",
}
user_resp = await http_fetch(GITHUB_USER_URL, headers=auth_headers)
```

---

## Why This Bug Is Invisible to Standard Testing

### Unit tests mock the HTTP layer

Tests in `tests/unit/test_auth.py` patch `http_fetch` with a mock that routes by URL pattern and returns canned `HttpResponse` objects:

```python
def _mock_http_fetch(token_data, user_data, ...):
    async def _fetch(url, *, method="GET", headers=None, body=None, ...):
        if "access_token" in url:
            return token_resp
        if "api.github.com/user" in url:
            return user_resp
        ...
    return AsyncMock(side_effect=_fetch)
```

This tests the control flow (correct status handling, error cases, cookie attributes) but never exercises the actual HTTP transport.

### Unit tests run in CPython

Even if you remove the mocks, httpx works correctly in CPython. The bug only manifests in Pyodide inside workerd.

### curl bypasses the runtime

`curl` makes direct OS-level HTTP requests. It has no involvement with Pyodide, WebAssembly, or workerd's networking stack.

### The only reliable test

The only way to catch this bug is to test in the actual Workers runtime:

1. Run `uv run pywrangler dev` to start the local workerd runtime
2. Open a real browser (not curl)
3. Click through the full OAuth flow: login -> GitHub authorize -> callback -> redirect
4. Check the Workers logs for the 403 from `api.github.com`

---

## Files Still Using httpx Directly

The following files in the codebase still use `httpx.AsyncClient` directly for outbound HTTP. These run inside the queue consumer (Pyodide runtime) and may have **latent bugs** if any of their target APIs enforce specific request headers.

### `src/articles/processing.py`

Uses httpx for fetching article pages and delegates to `browser_rendering.py` and `images.py`:

```python
async with httpx.AsyncClient(follow_redirects=True) as client:
    html, final_url = await _fetch_page(client, original_url)
    # ...
    html = await scrape(client, final_url, account_id, api_token)
    # ...
    images = await download_images(client, clean_html)
```

The `_fetch_page` function sets a `User-Agent` header:

```python
resp = await client.get(
    url,
    timeout=30.0,
    headers={
        "User-Agent": "Mozilla/5.0 (compatible; Tasche/1.0; +https://github.com/tasche)",
    },
)
```

If httpx drops this header in Pyodide, some websites may block the request or return different content.

### `src/articles/browser_rendering.py`

Uses httpx to call the Cloudflare Browser Rendering REST API with an `Authorization` header:

```python
headers = {
    "Authorization": f"Bearer {api_token}",
    "Content-Type": "application/json",
}
resp = await client.post(endpoint, json=payload, headers=headers, timeout=30.0)
```

If the `Authorization` header is dropped, the Cloudflare API will reject the request. However, this may not have surfaced yet if Cloudflare's internal routing recognizes the request origin.

### `src/articles/images.py`

Uses httpx to download images referenced in article HTML:

```python
resp = await client.get(url, timeout=15.0, follow_redirects=True)
```

No custom headers are set on image downloads, so this is less likely to trigger the bug. But if headers from the `AsyncClient` constructor were relied upon, they would be lost.

### `src/articles/health.py`

Uses httpx for HEAD requests to check if original article URLs are still alive:

```python
resp = await client.head(
    url,
    headers={"User-Agent": _USER_AGENT},
)
```

Some websites block requests without a `User-Agent`, which could cause false `domain_dead` or `unknown` status classifications.

---

## Broader Implications

### Rule: Use `js.fetch()` for all outbound HTTP in Python Workers

Any outbound HTTP request from a Cloudflare Python Worker that relies on custom headers being transmitted should use `js.fetch()` via the Pyodide FFI, not httpx.

This means:

1. **Auth flows** -- Already fixed via `http_fetch()` in `src/wrappers.py`
2. **API calls with tokens** -- Browser Rendering, any third-party APIs
3. **Web scraping with User-Agent** -- Article fetching, health checks
4. **Image downloads** -- Less critical but still affected if headers matter

### The `http_fetch()` helper may need extension

The current `http_fetch()` returns text responses only (via `response.text()`). The article processing pipeline needs:

- **Binary responses** (images, screenshots) -- would need a `response.arrayBuffer()` path
- **Redirect following with final URL capture** -- `js.fetch()` follows redirects by default but the final URL is available via `response.url`
- **Streaming large responses** -- for content-length validation before reading the body

Extending `http_fetch()` or creating specialized variants for these cases would fully eliminate httpx from the production runtime.

### httpx is still fine for tests

The CPython fallback in `http_fetch()` uses httpx, and that is correct. httpx works perfectly in CPython. The issue is exclusively in the Pyodide/workerd runtime.

---

## Timeline

1. OAuth login flow implemented using httpx for all GitHub API calls
2. Token exchange (POST to `github.com`) worked -- this endpoint does not enforce `User-Agent`
3. User info (GET to `api.github.com`) returned 403 -- this endpoint enforces `User-Agent`
4. Debug logging confirmed headers were correct on the Python side
5. Discovered that httpx silently drops headers in Pyodide/workerd
6. Implemented `http_fetch()` using `js.fetch()` for the Workers runtime
7. OAuth login flow now works end-to-end in production

---

## Key Takeaway

If you are building on Cloudflare Python Workers and your outbound HTTP requests are failing despite having correct headers in your Python code, **httpx is not transmitting them**. Use `js.fetch()` via `from js import fetch` (or via a helper like `http_fetch()`) for all outbound HTTP that needs headers to arrive at the destination.

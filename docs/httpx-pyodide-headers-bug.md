# httpx Drops the `User-Agent` Header in Cloudflare Python Workers

> **Note:** Line numbers reference httpx 0.28.1 as vendored at the time of this investigation.

## Summary

When deployed via `pywrangler`, httpx is bundled with a modified transport (`jsfetch.py`) that replaces httpcore with a `js.fetch()`-based implementation. This transport intentionally filters out the `User-Agent` header to prevent CORS preflight failures in browser-based Pyodide ([pyodide-http#22](https://github.com/koenvo/pyodide-http/issues/22)). Cloudflare Workers are not browsers and have no CORS restrictions on outbound fetch, but the filter still applies.

Other headers (`Authorization`, `Accept`, custom headers) are transmitted normally. Only `User-Agent` is stripped.

The exact code:

```python
# python_modules/httpx/_transports/jsfetch.py line 57
HEADERS_TO_IGNORE = ("user-agent",)

# python_modules/httpx/_transports/jsfetch.py line 157 (_do_fetch)
headers = {k: v for k, v in request.headers.items() if k not in HEADERS_TO_IGNORE}
```

The transport is activated when `sys.platform == "emscripten"` (always true in Pyodide):

```python
# python_modules/httpx/_transports/__init__.py line 8
if sys.platform == "emscripten":
    from .jsfetch import *
    AsyncHTTPTransport = AsyncJavascriptFetchTransport
```

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

The code explicitly set `User-Agent: tasche/1.0`:

```python
auth_headers = {
    "Authorization": f"Bearer {access_token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "tasche/1.0",
}
user_resp = await http_fetch(GITHUB_USER_URL, headers=auth_headers)
```

The `Authorization`, `Accept`, and `X-GitHub-Api-Version` headers all reached GitHub (the token worked; the response format was JSON). Only `User-Agent` was missing, which is exactly what `HEADERS_TO_IGNORE` strips.

---

## Why It Looked Like All Headers Were Dropped

The token exchange POST to `github.com/login/oauth/access_token` does not enforce `User-Agent` — it accepts requests with or without it. The user info GET to `api.github.com/user` strictly requires it and returns 403 without it.

Because the first request "worked" and the second "failed" with the same headers dict, the initial diagnosis was that httpx was dropping all headers. In reality, the first endpoint was lenient and the second was strict — about the one header that was actually being stripped.

---

## The Fix

The fix in `src/wrappers.py` is to bypass the bundled httpx transport entirely and call `js.fetch()` directly in Pyodide. This avoids the `HEADERS_TO_IGNORE` filter:

```python
if HAS_PYODIDE:
    opts = _to_js_value({"method": method, "headers": all_headers})
    response = await js.fetch(url, opts)
    ...
else:
    # CPython fallback — httpx works correctly here
    import httpx
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        resp = await client.request(method, url, headers=all_headers, content=body)
        ...
```

A simpler alternative: patch `python_modules/httpx/_transports/jsfetch.py` line 57 to `HEADERS_TO_IGNORE = ()`. The CORS preflight concern does not apply in Workers.

---

## Affected Code

The `http_fetch()` wrapper in `src/wrappers.py` bypasses httpx in Pyodide for all outbound HTTP. This covers:

- **Auth flows** (`src/auth/routes.py`) -- GitHub API calls that require `User-Agent`
- **Article fetching** (`src/articles/processing.py`) -- `_fetch_page()` sets a `User-Agent`
- **Browser Rendering** (`src/articles/browser_rendering.py`) -- `Authorization` header (not affected by this bug, but also migrated)
- **Health checks** (`src/articles/health.py`) -- HEAD requests with `User-Agent` that some servers enforce
- **Image downloads** (`src/articles/images.py`) -- no custom headers, least affected

---

## Why Standard Testing Misses This

**Unit tests mock `http_fetch`:** They return canned responses and never exercise the transport.

**Unit tests run in CPython:** The jsfetch transport only activates when `sys.platform == "emscripten"`. In CPython, httpx uses httpcore which transmits all headers correctly.

**curl bypasses the runtime:** curl makes OS-level HTTP requests, not through Pyodide.

The only reliable test is to deploy to workerd (`uv run pywrangler dev`) and make real outbound requests.

---

## Reproduction

A minimal reproduction is at [`python-workers-issues/3-httpx-headers`](https://github.com/anthropics/python-workers-issues/tree/main/3-httpx-headers). It sends `{"User-Agent": "repro/1.0", "X-Custom": "preserved"}` to httpbin.org/headers via both httpx and `js.fetch()`, showing that httpx drops `User-Agent` while preserving `X-Custom`.

---

## Timeline

1. OAuth login flow implemented using httpx for all GitHub API calls
2. Token exchange (POST to `github.com`) worked -- does not enforce `User-Agent`
3. User info (GET to `api.github.com`) returned 403 -- enforces `User-Agent`
4. Debug logging confirmed headers were correct on the Python side
5. Initial diagnosis: httpx drops all headers (incorrect -- only `User-Agent`)
6. Root cause found: `HEADERS_TO_IGNORE = ("user-agent",)` in bundled `jsfetch.py` transport
7. Implemented `http_fetch()` using `js.fetch()` directly, bypassing the filter
8. OAuth login flow now works end-to-end in production

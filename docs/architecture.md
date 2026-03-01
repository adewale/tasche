# Tasche Architecture Deep-Dive

A comprehensive reference covering every significant piece of infrastructure. Each section explains the tool/model used, how we call it, data flow, limits, error handling, and storage.

---

## 1. Runtime Environment

Tasche runs Python on **Pyodide** (CPython compiled to WebAssembly) inside Cloudflare's **V8 isolates**. This is not a container or a VM — it's a JavaScript runtime that loads a Python interpreter as a Wasm module.

**Constraints this imposes:**

- **All handlers must be `async def`.** Sync handlers trigger `RuntimeError: can't start new thread` because V8 isolates are single-threaded with no pthreads. Even seemingly harmless sync code can fail if it blocks the event loop.
- **No C extensions.** Libraries must be pure Python or have Pyodide-compatible builds. This rules out `lxml`, `readability-lxml`, `requests`, `playwright`, and anything using `ctypes` or native compilation.
- **No threading or multiprocessing.** The `threading`, `multiprocessing`, and `concurrent.futures` modules are non-functional.
- **No `eval()` from JS.** `js.eval()` throws `EvalError: Code generation from strings disallowed` in Workers. This breaks `python-readability` which uses `js.eval()` to load Mozilla's Readability JS. The `allow_eval_during_startup` flag doesn't help because eager import exceeds startup time limits.
- **No module-level PRNG calls.** Calling `random` or `secrets` at import time breaks the Wasm snapshot used for fast cold starts.
- **Isolate reuse.** V8 isolates are reused across requests, so global state persists between invocations. Never store request-scoped data in module-level variables — pass state via function args or `request.state`.

**Framework:** FastAPI with Cloudflare's ASGI adapter. The adapter bridges JavaScript's fetch event to Python's ASGI protocol: `asgi.fetch(app, request.js_object, env)`.

**Package management:** Packages are declared in `pyproject.toml` (not `requirements.txt`) and deployed via `pywrangler` (not regular `wrangler`). Regular wrangler cannot deploy Python Workers with packages.

---

## 2. Entry Point & Routing

**File:** `src/entry.py` — `class Default(WorkerEntrypoint)`

### Three Handlers

| Handler | Trigger | Purpose |
|---------|---------|---------|
| `fetch(request)` | Every HTTP request | Route to FastAPI or ASSETS |
| `queue(batch, env, ctx)` | Queued messages from ARTICLE_QUEUE | Async article processing and TTS |
| `scheduled(event)` | Cron trigger | URL health checks |

### Fetch Handler: API vs ASSETS Dispatch

```
Request → path starts with /api/ ?
            ├─ YES → asgi.fetch(app, request.js_object, SafeEnv(self.env))
            └─ NO  → env.ASSETS.fetch(request)
                       └─ 404? → fetch /index.html (SPA fallback)
```

**Why Worker-first routing:** Cloudflare's `not_found_handling: "single-page-application"` intercepts browser navigation requests (`Sec-Fetch-Mode: navigate`) to API paths and serves `index.html` instead of forwarding to the Worker. `curl` doesn't send this header, so it can't catch this bug — you must test with a real browser. The fix is to always use the ASSETS binding with manual SPA fallback.

### Queue Handler

The queue handler signature must be `queue(self, batch, env=None, ctx=None)`. Workers runtime passes 3 args (batch, env, ctx), not just batch. Using only `(self, batch)` causes `TypeError: takes 2 positional arguments but 4 were given`. The `env` parameter is used as a fallback since `self.env` may not be populated in queue context.

Messages are dispatched by the `type` field in the JSON body:

```python
QUEUE_HANDLERS = {
    "article_processing": _handle_article_processing,
    "tts_generation": _handle_tts_generation,
}
```

Each message is processed individually within a batch. On success: `message.ack()`. On transient error (network, 5xx): `message.retry()`. On permanent error (4xx, validation): `message.ack()` + update status to `failed`.

### Scheduled Handler

Runs periodically via cron. Queries D1 for up to 10 articles where `original_status = 'unknown'` or `last_checked_at` is NULL or older than 30 days. For each, performs a HEAD request to classify the original URL's status, then updates D1.

---

## 3. The FFI Boundary

**File:** `src/wrappers.py`

All JavaScript↔Python conversion is centralized in this single module. Application code never touches `JsProxy` directly. The module detects Pyodide at import time (`HAS_PYODIDE` flag) and falls back to pass-through behavior for CPython unit tests.

### SafeEnv

Wraps all Cloudflare bindings at construction time. Idempotent — double-wrapping returns the existing wrapper.

```python
SafeEnv(env)
  ├─ .DB         → SafeD1
  ├─ .CONTENT    → SafeR2
  ├─ .SESSIONS   → SafeKV
  ├─ .ARTICLE_QUEUE → SafeQueue
  ├─ .AI         → SafeAI
  ├─ .READABILITY → SafeReadability
  └─ .get(key, default) → env var with undefined detection
```

### Safe Wrappers

**SafeD1 / SafeD1Statement:**
- `prepare(sql)` → returns SafeD1Statement
- `bind(*args)` → converts every `None` to JS `null` via `d1_null()` (D1 rejects `undefined`)
- `first()` → calls `d1_first()` which unwraps the result wrapper `{results, success, meta}` that Pyodide sometimes returns instead of a plain row
- `all()` → calls `d1_rows()` which extracts the `.results` property
- `run()` → pass-through for write operations

**SafeR2:**
- `put(key, data)` → auto-converts `bytes`/`bytearray`/`memoryview` to JS `Uint8Array` via `to_js_bytes()`
- `get(key)` → returns R2 object or `None` (checks for `js.undefined`)
- `delete(key)`, `list(**kwargs)` → standard operations with JsProxy conversion

**SafeKV:**
- Thin passthrough with null/undefined detection on `get()`

**SafeQueue:**
- `send(message)` → converts Python dict to JS Object via `_to_js_value()` with `dict_converter=Object.fromEntries` (without this, dicts become JS `Map` objects)

**SafeAI:**
- `run(model, inputs)` → converts inputs dict to JS Object, checks for null/undefined result

**SafeReadability:**
- `parse(html, url)` → calls the Readability JS Worker via Service Binding, converts JsProxy result to Python dict via `_to_py_safe()`

### Four Classes of FFI Bugs

1. **JsNull is not Python None, not a JsProxy.** D1's `.first()` returns JsNull when no rows match. It bypasses `isinstance(JsProxy)` checks. Detection: `type(value).__name__ == "JsNull"`.

2. **Python None becomes JS undefined.** D1 requires JS null for SQL NULL. The `d1_null()` helper calls `get_js_null()` which returns `js.JSON.parse("null")` — called as a function (not a constant) to avoid executing JS during Wasm snapshot.

3. **`.to_py()` leaves JsNull inside converted dicts.** The `_to_py_safe()` function must recurse into plain dicts and lists (depth limit: 20) to scrub remaining JsNull values. Without this, FastAPI/Pydantic can't serialize the response.

4. **`to_js(bytes)` creates a Wasm memory VIEW.** In theory, `memory.grow()` could detach the backing ArrayBuffer during async ops. In practice, Python yields to JS during `await`, preventing `memory.grow()`. Empirical staging tests (2026-02-25) showed zero corruption without `.slice()`.

### Key Conversion Functions

| Function | Direction | Purpose |
|----------|-----------|---------|
| `_to_py_safe(value)` | JS → Python | Recursive JsProxy→dict/list, scrubs JsNull |
| `_to_js_value(value)` | Python → JS | Dict→Object via `Object.fromEntries` |
| `d1_first(results)` | JS → Python | Unwrap `.first()` result wrapper |
| `d1_rows(results)` | JS → Python | Extract `.results` from `.all()` |
| `to_js_bytes(data)` | Python → JS | bytes → Uint8Array (Wasm view) |
| `to_py_bytes(value)` | JS → Python | ArrayBuffer/Uint8Array → bytes |
| `consume_readable_stream(value)` | JS → Python | ReadableStream → bytes via `getReader()` |

### ReadableStream Consumption

`consume_readable_stream()` prefers `getReader()` over `.arrayBuffer()`. In Pyodide, `await stream.arrayBuffer()` may only capture the first buffered chunk, silently truncating multi-chunk responses (discovered with Workers AI TTS audio). The `getReader()` path reads all chunks sequentially via `reader.read()` until `done=True`, then calls `reader.releaseLock()`.

**Never use `StreamingResponse` with async generators.** The Cloudflare Workers ASGI adapter only consumes the first yielded chunk, silently truncating the response. Always read the full body and return a single `Response`.

### Timing Instrumentation

Every Safe* method records elapsed time to the current WideEvent via `_record(method_name, t0)`. This is called in `finally` blocks so timing is captured even on exceptions. If no WideEvent is active (e.g., in unit tests), the recording is a no-op.

---

## 4. Authentication

**Files:** `src/auth/routes.py`, `src/auth/session.py`, `src/auth/dependencies.py`

### GitHub OAuth 3-Leg Flow

1. **`GET /api/auth/login`** — Generate 32-byte CSRF state token via `secrets.token_urlsafe(32)`, store in KV with 10-minute TTL, redirect to GitHub's authorize URL with `scope=user:email`.

2. **`GET /api/auth/callback`** — Validate CSRF state (KV lookup, delete immediately to prevent replay), exchange authorization code for access token via POST to GitHub's token endpoint, fetch user profile from `/user` API, fall back to `/user/emails` if no public email (find first primary+verified email).

3. **Email whitelist enforcement:** The `ALLOWED_EMAILS` env var (comma-separated, case-insensitive) restricts who can log in. Returns 403 if the authenticated email isn't in the list.

4. **User upsert:** Check D1 for existing user by `github_id`. If found, update profile fields. If new, INSERT with `generate_id()` (16-byte URL-safe token).

5. **Session creation:** `create_session()` generates a 32-byte URL-safe token, stores JSON-serialized user data in KV at `session:{session_id}` with 7-day TTL (604800 seconds).

6. **Cookie configuration:**
   - Name: `tasche_session`
   - `httponly=True` (no JS access)
   - `secure` derived from `SITE_URL` (https → true, http → false for local dev)
   - `samesite=lax`
   - `max_age=604800` (7 days)

### Session Refresh

`refresh_session()` extends the KV TTL but is throttled to once per hour (`_REFRESH_INTERVAL = 3600`). This reduces KV write costs — a user making 100 requests/hour only triggers 1 KV write instead of 100.

### DISABLE_AUTH Dev Mode

When `DISABLE_AUTH=true` in env vars, the `get_current_user()` dependency bypasses OAuth entirely. It calls `_get_or_create_dev_user()` which does an idempotent `INSERT OR IGNORE` of a user with `github_id=0`, email `dev@localhost`, username `dev`. The result is cached in a module-level variable for subsequent calls.

### Auth Dependency

`get_current_user()` is a FastAPI dependency injected per-route:

1. Check for DISABLE_AUTH bypass
2. Read `tasche_session` cookie (401 if missing)
3. Look up session in KV (401 if expired)
4. Validate email against ALLOWED_EMAILS whitelist (401 if not authorized)
5. Refresh session TTL (throttled)
6. Store `user_id` on `request.state` for observability middleware

---

## 5. Article Processing Pipeline

**File:** `src/articles/processing.py` — `process_article(article_id, original_url, env)`

The full pipeline from URL save to content ready, executed by the queue consumer.

### Pipeline Steps

| Step | Action | Binding | Error Handling |
|------|--------|---------|----------------|
| Mark processing | `UPDATE status='processing'` | D1 | — |
| Check pre-supplied content | Check R2 for `raw.html` (bookmarklet) | R2 | Skip fetch if found |
| Fetch page | `_fetch_page(url)` with 30s timeout, 10 MB limit, content-type validation | HTTP | HttpError, TimeoutError |
| SSRF check | Post-redirect SSRF check on final URL hostname | — | ValueError if private |
| JS-heavy detection | `_is_js_heavy(html)` → `scrape()` via Browser Rendering REST API | HTTP | Non-fatal catch |
| URL extraction | `extract_canonical_url(html)` + `extract_domain(final_url)` | — | — |
| Thumbnail | Screenshot (1200x630) via Browser Rendering | HTTP, R2 | Non-fatal catch |
| Full screenshot | Screenshot (1200x800, full_page=True) via Browser Rendering | HTTP, R2 | Non-fatal catch |
| Content extraction | Readability Service Binding, then BS4 fallback | Service Binding | Fallback on any error |
| Image processing | `download_images()` + `store_images()` — WebP conversion, SSRF-checked | HTTP, R2 | Per-image catch |
| Image path rewrite | `rewrite_image_paths()` to `/api/articles/{id}/images/{hash}.ext` | — | — |
| Markdown conversion | `html_to_markdown()` via markdownify, `count_words()`, `calculate_reading_time()` | — | — |
| Store content | Store content.html + metadata.json to R2 | R2 | — |
| Update D1 | UPDATE D1 with extracted columns, `status='ready'` | D1 | — |
| FTS5 indexing | Automatic via D1 triggers | D1 | — |
| Auto-tagging | `apply_auto_tags()` — evaluate tag rules | D1 | Non-fatal catch |
| TTS enqueue | If `audio_status='pending'`, enqueue TTS generation | Queue | Non-fatal catch |

### Error Classification

```
ConnectionError, TimeoutError     → transient → message.retry()
HttpError (status >= 500)         → transient → message.retry()
HttpError (status < 500)          → permanent → status='failed', message.ack()
All other exceptions              → permanent → status='failed', message.ack()
```

### JS-Heavy Heuristic

`_is_js_heavy(html)`: Parse HTML with BeautifulSoup, remove `<script>`, `<style>`, `<noscript>` tags, extract plain text. If `len(text) < 500` characters, the page likely relies on JavaScript rendering and needs Browser Rendering's `scrape()` endpoint.

---

## 6. Content Extraction

**File:** `src/articles/extraction.py`

### Primary: Readability Service Binding

A separate JS Worker running Mozilla's Readability library, accessed via Cloudflare Service Binding. Called as `env.READABILITY.parse(html, url)` → returns `{title, html, excerpt, byline}`.

### Fallback: BeautifulSoup Heuristic

Used when the Readability binding fails or is unavailable (e.g., `eval()` blocked in Pyodide prevents direct usage of the JS library).

**Algorithm:**

1. **Remove junk elements:** Tags in `_JUNK_TAGS` (script, style, nav, footer, header, aside, form, noscript, iframe, svg, button, input, select, textarea) and elements whose class/id/role matches `_JUNK_PATTERNS` (nav, menu, sidebar, footer, comment, widget, advert, promo, social, related, share, signup, subscribe, cookie, banner, popup, modal).

2. **Find content container:** Try `<article>` and `<main>` tags first (pick the one with the most text). If none found or text < 100 chars, score all `<div>` and `<section>` tags: `score = len(text) - 10 * depth`. Penalizing depth prevents wrapper divs from winning. Last resort: `<body>`.

3. **Extract title:** Prefer `<h1>` inside the content container, fall back to `<title>` tag.

4. **Build excerpt:** Plain text from content, truncated at word boundary to 300 characters, appended with `...` if truncated.

### Markdown Conversion

`html_to_markdown(html)` uses `markdownify` with ATX heading style (`# ## ###`). Before conversion, layout tables are unwrapped: tables with a single cell or 50%+ empty cells are replaced with their content. Code language is extracted from `<pre><code class="language-python">` patterns. Excessive whitespace is collapsed to max 2 newlines.

### Reading Metrics

- Word count: `len(text.split())`
- Reading time: `max(1, ceil(word_count / 200))` minutes (200 WPM, minimum 1 minute)

---

## 7. Browser Rendering

**File:** `src/articles/browser_rendering.py`

Uses the Cloudflare Browser Rendering **REST API** (not the Puppeteer binding) for screenshots and JS-heavy page scraping.

### API Endpoint

```
https://api.cloudflare.com/client/v4/accounts/{account_id}/browser-rendering
```

Authenticated via `CF_API_TOKEN` in the `Authorization: Bearer` header. Requires `CF_ACCOUNT_ID` and `CF_API_TOKEN` to be set in env vars.

### Screenshot

```python
async def screenshot(url, account_id, api_token, *,
                     viewport_width=1200, viewport_height=630,
                     full_page=False) -> bytes
```

POST to `/screenshot` with JSON body. Two uses in the processing pipeline:
- **Thumbnail:** 1200x630 viewport, above-the-fold capture → stored as `thumbnail.webp`
- **Full-page archival:** 1200x800 viewport, `full_page=True` → stored as `original.webp`

### Scrape

```python
async def scrape(url, account_id, api_token) -> str
```

POST to `/scrape`. Returns the fully-rendered DOM HTML after JavaScript execution. Triggered when `_is_js_heavy(html)` detects less than 500 characters of body text in the initial HTTP fetch.

### Error Handling

Both functions raise `BrowserRenderingError` on non-200 status. In the processing pipeline, all Browser Rendering calls are wrapped in non-fatal try/except — failures fall back to the original HTTP-fetched HTML or simply skip screenshots.

---

## 8. Text-to-Speech

**File:** `src/tts/processing.py`

### Model

Configurable via the `TTS_MODEL` env var (default: `melotts`). Supported values: `melotts` (`@cf/myshell-ai/melotts-en-default`), `aura-2-en` (`@cf/deepgram/aura-2-en`), `aura-2-es`, `aura-1`. Called through the `AI` binding: `env.AI.run(model_id, {"text": chunk})`.

### Pipeline

1. **Idempotency:** Skip if `audio_status` is already `'ready'`.
2. **Status update:** Set `audio_status = 'generating'` in D1.
3. **Text preparation:** Fetch `markdown_content` from D1, strip markdown syntax (headings, bold/italic, links, images, code blocks, blockquotes, list markers, HTML tags).
4. **Truncation:** Cap at 100,000 characters (`_MAX_TTS_TEXT_LENGTH`).
5. **Sentence splitting:** Regex `(?<=[.!?])\s+` — splits after sentence-ending punctuation followed by whitespace.
6. **Chunking:** Greedy bin-packing of sentences into groups of ≤1,900 characters (`_MAX_CHARS_PER_CHUNK`). This provides headroom below the 2,000-character API limit. If a single sentence exceeds the limit, it becomes its own chunk.
7. **Audio generation:** For each chunk, call Workers AI and consume the response via `consume_readable_stream()` (must use `getReader()`, not `.arrayBuffer()`, to avoid silent truncation).
8. **Concatenation:** All MP3 chunks are concatenated as raw bytes (MP3 frames are self-describing, so simple concatenation works).
9. **Storage:** Store concatenated audio to R2 at `articles/{id}/audio.mp3`.
10. **Verification:** Compare expected size with actual R2 object size. Log mismatch but continue.
11. **Timing map:** Generate sentence-level timing at 150 WPM. Each entry: `{text, start, end, word_count}`. Stored as `articles/{id}/audio-timing.json` in R2.
12. **D1 update:** Set `audio_key`, `audio_duration_seconds`, `audio_status = 'ready'`.

### Error Handling

- `ValueError` (no markdown content, empty text after stripping): Set `audio_status = 'failed'` (permanent — no point retrying with no content).
- All other exceptions: Propagate to queue for automatic retry.

### Audio Serving

`GET /api/articles/{id}/audio` reads the full R2 body via `getReader()` and returns a single `Response` (not `StreamingResponse`). Cache-Control: `public, max-age=86400, immutable`.

`GET /api/articles/{id}/audio-timing` returns the sentence timing JSON. Used by the frontend to highlight sentences during playback by wrapping text in `<span data-sentence-idx>` elements.

---

## 9. Search

**File:** `src/search/routes.py`

### FTS5 Virtual Table

D1 maintains an `articles_fts` FTS5 virtual table indexing `title`, `excerpt`, and `markdown_content`. Triggers automatically sync the FTS5 table on INSERT/UPDATE/DELETE of the `articles` table.

### Query Sanitization

`_sanitize_fts5_query(query)` prevents FTS5 operator injection:

1. Split query on whitespace into tokens.
2. Strip all FTS5 special characters: `"*+-^():{}[]|\`
3. Wrap each token in double quotes: `hello* world` → `"hello" "world"`.
4. FTS5 operators (OR, AND, NOT) become quoted literals, not operators.

### SQL Pattern

```sql
SELECT articles.* FROM articles
INNER JOIN articles_fts ON articles.rowid = articles_fts.rowid
WHERE articles_fts MATCH ? AND articles.user_id = ?
ORDER BY articles_fts.rank
LIMIT ? OFFSET ?
```

**Key details:**
- Always use `INNER JOIN` (not subquery) for correct ranking.
- Column names must be prefixed with `articles.` to avoid ambiguity with the FTS5 virtual table.
- `articles_fts.rank` is negative; closer to 0 = more relevant.
- User filtering via `articles.user_id = ?` in the WHERE clause.

---

## 10. Tags & Auto-Tagging

**Files:** `src/tags/routes.py`, `src/tags/rules.py`

### Tag CRUD

Tags are per-user (UNIQUE constraint on `(user_id, name)`). Two separate routers:

- **`/api/tags`** — Tag CRUD (create, list with article counts, rename, delete with cascade)
- **`/api/articles/{id}/tags`** — Article-tag associations (add, remove, list)

Tag names: max 100 characters, validated for uniqueness per user.

### Auto-Tagging Rules

Rules are evaluated during article processing (auto-tagging step, non-fatal). Each rule has a `tag_id`, `match_type`, and `pattern`.

| Match Type | Evaluation | Example |
|-----------|------------|---------|
| `domain` | Exact match or `fnmatch` glob (case-insensitive) against article domain | `github.com`, `*.substack.com` |
| `title_contains` | Substring match (case-insensitive) against article title | `AI`, `Rust` |
| `url_contains` | Substring match (case-insensitive) against original/final URL | `arxiv.org`, `/blog/` |

**Algorithm in `apply_auto_tags()`:**
1. Fetch all `tag_rules` from D1 for the user.
2. Evaluate each rule against the article's domain, title, and URL.
3. Collect matching `tag_id` values into a set (deduplicate).
4. For each match: `INSERT OR IGNORE INTO article_tags`.
5. Return count of applied tags.

Pattern max length: 500 characters. Rule uniqueness: `(tag_id, match_type, pattern)`.

---

## 11. Queue System

**Binding:** `ARTICLE_QUEUE` (Cloudflare Queues)

### Message Types

| Type | Payload | Handler | Target |
|------|---------|---------|--------|
| `article_processing` | `{article_id, url, user_id}` | `_handle_article_processing` | `process_article()` |
| `tts_generation` | `{article_id, user_id}` | `_handle_tts_generation` | `process_tts()` |

### Enqueue-or-Fail Pattern

`_enqueue_or_fail(env, db, message, article_id, status_field, rollback_value)` — sends a message to the queue. On failure, updates the article's status field to the rollback value (typically `'failed'`) and raises HTTP 503.

### Error Categorization

The queue consumer processes each message in a batch individually:

- **Transient errors** (ConnectionError, TimeoutError, HTTP 5xx): Call `message.retry()` — the message goes back to the queue for automatic retry with backoff.
- **Permanent errors** (HTTP 4xx, ValueError, other exceptions): Call `message.ack()` — the message is removed from the queue. Update the article's status to `'failed'` in D1.

### Wide Event Logging

Each queue message gets its own WideEvent with `pipeline="queue"`. The event captures: message type, article_id, processing outcome, error details, and all infrastructure timing from Safe* wrappers.

---

## 12. Database

**Binding:** `DB` (Cloudflare D1, SQLite)

### Tables

> **Note:** The authoritative schema lives in `migrations/`. The tables below are a snapshot for reference and may lag behind the latest migrations.

**users**
| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | `secrets.token_urlsafe(16)` |
| github_id | INTEGER UNIQUE | GitHub user ID |
| email | TEXT | Verified email |
| username | TEXT | GitHub login |
| avatar_url | TEXT | GitHub avatar |
| created_at | TEXT | ISO 8601 UTC |
| updated_at | TEXT | ISO 8601 UTC |

**articles**
| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | `secrets.token_urlsafe(16)` |
| user_id | TEXT FK→users | CASCADE delete |
| original_url | TEXT NOT NULL | User-provided URL |
| final_url | TEXT | After redirects |
| canonical_url | TEXT | From `<link rel=canonical>` |
| domain | TEXT | Extracted hostname |
| title | TEXT | From extraction or user |
| excerpt | TEXT | ≤300 chars |
| author | TEXT | From meta tags |
| word_count | INTEGER | From markdown |
| reading_time_minutes | INTEGER | `ceil(words / 200)` |
| image_count | INTEGER DEFAULT 0 | Downloaded images |
| status | TEXT DEFAULT 'pending' | CHECK: pending/processing/ready/failed |
| reading_status | TEXT DEFAULT 'unread' | CHECK: unread/archived |
| is_favorite | INTEGER DEFAULT 0 | Boolean as int |
| audio_key | TEXT | R2 key for audio.mp3 |
| audio_duration_seconds | INTEGER | TTS duration |
| audio_status | TEXT DEFAULT NULL | CHECK: NULL/pending/generating/ready/failed |
| html_key | TEXT | R2 key for content.html |
| thumbnail_key | TEXT | R2 key for thumbnail.webp |
| original_key | TEXT | R2 key for original.webp |
| markdown_content | TEXT | Full markdown (for FTS5) |
| original_status | TEXT DEFAULT 'unknown' | CHECK: available/paywalled/gone/domain_dead/unknown |
| last_checked_at | TEXT | Last health check |
| scroll_position | REAL DEFAULT 0 | Reading progress (px) |
| reading_progress | REAL DEFAULT 0 | 0.0–1.0 |
| created_at | TEXT | ISO 8601 UTC |
| updated_at | TEXT | ISO 8601 UTC |

**tags**
| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | — |
| user_id | TEXT FK→users | CASCADE delete |
| name | TEXT NOT NULL | UNIQUE(user_id, name) |
| created_at | TEXT | — |

**article_tags**
| Column | Type | Notes |
|--------|------|-------|
| article_id | TEXT FK→articles | CASCADE delete |
| tag_id | TEXT FK→tags | CASCADE delete |
| PK | (article_id, tag_id) | Composite |

**tag_rules**
| Column | Type | Notes |
|--------|------|-------|
| id | TEXT PK | — |
| tag_id | TEXT FK→tags | CASCADE delete |
| match_type | TEXT NOT NULL | CHECK: domain/title_contains/url_contains |
| pattern | TEXT NOT NULL | ≤500 chars, glob for domain |
| created_at | TEXT NOT NULL | — |

**articles_fts** (FTS5 virtual table)
- Columns: title, excerpt, markdown_content
- Synced via D1 triggers on articles INSERT/UPDATE/DELETE

### Key Indexes

```sql
idx_articles_user_created         (user_id, created_at)
idx_articles_user_reading_status  (user_id, reading_status)
idx_articles_user_audio_status    (user_id, audio_status)
idx_articles_user_favorite        (user_id, is_favorite)
idx_articles_user_status          (user_id, status)
idx_articles_final_url            (final_url)
idx_articles_canonical_url        (canonical_url)
idx_articles_user_url             (user_id, original_url) UNIQUE
idx_tags_user_name                (user_id, name) UNIQUE
idx_tag_rules_tag                 (tag_id)
idx_users_email                   (email)
```

### Null Handling

All D1 bind parameters go through `d1_null()` which converts Python `None` to JS `null`. D1 rejects `undefined` (which is what Python `None` becomes across the FFI boundary by default).

---

## 13. Object Storage

**Binding:** `CONTENT` (Cloudflare R2)

### Key Layout

All keys follow the pattern `articles/{article_id}/{suffix}`:

| Key | Content | Media Type | Cache |
|-----|---------|------------|-------|
| `articles/{id}/content.html` | Cleaned article HTML | text/html | max-age=86400 |
| `articles/{id}/metadata.json` | Processing metadata | application/json | — |
| `articles/{id}/thumbnail.webp` | Above-the-fold screenshot | image/webp | max-age=86400 |
| `articles/{id}/original.webp` | Full-page screenshot | image/webp | max-age=86400 |
| `articles/{id}/audio.mp3` | TTS audio | audio/mpeg | max-age=86400, immutable |
| `articles/{id}/audio-timing.json` | Sentence timing map | application/json | max-age=86400, immutable |
| `articles/{id}/raw.html` | Bookmarklet pre-supplied HTML | — | Internal only |
| `articles/{id}/images/{hash}.ext` | Downloaded article images | image/* | max-age=31536000, immutable |

### Image Keys

Image keys are content-addressed: `articles/{id}/images/{sha256(original_url)[:16]}{ext}`. The hash is of the original image URL, not the image content. Extension is derived from Content-Type header, falling back to the URL extension, falling back to `.bin`. Extensions are sanitized: max 5 chars, alphanumeric only.

### Path Traversal Prevention

`article_key(article_id, filename)` validates both `article_id` and `filename` for `/` and `..` characters. Raises `ValueError` on violation.

### Deletion

`delete_article_content(r2, article_id)` performs paginated deletion using `r2.list(prefix=f"articles/{article_id}/")` with cursor-based pagination, deleting each object individually.

---

## 14. Observability

**File:** `src/wide_event.py`

### Wide Events Pattern

One JSON log line per request or queue message. The event is built incrementally during processing and emitted in a `finally` block via `print(json.dumps(event))`. Workers Logs captures stdout.

### WideEvent Class

Uses `__slots__` for memory efficiency (39 attributes). Created via `begin_event(pipeline, **initial_fields)` and installed as a `contextvars.ContextVar` for async context isolation.

**Standard fields:** timestamp, pipeline, cf-ray, method, path, status_code, duration_ms, outcome, user_id.

**Infrastructure counters** (auto-populated by Safe* wrappers):

| Counter | Source |
|---------|--------|
| d1.count / d1.ms | SafeD1Statement.first(), .all(), .run() |
| r2.get.count / r2.get.ms | SafeR2.get(), .list() |
| r2.put.count / r2.put.ms | SafeR2.put() |
| r2.del.count / r2.del.ms | SafeR2.delete() |
| kv.count / kv.ms | SafeKV.get(), .put(), .delete() |
| queue.count / queue.ms | SafeQueue.send() |
| ai.count / ai.ms | SafeAI.run() |
| http.count / http.ms | http_fetch() |
| svc.count / svc.ms | SafeReadability.parse() |

**Domain fields:** Added by handlers via `event.set(key, value)` or `event.set_many(dict)`. Examples: extraction_method, word_count, image_count, audio_chunks, error details.

### ObservabilityMiddleware

The outermost middleware in the FastAPI stack. Wraps each request:

1. `begin_event("http", method=method, path=path, cf_ray=cf_ray)`
2. Call next middleware
3. `event.set("status_code", response.status_code)`
4. `emit_event(event)` in `finally` block

### Finalization

`event.finalize()` computes `duration_ms` from `time.monotonic()` delta, includes only non-zero counters, and rounds milliseconds to 2 decimal places.

---

## 15. Security

### SSRF Protection

**File:** `src/articles/urls.py`

Three-point validation prevents Server-Side Request Forgery:

1. **Pre-fetch validation** (`validate_url()`):
   - Scheme must be `http` or `https`
   - Must have valid hostname
   - Reject embedded credentials (`user:pass@host`)
   - Check hostname against `_BLOCKED_HOSTNAMES` (localhost, 127.0.0.1, 0.0.0.0, ::1, metadata.google.internal, 169.254.169.254)
   - Check IPv4 against RFC1918 ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16), link-local (169.254.0.0/16), loopback (127.0.0.0/8)
   - Check IPv6 via `ipaddress` module: `is_private`, `is_loopback`, `is_reserved`, `is_link_local`, IPv6-mapped IPv4

2. **Post-redirect validation** (in `process_article()`):
   - After HTTP fetch follows redirects, validate the final URL's hostname through the same checks
   - An attacker could use a redirect from a public URL to a private one

3. **Image download validation** (in `download_images()`):
   - Each `<img src>` URL is independently SSRF-checked before fetching
   - Both the initial hostname and post-redirect hostname are validated

### FTS5 Injection Prevention

The `_sanitize_fts5_query()` function strips all FTS5 special characters and wraps tokens in double quotes. This prevents FTS5 operator injection where a user could craft queries using `OR`, `NOT`, `NEAR`, etc. to access data or cause denial of service.

### Content Security Policy

The `/api/articles/{id}/content` endpoint serves archived HTML with a restrictive CSP:

```
default-src 'none'; img-src * data:; style-src 'unsafe-inline'
```

This prevents script execution in archived article content while allowing images and inline styles.

### Security Headers Middleware

**File:** `src/security.py`

Applied to all responses:

| Header | Value |
|--------|-------|
| X-Content-Type-Options | nosniff |
| X-Frame-Options | DENY |
| Referrer-Policy | strict-origin-when-cross-origin |
| Permissions-Policy | camera=(), microphone=(), geolocation=() |
| Strict-Transport-Security | max-age=31536000; includeSubDomains (HTTPS only) |

HSTS is conditional on `SITE_URL` starting with `https://`.

### Cookie Security

- `httponly=True` — prevents JavaScript access
- `secure` derived from `SITE_URL` protocol
- `samesite=lax` — prevents CSRF from cross-site POST
- 7-day expiry with server-side TTL in KV

### Input Validation Limits

| Field | Limit |
|-------|-------|
| URL | 2,048 characters |
| Title | 500 characters |
| Pre-supplied HTML content | 5 MB |
| Tag name | 100 characters |
| Tag rule pattern | 500 characters |
| Batch article IDs | 100 per request |
| Search query limit | 100 results per page |
| Image per file | 2 MB |
| Images total per article | 10 MB |
| Page fetch | 10 MB |
| TTS text | 100,000 characters |

---

## 16. Statistics & Export

**File:** `src/stats/routes.py`, `src/articles/export.py`

### Statistics Aggregation

`GET /api/stats` runs 9+ D1 queries (all user-scoped):

| Metric | SQL Pattern |
|--------|-------------|
| total_articles | `COUNT(*)` |
| total_words_read | `SUM(word_count) WHERE reading_status='archived'` |
| articles_by_status | `GROUP BY reading_status` |
| articles_this_week | `created_at >= datetime('now', '-7 days')` |
| articles_this_month | `created_at >= datetime('now', '-30 days')` |
| archived_this_week | `reading_status='archived' AND updated_at >= datetime('now', '-7 days')` |
| archived_this_month | `reading_status='archived' AND updated_at >= datetime('now', '-30 days')` |
| top_domains | `GROUP BY domain ORDER BY cnt DESC LIMIT 10` |
| avg_reading_time_minutes | `AVG(reading_time_minutes)`, rounded to 1 decimal |

### Streak Calculation

`_calculate_streak(rows)` — rows are distinct dates from `SELECT DISTINCT date(updated_at) ... WHERE reading_status='archived' ORDER BY d DESC`.

Algorithm: Start from today (or yesterday). Count consecutive backward days in the set until a gap is found. Return 0 if neither today nor yesterday has activity.

### Monthly Trends

Two queries for the last 12 months — saved articles by `created_at` month and archived articles by `updated_at` month. Merged into `articles_by_month: [{month, saved, archived}]`.

### JSON Export

`GET /api/export/json` — all articles with tags as a JSON array. Filename: `tasche-export-{YYYY-MM-DD}.json`.

### Netscape Bookmark Export

`GET /api/export/html` — standard `NETSCAPE-Bookmark-file-1` format compatible with browsers and services like Pinboard. Each entry has `HREF`, `ADD_DATE` (Unix timestamp), `TAGS` (comma-separated), title text, and optional `<DD>` excerpt. Filename: `tasche-export-{YYYY-MM-DD}.html`.

---

## 17. Frontend

**Directory:** `frontend/src/`

### Technology Stack

- **Preact** — lightweight React alternative (3KB)
- **@preact/signals** — reactive state management without hooks boilerplate
- **Vite** — build tool
- **marked** + **DOMPurify** — markdown rendering with sanitization
- **No routing library** — hand-rolled hash router

### State Management

All state lives in module-level signals (`frontend/src/state.js`):

```
user, articles, tags, searchResults, searchQuery
filter (unread|listen|favorites|archived), offset, limit, hasMore, loading
isOffline, syncStatus, theme, toasts, showShortcuts
```

Signals are reactive — components that read a signal's `.value` automatically re-render when it changes. Theme is persisted to localStorage.

### Hash Router

Implemented in `AppRouter` component with a `window.hashchange` listener:

| Hash | View |
|------|------|
| `#/` | Library (default) |
| `#/?tag={id}` | Library filtered by tag |
| `#/article/{id}` | Reader |
| `#/article/{id}/markdown` | Markdown viewer |
| `#/search` | Search |
| `#/tags` | Tag management |
| `#/stats` | Statistics |
| `#/settings` | Settings |
| `#/login` | Login |

### API Client

`frontend/src/api.js` — all calls use a base `request(method, path, body)` function with `credentials: 'include'` for cookie auth. Auto-redirects to `#/login` on 401 responses.

### Optimistic Updates

Article actions (archive, favorite, delete) update the local signal immediately, then call the API. On failure: if offline, queue the mutation via Service Worker; if online, show an error toast.

### Reader Features

- Three content modes: HTML (from R2), rendered Markdown, source Markdown
- Reader preferences (font size, line height, content width, font family) persisted to localStorage, applied via CSS custom properties
- Scroll position and reading progress auto-saved via debounced scroll listener
- TTS sentence highlighting during audio playback (wraps text in `<span data-sentence-idx>`)
- Original URL health status indicator (available/paywalled/gone/domain_dead/unknown)

### Keyboard Shortcuts

Library: j/k (navigate), o/Enter (open), a (archive), s (favorite), d (delete), / (search), n (new URL).
Reader: Escape/h (back), a (archive), s (favorite), m (cycle content mode).
Global: ? (help overlay).

---

## 18. Service Worker & Offline

**File:** `frontend/public/sw.js` — vanilla JS, no build step.

### Four Named Caches

| Cache | Purpose | Strategy |
|-------|---------|----------|
| `tasche-static-v2` | Hashed assets (JS/CSS with content hash) | Cache-first |
| `tasche-api-v1` | Automatic GET response caching | Network-first |
| `tasche-offline-v1` | Explicitly saved articles + audio | Network-first with offline fallback |
| `tasche-v1` | Sync queue storage | Internal |

### Fetch Strategies

1. **Hashed assets** (`/assets/*.js` with content hash in filename): Cache-first. Safe because the hash changes when content changes.
2. **Navigation** (`/`, `/manifest.json`, static files): Network-first. Always fetch latest `index.html` to get correct script hashes.
3. **API GETs** (`/api/*`): Network-first → offline fallback to `tasche-offline-v1` → fallback to `tasche-api-v1` → 503 JSON response.

### Offline Mutation Queue

When offline, the frontend calls `queueOfflineMutation(url, method, body)` which posts a message to the SW. The SW stores mutations in the `tasche-v1` cache under a sync queue key.

On reconnect, the `sync` event triggers `replayQueue()`:
- Fetches all queued items
- Retries each request
- 2xx: remove from queue (success)
- 4xx: remove from queue (permanent failure)
- 5xx: keep in queue (transient, retry later)
- Notifies clients of sync status: syncing → synced | error

### Auto-Precaching

On app load, `triggerAutoPrecache(limit=20)` asks the SW to cache the 20 most recent unread articles. The SW fetches `/api/articles?reading_status=unread&limit=20&sort=created_at:desc`, filters for `status === 'ready'` articles not already cached, and caches both the article detail and content endpoints with a 10-second timeout per request.

### Explicit Offline Save

Users can explicitly save articles (and audio) for offline reading. These go to the `tasche-offline-v1` cache which has higher priority than the automatic `tasche-api-v1` cache.

### LRU Eviction

Offline metadata tracks cached articles: `{articleId: {hasContent, hasAudio, accessedAt, autoCached}}`. When the count exceeds `MAX_OFFLINE_ARTICLES` (100), the oldest-accessed auto-cached articles are evicted. Explicitly saved articles are not auto-evicted.

### Message Types

| Message | Action |
|---------|--------|
| `QUEUE_REQUEST` | Add offline mutation to sync queue |
| `CACHE_ARTICLES` | Batch precache article details |
| `SAVE_FOR_OFFLINE` | Explicitly cache article + content |
| `SAVE_AUDIO_OFFLINE` | Explicitly cache audio |
| `CHECK_OFFLINE_STATUS` | Query cache status for article |
| `AUTO_PRECACHE` | Precache recent unread articles |
| `GET_CACHE_STATS` | Calculate total cached size |
| `SKIP_WAITING` | Activate new SW version |
| `REPLAY_QUEUE` | Manual sync queue replay |

---

## 19. Scheduled Tasks

**Handler:** `Default.scheduled(event)` in `entry.py`

### URL Health Checker

Runs on a cron schedule. Queries D1 for up to 10 articles where:
- `original_status = 'unknown'`, OR
- `last_checked_at IS NULL`, OR
- `last_checked_at < datetime('now', '-30 days')`

For each article, calls `check_original_url(original_url)` which performs a HEAD request with a 10-second timeout.

### HEAD Request Strategy

**File:** `src/articles/health.py`

Uses HEAD (not GET) to minimize bandwidth. Pre-flight and post-redirect SSRF validation applied.

### Status Classification

| HTTP Response | Classification |
|---------------|---------------|
| 2xx | `available` |
| 401, 403 | `paywalled` |
| 404, 410 | `gone` |
| ConnectionError, TimeoutError | `domain_dead` |
| Any other | `unknown` |

After classification, updates D1: `original_status`, `last_checked_at`, `updated_at`.

The frontend can also trigger health checks on individual articles via `POST /api/articles/{id}/check-original` and in batch via `POST /api/articles/batch-check-originals` (processes 10 at a time).

---

## 20. Deployment

### Configuration

All configuration lives in `wrangler.jsonc`. Three environments:

| Environment | URL | Auth | Notes |
|-------------|-----|------|-------|
| Local dev | `localhost:port` | DISABLE_AUTH=true in `.dev.vars` | Uses Miniflare for D1/R2/KV |
| Staging | `tasche-staging.adewale-883.workers.dev` | GitHub OAuth required | Full Cloudflare bindings |
| Production | `tasche-production.adewale-883.workers.dev` | GitHub OAuth required | ALLOWED_EMAILS enforced |

### Bindings in wrangler.jsonc

```jsonc
{
  "d1_databases": [{"binding": "DB", "database_name": "tasche-db"}],
  "r2_buckets": [{"binding": "CONTENT", "bucket_name": "tasche-content"}],
  "kv_namespaces": [{"binding": "SESSIONS"}],
  "queues": {
    "producers": [{"binding": "ARTICLE_QUEUE", "queue": "tasche-queue"}],
    "consumers": [{"queue": "tasche-queue", "max_batch_size": 10}]
  },
  "ai": {"binding": "AI"},
  "services": [{"binding": "READABILITY", "service": "readability-worker"}],
  "assets": {"binding": "ASSETS", "directory": "./assets"}
}
```

### Deploy Commands

See `CLAUDE.md` and `Makefile` for current deploy commands. Key requirement: must use `pywrangler` (not regular `wrangler`) because regular wrangler cannot deploy Python Workers with packages. `pywrangler` handles Pyodide bundling and package installation from `pyproject.toml`.

### Testing

See `CLAUDE.md` for current test commands. Run `make check` for all gates (backend lint + pytest + frontend lint + format + vitest).

### Testing Blind Spots

- Unit tests (pytest + TestClient) run in CPython and bypass wrangler's asset layer — cannot catch routing conflicts or Pyodide FFI bugs.
- Queue handler bugs only surface in live Cloudflare deployment (not Miniflare, not pytest).
- Miniflare queue consumer is unreliable — use `POST /api/articles/{id}/process-now` to bypass queue for local dev/debugging.

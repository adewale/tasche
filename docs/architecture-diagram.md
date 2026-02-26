# Tasche Architecture Diagram

An annotated visual map of every component, binding, and data flow in the system.

```
                            CLOUDFLARE EDGE
 ============================================================================
 |                                                                          |
 |   V8 Isolate  ──────────────────────────────────────────────────────┐    |
 |   │  Pyodide (WebAssembly)                                         │    |
 |   │  Python 3.12 + FastAPI                                         │    |
 |   │                                                                 │    |
 |   │  ┌──────────────────────────────────────────────────────────┐   │    |
 |   │  │              Worker Entry Point (entry.py)               │   │    |
 |   │  │                class Default(WorkerEntrypoint)           │   │    |
 |   │  │                                                          │   │    |
 |   │  │  ┌──────────┐   ┌──────────┐   ┌────────────────────┐   │   │    |
 |   │  │  │ fetch()   │   │ queue()  │   │ scheduled(event)   │   │   │    |
 |   │  │  │ HTTP reqs │   │ async    │   │ cron trigger       │   │   │    |
 |   │  │  └─────┬─────┘   │ jobs     │   │ URL health checks  │   │   │    |
 |   │  │        │          └────┬─────┘   └────────────────────┘   │   │    |
 |   │  └────────┼───────────────┼──────────────────────────────────┘   │    |
 |   │           │               │                                      │    |
 |   │           ▼               ▼                                      │    |
 |   │   ┌──────────────┐  ┌────────────────────────┐                  │    |
 |   │   │ Path router  │  │ Queue dispatch table    │                  │    |
 |   │   │              │  │                          │                  │    |
 |   │   │ /api/* ──────┼──│─► FASTAPI APP           │                  │    |
 |   │   │              │  │                          │                  │    |
 |   │   │ else  ──►    │  │ "article_processing"    │                  │    |
 |   │   │ ASSETS       │  │   └─► process_article() │                  │    |
 |   │   │ binding      │  │                          │                  │    |
 |   │   │ (SPA         │  │ "tts_generation"         │                  │    |
 |   │   │  fallback    │  │   └─► process_tts()      │                  │    |
 |   │   │  on 404)     │  │                          │                  │    |
 |   │   └──────────────┘  └────────────────────────┘                  │    |
 |   │                                                                 │    |
 |   └─────────────────────────────────────────────────────────────────┘    |
 |                                                                          |
 ============================================================================


                         FASTAPI MIDDLEWARE STACK
 ============================================================================
 |                                                                          |
 |  Request ──► ObservabilityMiddleware  (outermost — wide event logging)   |
 |                  │                                                       |
 |                  ▼                                                       |
 |              SecurityHeadersMiddleware  (CSP, HSTS, X-Frame-Options)     |
 |                  │                                                       |
 |                  ▼                                                       |
 |              CORSMiddleware  (innermost — localhost:* dev origins)        |
 |                  │                                                       |
 |                  ▼                                                       |
 |              get_current_user()  dependency  (per-route auth check)      |
 |                  │                                                       |
 |                  ▼                                                       |
 |         ┌────────────────────────────────────────────────────┐           |
 |         │                  9 API ROUTERS                     │           |
 |         │                                                    │           |
 |         │  /api/auth          auth.routes         (OAuth)    │           |
 |         │  /api/articles      articles.routes     (CRUD)     │           |
 |         │  /api/export        articles.export     (export)   │           |
 |         │  /api/tags          tags.routes         (tag CRUD) │           |
 |         │  /api/articles/*/tags  tags.routes      (assoc.)   │           |
 |         │  /api/tag-rules     tags.rules          (rules)    │           |
 |         │  /api/search        search.routes       (FTS5)     │           |
 |         │  /api/articles/*/audio  tts.routes      (TTS)      │           |
 |         │  /api/stats         stats.routes        (stats)    │           |
 |         │                                                    │           |
 |         │  /api/health        (inline)  health check         │           |
 |         │  /api/health/config (inline)  binding verification │           |
 |         └────────────────────────────────────────────────────┘           |
 |                                                                          |
 ============================================================================


                           FFI BOUNDARY (wrappers.py)
 ============================================================================
 |                                                                          |
 |  SafeEnv wraps all bindings at construction time:                        |
 |                                                                          |
 |  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌──────────────┐ |
 |  │ SafeD1   │ │ SafeR2   │ │ SafeKV   │ │ SafeQueue │ │ SafeAI       │ |
 |  │ (DB)     │ │ (CONTENT)│ │(SESSIONS)│ │(ART_QUEUE)│ │ (AI)         │ |
 |  │          │ │          │ │          │ │           │ │              │ |
 |  │ prepare()│ │ put()    │ │ get()    │ │ send()    │ │ run()        │ |
 |  │ bind()   │ │ get()    │ │ put()    │ │           │ │              │ |
 |  │ first()  │ │ delete() │ │ delete() │ │ dict→     │ │ dict→        │ |
 |  │ all()    │ │ list()   │ │          │ │ JS Object │ │ JS Object    │ |
 |  │ run()    │ │          │ │          │ │           │ │              │ |
 |  │          │ │ bytes→   │ │ checks   │ │ _to_js_   │ │ consume_     │ |
 |  │ None→    │ │ Uint8Arr │ │ null/    │ │ value()   │ │ readable_    │ |
 |  │ JS null  │ │ via to_  │ │ undef    │ │           │ │ stream()     │ |
 |  │ via      │ │ js_bytes │ │          │ │           │ │              │ |
 |  │ d1_null()│ │          │ │          │ │           │ │              │ |
 |  └──────────┘ └──────────┘ └──────────┘ └───────────┘ └──────────────┘ |
 |                                                                          |
 |  ┌──────────────────┐                                                   |
 |  │ SafeReadability   │    Key conversions:                               |
 |  │ (READABILITY)     │    _to_py_safe()  — JsProxy → Python (recursive) |
 |  │                   │    _to_js_value() — Python → JS Object            |
 |  │ parse(html, url)  │    d1_first()     — unwrap .first() result        |
 |  │ → {title, html,   │    d1_rows()      — unwrap .all() result          |
 |  │    excerpt,byline} │    to_js_bytes()  — bytes → Uint8Array            |
 |  └──────────────────┘    consume_readable_stream() — ReadableStream→bytes|
 |                                                                          |
 |  Every method records timing via _record() → WideEvent                   |
 |                                                                          |
 ============================================================================


                    7 CLOUDFLARE BINDINGS
 ============================================================================
 |                                                                          |
 |  ┌─────────────────────────────────────────────────────────────────┐     |
 |  │                         D1 (DB)                                 │     |
 |  │  SQLite database — users, articles, tags, article_tags,         │     |
 |  │  tag_rules, articles_fts (FTS5 virtual table)                   │     |
 |  │  Triggers auto-sync articles_fts on INSERT/UPDATE/DELETE        │     |
 |  └─────────────────────────────────────────────────────────────────┘     |
 |                                                                          |
 |  ┌─────────────────────────────────────────────────────────────────┐     |
 |  │                        R2 (CONTENT)                             │     |
 |  │  Key layout: articles/{id}/content.html                         │     |
 |  │              articles/{id}/metadata.json                        │     |
 |  │              articles/{id}/thumbnail.webp                       │     |
 |  │              articles/{id}/original.webp                        │     |
 |  │              articles/{id}/audio.mp3                            │     |
 |  │              articles/{id}/audio-timing.json                    │     |
 |  │              articles/{id}/raw.html  (bookmarklet pre-supplied) │     |
 |  │              articles/{id}/images/{hash}.webp                   │     |
 |  └─────────────────────────────────────────────────────────────────┘     |
 |                                                                          |
 |  ┌───────────────────────┐  ┌──────────────────────────────────────┐    |
 |  │     KV (SESSIONS)     │  │      Queues (ARTICLE_QUEUE)          │    |
 |  │                       │  │                                      │    |
 |  │ session:{id} → JSON   │  │  Two message types:                  │    |
 |  │   TTL: 7 days         │  │  ├─ article_processing               │    |
 |  │   Refresh: 1hr throt. │  │  │    {article_id, url, user_id}     │    |
 |  │                       │  │  └─ tts_generation                   │    |
 |  │ oauth_state:{state}   │  │       {article_id, user_id}          │    |
 |  │   TTL: 10 min         │  │                                      │    |
 |  └───────────────────────┘  │  Error handling:                     │    |
 |                              │  ├─ Transient → message.retry()      │    |
 |                              │  └─ Permanent → message.ack() + fail │    |
 |                              └──────────────────────────────────────┘    |
 |                                                                          |
 |  ┌───────────────────────┐  ┌──────────────────────────────────────┐    |
 |  │   Workers AI (AI)     │  │  Service Binding (READABILITY)       │    |
 |  │                       │  │                                      │    |
 |  │ @cf/deepgram/aura-2-en│  │  Mozilla Readability in JS Worker    │    |
 |  │ Text-to-speech        │  │  parse(html, url) → article fields   │    |
 |  │ 2000 char/chunk limit │  │  Fallback: BeautifulSoup heuristic   │    |
 |  └───────────────────────┘  └──────────────────────────────────────┘    |
 |                                                                          |
 |  ┌─────────────────────────────────────────────────────────────────┐     |
 |  │                     Assets (ASSETS)                             │     |
 |  │  Static frontend: Preact SPA (Vite build)                       │     |
 |  │  Worker-first routing: API handled by FastAPI, else ASSETS      │     |
 |  │  404 fallback serves /index.html for client-side hash routing   │     |
 |  └─────────────────────────────────────────────────────────────────┘     |
 |                                                                          |
 ============================================================================


                  ARTICLE SAVE → READY LIFECYCLE
 ============================================================================
 |                                                                          |
 |  Browser                                                                 |
 |    │                                                                     |
 |    ├─ POST /api/articles {url, title?, content?, listen_later?}          |
 |    │                                                                     |
 |    ▼                                                                     |
 |  articles.routes.create_article()                                        |
 |    │                                                                     |
 |    ├─ 1. Validate URL (SSRF blocklist, scheme, no credentials)           |
 |    ├─ 2. Check duplicate (original_url, final_url, canonical_url)        |
 |    ├─ 3. INSERT article (status: pending, reading_status: unread)        |
 |    ├─ 4. If content provided: store raw.html to R2 (bookmarklet)         |
 |    ├─ 5. Enqueue {type: "article_processing", article_id, url}           |
 |    │                                                                     |
 |    ▼                                                                     |
 |  ◄──── 201 {id, status: "pending"} ────►  Browser                       |
 |                                                                          |
 |    ┌─────────────────────────────────────────────────────┐               |
 |    │              QUEUE CONSUMER                         │               |
 |    │                                                     │               |
 |    │  process_article(article_id, url, env)              │               |
 |    │    │                                                │               |
 |    │    ├─ 1.  SET status = 'processing'                 │               |
 |    │    ├─ 2.  Check R2 for raw.html (bookmarklet)       │               |
 |    │    ├─ 3.  Fetch page (or use raw.html)              │               |
 |    │    ├─ 4.  Post-redirect SSRF check                  │               |
 |    │    ├─ 5.  JS-heavy? → Browser Rendering scrape()    │               |
 |    │    ├─ 6.  Extract canonical URL + domain             │               |
 |    │    ├─ 7.  Screenshots (thumbnail 1200x630,           │               |
 |    │    │      full-page 1200x800) via Browser Rendering  │               |
 |    │    ├─ 8.  Extract content:                           │               |
 |    │    │        Readability Service Binding               │               |
 |    │    │        └─ fallback: BeautifulSoup heuristic      │               |
 |    │    ├─ 9.  Download images → convert WebP → store R2  │               |
 |    │    ├─ 10. Rewrite <img src> to /api/articles/*/images│               |
 |    │    ├─ 11. Convert HTML → Markdown (markdownify)      │               |
 |    │    ├─ 12. Store content.html + metadata.json to R2   │               |
 |    │    ├─ 13. UPDATE D1 with all metadata, status=ready  │               |
 |    │    ├─ 14. FTS5 auto-indexed via D1 trigger           │               |
 |    │    ├─ 15. Apply auto-tag rules                       │               |
 |    │    └─ 16. If audio_status=pending → enqueue TTS      │               |
 |    │                                                     │               |
 |    │  Error handling:                                    │               |
 |    │    ConnectionError/TimeoutError → retry()           │               |
 |    │    HTTP 5xx → retry()                               │               |
 |    │    HTTP 4xx / other → ack() + status=failed         │               |
 |    └─────────────────────────────────────────────────────┘               |
 |                                                                          |
 ============================================================================


                      TTS GENERATION LIFECYCLE
 ============================================================================
 |                                                                          |
 |  Browser                                                                 |
 |    │                                                                     |
 |    ├─ POST /api/articles/{id}/listen-later                               |
 |    │                                                                     |
 |    ▼                                                                     |
 |  tts.routes.listen_later()                                               |
 |    │                                                                     |
 |    ├─ 1. Idempotency check (skip if already ready/pending)               |
 |    ├─ 2. SET audio_status = 'pending'                                    |
 |    ├─ 3. Enqueue {type: "tts_generation", article_id, user_id}           |
 |    │                                                                     |
 |    ▼                                                                     |
 |  ◄──── 202 {id, audio_status: "pending"} ────►  Browser                 |
 |                                                                          |
 |    ┌─────────────────────────────────────────────────────┐               |
 |    │              QUEUE CONSUMER                         │               |
 |    │                                                     │               |
 |    │  process_tts(article_id, env, user_id)              │               |
 |    │    │                                                │               |
 |    │    ├─ 1. Idempotency: skip if audio_status=ready    │               |
 |    │    ├─ 2. SET audio_status = 'generating'            │               |
 |    │    ├─ 3. Fetch markdown_content from D1             │               |
 |    │    ├─ 4. Strip markdown syntax                      │               |
 |    │    ├─ 5. Truncate to 100K chars                     │               |
 |    │    ├─ 6. Split into sentences (regex: [.!?]\s+)     │               |
 |    │    ├─ 7. Chunk into <=1900 char groups              │               |
 |    │    │     (headroom below 2000 API limit)            │               |
 |    │    ├─ 8. For each chunk:                            │               |
 |    │    │       Workers AI @cf/deepgram/aura-2-en        │               |
 |    │    │       consume_readable_stream() → bytes        │               |
 |    │    ├─ 9. Concatenate all audio chunks               │               |
 |    │    ├─ 10. Store audio.mp3 to R2                     │               |
 |    │    ├─ 11. Verify R2 write (size check)              │               |
 |    │    ├─ 12. Generate sentence timing map              │               |
 |    │    ├─ 13. Store audio-timing.json to R2             │               |
 |    │    └─ 14. UPDATE D1: audio_key, duration,           │               |
 |    │           audio_status = 'ready'                    │               |
 |    │                                                     │               |
 |    │  Error handling:                                    │               |
 |    │    ValueError (no content) → status=failed (perm)   │               |
 |    │    Other exceptions → raise for queue retry         │               |
 |    └─────────────────────────────────────────────────────┘               |
 |                                                                          |
 ============================================================================


                           FRONTEND ARCHITECTURE
 ============================================================================
 |                                                                          |
 |  ┌──────────────────────────────────────────────────────────────────┐    |
 |  │                 Preact SPA (Vite build)                          │    |
 |  │                                                                  │    |
 |  │  State: @preact/signals (reactive, module-level)                 │    |
 |  │  Router: manual hash-based (#/article/{id}, #/search, etc.)     │    |
 |  │  Markdown: marked + DOMPurify                                    │    |
 |  │  API client: fetch() with credentials:'include'                  │    |
 |  │                                                                  │    |
 |  │  ┌────────────────────────────────────────────────────────────┐  │    |
 |  │  │                     VIEWS                                  │  │    |
 |  │  │                                                            │  │    |
 |  │  │  Library    — article list, filters, sort, bulk ops        │  │    |
 |  │  │  Reader     — article content, TTS player, tag picker      │  │    |
 |  │  │  Search     — FTS5 with highlighted results                │  │    |
 |  │  │  Tags       — tag CRUD + auto-tag rules                   │  │    |
 |  │  │  Stats      — reading stats, streaks, trends               │  │    |
 |  │  │  Settings   — offline config, bookmarklet, export          │  │    |
 |  │  │  Login      — GitHub OAuth redirect                        │  │    |
 |  │  └────────────────────────────────────────────────────────────┘  │    |
 |  │                                                                  │    |
 |  │  Components: Header, ArticleCard, AudioPlayer, ReaderToolbar,    │    |
 |  │              TagPicker, KeyboardShortcutsHelp, Toast, Icons       │    |
 |  └──────────────────────────────────────────────────────────────────┘    |
 |                                                                          |
 |  ┌──────────────────────────────────────────────────────────────────┐    |
 |  │              Service Worker (sw.js, vanilla JS)                  │    |
 |  │                                                                  │    |
 |  │  4 Named Caches:                                                 │    |
 |  │  ├─ tasche-static-v2   — hashed assets (cache-first)            │    |
 |  │  ├─ tasche-api-v1      — automatic GET caching                   │    |
 |  │  ├─ tasche-offline-v1  — explicitly saved articles               │    |
 |  │  └─ tasche-v1          — sync queue storage                      │    |
 |  │                                                                  │    |
 |  │  Strategies:                                                     │    |
 |  │  ├─ Hashed assets → cache-first                                  │    |
 |  │  ├─ Navigation (/, manifest) → network-first                     │    |
 |  │  └─ API GETs → network-first, offline fallback to cache          │    |
 |  │                                                                  │    |
 |  │  Offline features:                                               │    |
 |  │  ├─ Mutation queue (PATCH/DELETE) replayed via background sync    │    |
 |  │  ├─ Auto-precache 20 recent unread articles                      │    |
 |  │  ├─ Explicit save for offline (article + audio)                  │    |
 |  │  └─ LRU eviction at 100 articles                                 │    |
 |  └──────────────────────────────────────────────────────────────────┘    |
 |                                                                          |
 ============================================================================


                        EXTERNAL SERVICES
 ============================================================================
 |                                                                          |
 |  ┌────────────────────┐   ┌─────────────────────────────────────────┐   |
 |  │   GitHub OAuth      │   │  Browser Rendering REST API             │   |
 |  │                     │   │  (Cloudflare)                           │   |
 |  │  3-leg flow:        │   │                                         │   |
 |  │  1. /login redirect │   │  POST /screenshot                       │   |
 |  │  2. /callback       │   │    viewport: 1200x630 (thumb)           │   |
 |  │     exchange code   │   │    viewport: 1200x800 (full, full_page) │   |
 |  │  3. create session  │   │                                         │   |
 |  │                     │   │  POST /scrape                           │   |
 |  │  CSRF state in KV   │   │    JS-rendered DOM HTML                 │   |
 |  │  Email whitelist    │   │    Triggered when <500 chars body text  │   |
 |  └────────────────────┘   └─────────────────────────────────────────┘   |
 |                                                                          |
 ============================================================================


                        OBSERVABILITY
 ============================================================================
 |                                                                          |
 |   Wide Events pattern: one JSON log line per request/queue message       |
 |                                                                          |
 |   WideEvent (ContextVar-scoped)                                          |
 |     ├─ timestamp, pipeline, cf-ray, method, path                         |
 |     ├─ status_code, duration_ms, outcome, user_id                        |
 |     ├─ d1.count / d1.ms                                                  |
 |     ├─ r2.get.count / r2.get.ms / r2.put.count / r2.put.ms              |
 |     ├─ kv.count / kv.ms                                                  |
 |     ├─ queue.count / queue.ms                                            |
 |     ├─ ai.count / ai.ms                                                  |
 |     ├─ http.count / http.ms                                              |
 |     ├─ svc.count / svc.ms (service bindings)                             |
 |     └─ domain-specific fields via .set() / .set_many()                   |
 |                                                                          |
 |   Flow: begin_event() → Safe* wrappers record timing →                   |
 |          handlers add domain fields → emit_event() in finally block      |
 |                                                                          |
 |   Output: print(json.dumps(event)) → Workers Logs captures stdout       |
 |                                                                          |
 ============================================================================
```

## Annotation Notes

### Worker Entry Point
The `Default` class in `entry.py` extends Cloudflare's `WorkerEntrypoint` and runs Python on Pyodide (WebAssembly) inside V8 isolates. All handlers must be `async def` because sync code triggers `RuntimeError: can't start new thread`. The three handlers cover the full event surface: HTTP requests, queued async jobs, and cron-triggered health checks.

### Fetch Handler Routing
Worker-first routing is critical. We never use `not_found_handling: "single-page-application"` because it intercepts browser navigation requests (`Sec-Fetch-Mode: navigate`) to API paths. Instead, the Worker checks the path prefix: `/api/*` goes to FastAPI via the ASGI adapter, everything else delegates to the ASSETS binding. If ASSETS returns 404, we serve `/index.html` for the SPA's hash router.

### Middleware Stack
FastAPI's `add_middleware()` prepends, so the last-added middleware executes first. ObservabilityMiddleware wraps the entire request lifecycle and emits the wide event in a `finally` block. SecurityHeadersMiddleware adds HSTS (only if SITE_URL is https), X-Frame-Options, Referrer-Policy, and Permissions-Policy. CORSMiddleware handles preflight for local dev origins.

### FFI Boundary
Every interaction between Python and JavaScript crosses through `wrappers.py`. The Safe* wrappers handle four classes of FFI bugs: JsNull (not Python None, not a JsProxy), None→undefined (D1 needs null), nested JsNull in `.to_py()` results (requires recursive scrubbing), and Wasm memory views from `to_js(bytes)`. Every Safe* method also records timing to the current WideEvent for observability.

### Queue System
The queue consumer dispatches by message `type` field. Error handling distinguishes transient errors (network failures, 5xx) that get `message.retry()` from permanent errors (4xx, validation failures) that get `message.ack()` plus a status update to `failed`. The queue handler signature must accept 3 positional args `(batch, env, ctx)` — using only `(self, batch)` causes a TypeError.

### Content Extraction
The Readability Service Binding (a separate JS Worker) provides Mozilla-quality extraction. If it fails or is unavailable, BeautifulSoup takes over with a heuristic that scores containers by text length minus depth penalty. This dual-path design handles both the common case (Readability works) and edge cases (eval() blocked, binding misconfigured).

### SSRF Protection
Three-point validation: (1) pre-fetch URL validation (scheme, hostname against blocklist, RFC1918 ranges), (2) post-redirect validation (the final URL after redirects could land on a private host), and (3) image download validation (each `<img src>` is independently checked). The blocklist covers localhost, link-local, cloud metadata endpoints, and IPv6-mapped IPv4 addresses.

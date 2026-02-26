# Lessons Learned — Tasche Implementation

## Implement→Audit Loop Summary

Each phase was implemented by a sub-agent, then audited by a separate sub-agent. If the audit failed, fixes were applied and the audit was re-run. Below is a summary of each phase's loop.

### Phase 1: Project Foundation
- **Iterations:** 2 (implement → audit → fix → re-audit → PASS)
- **Files:** `wrangler.jsonc`, `pyproject.toml`, `Makefile`, `src/wrappers.py`, `src/entry.py`, `migrations/0001_initial.sql`, `tests/conftest.py`, `.gitignore`
- **Audit issues:** Minor issues with FFI boundary layer and mock completeness
- **Key fix:** Ensured `_to_py_safe`, `_to_js_value`, `_is_js_undefined`, `get_js_null()` were all implemented in `wrappers.py`

### Phase 2: Authentication
- **Iterations:** 2 (implement → audit → fix → re-audit → PASS)
- **Files:** `src/auth/routes.py`, `src/auth/session.py`, `src/auth/middleware.py`, `src/auth/dependencies.py`, `tests/unit/test_auth.py`
- **Audit issues:** GitHub OAuth flow edge cases
- **Key fix:** CSRF state parameter validation and `ALLOWED_EMAILS` enforcement

### Phase 3: Article CRUD API
- **Iterations:** 2 (implement → audit → fix → re-audit → PASS)
- **Files:** `src/articles/routes.py`, `src/articles/storage.py`, `src/articles/urls.py`, `tests/unit/test_articles.py`, `tests/unit/test_urls.py` (note: `src/articles/models.py` was later deleted — Pydantic models were inlined into routes)
- **Audit issues:** URL validation and duplicate detection across 3 URL fields
- **Key fix:** Duplicate detection checking `original_url`, `final_url`, and `canonical_url`

### Phase 4: Content Processing Pipeline
- **Iterations:** 2 (implement → audit → fix → re-audit → PASS)
- **Files:** `src/articles/processing.py`, `src/articles/browser_rendering.py`, `tests/unit/test_processing.py`
- **Audit issues:** Error handling in the 14-step pipeline
- **Key fix:** Proper status transitions (pending → processing → ready/failed)

### Phase 5: Search, Tags, Organization
- **Iterations:** 2 (implement → audit → fix → re-audit → PASS)
- **Files:** `src/search/routes.py`, `src/tags/routes.py`, `tests/unit/test_search.py`, `tests/unit/test_tags.py` (note: `src/tags/models.py` was later deleted — Pydantic models were inlined into routes)
- **Audit found 4 MEDIUM issues:**
  1. Search not ordered by FTS5 relevance — used `ORDER BY created_at DESC` instead of `rank`
  2. `reading_status` enum mismatch — Python had `"finished"` not in DB CHECK constraint
  3. Awkward article-tag URL paths — `/api/tags/articles/{id}/tags` instead of `/api/articles/{id}/tags`
  4. Missing tag deletion endpoint
- **Key fixes:**
  - Changed search SQL to `INNER JOIN articles_fts` with `ORDER BY articles_fts.rank`
  - Removed `"finished"` from `_VALID_READING_STATUSES`
  - Split `tags/routes.py` into two routers (`router` + `article_tags_router`)
  - Added `DELETE /{tag_id}` endpoint
  - Made `idx_tags_user_name` a UNIQUE index

### Phase 6: Listen Later (TTS)
- **Iterations:** 2 (implement → audit → fix → re-audit → PASS)
- **Files:** `src/tts/routes.py`, `src/tts/processing.py`, `tests/unit/test_tts.py`
- **Audit found 3 MEDIUM issues:**
  1. `audio_status` used `'processing'` instead of spec's `'generating'`
  2. Duplicated `_get_user_article` helper (copy in tts/routes.py)
  3. Audio endpoint didn't check `audio_status='ready'` before streaming
- **Key fixes:**
  - Changed `"processing"` to `"generating"` in processing.py
  - Imported `_get_user_article` from `articles.routes` instead of duplicating
  - Added `audio_status` check returning 409 for pending/generating, 404 for others

### Phase 7: Frontend PWA
- **Iterations:** 2 (implement → audit → fix → re-audit → PASS)
- **Files:** `assets/index.html`, `assets/static/app.js`, `assets/static/style.css`, `assets/manifest.json`, `assets/sw.js`, `assets/bookmarklet.js`, `assets/static/icon-192.png`, `assets/static/icon-512.png`
- **Audit found 4 HIGH + 7 MEDIUM issues:**
  1. **HIGH:** No UI to assign tags to articles in reader view
  2. **HIGH:** XSS via `javascript:` URLs in markdown renderer
  3. **HIGH:** Bookmarklet used `location.origin` (wrong origin when run on external sites)
  4. **HIGH:** PWA icon files missing (referenced but not created)
  5. **MEDIUM:** Audio skip forward was 30s instead of spec's 15s
  6. **MEDIUM:** No tags shown on article cards
  7. **MEDIUM:** `is_favorite` query param sent as boolean `true` instead of integer `1`
  8. **MEDIUM:** No proactive offline content caching
  9. **MEDIUM:** Reader uses `markdown_content` field (acceptable approach)
  10. **MEDIUM:** No `share_target` in PWA manifest
  11. **MEDIUM:** Reading progress scroll position not restored on re-open
- **Key fixes:**
  - Added tag picker UI (dropdown + Add button) in reader view
  - Sanitized `javascript:` protocol URLs in markdown link/image rendering
  - Bookmarklet now uses `__SITE_URL__` placeholder; app generates correct bookmarklet with origin baked in
  - Generated placeholder PNG icons programmatically
  - Changed skip forward to 15s, `is_favorite` to integer `1`
  - Added `share_target` to manifest with GET method and URL params
  - Added scroll position restoration with `setTimeout` in reader
  - Added `CACHE_ARTICLES` message to SW for proactive offline caching of unread articles

### Phase 8: Observability
- **Iterations:** 1 (implement → audit → PASS)
- **Files:** `src/observability.py` (ASGI middleware), `src/wide_event.py` (event accumulator), `tests/unit/test_observability.py`, `tests/unit/test_wide_event.py`
- **Audit: PASS on first review** (5 LOW issues only)
- This was the cleanest phase — the wide events middleware pattern is well-defined and self-contained

### Phase 9: Edge Case Hardening
- **Iterations:** 1 (implement → audit → PASS)
- **Files:** 20 files changed (12 source, 7 test files, 1 new migration)
- **17 edge cases fixed:** 3 CRITICAL, 6 HIGH, 8 MEDIUM
- **Audit: PASS on first review** — all 17 fixes verified correct
- **Key fixes:**
  - FTS5 query injection sanitization
  - Request body size limits on all input fields
  - TTS idempotency, markdown stripping, text truncation, and streaming
  - SSRF protection blocking private/internal network URLs
  - Unique constraint on (user_id, original_url) to prevent duplicate race condition
  - Queue retry enabled for transient errors
  - Session revocation on ALLOWED_EMAILS change
  - Bookmarklet changed from fetch() to window.open() for SameSite cookie compatibility

## Final Stats

- **Total test count:** 877 unit tests + 32 integration tests passing (909 total)
- **Lint:** `ruff check src/ tests/` — all clean
- **Total audit iterations:** 17 (9 initial audits + 8 re-audits after fixes)
- **Phases 8 and 9 passed audit on first attempt** — all others required one fix cycle
- **UI audit (v0.2):** 17 LLM tells identified and fixed; Worker-first routing adopted

## Patterns Discovered

### 1. Enum/Status String Mismatches Are Common
Three separate phases had issues with status strings not matching between layers:
- Phase 5: `"finished"` in Python, not in DB CHECK constraint
- Phase 6: `"processing"` vs spec's `"generating"` for audio_status
- These are easy to introduce and hard to catch without an audit

**Lesson:** Define status enums in one place and reference them everywhere. Database CHECK constraints are the source of truth.

### 2. Code Duplication Creeps In Across Phase Boundaries
Phase 6's TTS routes duplicated `_get_user_article` from Phase 3's articles routes. When phases are implemented by different agents (or at different times), helper functions get copied instead of imported.

**Lesson:** Before writing a helper, grep the codebase for similar logic. Import from the original module.

### 3. FTS5 Queries Need Explicit Relevance Ordering
The initial search implementation used a subquery approach with `ORDER BY created_at DESC`, completely ignoring FTS5's `rank` column. The JOIN-based approach is required for relevance ordering.

**Lesson:** When using FTS5, always use `INNER JOIN` with `ORDER BY rank` — never use a subquery that discards ranking information.

### 4. URL Routing Hierarchy Matters
Phase 5's first attempt mounted all tag-related routes under `/api/tags`, creating awkward paths like `/api/tags/articles/{id}/tags`. Splitting into two routers (`router` for tag CRUD, `article_tags_router` for article-tag associations) produced cleaner RESTful paths.

**Lesson:** Design URL hierarchy before implementation. Resources that belong to a parent should be nested under the parent's path.

### 5. Frontend Security (XSS) Requires Active Sanitization
The markdown renderer correctly escaped all content via `escapeHtml()` first, but then regex replacements for links and images re-introduced `href`/`src` attributes that could contain `javascript:` URLs.

**Lesson:** Escaping input is necessary but not sufficient. Any post-processing that creates HTML attributes from user content needs additional protocol sanitization.

### 6. Bookmarklets Run in Foreign Origins
The bookmarklet used `location.origin` assuming it would be the Tasche server, but bookmarklets execute in the context of the page they're invoked on. This is a fundamental misunderstanding of bookmarklet execution context.

**Lesson:** Bookmarklets must have the target server URL hardcoded (or templated) since `location` refers to the current page, not the app that generated the bookmarklet.

### 7. PWA Requirements Are Easy to Miss
Missing icon files, `share_target`, and `scope` in the manifest are easy to overlook because the app still "works" without them — but PWA installability and mobile integration depend on these being correct.

**Lesson:** Use the PWA checklist: manifest (name, icons, display, scope, share_target), service worker (install, activate, fetch strategies), and app shell (meta tags, manifest link).

### 8. The Audit Loop Catches What Tests Don't
All tests passed throughout (at the time of each audit), yet the audit found significant issues (XSS, wrong status strings, missing features, broken bookmarklet). Unit tests verify behavior; audits verify intent and completeness against the spec.

**Lesson:** Tests and audits serve different purposes. Tests catch regressions; audits catch design mistakes, missing features, and spec deviations.

### 9. Wide Events Middleware Is a Clean, Self-Contained Pattern
Phase 8 (Observability) was the only phase to pass audit on the first attempt. The wide events pattern — build one JSON event per request, emit in `finally` — is well-defined, has clear boundaries, and is easy to test in isolation.

**Lesson:** Self-contained middleware patterns with clear inputs/outputs are easier to get right than cross-cutting features that touch multiple modules.

### 10. The FFI Boundary Layer Pays Off
The `wrappers.py` module (`_to_py_safe`, `_to_js_value`, `d1_first`, etc.) established in Phase 1 prevented JsProxy leakage throughout the codebase. Every phase that interacted with D1, R2, or KV used these wrappers consistently.

**Lesson:** Invest in the FFI boundary layer early. Convert at the boundary, use native Python types everywhere else.

---

## Edge Case Hardening — Patterns Discovered

### 11. FTS5 Is Its Own Query Language
FTS5's `MATCH` clause accepts operators (`OR`, `NOT`, `NEAR`, `*`, column filters). Passing unsanitized user input is essentially query injection. Unlike SQL injection (prevented by parameterized queries), FTS5 injection happens *within* the parameter value.

**Lesson:** Always sanitize FTS5 input — strip operators and quote each word as a literal. Parameterized queries are necessary but not sufficient for FTS5.

### 12. Input Validation Is Not Optional, Even for Internal APIs
The initial implementation had no field length limits anywhere. A single POST request could insert megabytes of text into D1 and FTS5. This is trivially exploitable.

**Lesson:** Add length limits on every text field at the API boundary. Define them in the spec. Make the schema enforce them with CHECK constraints too.

### 13. Idempotency Must Be Explicit for Expensive Operations
The TTS endpoint would happily enqueue duplicate Workers AI jobs costing real money. Queue consumers had no deduplication, so every click = another AI invocation.

**Lesson:** Expensive operations need idempotency checks. Check the current state before triggering work. Return the existing result if work is already done or in progress.

### 14. TTS Needs Text Preprocessing
Sending raw markdown to a speech model produces garbled output ("hash hash Introduction, asterisk asterisk bold"). The content pipeline converts HTML→Markdown for storage, but TTS needs a third format: plain text.

**Lesson:** Different consumers need different content formats. Spec the transformations explicitly: HTML for reading, Markdown for FTS5, plain text for TTS.

### 15. Error Categorization Determines Retry Behavior
The queue handlers caught all exceptions and ACKed every message, preventing Cloudflare Queues' built-in retry from ever firing. Transient errors (network timeouts) and permanent errors (invalid content) need different handling.

**Lesson:** Categorize errors explicitly. Transient errors should propagate for infrastructure retry. Permanent errors should be caught and recorded. This distinction must be designed, not left to a catch-all.

### 16. Delete in the Right Order
Deleting the D1 reference before R2 content creates orphaned objects with no way to find or clean them. The reverse order is safe: if R2 deletion fails, the D1 row still exists as a reference for retry.

**Lesson:** When cleaning up across multiple stores, delete the data first, then the reference. Never delete the reference first.

### 17. SameSite Cookies Break Cross-Origin Bookmarklets
Bookmarklets using `fetch()` with `credentials: 'include'` fail silently when the session cookie is `SameSite=Lax`. The browser won't send the cookie on a cross-origin fetch. Using `window.open()` for a top-level navigation is the correct pattern.

**Lesson:** Cross-origin integrations (bookmarklets, browser extensions, share targets) must account for SameSite cookie policies. Top-level navigations work; cross-origin fetches don't.

### 18. SSRF Is a Server-Side Concern for URL-Based Services
Any service that fetches URLs on behalf of users is a potential SSRF vector. The processing pipeline will happily fetch `http://169.254.169.254/` (cloud metadata) or `http://localhost:8080/admin` if not blocked.

**Lesson:** Block private/internal network URLs at the validation layer. This should be in the spec, not discovered after implementation.

---

## What We Would Spec Differently

### 1. Define Input Constraints Explicitly
The spec described fields (title, url, notes, tag name) but never specified maximum lengths. This led to no validation at all in the initial implementation. Every text field should have a maximum length in the spec.

### 2. Specify Content Formats Per Consumer
The spec described HTML→Markdown conversion but didn't specify what format TTS should consume. "Plain text stripped of markup" should have been an explicit pipeline output.

### 3. Include SSRF Protection in URL Handling Requirements
The spec described URL validation (scheme, format) but not SSRF protection. Blocking private network addresses should have been a stated requirement.

### 4. Specify Idempotency Semantics for Expensive Operations
The spec described "POST to enqueue TTS" but not what happens on repeated calls. Idempotency behavior (409 if in progress, 200 if already done) should be specified for any operation that costs money or takes significant time.

### 5. Specify Error Retry Categories
The spec mentioned "Queues: automatic retry with exponential backoff" but didn't specify which errors should be retried vs. permanently failed. This distinction is critical for queue-based architectures.

### 6. Specify Deletion Order Across Stores
The spec described article deletion but not the order of operations across D1 and R2. "Delete storage first, then references" should be a stated architectural principle.

### 7. Specify FTS5 Input Sanitization
The spec described FTS5 search but not how to handle FTS5-specific query syntax in user input. This is a domain-specific security concern that should be called out explicitly.

### 8. Specify Cross-Origin Cookie Behavior for Integrations
The spec described the bookmarklet and share target but not how authentication works cross-origin. SameSite cookie behavior should have been analyzed when specifying the bookmarklet flow.

---

## Structuring Specs for Coding Agents

The following lessons were discovered after v0.1.0 when reviewing why the project was initially perceived as "backend-complete, frontend-missing" — even though a frontend MVP existed. The root cause was spec structure, not implementation quality.

### 19. Vertical Slices Beat Horizontal Layers

The original spec had no explicit phases. The implicit structure was horizontal: "build all API endpoints" → "build the frontend." This encouraged depth-first backend work. When phases 1–6 were all backend, the frontend didn't appear until Phase 7 — meaning no user could actually use the app until 7/9ths of the work was done.

**Better structure:** Each milestone should be a vertical slice that delivers an end-to-end user capability across all layers (API, storage, frontend, tests). For example: "Save a URL and see it in a list" touches the API, D1, Queue, and a minimal list UI — all in one slice.

**Lesson:** Structure specs as vertical slices, not horizontal layers. A coding agent will complete whatever slice you define — if the slice is "build all API endpoints," that's exactly what you'll get, with no way for a user to verify the result.

### 20. Define "Done" as a User Journey, Not a Component

The original spec defined features by component ("Articles table has these fields", "FTS5 search uses INNER JOIN"). This made it easy to verify that code exists, but hard to verify that a user can actually accomplish anything.

**Better structure:** Each milestone should have a user journey sentence: *"I open Tasche, see article cards with thumbnails, tap one, and read it."* If the user can't perform this journey end-to-end, the milestone isn't done — regardless of how many tests pass.

**Lesson:** Acceptance criteria should be user journeys, not technical checklists. "all tests passing" is not the same as "a user can save and read an article."

### 21. Specify the Frontend Stack with the Same Precision as the Backend

The original spec gave the backend precise technology choices: FastAPI, D1, R2, KV, python-readability, httpx. The frontend got "Progressive Web App" with aspirational descriptions of offline caching and audio playback, but no framework, no file structure, no build tool, no component model.

A coding agent gravitates toward what's most precisely defined. The backend had SQL schemas, exact endpoint signatures, and status enum values. The frontend had prose paragraphs.

**Lesson:** Specify the frontend stack explicitly: framework (or explicit "vanilla JS" with rationale), routing approach, state management pattern, build tool, output directory, and file structure. A spec that's precise about the backend and vague about the frontend will produce exactly a backend.

### 22. Include UI Wireframes, Not Just Data Flow Diagrams

The original spec had excellent architecture diagrams (Mermaid flowcharts, ASCII system diagrams, data flow tables). But it had zero UI wireframes. A coding agent can implement an API from a schema, but it can't implement a UI from a description like "article cards show thumbnails."

**Better structure:** ASCII wireframes for each screen showing layout structure, component hierarchy, and what data appears where. These don't need to be pixel-perfect — they just need to make the agent's output verifiable.

**Lesson:** Include at least one wireframe per screen/view. Wireframes are to frontend specs what SQL schemas are to backend specs — they make the expected output concrete and verifiable.

### 23. Phased Milestones Need Explicit Dependency Graphs

The original spec had implicit phase references ("Phase 2: auth router", "Phase 5: tags router") embedded in code comments, but never defined a milestone list, dependency graph, or completion criteria. This meant there was no way to know what "v0.1.0" actually included, and no way to prioritize remaining work.

**Better structure:** A numbered milestone list where each milestone has: (1) a name, (2) a user journey as acceptance criteria, (3) a task table showing what to build in each layer, and (4) a dependency graph showing which milestones can run in parallel.

**Lesson:** Make milestones explicit. If they're not written down, they don't exist — and a coding agent will simply work through the spec top-to-bottom, building whatever it encounters first.

### 24. SPA Asset Routing Silently Swallows API Navigation Requests

The `not_found_handling: "single-page-application"` setting in Cloudflare Workers assets config intercepts ALL browser navigation requests (`Sec-Fetch-Mode: navigate`) to paths that don't match a file, and serves `index.html` instead. This silently broke `/api/auth/login` — the only API route accessed via an `<a>` tag rather than JavaScript `fetch()`.

Three things conspired to hide the bug:
1. **Unit tests bypass the asset layer** — TestClient talks directly to FastAPI, so the routing conflict is invisible.
2. **`curl` doesn't reproduce it** — curl doesn't send `Sec-Fetch-Mode: navigate`, so the asset layer doesn't intercept.
3. **Only one route was affected** — every other API call uses JS `fetch()`, which doesn't trigger navigation mode.

The fix was architectural: use the ASSETS binding so the Worker runs first and routes `/api/*` to FastAPI before the asset layer ever sees the request. SPA fallback is now explicit code in the Worker's `fetch()` handler, not an implicit config flag that can't distinguish API routes from page routes.

**Lesson:** When mixing API routes with SPA static assets on Cloudflare Workers, always use Worker-first routing (ASSETS binding). Never rely on `not_found_handling: "single-page-application"` — it cannot distinguish between client-side routes and server-side API endpoints. Test navigation flows with a real browser, not `curl`.

### 25. LLM-Generated UI Has Recognisable Tells

An audit of the Tasche frontend identified 17 telltale signs of LLM-generated UI: emoji as icons (render inconsistently across platforms), ornate ASCII-art CSS comment blocks, hand-rolled utilities that mimic Tailwind, unused CSS for features that were never implemented (modal styles with `window.confirm()` usage), marketing taglines as UI copy, installed-but-unused npm dependencies (`preact-router` in package.json while using a hand-rolled router), duplicate logic across components, hardcoded Apple HIG colour values, a hand-rolled markdown renderer that misses edge cases, and inline styles mixed with CSS classes.

The fixes: inline SVG icons (no dependency), `marked` library (8KB, handles all edge cases), removed unused deps, deduplicated shared logic, shifted the colour palette, stripped ornate comments.

**Lesson:** LLM-generated UIs are functional but generic. The strongest tells are: emoji as icons, marketing copy where functional text belongs, generating "complete" design systems (modals, utilities) that go unused, and including dependencies that are never imported. An audit pass specifically looking for these patterns is worthwhile before shipping.

### 26. Cookie `secure` Flag Must Match the Protocol

The session cookie was set with `secure: True` unconditionally, which means browsers reject it over plain `http://localhost`. This makes local development impossible — OAuth completes but the session cookie is silently dropped.

**Lesson:** Derive the `secure` flag from `SITE_URL` — `True` when it starts with `https://`, `False` otherwise. This is a single line of code but completely blocks local testing if missed.

### 27. 480 Tests Passing, Core Workflow Broken: The Runtime Gap

Three separate bugs prevented the fundamental user journey ("save a URL and read it later") from ever working in production, despite hundreds of unit tests, integration tests, and Playwright smoke tests all passing:

1. **Queue handler signature mismatch** — Workers passes `(batch, env, ctx)` to the queue handler, but the code only accepted `(self, batch)`. Result: `TypeError: takes 2 positional arguments but 4 were given`. The queue consumer silently crashed on every message.

2. **python-readability uses `eval()`** — The content extraction library loads Mozilla Readability via `js.eval()`, which V8 isolates block. Result: `EvalError: Code generation from strings disallowed for this context`. The entire extraction pipeline was dead on arrival.

3. **Python `None` → JS `undefined` in D1 bind** — D1 rejects `undefined` values; it needs `null`. Every nullable field in the processing pipeline's UPDATE statement would crash. Result: `D1_TYPE_ERROR: Type 'undefined' not supported`.

All three bugs share the same root cause: **unit tests run in CPython, but the code runs in Pyodide inside V8 isolates**. These are fundamentally different runtimes:
- CPython has `eval()`. Workers V8 doesn't.
- CPython's `None` is `None`. Pyodide's `None` is JS `undefined`.
- CPython method dispatch is standard. Workers' entrypoint passes extra arguments to `queue()`.

The test suite verified *correctness of logic* but never verified *reachability in the runtime*. The mocked D1, mocked R2, mocked queue, and mocked HTTP meant the tests were testing a simulation of Cloudflare Workers, not actual Cloudflare Workers.

**What would have caught these earlier:**
- A single live smoke test that submits a URL and polls until `status=ready`
- This should have been the very first test written, even before unit tests
- The 14-step processing pipeline is the core value proposition — if it doesn't work, nothing else matters

**Lesson:** For platform-specific runtimes (Pyodide, Workers, WASM), unit tests in the host language are necessary but not sufficient. A live integration test against the real platform must exist before any other testing. "All tests pass" means nothing if the tests don't exercise the actual runtime. The most important test is: can a user complete the primary workflow end-to-end on the real platform?

### 28. Specs That Are Precise About the Backend and Vague About the Frontend Will Get Exactly a Backend

This is the meta-lesson that encompasses all the above. The original spec had:
- 14-step processing pipeline with numbered steps ← precise
- SQL schemas with field types and max lengths ← precise
- Endpoint tables with methods and purposes ← precise
- "PWA with offline support" ← vague
- "Audio player with play/pause, skip, speed" ← vague
- "Article cards show thumbnails" ← vague

The implementation perfectly reflected this asymmetry: the backend was robust, tested, and hardened. The frontend existed but was perceived as incomplete because the spec never defined what "complete" looked like for the frontend.

**Lesson:** Measure the precision of your spec across all layers. If the backend section has tables, schemas, and exact values, the frontend section needs wireframes, component specs, and state management patterns. Asymmetric precision produces asymmetric results.

### 29. Python `bytes` Cannot Cross the FFI Boundary to R2

R2's `.put()` method accepts JS types: `ReadableStream`, `ArrayBuffer`, `ArrayBufferView`, `string`, or `Blob`. Python `bytes` are **none of these** — they cross the Pyodide FFI as an opaque PyProxy that R2 rejects with `TypeError: parameter 2 is not of type 'ReadableStream or ArrayBuffer or ArrayBufferView or string or Blob'`.

The fix is explicit conversion: `to_js(data)` converts Python `bytes` to a JS `Uint8Array`. `str` values work natively because JS strings are a primitive type that Pyodide passes through automatically.

This bug affected every binary write: image storage, screenshot storage, and TTS audio storage. It went undetected because unit tests use mock R2 objects that accept any Python type.

**The full Pyodide→JS type compatibility matrix:**

| Python type | JS result | R2 `.put()` | D1 `.bind()` | KV `.put()` |
|---|---|---|---|---|
| `str` | JS string | OK | OK | OK |
| `int` | JS number | N/A | OK | N/A |
| `float` | JS number | N/A | OK | N/A |
| `bool` | JS boolean | N/A | OK | N/A |
| `None` | JS `undefined` | N/A | **FAILS** (use `d1_null()`) | N/A |
| `bytes` | PyProxy | **FAILS** (use `to_js_bytes()`) | N/A | **FAILS** (use `to_js_bytes()`) |
| `dict` | PyProxy/Map | **FAILS** (use `_to_js_value()`) | N/A | N/A |
| `list` | PyProxy | **FAILS** (use `_to_js_value()`) | N/A | N/A |

**Lesson:** Every Python type that isn't a primitive (`str`, `int`, `float`, `bool`) needs explicit conversion before passing to a Cloudflare binding. Centralise all writes through Safe* wrappers (`SafeR2.put()`, `SafeD1Statement.bind()` with `d1_null()`, `SafeQueue.send()` with `_to_js_value()`) so the conversion happens in exactly one place. Mock-based tests cannot catch these failures — only a live smoke test can.

### 30. The FFI Boundary Is a Write Problem, Not Just a Read Problem

Earlier lessons (1, 10, 27) focused on the JS→Python direction: converting D1 results, R2 responses, and queue messages from JsProxy to native Python types. But the Python→JS direction is equally treacherous:

- `None` → `undefined` (not `null`) breaks D1 `.bind()`
- `bytes` → PyProxy (not `Uint8Array`) breaks R2 `.put()`
- `dict` → Map (not Object) breaks queue `.send()`

The centralised boundary layer (`wrappers.py`) must handle **both directions**. The Safe* wrapper classes (`SafeD1`, `SafeR2`, `SafeKV`, `SafeQueue`, `SafeAI`, `SafeReadability`) encapsulate both read and write conversions. Low-level helpers like `d1_null()`, `to_js_bytes()`, and `_to_js_value()` handle specific type conversions, while the Safe* wrappers compose them into a seamless interface.

**Lesson:** Design the FFI boundary layer as a bidirectional gateway. For each Cloudflare binding, there should be wrapper helpers for both reading (JS→Python) and writing (Python→JS). If you only wrap reads, writes will eventually break in production.

### 31. Miniflare Queue Consumer Is Unreliable

The same `process_article()` code that works correctly when called inline via the fetch handler silently fails when invoked through Miniflare's queue consumer in local development. No errors appear in logs — the queue messages simply don't trigger the handler, or the handler runs but produces no visible output.

This was discovered by building a `POST /api/articles/{id}/process-now` endpoint that runs the processing pipeline synchronously in the request handler. Both test articles processed successfully via this endpoint but had never processed via the queue.

**Lesson:** Don't trust Miniflare's queue consumer for verifying queue-based workflows. Build an inline processing endpoint (`process-now`) for local development and debugging. The queue path must be verified on the real Cloudflare Workers runtime.

### 32. python-readability Cannot Run in Cloudflare Workers

`python-readability` calls `js.eval()` to load Mozilla Readability JS. Cloudflare Workers blocks `eval()` with `EvalError: Code generation from strings disallowed`. `js.Function()` is also blocked (equivalent to eval). `allow_eval_during_startup` is already the default and doesn't help. Eager-importing exceeds startup time limits.

Every Python readability library that provides high-quality extraction requires lxml (a C extension unavailable in Pyodide). The only Pyodide-safe option is BeautifulSoup heuristic extraction, which lacks Readability's scoring algorithm.

**Solution:** Service Binding to a JS Worker. Deploy a separate JavaScript Worker that bundles `@mozilla/readability` + `linkedom`, call via Service Binding RPC from the Python Worker. The RPC is in-process (~1-5ms), gives 100% Readability fidelity, and keeps the BS4 extractor as fallback.

**Lesson:** When a Python library can't run in Pyodide due to runtime restrictions, consider Service Bindings to a JS Worker rather than porting the algorithm. JS Workers can use npm packages natively, and Service Binding RPC is in-process communication — not a network call. This pattern applies to any JS-native functionality that Python can't replicate in WebAssembly.

### 33. Nearly Every Python Content Extraction Library Requires lxml

Trafilatura (F1=0.958), Newspaper4k (F1=0.949), Goose3 (F1=0.896), ReadabiliPy, readability-lxml, jusText, Inscriptis — all require lxml as a hard dependency. lxml is a C extension built on libxml2/libxslt, making it incompatible with Pyodide/WebAssembly.

The only Pyodide-safe options found: BoilerPy3 (zero deps, but text-only output — no HTML), article-extractor (pure Python, unproven), and hand-rolled BeautifulSoup heuristics.

**Lesson:** Before choosing a Python library for a Pyodide/WASM project, check the full dependency tree for C extensions — not just the top-level deps. Most "pure Python" claims are aspirational. The lxml dependency is so pervasive in HTML processing that it effectively eliminates the entire Python content extraction ecosystem from Pyodide use.

### 34. Commit History Reveals the Testing-Simulation Gap

Across 25 commits over 8 days, 11 were corrective (fix-to-feature ratio nearly 1:1). The core user journey was broken for 7 of 8 days while test counts kept climbing. Three fatal runtime bugs hid behind CPython tests with mock Cloudflare bindings that accepted wrong types.

The most damning statistic: the primary user journey ("save a URL, read it later") didn't work until commit 20 of 25.

**What was avoidable:** All three fatal bugs (queue signature, eval() restriction, None→undefined) would have been caught by a single live smoke test on day 1. Feature churn (notes, listen_later — added then removed within minutes) could have been prevented by tighter spec review. Three FFI centralization commits could have been one if wrappers.py was designed bidirectional from the start.

**What was inherent:** The Pyodide FFI type matrix is genuinely non-obvious (no docs say None→undefined). JsNull being distinct from None and not a JsProxy is a platform gotcha. Content extraction fallback (BS4 replacing readability due to eval()) is an inherent Workers constraint.

**Lesson:** For novel runtimes, deploy to the real platform on day 1 with one smoke test. Every subsequent fix commit in this project was downstream of not doing this. Tests measure correctness of logic in the wrong runtime; smoke tests measure reachability of function in the right one.

### 35. Pyodide Cold Start Cancels First Queue Invocation

Python Workers using Pyodide/WASM have a heavy cold start (~1100ms CPU). When a queue message hits a cold isolate, the Workers runtime cancels the first invocation (`outcome: "canceled"`, zero logs, zero exceptions). The automatic retry then succeeds (~600ms CPU) because the isolate is warm. This means every article's first queue attempt silently fails and processes on the second try, adding up to `max_batch_timeout` seconds of perceived delay.

**Why it looks broken:** The article stays in `pending` for 30-60+ seconds. `process-now` (which reuses a warm HTTP isolate) works instantly, making the queue look broken by comparison. `curl` testing sees the same delay.

**Lesson:** Pyodide queue consumers pay a cold-start tax that Cloudflare's retry mechanism absorbs silently. Don't chase this as a bug — it's an inherent platform cost. If latency matters, consider: (1) keeping isolates warm with scheduled pings, (2) moving time-critical work to the HTTP handler path, (3) accepting the delay as a background processing tradeoff.

### 36. Safe* Wrappers Must Guard Reads, Not Just Writes

`SafeR2.get()` returned raw JsNull when an R2 key didn't exist, because the wrapper only protected writes (`bytes→Uint8Array`, `None→null`) and passed reads through unchanged. Code checking `if raw_obj is not None` missed JsNull, crashing `process_article()` with `AttributeError: 'JsNull' object has no attribute 'text'`.

The `_is_js_null_or_undefined()` helper existed in `wrappers.py` but was never called in `SafeR2.get()`, `SafeKV.get()`, or `SafeAI.run()`. The inline fix in `processing.py` (`type(raw_obj).__name__ != "JsNull"`) was correct but in the wrong place — it should be in the wrapper so every caller is protected.

**Lesson:** Every Safe* wrapper method that returns a value from JS must convert JsNull/undefined→None on the way out. The FFI boundary layer must be bidirectional: convert on writes (Python→JS) AND on reads (JS→Python). If you only guard one direction, the other will eventually crash in production.

---

## Commit History Analysis — Patterns of Rework

An analysis of 53 commits over 11 days (2026-02-15 to 2026-02-25) reveals systematic patterns of rework that could be prevented with better tooling and processes.

### Rework Statistics

- **53 total commits**, of which **17 are corrective** (fix-to-feature ratio: 1:2.1)
- **Top 5 most modified files** (changes across all commits):
  1. `src/articles/routes.py` — 21 modifications
  2. `src/articles/processing.py` — 20 modifications
  3. `tests/unit/test_articles.py` — 13 modifications
  4. `tests/conftest.py` — 12 modifications
  5. `src/tts/processing.py` — 12 modifications
  6. `src/entry.py` — 12 modifications
  7. `src/wrappers.py` — 10 modifications

### Pattern 1: FFI Boundary Centralization Required 7 Commits

The FFI boundary layer (`wrappers.py`) was touched in 10 separate commits, with 7 specifically about centralization or fixing FFI gaps:

1. `12575c6` — Harden FFI boundary, fix D1 result wrapper bug
2. `a71559f` — Fix JsNull detection and None→null conversion
3. `fd5c262` — Centralize all JS/Python FFI operations
4. `a28bc2d` — Route all R2 writes through r2_put() boundary helper
5. `ea08511` — Centralize FFI boundary and update all callers
6. `63e6c41` — Fix FFI boundary gaps and implement wide events
7. `f47953c` — Add FFI boundary checker script (still in `scripts/agent-tools/`)

The first three happened on consecutive days (Feb 20-22), with each commit discovering a new category of FFI leaks that the previous commit missed. The root cause: the boundary was designed incrementally (reads first, then writes, then null handling) instead of comprehensively from the start.

### Pattern 2: Article Processing Pipeline Is the Perpetual Hotspot

`src/articles/processing.py` and `src/articles/routes.py` together account for 41 modifications — nearly one change per commit on average. This is because:

- The processing pipeline is the longest code path (14 steps across 6 modules)
- It touches every Cloudflare binding (D1, R2, Queue, AI, Browser Rendering)
- It is the primary user journey and therefore the first thing tested after any change
- New features (favicons, auto-tagging, listen-later) all add steps to this pipeline

### Pattern 3: Feature Churn — Added Then Removed

Several features were added and later removed, each requiring two commits:

- **Notes field**: Added in initial impl, removed in `c0df59d` ("no UI specified, unnecessary complexity")
- **listen_later column**: Added then removed in `f5a79d2`
- **Highlights view**: Added in `8d05ec2`, removed in `def6c02`
- **Feeds view**: Added in `8d05ec2`, removed in `def6c02`
- **Review view**: Added in `8d05ec2`, removed in `def6c02`

Each add-then-remove cycle required changes to routes, tests, frontend, and the service worker.

### Pattern 4: Content Extraction Was Rearchitected 3 Times

1. **Initial**: python-readability (crashed due to `eval()` restriction)
2. **Fallback**: BeautifulSoup heuristic extractor (lower quality)
3. **Final**: Readability Service Binding to JS Worker + BS4 fallback

Each pivot required changes to `extraction.py`, `processing.py`, test mocks, and `wrangler.jsonc`.

### Pattern 5: CSS/Design Fixes Cluster After Feature Additions

UI-related commits cluster after major feature additions:
- `8d05ec2` (16 features) was immediately followed by `9dc0235` (favicon fixes), then `ba0061c` (card design + markdown crash), then `7220f81` (compact cards)
- Each feature addition changed `app.css`, `ArticleCard.jsx`, and `Library.jsx`

### Diagnostic Tools Created

Based on these patterns, four diagnostic scripts were created in `scripts/agent-tools/`:

1. **`check_ffi_boundary.py`** — Validates that all FFI operations go through Safe* wrappers; detects raw JsProxy usage, direct `.to_py()` calls, and unsafe null handling outside the boundary layer.

2. **`check_pyodide_pitfalls.py`** — Detects common Pyodide/Workers runtime pitfalls: sync handlers (must be async), `eval()`/`Function()` usage, direct `import js` outside boundary modules, `None` comparisons that miss JsNull, and module-level PRNG calls.

3. **`check_handler_consistency.py`** — Audits API route handlers for structural consistency: verifies env access patterns, authentication dependencies, error handling, and response format consistency across all route files.

4. **`trace_tts_pipeline.py`** — Traces the TTS audio generation pipeline end-to-end for debugging truncation and FFI issues.

### 37. Unit Tests That Only Exercise the Fallback Path Give False Confidence

All 100 `test_wrappers.py` tests ran with `HAS_PYODIDE = False`, exercising only the CPython fallback code path. The actual production code — the `if HAS_PYODIDE:` branches that convert JsProxy→dict, detect JsNull, convert None→null, and transform bytes→Uint8Array — had zero test coverage. Every historical FFI production bug (JsNull leak, None→undefined in D1, bytes→PyProxy in R2) lived in these untested branches.

The core issue: the module uses a feature flag (`HAS_PYODIDE`) to switch between two completely different implementations. Testing only one side of the flag is equivalent to testing a different program than the one that runs in production.

**Solution:** Create JS-type fakes (`FakeJsProxy`, `JsNull` sentinel, `FakeJsModule` with `.undefined`/`.JSON`/`.Object`) that simulate Pyodide's types in CPython. Monkeypatch `HAS_PYODIDE=True` plus the three module globals (`JsProxy`, `js`, `to_js`) to point at the fakes. This forces every `if HAS_PYODIDE:` branch to execute with types that behave like the real thing — `type(x).__name__ == "JsNull"` returns True, `isinstance(x, JsProxy)` works, `.to_py()` returns the wrapped value.

**Rule for future sessions:** When a module has a feature flag that switches between a production path and a fallback path, both paths need dedicated tests. The fallback-path tests verify graceful degradation; the production-path tests (using fakes + monkeypatching) verify the actual conversion logic. If the test file only imports the module once without any patching, it's only testing one side.

### 38. E2E Tests Against Real Infrastructure Catch What Three Tiers of Mocks Cannot

Adding 10 E2E smoke tests against the live staging Worker (real D1, real R2, real Pyodide FFI) immediately caught two issues that hundreds of unit tests missed:

1. **Wrong search endpoint path.** Unit tests used `/api/articles/search` — which always returned a mock-backed 200. The real endpoint is `/api/search`. The unit test mocks never validated the route prefix because the mock router accepted any path the test called. On real infrastructure, this is a 404.

2. **Wrong assumption about duplicate URL behavior.** Unit tests for `check_duplicate()` used a mock D1 that returned a pre-set result, and the test asserted 409. But the real code path on duplicate URL is: find existing → reset status to pending → re-process → return 201 with the *same* article ID. The 409 only fires on a race condition (unique constraint violation). The mock-based test verified the wrong contract.

Both bugs share the same root cause: **mock-based tests verify the test author's mental model of the system, not the system itself.** When the mental model is wrong (wrong URL path, wrong status code for duplicates), the mock obligingly returns whatever the test expects.

Comparing to planet_cf's three-tier strategy (unit→integration→E2E against real Workers), the pattern is clear: each tier catches a different class of bug. Unit tests catch logic errors. FFI contract tests (monkeypatched fakes) catch conversion bugs. E2E tests against real infrastructure catch routing, integration, and contract mismatches. Removing any tier leaves a blind spot.

**What the E2E tests confirmed works correctly on real Cloudflare:**
- Full article lifecycle: create → process-now → read → update → delete
- D1 nullable fields return as JSON `null` (not string "undefined")
- R2 content storage works (bytes cross the FFI correctly)
- FTS5 search on real D1 returns results
- Tag creation, assignment, and filtering work end-to-end
- Worker-first routing correctly sends `/api/*` to FastAPI, not the SPA asset layer

**Lesson:** For platform-specific runtimes, E2E tests against real infrastructure are not optional — they are the only tier that validates the actual contract between your code and the platform. Mock-based tests at any sophistication level (including monkeypatched FFI fakes) can only verify that your code works *if* your assumptions about the platform are correct. E2E tests verify the assumptions themselves.

### 39. TTS Audio Truncation: Two Bugs at Different Pipeline Stages

TTS-generated audio served as 3.4KB (0.57 seconds) when it should have been 1.4–2.6MB (minutes long). Investigation revealed **two independent bugs** at different pipeline stages.

**Bug 1 (theory disproved): `to_js(bytes)` Wasm memory views.** `pyodide.ffi.to_js(bytes)` creates a `Uint8Array` *view* into Wasm linear memory — not a copy. In theory, `memory.grow()` could detach the backing `ArrayBuffer` during async JS APIs like `r2.put()`. A `.slice()` after `to_js()` was added as a defensive fix. **However, empirical testing on staging (2026-02-25) disproved this:** removing `.slice()` and stress-testing with 5 articles + TTS produced zero corruption. The reason: Python yields to JS during `await`, so no Python allocations (and thus no `memory.grow()`) occur while JS APIs are in flight. The `.slice()` was removed.

**Bug 2 (actual serving root cause): Cloudflare Workers ASGI adapter truncates StreamingResponse.** The ASGI adapter for Python Workers only consumes the **first yielded chunk** from async generators used in `StreamingResponse`. The audio endpoint used `StreamingResponse(stream_r2_body(...))` which yielded R2 body chunks (first chunk: 3,417 bytes, then 4KB chunks). The adapter consumed only the first 3,417-byte chunk and closed the response. **Fix:** Replace `StreamingResponse` with `Response` — read all body chunks via `body.getReader()` into memory, join them, and return as a single `Response`.

**Diagnostic journey (5 hypotheses tested, 4 wrong):**
1. ~~`arrayBuffer()` truncation on ReadableStream~~ — Workers AI streams don't even have `arrayBuffer`
2. ~~`to_py_bytes()` truncation~~ — conversion was correct
3. ~~Workers AI rate limiting~~ — all chunks returned full audio
4. ~~`to_js(bytes)` Wasm view detachment~~ — R2 read-back verified 2.6MB stored correctly
5. **ASGI adapter StreamingResponse truncation** — the adapter only consumed first yielded chunk

**Key diagnostic: `X-R2-Size` header.** Adding `X-R2-Size: {r2_size}` to the response revealed R2 had 2,684,016 bytes but `Content-Length` was 3,417. The write was correct; only serving was broken.

**Lesson 1:** In Pyodide, `to_js(bytes)` views do NOT need `.slice()` — Python yields to JS during `await`, preventing `memory.grow()` from firing mid-operation. Don't add defensive copies without empirical evidence of corruption.

**Lesson 2:** Never use `StreamingResponse` with async generators in Cloudflare Python Workers — the ASGI adapter silently truncates to the first yielded chunk. Use `Response` with the full body instead.

**Lesson 3:** When diagnosing a data pipeline bug, trace values at **every stage boundary** (write vs. serve, not just generate vs. store). Adding a read-back verification after R2 write immediately separated "write bug" from "serve bug."

---

## Frontend Quality Gates — ESLint, Vitest, Preact

### 40. ESLint 10 Breaks jsx-a11y (Pin to ESLint 9)

`npm install eslint` resolves to ESLint 10, but `eslint-plugin-jsx-a11y@6.x` declares `peerDependencies: { eslint: "^3 || ^4 || ^5 || ^6 || ^7 || ^8 || ^9" }`. The install succeeds with warnings, but the plugin may fail at runtime with version-gated code paths.

**Fix:** Pin `eslint@^9` and `@eslint/js@^9` explicitly. Don't rely on `npm install eslint` resolving to a compatible major version.

**Lesson:** When adding ESLint plugins, check their `peerDependencies` range before installing ESLint itself. The ecosystem lags behind major ESLint releases — most plugins support up to v9 as of early 2026.

### 41. Preact Uses HTML Attribute Names, Not React's camelCase

`eslint-plugin-react`'s `react/no-unknown-property` rule flags ~100+ false positives in a Preact codebase because Preact uses standard HTML/SVG attribute names (`class`, `for`, `stroke-width`, `stroke-linecap`, `fill-rule`, `clip-rule`, `text-anchor`, `dominant-baseline`, `autocomplete`, `autofocus`) while the rule expects React's camelCase equivalents (`className`, `htmlFor`, `strokeWidth`, etc.).

**Fix:** Add all offending attributes to the rule's `ignore` list:
```js
'react/no-unknown-property': ['error', {
  ignore: ['class', 'for', 'autocomplete', 'autofocus',
           'stroke-width', 'stroke-linecap', 'stroke-linejoin',
           'fill-rule', 'clip-rule', 'text-anchor', 'dominant-baseline']
}]
```

**Lesson:** There is no maintained `eslint-config-preact` for ESLint 9 flat config. When using `eslint-plugin-react` with Preact, expect to maintain a manual ignore list for HTML attributes. The list grows with every new SVG icon added to the codebase.

### 42. JSX Comment Placement Breaks Parser After Open Parens

Placing `{/* eslint-disable-next-line */}` immediately after `(` in arrow function returns causes a parsing error:

```jsx
// BREAKS: Parsing error: Unexpected token
.map((item) => (
  {/* eslint-disable-next-line */}
  <div>...</div>
))
```

The parser interprets `{` as the start of an object expression, not a JSX expression container. The `//` comment style works in these positions because it's outside the JSX context.

**Fix:** Use `// eslint-disable-next-line` (JS comment) instead of `{/* */}` (JSX comment) when the comment is at the boundary between JS and JSX expressions. Reserve `{/* */}` for comments inside JSX element bodies.

**Lesson:** JSX comments and JS comments are not interchangeable. At the boundary between JS expressions and JSX returns (especially after `(` or before the first JSX element), use JS-style `//` comments.

### 43. `caughtErrorsIgnorePattern` Is Separate from `varsIgnorePattern`

Renaming unused catch bindings to `_e` and setting `varsIgnorePattern: '^_'` in `no-unused-vars` does NOT suppress the warning. Catch bindings have their own config key:

```js
'no-unused-vars': ['error', {
  varsIgnorePattern: '^_',
  caughtErrorsIgnorePattern: '^_',  // <-- required separately
}]
```

Without `caughtErrorsIgnorePattern`, every `catch (_e)` still triggers `'_e' is defined but never used`.

**Lesson:** ESLint's `no-unused-vars` has four separate ignore patterns: `varsIgnorePattern`, `argsIgnorePattern`, `caughtErrorsIgnorePattern`, and `destructuredArrayIgnorePattern`. Each must be set independently. The `_` prefix convention requires explicit opt-in for each category.

### 44. Mocked Preact Signals Are Not Reactive

When mocking `@preact/signals` or a module that re-exports signals:

```js
vi.mock('../state.js', () => ({
  tags: { value: [] },
}));
```

Setting `tagsSignal.value = [{ id: 'tag-1', name: 'JS' }]` before `render()` puts the data in the mock object, but the component won't reactively see it. If the component loads data via a `useEffect` → `listTags()` call, the signal assignment is irrelevant — the component will call `listTags()` regardless.

**Fix:** Mock the API function to return the desired data, not the signal:

```js
listTags.mockResolvedValueOnce([{ id: 'tag-1', name: 'JavaScript', article_count: 3 }]);
render(<Tags />);
await waitFor(() => screen.getByText('Delete'));
```

**Lesson:** In Preact/React component tests, understand how the component gets its data. If it loads via `useEffect` + API call, mock the API call. If it reads directly from a signal/store, mock the signal. Mocking the wrong layer means the test data never reaches the component.

### 45. Format-Then-Lint Ordering: New Files Need Re-Formatting

Running `prettier --write` to normalize the codebase, then creating new test files, then running `prettier --check` will fail — the new files weren't included in the initial formatting pass.

Similarly, running `eslint --fix` can introduce changes that Prettier disagrees with (or vice versa). The safe order is:

1. Create all files
2. Run `prettier --write` on everything
3. Run `eslint --fix`
4. Run `prettier --write` again (to fix any formatting ESLint introduced)
5. Verify with `prettier --check && eslint`

**Lesson:** Format and lint are not one-shot operations during setup. Every time new files are created, both must run again. In a Makefile/CI pipeline, always run format-check before lint so formatting failures are caught first.

---

## Codebase Audit & Cleanup — Patterns Discovered

### 46. Dead Code Accumulates Across Feature Addition Cycles

After 16 competitive features were added in a single commit (`8d05ec2`), three subsequent cleanup commits were needed to remove dead code:
- `22d0c11` — Remove dead email ingestion code (never wired to any UI)
- `8b882ed` — Remove dead Newsletter Ingestion section from Settings
- `def6c02` — Remove Highlights, Feeds, and Review features (added but never finished)

Dead code comes in three forms: (1) features added then abandoned before completion, (2) backward-compatibility aliases kept "just in case" that nothing ever imports, (3) helper functions extracted during refactoring whose callers were later deleted. Each form requires a different detection method: unused imports for type 1, grep for type 2, coverage analysis for type 3.

**Lesson:** After any large feature addition, run a dedicated dead code audit before moving on. The cost of removing dead code grows as other code starts depending on its presence (even accidentally). The `4e6615d` cleanup commit ("Eliminate codebase duplication and remove backward-compat aliases") was straightforward precisely because it happened within days of the additions.

### 47. Safe* Wrappers Supersede Standalone FFI Helpers

The FFI boundary evolved through three stages:
1. **Standalone helpers** (`JS_NULL` constant, `r2_put()` function, `get_js_null()`) — each function handled one conversion.
2. **Centralized module** — all helpers in `wrappers.py`, but callers still needed to know which helper to call for each binding.
3. **Safe* wrapper classes** (`SafeD1`, `SafeR2`, `SafeKV`, `SafeQueue`, `SafeAI`, `SafeReadability`) wrapped at construction time by `SafeEnv` — callers use the same API as raw bindings but get automatic conversion.

The standalone helpers (`JS_NULL`, `r2_put()`) were removed because the Safe* wrappers made them unnecessary at the call site. Lower-level helpers that the Safe* wrappers compose internally (`d1_null()`, `to_js_bytes()`, `_to_js_value()`) still exist. `get_js_null()` still exists as the underlying mechanism for `d1_null()`.

**Lesson:** FFI boundary layers should evolve toward construction-time wrapping. When the wrapper is applied once at init, callers cannot accidentally bypass it. Standalone conversion helpers are a stepping stone — they force every call site to remember the right helper, which is error-prone across a growing codebase.

### 48. Test Helper Factories Reduce Boilerplate Without Hiding Intent

Test files originally duplicated `_make_app()` and `_authenticated_client()` helper functions, each slightly different. The `make_test_helpers(*routers)` factory in `conftest.py` generates these helpers from a router specification, so each test file declares its routers once:

```python
_make_app, _authenticated_client = make_test_helpers(
    (router, "/api/articles"),
)
```

This replaced ~15 lines of boilerplate per test file while keeping test intent visible — the router configuration is right at the top.

**Lesson:** When test boilerplate is duplicated across files with only the router/resource varying, extract a factory that takes the variable part as a parameter. But keep the factory call at the top of each test file so readers can see which routes are under test without jumping to conftest.py.

### 49. Documentation Lessons File Itself Needs Periodic Auditing (see also Lesson 50)

This file (`LESSONS_LEARNED.md`) accumulated stale references over time:
- Test counts (280, 480, 893) all went stale as tests were added/removed
- References to `JS_NULL` constant, `r2_put()` standalone function, and `_now()` helper persisted after those were removed
- `src/articles/models.py` and `src/tags/models.py` were listed in phase summaries after being deleted
- The FFI boundary checker script was described as "later removed during cleanup" when it still exists
- Commit counts didn't reflect new commits added after the analysis was written

**Lesson:** A lessons-learned document is code documentation — it rots at the same rate as code comments. Any claim that references a specific count, function name, or file path will become stale. Prefer describing patterns and principles over citing specific numbers. When numbers are included, mark them as "as of [date]" so future readers know to verify.

---

## Test Architecture

### 50. Integration Tests That Mock Their Integration Points Are Unit Tests in Disguise

`tests/integration/test_processing_pipeline.py` patched `http_fetch` with canned HTML in 6 of 7 tests. The intent was to test "the full pipeline end-to-end," but with HTTP mocked, the tests never exercised real content extraction, real image downloading, or real redirect handling — the exact areas where production bugs appeared (example.com 404s, Readability service failures).

`tests/integration/test_api_flow.py` contained a 334-line `StatefulMockD1` class reimplementing D1's SQL routing in Python. Every CRUD test exercised business logic against a hand-rolled in-memory database, not real D1. This meant FTS5 behavior, constraint enforcement, and transaction ordering were all simulated, not tested.

**Audit results (2026-02-25):**
- 7 pipeline tests: 6 already had equivalent unit tests, 1 promoted to E2E
- 24 API flow tests: 16 already covered by E2E staging tests, 8 promoted to E2E
- `tests/integration/` directory deleted entirely
- Net test movement: 0 tests lost, 9 new E2E tests against real staging infrastructure

**Lesson 1:** If an integration test patches the very dependency it's supposed to integrate with, it's a unit test with extra steps. Either test against real infrastructure or admit it's a unit test and put it in the unit test directory.

**Lesson 2:** Before writing new integration tests, audit whether the behavior is already covered at another tier. In this project, every integration test was duplicated in either unit tests (error injection) or E2E tests (happy paths). The integration tier added no unique coverage — it was a maintenance burden providing false confidence.

**Lesson 3:** A 334-line hand-rolled database mock is a code smell. If you need that much simulation to test something, you're either testing at the wrong tier or your code needs restructuring to be testable with simpler mocks.

### 51. Deploy Without Migrations Is Deploy Without Schema

Migration `0004_tag_rules.sql` (creates the `tag_rules` table) was committed alongside the code that queries it (`src/tags/rules.py`, mounted at `/api/tag-rules`). The code was deployed to staging via `pywrangler deploy`, but the migration was never applied. Result: `GET /api/tag-rules` returned 500 (table doesn't exist), which broke the Tags view — it calls both `loadTags()` and `loadRules()` on mount, and the second call failed.

The bug persisted undetected because three layers of testing all missed it:

1. **Backend unit tests** (`test_tag_rules.py`) — 15 tests, all using mock D1 that pattern-matches SQL strings and returns canned data. The mock never executes real SQL, so a missing table is invisible.
2. **Frontend unit tests** (`Tags.test.jsx`) — mocks the API module (`getTagRules: vi.fn(() => Promise.resolve([]))`). No HTTP call is made.
3. **E2E smoke tests** (`test_staging_smoke.py`) — tested `GET /api/tags` (200) but not `GET /api/tag-rules`. The smoke test suite had a coverage gap for this endpoint.

The root cause is a **deployment process gap**, not a code bug. `pywrangler deploy` ships code; `wrangler d1 migrations apply` ships schema. These were two independent manual steps with no enforcement that both happened.

**Fixes applied:**
- `Makefile` deploy targets now run `wrangler d1 migrations apply` before `pywrangler deploy`, making it impossible to deploy code without applying pending migrations.
- Added `test_tag_rules_endpoint_returns_json_array` to E2E smoke tests, so a missing table is caught on the next test run even if the migration step is somehow bypassed.

**Lesson 1:** Schema migrations and code deployment must be a single atomic operation. If they're separate manual steps, they will drift. Wire migrations into the deploy command so they can't be forgotten. For D1, this means `wrangler d1 migrations apply --remote` runs before `pywrangler deploy` in every deploy target.

**Lesson 2:** E2E smoke tests should cover every API endpoint that the frontend calls on page load. The Tags view calls two endpoints (`/api/tags` and `/api/tag-rules`) but the smoke tests only covered one. Any endpoint that a view hits unconditionally on mount is a candidate for a smoke test — if it returns 500, the entire view is broken.

**Lesson 3:** When adding a new database table, the checklist is: (1) write the migration, (2) write the route code, (3) add a smoke test for the new endpoint, (4) deploy via the make target that applies migrations. Missing any step creates a latent failure that mocks can't detect.

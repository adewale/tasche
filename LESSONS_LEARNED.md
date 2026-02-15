# Lessons Learned — Tasche Implementation

## Implement→Audit Loop Summary

Each phase was implemented by a sub-agent, then audited by a separate sub-agent. If the audit failed, fixes were applied and the audit was re-run. Below is a summary of each phase's loop.

### Phase 1: Project Foundation
- **Iterations:** 2 (implement → audit → fix → re-audit → PASS)
- **Files:** `wrangler.jsonc`, `pyproject.toml`, `Makefile`, `src/wrappers.py`, `src/entry.py`, `migrations/0001_initial.sql`, `tests/conftest.py`, `.gitignore`
- **Audit issues:** Minor issues with FFI boundary layer and mock completeness
- **Key fix:** Ensured `_to_py_safe`, `_to_js_value`, `_is_js_undefined`, `JS_NULL` were all implemented in `wrappers.py`

### Phase 2: Authentication
- **Iterations:** 2 (implement → audit → fix → re-audit → PASS)
- **Files:** `src/auth/routes.py`, `src/auth/session.py`, `src/auth/middleware.py`, `src/auth/dependencies.py`, `tests/unit/test_auth.py`
- **Audit issues:** GitHub OAuth flow edge cases
- **Key fix:** CSRF state parameter validation and `ALLOWED_EMAILS` enforcement

### Phase 3: Article CRUD API
- **Iterations:** 2 (implement → audit → fix → re-audit → PASS)
- **Files:** `src/articles/models.py`, `src/articles/routes.py`, `src/articles/storage.py`, `src/articles/urls.py`, `tests/unit/test_articles.py`, `tests/unit/test_urls.py`
- **Audit issues:** URL validation and duplicate detection across 3 URL fields
- **Key fix:** Duplicate detection checking `original_url`, `final_url`, and `canonical_url`

### Phase 4: Content Processing Pipeline
- **Iterations:** 2 (implement → audit → fix → re-audit → PASS)
- **Files:** `src/articles/processing.py`, `src/articles/browser_rendering.py`, `tests/unit/test_processing.py`
- **Audit issues:** Error handling in the 14-step pipeline
- **Key fix:** Proper status transitions (pending → processing → ready/failed)

### Phase 5: Search, Tags, Organization
- **Iterations:** 2 (implement → audit → fix → re-audit → PASS)
- **Files:** `src/search/routes.py`, `src/tags/routes.py`, `src/tags/models.py`, `tests/unit/test_search.py`, `tests/unit/test_tags.py`
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
- **Files:** `src/observability.py`, `tests/unit/test_observability.py`
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

- **Total test count:** 239 tests passing (203 original + 36 new)
- **Lint:** `ruff check src/ tests/` — all clean
- **Total audit iterations:** 17 (9 initial audits + 8 re-audits after fixes)
- **Phases 8 and 9 passed audit on first attempt** — all others required one fix cycle

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
All 203 tests passed throughout, yet the audit found significant issues (XSS, wrong status strings, missing features, broken bookmarklet). Unit tests verify behavior; audits verify intent and completeness against the spec.

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

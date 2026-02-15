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

## Final Stats

- **Total test count:** 203 tests passing
- **Lint:** `ruff check src/ tests/` — all clean
- **Total audit iterations:** 15 (8 initial audits + 7 re-audits after fixes)
- **Only Phase 8 passed audit on first attempt** — all others required one fix cycle

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

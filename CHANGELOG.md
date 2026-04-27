# Changelog

All notable changes to Tasche are documented in this file.

## [Unreleased]

### Changed
- **Bookmarklet rewritten** — replaced cross-origin `fetch()` with a `window.open()` popup backed by a dedicated `/bookmarklet` page. The popup makes a same-origin request, shows "Saved!", and auto-closes. No more CORS issues with `SameSite=Lax` cookies.
- **CORS locked down to localhost** — production requests are now same-origin (via the bookmarklet popup), so CORS is restricted to `localhost` and `127.0.0.1` for local dev only.
- **README simplified** — deploy-button flow now leads with dashboard-only steps (no CLI needed). Screenshot added.

### Removed
- **Browser extension deleted** — the `extension/` directory is gone. The bookmarklet popup replaces it with zero maintenance burden.

### Added
- **Deploy guide** (`docs/deploy-guide.md`) — operational reference for what's auto-created vs auto-detected vs manual in the deploy flow.

## [0.3.0] - 2026-03-02

### Added
- **Logo mark** in header, favicon, and PWA icons
- **Hamburger menu** for mobile navigation
- **Friendly error page** for unauthorized users
- **Offline sync E2E test** with Playwright

### Fixed
- Stuck audio: allow retry when `audio_status` is `generating`

### Removed
- Cron-based URL health check (unnecessary complexity)

## [0.2.0] - 2026-02-27

### Added
- **One-click Deploy to Cloudflare** button with automatic resource provisioning (D1, R2, KV, Queues, AI)
- **First-boot setup checklist** — Login page shows missing config via `/api/health/config` instead of a cryptic 500 error
- **SITE_URL auto-detection** from request `Host` header (no manual config needed for workers.dev deployments)
- **Readability Service Binding** for high-fidelity content extraction (with BeautifulSoup fallback)
- **Configurable TTS model** via `TTS_MODEL` env var (default: MeloTTS)
- **Design language reference** page (`/design-language`) documenting the UI system
- **WCAG AAA dark mode** — rebuilt palette from scratch (`#141416`/`#1e1e20`/`#333336`)
- **Preact + Vite frontend** replacing the vanilla JS SPA, with `@preact/signals` for state management
- **Frontend quality gates** — ESLint, Prettier, Vitest, axe-core a11y
- **16 competitive features**: tag rules/auto-tagging, data export (JSON + HTML bookmarks), reading statistics, batch operations, markdown view, reader preferences, keyboard shortcuts (`j`/`k` navigation, `?` help), tag rename, article retry, favicons on article cards
- **Article-status polling** — cards update automatically when processing completes
- **Media Session API** — artwork, position state, seekto handler for system media controls
- **Loading states** on all async buttons to prevent double-submit
- **Ink design features** — sidenotes, favicons, thumbnails, breath marks in reader view
- **Cross-hatch disabled states** for buttons
- Service worker rewrite with 4 named caches

### Fixed
- 6 security issues: traceback leak, SSRF bypass, CSP gaps, auth guard on all routes
- TTS audio truncation (ASGI adapter only reads first chunk of `StreamingResponse` — switched to buffered `Response`)
- `to_js_bytes()` Wasm memory detachment risk
- Skeleton flash when switching to empty filter tabs
- D1 batch-update FFI bug
- Dark-mode contrast across all components
- `make dev` now works from a fresh clone (applies migrations, copies `.dev.vars.example`)

### Changed
- Simplified library filter tabs: Unread | Audio | Favourites | Archived
- Replaced save form toggle with explicit "Save audio" button
- `ALLOWED_EMAILS` moved from env var to secret (fixes wrangler binding conflict)
- `DISABLE_AUTH` removed from staging env
- Workers Builds CI/CD enabled (auto-deploy on push to main)

### Removed
- Highlights, Feeds, and Review features (unused complexity)
- Newsletter ingestion settings UI (dead code)
- `python-readability` dependency (`js.eval()` blocked in Workers — BeautifulSoup + Readability Service Binding used instead)
- `notes` field (no UI specified)
- `listen_later` column (replaced by `audio_status`)

## [0.1.0] - 2026-02-15

Initial release of Tasche — a self-hosted read-it-later service on Cloudflare Python Workers.

### Added

**Core**
- GitHub OAuth authentication with KV sessions (7-day TTL)
- `ALLOWED_EMAILS` whitelist with per-request re-validation and session revocation
- FFI boundary layer (`src/boundary/__init__.py`) for safe JsProxy conversion at D1/R2/KV/Queue boundaries

**Articles**
- Save articles by URL with automatic content processing via Queues
- 14-step processing pipeline: fetch, redirect resolution, readability extraction, image download + WebP conversion, HTML/Markdown storage to R2, FTS5 indexing
- Duplicate detection across `original_url`, `final_url`, and `canonical_url`
- CRUD API: create, list, get, update, delete
- Reading status tracking (unread/archived)
- Favorites toggle
- Scroll position persistence (percentage-based for cross-device consistency)
- Input validation: URL max 2048 chars, title max 500

**Search**
- FTS5 full-text search across title, excerpt, and markdown content
- Results ordered by relevance (`rank`), not recency
- Query sanitization: FTS5 operators stripped, words quoted as literals

**Tags**
- Tag CRUD with max 100 character names
- Article-tag associations via RESTful nested routes (`/api/articles/:id/tags`)
- Filter articles by tag

**Listen Later (TTS)**
- Queue-based TTS generation via Workers AI
- Markdown stripped to plain text before TTS
- Text truncated to 100K characters for model limits
- Idempotent: 409 if pending/generating, 200 if ready, enqueue only for null/failed
- Retry after failure: button reappears when `audio_status` is `failed`

**Frontend PWA**
- Single-page application with hash-based routing
- Responsive reader view with clean typography
- Audio player: play/pause, skip ±15s, playback speed (0.75x-2x)
- Tag picker UI in reader view (add/remove tags)
- Bookmarklet and PWA share target
- Service worker: cache-first for static assets, network-first for API
- Offline mutation queue with URL+method deduplication (last-write-wins)

**Observability**
- Wide events middleware: one canonical JSON log line per request

**Security**
- SSRF protection: private network blocklist on URL submission, after redirect resolution, and on image downloads
- FTS5 query injection prevention
- Security headers: X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy, conditional HSTS

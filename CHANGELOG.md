# Changelog

All notable changes to Tasche are documented in this file.

## [0.1.0] - 2026-02-15

Initial release of Tasche — a self-hosted read-it-later service on Cloudflare Python Workers.

### Added

**Core**
- GitHub OAuth authentication with KV sessions (7-day TTL)
- `ALLOWED_EMAILS` whitelist with per-request re-validation and session revocation
- FFI boundary layer (`src/wrappers.py`) for safe JsProxy conversion at D1/R2/KV/Queue boundaries

**Articles**
- Save articles by URL with automatic content processing via Queues
- 14-step processing pipeline: fetch, redirect resolution, readability extraction, image download + WebP conversion, HTML/Markdown storage to R2, FTS5 indexing
- Duplicate detection across `original_url`, `final_url`, and `canonical_url` with `UNIQUE(user_id, original_url)` constraint
- CRUD API: create, list, get, update, delete
- Reading status tracking (unread, reading, archived)
- Favorites toggle
- Scroll position persistence (percentage-based for cross-device consistency)
- Input validation: URL max 2048 chars, title max 500, notes max 10000

**Content Serving**
- `GET /api/articles/:id/content` serves clean HTML from R2 with local image paths
- Frontend tries R2 HTML first, falls back to markdown rendering from D1

**Search**
- FTS5 full-text search across title, excerpt, and markdown content
- Results ordered by relevance (`rank`), not recency
- Query sanitization: FTS5 operators stripped, words quoted as literals

**Tags**
- Tag CRUD with max 100 character names
- Article-tag associations via RESTful nested routes (`/api/articles/:id/tags`)
- Filter articles by tag

**Listen Later (TTS)**
- Queue-based TTS generation via Workers AI (`@cf/deepgram/aura-2-en`)
- Markdown stripped to plain text before TTS (no "hash hash Introduction")
- Text truncated to 100K characters for model limits
- Idempotent: 409 if pending/generating, 200 if ready, enqueue only for null/failed
- Audio streamed from R2 ReadableStream (not buffered in memory)
- Retry after failure: button reappears when `audio_status` is `failed`

**Frontend PWA**
- Vanilla JS single-page application with hash-based routing
- Responsive reader view with clean typography
- Audio player: play/pause, skip ±15s, playback speed (0.75x–2x)
- Tag picker UI in reader view (add/remove tags)
- Bookmarklet using `window.open()` (compatible with `SameSite=Lax` cookies)
- PWA share target for mobile "Share to Tasche" flow
- Service worker: cache-first for static assets, network-first for API
- Proactive offline caching of unread article detail endpoints
- Offline mutation queue with URL+method deduplication (last-write-wins)
- Sync queue preserved during service worker cache cleanup

**Observability**
- Wide events middleware: one canonical JSON log line per request
- Tail sampling: 100% errors/slow, 5–10% success

**Security**
- SSRF protection: private network blocklist on URL submission, after redirect resolution, and on image downloads
- FTS5 query injection prevention
- XSS protection: `javascript:` URL sanitization in markdown renderer
- Markdown image regex runs before link regex (prevents `![alt](url)` → `!<a>` breakage)

**Infrastructure**
- D1 schema with CHECK constraints for status enums
- R2 deletion: data deleted before D1 reference (prevents orphaned objects)
- R2 list pagination with cursor (handles >1000 objects)
- Queue error categorization: transient errors propagate for retry, permanent errors caught and marked failed
- TTS queue processor verifies article ownership via `user_id`

**Testing**
- Unit test suite passing
- Lint clean (`ruff check`)

### Spec
- Updated spec with §9 Security Requirements and §10 Failure Modes & Edge Cases
- Field length limits, content format per consumer, cross-origin cookie behavior, deletion order, queue error categories

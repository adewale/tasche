# Tasche Admin Features Specification

**Last Updated:** March 2026
**Status:** Draft
**Depends on:** Core article pipeline (Phases 1–9), Export (v0.1.0)

---

## 1. Overview

Tasche is single-user and self-hosted — "admin" means giving the instance owner visibility and control over their deployment's health, storage, and data lifecycle. These features surface information that is otherwise invisible without `wrangler` CLI access or Cloudflare dashboard spelunking.

### Design Principles

1. **No new bindings.** Admin features use existing D1, R2, KV, and Queue bindings — no additional infrastructure.
2. **Read-heavy, write-careful.** Most admin endpoints are diagnostic reads. Destructive actions (cleanup, purge) require explicit confirmation via a `confirm: true` body parameter.
3. **Same auth.** All admin endpoints require the same `get_current_user` authentication. No separate admin role — the single user *is* the admin.
4. **Additive.** Admin features are a new router (`/api/admin/*`) that doesn't modify existing routes or data models.

---

## 2. How Data Export Works Today

Before specifying import (the natural counterpart), here's how export currently works.

### 2.1 Export Endpoints

| Endpoint | Format | Content-Disposition | Filename Pattern |
|----------|--------|--------------------|--------------------|
| `GET /api/export/json` | JSON array | `attachment` | `tasche-export-YYYY-MM-DD.json` |
| `GET /api/export/html` | Netscape bookmark HTML | `attachment` | `tasche-export-YYYY-MM-DD.html` |

Both endpoints are authenticated via `get_current_user` and defined in `src/articles/export.py`.

### 2.2 JSON Export Shape

The JSON export returns **all articles** for the user, ordered by `created_at DESC`. Each article includes every D1 column plus an augmented `tags` key:

```json
[
  {
    "id": "abc123...",
    "user_id": "user_...",
    "original_url": "https://example.com/article",
    "final_url": "https://example.com/article",
    "canonical_url": "https://example.com/article",
    "domain": "example.com",
    "title": "Article Title",
    "excerpt": "First paragraph...",
    "author": "Jane Doe",
    "word_count": 1500,
    "reading_time_minutes": 6,
    "status": "ready",
    "reading_status": "unread",
    "is_favorite": 0,
    "audio_key": null,
    "audio_status": null,
    "audio_duration_seconds": null,
    "html_key": "articles/abc123.../content.html",
    "thumbnail_key": "articles/abc123.../thumbnail.webp",
    "markdown_content": "# Article Title\n\nFull markdown...",
    "original_status": "available",
    "scroll_position": 0,
    "reading_progress": 0.0,
    "created_at": "2026-02-15T10:30:00Z",
    "updated_at": "2026-02-15T10:31:00Z",
    "tags": ["javascript", "web-dev"]
  }
]
```

Key details:
- **Two D1 queries:** One `SELECT *` for articles, one `JOIN` for all article-tag associations. Tags are grouped by `article_id` in Python.
- **No R2 content included.** The export contains `markdown_content` (stored in D1 for FTS5) but not the full HTML or images from R2. This keeps exports fast and small.
- **No pagination.** All articles are fetched in a single query. For large libraries (1000+ articles), this could be slow but is acceptable for an infrequent export operation.
- **`Content-Disposition: attachment`** triggers a browser download rather than inline display.

### 2.3 Netscape Bookmark HTML Export

The HTML export produces the standard Netscape bookmark format used by every browser and read-it-later service:

```html
<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Tasche Export</TITLE>
<H1>Tasche Export</H1>
<DL><p>
  <DT><A HREF="https://example.com/article" ADD_DATE="1739612400" TAGS="javascript,web-dev">Article Title</A>
  <DD>First paragraph excerpt...
</DL><p>
```

Key details:
- **`ADD_DATE`** is a Unix timestamp converted from the article's `created_at` ISO string.
- **`TAGS`** attribute contains comma-separated tag names (HTML-escaped).
- **`<DD>`** contains the article excerpt (omitted if empty).
- All text values are HTML-escaped via a simple `&`, `<`, `>`, `"` replacement.

### 2.4 What Export Does NOT Include

| Data | Included? | Why |
|------|-----------|-----|
| Article metadata (D1) | Yes | Core article data |
| Tags and associations | Yes | Augmented onto each article |
| Markdown content | Yes (JSON only) | Stored in D1 for FTS5 |
| Archived HTML (R2) | No | Too large, slow to assemble |
| Images (R2) | No | Too large, would need zip packaging |
| Audio files (R2) | No | Generated content, can be regenerated |
| Tag rules | No | Not part of the export |
| User preferences | No | Not part of the export |
| Reading progress | Yes (JSON only) | Part of article D1 row |

---

## 3. Admin Features

### 3.1 Instance Health Dashboard

**Endpoint:** `GET /api/admin/health`

Returns a comprehensive health check that goes beyond the existing `/api/health/config` (which only checks binding existence).

```json
{
  "status": "healthy",
  "timestamp": "2026-03-06T12:00:00Z",
  "bindings": {
    "d1": { "status": "ok", "latency_ms": 3 },
    "r2": { "status": "ok", "latency_ms": 12 },
    "kv": { "status": "ok", "latency_ms": 2 },
    "readability": { "status": "ok", "latency_ms": 5 },
    "ai": { "status": "ok", "latency_ms": null }
  },
  "checks": {
    "d1_query": "SELECT 1 returned 1",
    "r2_list": "Listed objects successfully",
    "kv_roundtrip": "Write/read/delete cycle succeeded",
    "readability_ping": "Service binding responded"
  }
}
```

**Implementation:**
- D1: `SELECT 1` query
- R2: `list({ limit: 1 })` call
- KV: Write a test key with 10s TTL, read it back, delete it
- Readability: Call the service binding's health method (if available) or skip
- AI: Skip active check (costs money) — report binding existence only
- Each check is wrapped in try/except with a 5-second timeout
- Overall `status` is `"healthy"` if all required bindings (D1, R2, KV) pass, `"degraded"` if optional bindings (Readability, AI) fail, `"unhealthy"` if any required binding fails

### 3.2 Storage Usage

**Endpoint:** `GET /api/admin/storage`

Reports D1 and R2 usage metrics.

```json
{
  "d1": {
    "articles": { "total": 342, "by_status": { "ready": 310, "failed": 20, "pending": 8, "processing": 4 } },
    "tags": 45,
    "tag_rules": 12,
    "article_tags": 890,
    "fts_entries": 310
  },
  "r2": {
    "total_objects": 2150,
    "estimated_size_mb": 485.3,
    "by_type": {
      "html": { "count": 310, "size_mb": 45.2 },
      "images": { "count": 1520, "size_mb": 380.1 },
      "audio": { "count": 28, "size_mb": 52.0 },
      "thumbnails": { "count": 280, "size_mb": 5.8 },
      "metadata": { "count": 310, "size_mb": 0.2 }
    }
  }
}
```

**Implementation:**
- D1 counts: `SELECT COUNT(*)` queries with group-by for status breakdowns. These are fast index scans.
- R2 usage: `list()` with pagination (R2 list returns up to 1000 objects per call). Iterate all objects, categorize by key prefix/suffix, sum sizes. **This is slow for large buckets** — consider caching the result in KV with a 1-hour TTL.
- FTS entries: `SELECT COUNT(*) FROM articles_fts`

**R2 size estimation note:** R2 `list()` returns `size` per object. For buckets with 10k+ objects, iterating all pages could take 10+ seconds. The endpoint should:
1. Check KV for a cached result (key: `admin:storage:r2`, TTL: 1 hour)
2. If cached and fresh, return it
3. If stale or missing, compute in the request (accept the latency) and cache the result
4. Return a `computed_at` timestamp so the UI can show staleness

### 3.3 Failed Article Management

**Endpoint:** `GET /api/admin/failed`

Lists all articles in a failed state with diagnostic information.

```json
{
  "articles": [
    {
      "id": "abc123",
      "original_url": "https://example.com/broken",
      "title": null,
      "status": "failed",
      "audio_status": null,
      "created_at": "2026-03-01T10:00:00Z",
      "updated_at": "2026-03-01T10:01:00Z",
      "domain": "example.com"
    }
  ],
  "audio_failures": [
    {
      "id": "def456",
      "original_url": "https://example.com/article",
      "title": "Good Article",
      "status": "ready",
      "audio_status": "failed",
      "created_at": "2026-02-20T08:00:00Z"
    }
  ],
  "stuck": [
    {
      "id": "ghi789",
      "original_url": "https://example.com/stuck",
      "status": "processing",
      "updated_at": "2026-03-06T10:00:00Z",
      "stuck_minutes": 45
    }
  ],
  "counts": {
    "failed_articles": 5,
    "failed_audio": 3,
    "stuck_processing": 1,
    "stuck_audio_generating": 0
  }
}
```

**Implementation:**
- Failed articles: `SELECT ... WHERE user_id = ? AND status = 'failed'`
- Failed audio: `SELECT ... WHERE user_id = ? AND audio_status = 'failed'`
- Stuck articles: `SELECT ... WHERE user_id = ? AND status = 'processing' AND updated_at < datetime('now', '-10 minutes')`
- Stuck audio: `SELECT ... WHERE user_id = ? AND audio_status = 'generating' AND updated_at < datetime('now', '-10 minutes')`

**Endpoint:** `POST /api/admin/failed/retry`

Bulk retry failed or stuck articles.

```json
// Request
{
  "article_ids": ["abc123", "ghi789"],
  "retry_type": "article"  // or "audio"
}

// Response
{
  "retried": 2,
  "skipped": 0,
  "errors": []
}
```

**Implementation:**
- For `retry_type: "article"`: Reset `status` to `"pending"`, enqueue `{ "type": "article_processing", "article_id": id }` to `ARTICLE_QUEUE`
- For `retry_type: "audio"`: Reset `audio_status` to `"pending"`, enqueue `{ "type": "tts_generation", "article_id": id }`
- Only retry articles that are in `failed` or stuck state — skip articles in `ready` or `pending`
- Note: The existing `POST /api/articles/{id}/retry` endpoint handles single-article retry. This endpoint adds **bulk** retry capability.

### 3.4 Queue Status

**Endpoint:** `GET /api/admin/queue`

Reports the current state of the processing pipeline.

```json
{
  "article_pipeline": {
    "pending": 8,
    "processing": 2,
    "ready": 310,
    "failed": 5
  },
  "tts_pipeline": {
    "pending": 3,
    "generating": 1,
    "ready": 28,
    "failed": 2,
    "not_requested": 300
  },
  "stuck": {
    "processing_over_10m": 1,
    "generating_over_10m": 0
  },
  "recent_failures": [
    {
      "id": "abc123",
      "url": "https://example.com/broken",
      "failed_at": "2026-03-06T11:50:00Z",
      "pipeline": "article"
    }
  ]
}
```

**Implementation:**
- Article counts: `SELECT status, COUNT(*) FROM articles WHERE user_id = ? GROUP BY status`
- Audio counts: `SELECT audio_status, COUNT(*) FROM articles WHERE user_id = ? GROUP BY audio_status`
- Stuck detection: Same queries as 3.3
- Recent failures: `SELECT ... WHERE status = 'failed' ORDER BY updated_at DESC LIMIT 5`

### 3.5 Data Import

**Endpoint:** `POST /api/admin/import`

Import articles from various formats. This is the natural counterpart to the existing export.

**Supported formats:**

| Format | Content-Type | Source |
|--------|-------------|--------|
| Tasche JSON | `application/json` | Round-trip from `GET /api/export/json` |
| Netscape HTML | `text/html` | Browser bookmarks, Pocket, Instapaper, Omnivore, Wallabag |
| Pocket CSV | `text/csv` | Pocket export (`ril_export.html` is actually HTML, but Pocket also offers CSV) |

**Request:** `multipart/form-data` with a `file` field.

**Response:**
```json
{
  "imported": 42,
  "skipped_duplicates": 8,
  "errors": [
    { "url": "not-a-url", "reason": "Invalid URL" }
  ],
  "total_in_file": 50
}
```

**Implementation:**

#### Tasche JSON Import
1. Parse the JSON array
2. For each article:
   - Check for duplicate URL (same logic as `POST /api/articles`: check `original_url`, `final_url`, `canonical_url`)
   - If no duplicate: insert into D1 with `status: "pending"`, enqueue for processing
   - Preserve `reading_status`, `is_favorite`, `tags` from the export
   - Create missing tags on the fly (match by name)
   - Do NOT preserve `id` — generate new IDs (the source instance's IDs are meaningless here)
   - Do NOT import `markdown_content` or R2 keys — let the processing pipeline re-fetch and re-extract
3. Return import summary

#### Netscape HTML Import
1. Parse the HTML using BeautifulSoup (already a dependency)
2. Extract `<A>` tags from `<DL>` structure:
   - `HREF` → `original_url`
   - Text content → `title` (provisional, will be overwritten by extraction)
   - `ADD_DATE` → `created_at` (convert Unix timestamp to ISO)
   - `TAGS` attribute → tag names (create tags if they don't exist)
3. For each URL: same duplicate check and enqueue logic as Tasche JSON import
4. Return import summary

#### Pocket Export Import
Pocket's export is actually Netscape bookmark HTML format with two sections (`<h3>Unread</h3>` and `<h3>Read Archive</h3>`). Use the same Netscape parser but:
- Articles under "Unread" → `reading_status: "unread"`
- Articles under "Read Archive" → `reading_status: "archived"`

**Size limits:**
- Max file size: 10MB (reject larger files with 413)
- Max articles per import: 1000 (to avoid overwhelming the queue)

**Rate limiting:**
- One import at a time. If an import is in progress (tracked via a KV flag with TTL), return 429.

### 3.6 Orphan Detection and Cleanup

**Endpoint:** `GET /api/admin/orphans`

Detect inconsistencies between D1 and R2.

```json
{
  "r2_without_d1": {
    "count": 3,
    "article_ids": ["old-id-1", "old-id-2", "old-id-3"],
    "estimated_size_mb": 12.5
  },
  "d1_without_r2": {
    "count": 1,
    "articles": [
      {
        "id": "broken-ref",
        "title": "Article with missing content",
        "html_key": "articles/broken-ref/content.html",
        "status": "ready"
      }
    ]
  },
  "computed_at": "2026-03-06T12:00:00Z"
}
```

**Implementation:**
- **R2 orphans:** List all R2 objects, extract `article_id` from the key prefix (`articles/{id}/...`). Query D1 for those IDs. Any ID in R2 but not in D1 is orphaned.
- **D1 orphans:** For articles with `status = "ready"` and a non-null `html_key`, attempt `r2.head(html_key)`. If the object doesn't exist, it's a broken reference.
- Cache results in KV (`admin:orphans`, TTL: 1 hour) since this scan is expensive.

**Endpoint:** `POST /api/admin/orphans/cleanup`

Delete orphaned R2 objects.

```json
// Request
{
  "confirm": true,
  "action": "delete_r2_orphans"  // or "reset_d1_broken_refs"
}

// Response
{
  "deleted_objects": 15,
  "reset_articles": 0
}
```

- `delete_r2_orphans`: Delete all R2 objects whose article_id has no matching D1 row
- `reset_d1_broken_refs`: For articles with missing R2 content, reset `status` to `"pending"` and re-enqueue for processing (the pipeline will re-fetch and re-store)
- **Requires `confirm: true`** — returns 400 without it

### 3.7 Session Management

**Endpoint:** `GET /api/admin/sessions`

List all active sessions for the current user.

```json
{
  "sessions": [
    {
      "session_id_prefix": "a3Bf....",
      "created_at": "2026-03-01T10:00:00Z",
      "last_accessed": "2026-03-06T11:55:00Z",
      "is_current": true
    }
  ],
  "total": 3
}
```

**Implementation challenge:** KV doesn't support listing by prefix efficiently. Sessions are stored as `session:{session_id}` keys. Options:
1. **Maintain a session index:** On session creation, also write the session_id to a KV key `sessions:{user_id}` that stores a JSON array of `{ session_id, created_at }`. On session delete, remove from the array. On list, read the index key, then fetch each session to check if it's still alive (TTL may have expired it).
2. **D1 sessions table:** Add a `sessions` table to D1 that tracks session metadata. KV remains the source of truth for session validity (TTL), but D1 provides the listing capability.

**Recommended approach:** Option 1 (KV index). Adding a D1 table for sessions adds migration complexity for a minor feature. The KV index is eventually consistent but acceptable for session listing.

**Endpoint:** `POST /api/admin/sessions/revoke`

```json
// Revoke a specific session
{ "session_id_prefix": "a3Bf...." }

// Revoke all except current
{ "revoke_all_others": true }
```

**Implementation:**
- Read the session index from KV
- Delete each target session from KV
- Update the session index
- The current session (identified by the request cookie) is never revoked unless explicitly requested

### 3.8 Reprocess Article

**Endpoint:** `POST /api/admin/articles/reprocess`

Re-run the content extraction pipeline on articles that are already in `ready` state. Useful when extraction quality was poor or the Readability service has been updated.

```json
// Request
{
  "article_ids": ["abc123", "def456"],
  "confirm": true
}

// Response
{
  "enqueued": 2,
  "skipped": 0
}
```

**Implementation:**
- Reset `status` to `"pending"` for each article
- Enqueue for processing
- The pipeline will re-fetch, re-extract, and overwrite existing R2 content
- **Note on R2 orphans (Lesson 69):** The pipeline should delete existing R2 objects for the article before writing new ones to prevent orphan accumulation. The `article_key()` helper generates deterministic paths, so overwrites are safe — but images with content-hash filenames may accumulate if the source page changed.
- Requires `confirm: true` since this overwrites existing content

**Difference from `POST /api/articles/{id}/retry`:** The existing retry endpoint only works on `failed` articles. This admin endpoint works on `ready` articles too, enabling intentional re-extraction.

---

## 4. API Router Structure

All admin endpoints live under a single router:

```python
# src/admin/routes.py
router = APIRouter(prefix="/api/admin", tags=["admin"])
```

Mounted in `entry.py` alongside existing routers:

```python
from admin.routes import router as admin_router
app.include_router(admin_router)
```

---

## 5. Frontend

Admin features are accessed via a new **Admin** section in the hamburger menu, linking to `#/admin`. The admin view is a single page with expandable sections:

1. **Instance Health** — binding status indicators (green/yellow/red dots)
2. **Storage** — D1 row counts, R2 usage breakdown with bar chart
3. **Queue Status** — pipeline state counts, stuck article warnings
4. **Failed Articles** — list with "Retry All" and per-article retry buttons
5. **Import** — file upload dropzone with format auto-detection
6. **Sessions** — list with "Revoke" buttons
7. **Cleanup** — orphan detection results with "Clean Up" button (requires confirmation dialog)

The admin page is informational by default — destructive actions always require an extra click through a confirmation dialog.

---

## 6. Implementation Priority

Based on lessons learned, ordered by user value and implementation effort:

| Priority | Feature | Effort | Lessons Applied |
|----------|---------|--------|-----------------|
| **P0** | Failed article management (3.3) | Low | Lesson 15 (error categorization), Lesson 35 (cold start) |
| **P0** | Queue status (3.4) | Low | Lesson 31 (Miniflare unreliable), Lesson 35 (cold start tax) |
| **P0** | Reprocess article (3.8) | Low | Lesson 69 (R2 orphaning during reprocess) |
| **P1** | Data import (3.5) | Medium | Lesson 67 (apply fixes comprehensively) |
| **P1** | Storage usage (3.2) | Medium | Lesson 16 (delete order), Lesson 69 (orphan accumulation) |
| **P2** | Instance health (3.1) | Low | Lesson 38 (E2E tests catch what mocks cannot) |
| **P2** | Session management (3.7) | Low | Lesson 17 (SameSite cookies) |
| **P3** | Orphan cleanup (3.6) | Medium | Lesson 16 (delete data first, then reference), Lesson 69 (R2 orphaning) |

---

## 7. Lessons Learned That Shaped This Spec

The 71 lessons in `LESSONS_LEARNED.md` directly influenced several design decisions:

### Lessons driving specific features:

| Lesson | # | Admin Feature It Motivates |
|--------|---|---------------------------|
| Error categorization determines retry behavior | 15 | Failed article management — users need to see *why* something failed and whether retry is appropriate |
| Delete in the right order (data first, reference second) | 16 | Orphan detection — when deletion order was wrong, orphans were created. Admin needs to find and clean these. |
| Miniflare queue consumer is unreliable | 31 | Queue status — if you can't trust local queue testing, you need production visibility into queue state |
| Pyodide cold start cancels first queue invocation | 35 | Queue status — "stuck" articles may just be waiting for warm isolate retry. The 10-minute threshold accounts for this. |
| Deploy without migrations is deploy without schema | 51 | Instance health — the enhanced health check verifies that D1 is actually queryable, catching missing-migration failures |
| R2 content orphaning during re-processing | 69 | Orphan detection and the reprocess endpoint's cleanup behavior |

### Lessons driving design decisions:

| Lesson | # | Design Decision |
|--------|---|-----------------|
| Unit tests that only exercise fallback give false confidence | 37 | Admin endpoints must have E2E smoke tests against staging, not just mock-based unit tests |
| E2E tests catch what mocks cannot | 38 | Every admin endpoint that the frontend calls on mount gets a smoke test |
| FTS5 is its own query language | 11 | Import doesn't directly insert into FTS5 — it goes through the normal pipeline which handles FTS5 triggers correctly |
| Idempotency must be explicit for expensive operations | 13 | Import uses duplicate URL detection to prevent re-importing articles that already exist |
| Input validation is not optional | 12 | Import enforces file size (10MB) and article count (1000) limits |
| SSRF is a server-side concern | 19 | Imported URLs go through the same SSRF protection as manually saved URLs (the processing pipeline handles this) |
| Feature churn (added then removed) | Pattern 3 | This spec deliberately excludes features that sound useful but have no clear user journey: per-article error messages (would require schema changes with unclear benefit), R2 full backup/restore (too expensive and slow), multi-user management (contradicts single-user design) |
| Asymmetric spec precision produces asymmetric results | 36 | This spec includes response shapes, implementation notes, and edge cases — not just endpoint names |

### Lessons suggesting features we explicitly excluded:

| Excluded Feature | Why | Lesson |
|------------------|-----|--------|
| Per-article error log in D1 | Requires schema change, wide events already capture this in Workers Logs | 9 (wide events are self-contained) |
| R2 full backup to zip | Expensive, slow, would require streaming zip assembly in Pyodide (no C extensions) | Pyodide constraints throughout |
| Scheduled health checks | Would need Cron Triggers (new binding), health is better checked on-demand | Principle 1 (no new bindings) |
| Admin-only auth bypass | Security regression, defeats ALLOWED_EMAILS | 18 (fix patterns, not security policy) |
| Usage-based alerts | No persistent monitoring — Workers are stateless. Use Cloudflare dashboard alerts instead. | 9 (wide events + Workers Logs) |

---

## 8. Migration

One new migration is needed for the session index (if using the D1 approach for session management). If using the KV index approach (recommended), **no migrations are needed** — all admin features read from existing D1 tables and R2 objects.

---

## 9. Testing Strategy

Following Lessons 37, 38, and 50:

| Tier | What to Test | How |
|------|-------------|-----|
| Unit | Import parsing (JSON, HTML, CSV formats) | pytest with fixture files |
| Unit | Orphan detection logic | pytest with mock D1/R2 |
| Unit | Health check aggregation logic | pytest with mock bindings |
| E2E | Every admin endpoint returns expected shape | httpx against staging |
| E2E | Import round-trip: export → import → verify | httpx against staging |
| E2E | Failed retry actually re-enqueues | httpx + poll for status change |

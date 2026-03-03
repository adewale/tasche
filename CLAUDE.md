# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Tasche is a single-user, self-hosted read-it-later service built entirely on the Cloudflare Developer Platform using **Python Workers (Pyodide)**. Users deploy their own instance with their own D1, R2, KV, and Queues. The full product spec lives in `specs/tasche-spec.md`.

## Development Commands

```bash
make dev                # Local dev server (installs deps, builds frontend, applies migrations)
make check              # All quality gates (lint, test, format, build)
make test               # Backend unit tests only
make deploy-staging     # Quality gates + deploy to staging
make deploy-production  # Quality gates + deploy to production

# Run a single test file or test
uv run pytest tests/unit/test_articles.py -x -q
uv run pytest tests/unit/test_articles.py::TestCreateArticle::test_creates_article -x -q
```

**Important:** Use `pywrangler` (not regular `wrangler`) — regular wrangler cannot deploy Python Workers with packages. Packages are defined in `pyproject.toml`, not `requirements.txt`. Always deploy via `make deploy-*` targets so D1 migrations are applied automatically before code goes out.

## Architecture

**Runtime:** Python on Pyodide (WebAssembly) inside V8 isolates — no threading, no C extensions.

**Framework:** FastAPI with ASGI adapter. Entry point: `src/entry.py`.

**Cloudflare bindings (accessed via `request.scope["env"]` in FastAPI handlers):**

| Binding       | Name             | Purpose                                      |
|---------------|------------------|----------------------------------------------|
| D1            | `DB`             | Users, articles, tags, FTS5 search index     |
| R2            | `CONTENT`        | Archived HTML, Markdown, images, thumbnails, audio |
| KV            | `SESSIONS`       | Auth sessions with TTL                       |
| Queues        | `ARTICLE_QUEUE`  | Async article processing and TTS generation  |
| Workers AI    | `AI`             | Text-to-speech audio generation              |
| Service       | `READABILITY`    | Readability extraction (Service Binding)     |
| Assets        | `ASSETS`         | Static frontend asset serving                |

**Key data flow:** Save URL → API creates article (status: pending) → enqueue to `ARTICLE_QUEUE` → queue consumer fetches page, extracts content via Readability Service Binding (with BeautifulSoup fallback), converts images to WebP, stores HTML+Markdown in R2, updates D1.

**Auth:** Manual GitHub OAuth → sessions in KV. Endpoints under `/api/auth/`.

**Storage philosophy:** Dual format — HTML in R2 for rendering fidelity, Markdown in both R2 and D1 (FTS5 search indexing).

## Python Workers Constraints

- **All handlers must be `async def`** — sync handlers cause `RuntimeError: can't start new thread`
- **Use `request.js_object`** for the ASGI adapter: `asgi.fetch(app, request.js_object, self.env)`
- **Call `.to_py()`** on D1/JS results to convert JsProxy → native Python dicts/lists
- **No global request state** — isolates are reused across requests; pass state via function args or `request.state`
- **Avoid C-extension libraries** — must be pure Python or Pyodide-compatible
- **Avoid:** `lxml`, `readability-lxml`, `requests`, `playwright`, `selenium`, any threading/multiprocessing library
- **Use instead:** `httpx` (async HTTP), Readability Service Binding (`env.READABILITY`), `beautifulsoup4` (fallback extraction), `markdownify`

## Configuration

All config lives in `wrangler.jsonc`. Auth secrets (`GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`) are set via `wrangler secret put`.

## Observability

Wide events pattern: emit one JSON log line per request (not many small lines). Build the event incrementally, emit in a `finally` block via `print(json.dumps(event))`. Always include: timestamp, request_id (cf-ray), method, path, status_code, duration_ms, outcome, user.id.

## Key Conventions

- **FFI boundary:** All JS↔Python conversion happens in `src/wrappers.py`. Application code accesses bindings through Safe* wrappers (`SafeD1`, `SafeR2`, `SafeKV`, `SafeQueue`, `SafeAI`, `SafeReadability`) via `SafeEnv`. Never expose JsProxy to business logic.
- **Status enums:** `reading_status`: unread/archived (D1 CHECK also allows 'reading' as a historical artifact, but the app enforces only unread/archived). `audio_status`: pending/generating/ready/failed. `article.status`: pending/processing/ready/failed. These match D1 CHECK constraints exactly.
- **IDs:** `secrets.token_urlsafe(16)` for article/tag IDs, `secrets.token_urlsafe(32)` for session IDs.
- **Timestamps:** `now_iso()` from `utils.py` everywhere.
- **R2 keys:** `articles/{article_id}/{suffix}` (e.g., `content.html`, `metadata.json`, `audio.mp3`, `thumbnail.webp`). Helper: `articles.storage.article_key()`.
- **Duplicate URL detection:** Checks `original_url`, `final_url`, AND `canonical_url`.
- **Two tag routers:** `tags.routes.router` (CRUD at `/api/tags`) and `tags.routes.article_tags_router` (associations at `/api/articles/{id}/tags`).
- **FTS5 search:** Always use `INNER JOIN articles_fts` with `ORDER BY bm25(articles_fts, 10.0, 5.0, 1.0)` (title 10×, excerpt 5×, content 1×) — never subquery.
- **Queue dispatch:** `entry.py` routes queue messages by `body["type"]` to handlers in `QUEUE_HANDLERS` dict.
- **JSON logging:** `print(json.dumps({...}))` — Workers Logs captures stdout.

## Skills

Use these skills when working on this project:

- **`/cloudflare`** — General Cloudflare platform reference (Workers, D1, R2, KV, Queues, AI, Browser Rendering, etc.). Covers configuration, patterns, and decision trees for all Cloudflare services. Source: [dmmulroy/cloudflare-skill](https://github.com/dmmulroy/cloudflare-skill)
- **Python Workers skill** — Python/Pyodide-specific reference for Cloudflare Workers. Covers the JS/Python FFI boundary, JsProxy conversion, async constraints, package compatibility, and testing strategies. Draft at: [planet_cf PR #7](https://github.com/adewale/planet_cf/pull/7) (files under `docs/tmp/`)
- **`logging-best-practices`** — Wide events / canonical log lines pattern for observability

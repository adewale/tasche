# Tasche Codebase & Documentation Audit — 2026-06-12

> **Status: all findings addressed** in the follow-up commits on this branch
> (see CHANGELOG `[Unreleased]`). H2 is mitigated in-Worker (terminal-failure
> marking + explicit `max_retries` + retry backoff) rather than with a
> dead-letter queue, so no new infrastructure needs provisioning.
>
> A subsequent adversarial verification pass (24 agents) re-checked every
> finding and hunted for regressions. It caught one the first round missed:
> the new strict CSP omitted `media-src`, which would have blocked the audio
> player's `blob:` source — fixed by adding `media-src 'self' blob:`. It also
> tightened M3 (the raw audio auth path now emits the same loud 500 as other
> endpoints on `DISABLE_AUTH` misconfiguration) and closed the last
> unscoped article UPDATE (the atomic TTS pending→generating guard) for L1.

**Scope:** Full repository — backend (`src/`, ~7k lines), frontend (`frontend/`, ~16k lines),
tests (~24k lines, 1,243 unit tests), migrations, CI, Makefile, `readability-worker/`,
scripts, and all documentation (`README.md`, `CLAUDE.md`, `docs/`, `specs/`).

**Method:** Manual review of the security-critical core (entry point, auth, FFI wrappers,
security/observability middleware), four parallel deep reviews (business logic, frontend,
docs accuracy, tests/CI/infra), live checks against the production deployment, and a full
run of the quality gates (`ruff` clean, `ruff format` clean, 1,243/1,243 unit tests pass).

**Overall assessment:** This is a well-engineered codebase. The FFI boundary design
(`SafeEnv`/`Safe*` wrappers), auth flow (CSRF state, allowlist re-check with revocation,
session rotation), SSRF protections (validated pre- and post-redirect), FTS5 query
sanitization, R2 path-traversal guards, mock-fidelity tests, and property-based tests are
all genuinely strong. Documentation matches the code with few exceptions. The findings
below are concentrated in deployment/configuration hardening and operational robustness
rather than application logic.

---

## High

### H1. Security headers (CSP/X-Frame-Options/HSTS) never reach the HTML document
- **Where:** `src/security.py` (middleware), `src/entry.py:330-342` (routing), no `assets/_headers` file.
- **Evidence:** `SecurityHeadersMiddleware` runs inside the FastAPI/ASGI app, which only
  handles `/api/*`. Static assets — including `index.html`, the page that actually renders
  saved-article HTML — are served directly from the ASSETS binding. Confirmed live:
  `curl -I https://tasche-production.../` returns no `Content-Security-Policy`,
  `X-Frame-Options`, `X-Content-Type-Options`, or `Strict-Transport-Security`. The CSP
  comment block in `security.py` discusses protecting the frontend, but the frontend never
  receives those headers; they are only applied to JSON API responses where they do little.
- **Why it matters:** Saved articles are attacker-influenced content (any page on the web).
  DOMPurify is correctly applied client-side (see Positives), but CSP is the standard
  backstop for a sanitizer bypass, and clickjacking protection is absent entirely.
- **Fix:** Add an `assets/_headers` file (Workers Assets supports it) carrying the CSP,
  XFO, nosniff, and HSTS headers for `/*`, or route asset responses through a small header
  wrapper in `Default.fetch`.

### H2. Queue pipeline has no dead-letter handling and no stuck-state recovery
- **Where:** `wrangler.jsonc` consumers (all three envs), `src/entry.py:464-528`,
  `src/articles/processing.py`, `src/tts/processing.py`.
- **Evidence:** Consumers configure only `max_batch_size`/`max_batch_timeout` — no
  `max_retries`, no `dead_letter_queue`. Cloudflare defaults to 3 retries, then drops the
  message. Handlers `raise` to trigger retry on transient failures; after the third
  failure the message vanishes while the article row stays `processing`/`pending` (or
  `audio_status='generating'`) forever. There is no reconciliation sweep; `articles/health.py`
  is link-rot checking, not pipeline recovery.
- **Mitigations already present:** failures are visible in wide-event logs, and manual
  re-process/regenerate endpoints exist (`src/articles/routes.py:1055-1075`), so recovery
  is possible per-article by hand.
- **Fix:** Configure `max_retries` + a DLQ (or at minimum log-and-mark-failed in a DLQ
  consumer), and/or add a cheap reconciliation pass (e.g., on list requests or a cron
  trigger) that flips articles stuck in transient states for >N hours to `failed` so the
  UI offers retry.

---

## Medium

### M1. `make test` / `make setup` fail on a fresh clone (CI masks it)
- **Where:** `Makefile:28-31` (`setup` runs bare `uv sync`), `pyproject.toml` (no
  `[tool.uv] default-groups`), `.github/workflows/ci.yml` (`uv sync --group test`).
- **Evidence:** Reproduced in this audit: bare `uv sync` installs only runtime deps;
  `uv run pytest` then falls back to a system pytest and dies with
  `ModuleNotFoundError: No module named 'fastapi'`. CI passes only because it explicitly
  syncs `--group test` first.
- **Fix:** Add `[tool.uv] default-groups = ["test"]` to `pyproject.toml`, or change
  `setup` to `uv sync --group test --group workers`.

### M2. E2E targets disable auth on live staging with no interrupt safety
- **Where:** `Makefile:4-19` (`test-e2e`), `Makefile:59-77` (`verify-staging`).
- **Evidence:** Both targets `wrangler secret put DISABLE_AUTH=true` on the
  internet-reachable staging worker, run tests, then delete the secret. There is no shell
  `trap`: Ctrl-C or a crash between put and delete leaves staging fully open. The
  restore step also swallows failures (`yes | npx wrangler secret delete ... 2>/dev/null`
  with no exit-code check in `test-e2e`).
- **Fix:** Wrap in `trap '...' EXIT INT TERM`, verify auth is re-enabled (curl for 401)
  after restore, and fail loudly if the delete fails.

### M3. `DISABLE_AUTH` production guard does not cover the default deploy path
- **Where:** `src/auth/dependencies.py:96-103`, `src/entry.py:359-361`, `wrangler.jsonc`.
- **Evidence:** The guard blocks `DISABLE_AUTH` only when `WORKER_ENV == "production"`.
  `WORKER_ENV` is set only in the `production` and `staging` envs; the *top-level* config —
  the documented quick-setup/deploy-button path (`workers_dev: true`, "deploy without
  --env") — sets no `WORKER_ENV`, so the guard is inert exactly where a novice operator's
  real instance lives. Only the operator can set the secret, so this is a footgun rather
  than a direct vulnerability.
- **Fix:** Treat "unset `WORKER_ENV`" as production for this check (allow bypass only when
  `WORKER_ENV` is explicitly `development`/`staging`), matching the deploy docs' framing of
  the default env as a real deployment.

### M4. Audio endpoint: no Range support, `Cache-Control: public` on authenticated content, no logging
- **Where:** `src/entry.py:382-462` (`_serve_audio`).
- **Evidence:** The handler ignores the `Range` header and always returns 200 with the full
  body; iOS Safari in particular is unreliable at seeking (and sometimes playing) audio
  from servers without Range support — notable for a PWA with an audio player. The response
  sets `Cache-Control: public, max-age=86400, immutable` although it is session-gated
  content (`public` invites shared-cache storage). Because this path bypasses ASGI, it also
  emits no wide event (audio requests are invisible in observability) and gets no security
  headers.
- **Fix:** Honor `Range` via R2 range reads (`get(key, {range: ...})` returning 206),
  switch to `private`, and `begin_event`/`emit_event` around the handler.

### M5. `docs/tts-models.md` contradicts the shipped TTS configuration
- **Where:** `docs/tts-models.md:9,31,35` vs `src/tts/processing.py:49-56`, `wrangler.jsonc`.
- **Evidence:** The doc says `melotts` is the default and that Aura returns
  "ReadableStream (MP3)". Code: `_DEFAULT_TTS_MODEL = "aura-2-en"`, all three wrangler envs
  set `TTS_MODEL: "aura-2-en"`, and Aura-2 is requested as Opus in an OGG container
  (`audio.ogg`). Anyone following this doc misunderstands both cost (~115× difference per
  the doc's own table) and output format. Related: the `tts/processing.py` module docstring
  still says audio is stored at `audio.mp3`.
- **Fix:** Update the doc (and docstring) to reflect the aura-2-en/Opus default.

### M6. The raw-JS request path (`_serve_audio`) has no test coverage
- **Where:** `src/entry.py:382-462`; `tests/`.
- **Evidence:** The path can't run under TestClient (it constructs `js.Response` across the
  FFI), and no e2e test downloads audio. Only `_authenticate_raw_request` is covered.
  This is the one substantial untested branch in an otherwise thoroughly tested codebase.
- **Fix:** Add a staging e2e test (save → TTS → GET audio, assert 200/content-type, and a
  Range request once M4 is fixed) to `tests/e2e/`.

---

## Low

### L1. Defense-in-depth gaps in SQL write scoping
- **Where:** `src/articles/routes.py:47` (`_enqueue_or_fail` rollback), `:256`, `:1062-1072`;
  `src/articles/processing.py` (`_mark_failed`, initial status update).
- **Evidence:** Several `UPDATE articles ... WHERE id = ?` statements omit `user_id`.
  All are currently safe — the article ID comes from `_get_user_article`, which enforces
  ownership — but the safety is implicit and one refactor away from an IDOR.
  `_enqueue_or_fail` also interpolates `status_field` into SQL via f-string (called only
  with the constants `"status"`/`"audio_status"` today). The PATCH endpoints' dynamic SET
  clauses were checked and are safe by construction (iteration over a server-defined
  whitelist, never user keys).
- **Fix:** Add `AND user_id = ?` to every write; replace the `status_field` parameter with
  an allowlisted enum.

### L2. Observability docstring promises tail sampling that doesn't exist
- **Where:** `src/observability.py:6-8` vs `src/wide_event.py:211-221`.
- **Evidence:** The middleware docstring describes tail sampling ("successful fast requests
  are sampled at a low rate"); `emit_event` prints 100% of events unconditionally.
  Harmless today (head_sampling_rate=1 in wrangler too), but the docs lie.
- **Fix:** Delete the claim or implement sampling. Also consider
  `json.dumps(data, default=str)` in `emit_event` so a stray non-serializable field (e.g.,
  a leaked JsProxy) can't crash the `finally` block.

### L3. CLAUDE.md gaps
- **Evidence:** (a) The Configuration section lists only `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET`
  as secrets, but `ALLOWED_EMAILS` is required (`src/entry.py:139` marks it required; auth
  hard-fails without it). README/deploy-guide document it correctly — only CLAUDE.md lags.
  (b) The Skills section references `/cloudflare` and `logging-best-practices` skills and a
  "Python Workers skill", none of which exist in `.claude/skills/` (only
  `implement-then-audit.md` is committed) — dead references for any environment other than
  the author's machine.

### L4. CHANGELOG is stale
- **Evidence:** Latest dated entry is `0.3.0 — 2026-03-02`; 40+ commits have landed since
  (mobile nav, search/tagging overhaul, 83-finding audit fix round, cache headers). An
  `[Unreleased]` section exists but captures only a fraction.

### L5. SPA fallback comment doesn't match observed behavior
- **Where:** `src/entry.py:338-342`.
- **Evidence:** The comment says "serve index.html for unmatched paths", but Workers Assets
  canonicalizes `/index.html`, so unmatched paths get a **307 redirect to `/`** (confirmed
  live). Harmless because routing is hash-based (fragments survive redirects), but worth a
  comment fix or serving the body directly to avoid the extra round-trip.

### L6. Minor items
- `src/articles/health.py:3` docstring says URLs are checked "periodically"; there is no
  cron trigger — checks run on-demand from article routes only.
- `SafeQueue.send` mutates the caller's dict (injects `enqueued_at`) — surprising side
  effect (`src/wrappers.py:569-573`).
- `http_fetch` timeout (`asyncio.wait_for`) abandons rather than aborts the underlying JS
  fetch (no `AbortController`) — leaked in-flight request on timeout (`src/wrappers.py:885`).
- `readability-worker/` has zero tests; mitigated by the BeautifulSoup fallback path.
- CORS allows credentialed localhost origins in all envs (`src/entry.py:82-88`) — benign
  given SameSite=Lax cookies, but could be gated to non-production.
- FastAPI docs URLs are gated on `os.environ["WORKER_ENV"]` (`src/entry.py:55`) while every
  other config read goes through the env binding — inconsistent, though moot in practice
  since `/docs` isn't under `/api/` and never reaches FastAPI in deployment.

---

## Documentation accuracy (verified claim-by-claim)

Checked against code: FTS5 bm25 weights and column order ✓, status enums vs CHECK
constraints (incl. the `'reading'` historical artifact and migration 0005 cleanup) ✓,
R2 key pattern and `article_key()` ✓, duplicate detection across all three URL columns ✓,
`QUEUE_HANDLERS` contents ✓, two tag routers ✓, session TTL ✓, Makefile targets in README ✓,
deploy-guide flow and SITE_URL auto-detection ✓, readability fallback ✓, voice default
(`athena`) ✓. Inaccuracies found are listed above (M5, L2, L3, L4, L5).
`docs/state-machines.md` and `docs/architecture.md` match the implementation.

## Tests / CI / migrations

- 1,243 unit tests pass in ~33s; ruff check and format are clean.
- CI (`ci.yml`) runs the same gates as `make check` (modulo M1's group-sync caveat); e2e is
  deliberately manual/pre-deploy.
- Migrations 0001–0008 are ordered and consistent with code: every column referenced in
  SQL exists; FTS5 external-content triggers cover insert/update/delete on all indexed
  columns; FKs are `ON DELETE CASCADE` throughout, so article/tag/user deletion can't
  orphan rows.
- Mock fidelity is explicitly tested (`test_mock_fidelity.py`: bind arity, KV TTL clock,
  R2 httpMetadata); `test_property.py` contains real Hypothesis properties (idempotent
  markdown stripping, chunking preserves content, FTS sanitizer output safety).

## Positives worth keeping

- **FFI boundary discipline:** all JS↔Python conversion centralized in `wrappers.py`;
  construction-time `SafeEnv` wrapping; documented Pyodide pitfalls (snapshot-safe PRNG,
  `getReader` vs `arrayBuffer` truncation) encoded as helpers rather than tribal knowledge.
- **Auth:** single-use KV-backed OAuth state with TTL, allowlist re-checked on every
  request with session revocation, 32-byte session tokens, refresh throttled to 1/hr.
- **SSRF defense:** private/loopback/metadata hosts blocked before fetch *and* re-validated
  after redirects, with manual hop counting.
- **Search:** FTS5 MATCH input is sanitized (quoted tokens, operators stripped); filters
  compose with bm25 ranking as documented.
- **Large-body handling:** audio streams never enter Python memory (ASGI bypass with the
  rationale documented); bounded reads elsewhere with explicit size limits.
- **Observability:** wide-events pattern implemented consistently, with per-binding timing
  recorded automatically in the Safe* wrappers and one JSON line per request/queue message.

## Suggested quick wins (smallest diffs first)

1. `pyproject.toml`: add `[tool.uv] default-groups = ["test"]` (M1).
2. `assets/_headers`: CSP + XFO + nosniff + HSTS for `/*` (H1).
3. `wrangler.jsonc`: add `max_retries` + `dead_letter_queue` to all consumers (H2, partial).
4. `Makefile`: `trap` + post-restore auth verification in `test-e2e`/`verify-staging` (M2).
5. `docs/tts-models.md` + `tts/processing.py` docstring: aura-2-en/Opus reality (M5).
6. `_serve_audio`: `Cache-Control: private` one-word change now; Range support next (M4).
7. CLAUDE.md: add `ALLOWED_EMAILS` to Configuration; prune dead skill references (L3).

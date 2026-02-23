# Readability Service Binding

**Last Updated:** February 2026

---

## Problem

Tasche's core value — "save a URL and read it later" — depends on extracting the article content from arbitrary web pages. The standard tool for this is Mozilla's Readability algorithm, used by Firefox Reader View and battle-tested on billions of pages.

The Python Worker can't use it directly. `python-readability` calls `js.eval()` to load the Readability JS engine, and Cloudflare Workers blocks `eval()` with `EvalError: Code generation from strings disallowed`. There is no workaround — `js.Function()` is equivalent to `eval()` and is also blocked, `allow_eval_during_startup` is already the default and doesn't help, and eager-importing exceeds startup time limits.

The current fallback is a hand-rolled BeautifulSoup heuristic extractor (`src/articles/extraction.py`). It identifies the main content container and strips boilerplate, but lacks Readability's scoring algorithm: no ancestor-based score propagation, no link density calculation, no class/ID weight scoring, no retry with relaxed flags. Estimated fidelity: 60-75% compared to Readability's 100%.

## Solution: Service Binding to a JS Worker

Deploy a separate JavaScript Worker that bundles `@mozilla/readability` and `linkedom` (a lightweight DOM parser). The Python Worker calls it via Cloudflare Service Binding RPC — in-process communication, not a network call.

```
┌─────────────────────────────────────────────────┐
│  Python Worker (tasche)                         │
│                                                 │
│  process_article()                              │
│    ├─ fetch page HTML                           │
│    ├─ env.READABILITY.parse(html, url)  ───────────► JS Worker (readability-worker)
│    │     (Service Binding RPC)                  │       │
│    │                                            │       ├─ linkedom: parse HTML → DOM
│    │  ◄─ { title, html, excerpt, byline } ──────────────┤
│    │     (plain JS object → _to_py_safe)        │       └─ Readability: extract article
│    ├─ download images                           │
│    ├─ store to R2                               │
│    └─ update D1                                 │
│                                                 │
│  Fallback: extract_article() via BS4 heuristic  │
│  (when READABILITY binding unavailable or fails)│
└─────────────────────────────────────────────────┘
```

### Why This Approach

**100% algorithm fidelity.** The real Readability.js, not a port or approximation.

**Minimal effort.** The JS Worker is ~30 lines. The Python integration is ~20 lines in `wrappers.py` + ~10 lines in `processing.py`.

**Clean FFI boundary.** The Service Binding returns a plain JS object `{title, html, excerpt, byline}` — four string fields. This crosses the FFI via `_to_py_safe()` in `SafeReadability.parse()`, same pattern as every other binding. No new FFI complexity class.

**Graceful degradation.** The BS4 extractor stays as fallback. Local dev (no JS Worker running) works transparently. If the Service Binding fails at runtime, the pipeline retries with BS4.

**Negligible latency.** Service Binding RPC is in-process V8 communication (~1-5ms), dominated by the HTTP fetch that already happens to retrieve the page.

## RPC Interface

### Method: `parse(html, url)`

**Parameters:**
- `html` (string) — Raw HTML of the fetched page
- `url` (string) — The final URL after redirects (used by Readability for resolving relative URLs)

**Returns:** Plain JS object with exactly four fields:

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Article title extracted by Readability |
| `html` | string | Clean article HTML (Readability's `content` field) |
| `excerpt` | string | Short summary/description |
| `byline` | string \| null | Author name if detected |

This matches the return contract of `extract_article()` in `extraction.py` exactly. The processing pipeline doesn't need to know which extractor ran.

**Error behavior:** If Readability returns `null` (page isn't an article), the method returns `{title: '', html: '', excerpt: '', byline: null}`. The processing pipeline checks for empty content and falls through to BS4.

## FFI Boundary

The Service Binding introduces one new FFI crossing point. It follows the existing pattern:

| Binding | Wrapper | FFI Direction |
|---------|---------|---------------|
| D1 (DB) | SafeD1 | Python→JS (bind params), JS→Python (results) |
| R2 (CONTENT) | SafeR2 | Python→JS (bytes), JS→Python (body stream) |
| KV (SESSIONS) | SafeKV | Python→JS (values), JS→Python (values) |
| Queue (ARTICLE_QUEUE) | SafeQueue | Python→JS (message dict) |
| AI | SafeAI | Python→JS (inputs), JS→Python (outputs) |
| **Readability** | **SafeReadability** | **Python→JS (html string), JS→Python (result dict)** |

`SafeReadability` is the simplest wrapper — strings cross the FFI boundary as-is (no `None→null` or `bytes→Uint8Array` conversion needed), and the result is a shallow dict of strings/null converted by `_to_py_safe()`.

The wrapper lives in `wrappers.py` alongside all other Safe* classes. `SafeEnv.__init__` wraps it at construction time, same as every other binding. Application code never touches the raw JS binding.

## Deployment

The readability-worker is a separate Cloudflare Worker deployed to the same account:

```
readability-worker/
  src/index.js        # WorkerEntrypoint with parse() RPC method
  wrangler.jsonc      # name: "readability-worker"
  package.json        # @mozilla/readability, linkedom
```

The Python Worker references it via Service Binding in `wrangler.jsonc`:
```jsonc
"services": [
  { "binding": "READABILITY", "service": "readability-worker", "entrypoint": "ReadabilityService" }
]
```

**Per-environment:** The same `services` config goes in `env.production` and `env.staging`. The readability-worker only needs one deployment (it's stateless, no per-env config).

**Local development:** Miniflare doesn't support cross-worker Service Bindings in `pywrangler dev`. The READABILITY binding will be absent, and the pipeline falls back to BS4. This is acceptable — the BS4 extractor is tested and functional for development.

## Fallback Behavior

The processing pipeline (`src/articles/processing.py`) uses a try/fallback pattern:

1. Check if `env.READABILITY` is not None
2. If available, call `await env.READABILITY.parse(html, final_url)`
3. If the call succeeds and returns non-empty content, use it
4. If the binding is absent, or the call raises, or the result is empty → fall back to `extract_article(html)` (BS4)

The `extraction_method` field in R2 metadata records which extractor ran: `"readability"` or `"bs4"`.

# Migrate Tasche boundary mechanics to generic gasket

## Summary

This PR migrates Tasche's reusable Cloudflare/Pyodide boundary mechanics to the new generic `gasket` library while keeping Tasche-specific binding names in Tasche.

## Changes

- Replaced the monolithic `src/wrappers.py` implementation with an application-local compatibility adapter over `gasket.ffi`.
- The adapter maps Tasche's existing binding-name properties (`DB`, `CONTENT`, `SESSIONS`, `ARTICLE_QUEUE`, `AI`, `READABILITY`) to gasket's generic methods (`d1`, `r2`, `kv`, `queue`, `ai`, `service`).
- Gasket remains generic and does not contain Tasche binding names or product concepts.
- The extracted gasket implementation provides generic handling for:
  - `None` → JS `null` for D1 binds.
  - JS null/undefined → Python `None` on reads.
  - Python dict/list → plain JS objects via `to_js` conversion.
  - bytes/bytearray/memoryview → JS typed arrays for binary writes.
  - ReadableStream consumption helpers.
  - D1, R2, KV, Queue, AI, Vectorize, service, Durable Object, Analytics Engine, Cache, Fetcher, and Assets bindings.

## Follow-ups

- Replace imports from `wrappers` with direct imports from `gasket.ffi` where app-specific binding names are not needed.
- Keep any Tasche binding-name convenience in a small app-local adapter.
- Add a real pinned dependency once `gasket` is tagged/published.
- Optionally adopt `gasket.deploy.validate_ready` and `gasket.testing.smoke.SmokeBase` in the deploy/smoke flow.

## Validation

- No GitHub operations were performed.
- Gasket and both wrapper files compile with `python3 -m compileall`.

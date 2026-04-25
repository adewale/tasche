# Prepare Tasche for a safe generic gasket migration

## Summary

This PR now uses the safer migration strategy discovered during testing: keep Tasche's existing `src/wrappers.py` API as an application compatibility layer while `gasket` stabilizes its generic Cloudflare/Pyodide boundary API.

The previous thin shim was too aggressive. Tasche's code and tests depend on app-specific wrapper behavior such as `SafeReadability`, Tasche's `HttpResponse` shape, observability hooks, and binding-name properties (`DB`, `CONTENT`, `SESSIONS`, `ARTICLE_QUEUE`, `AI`, `READABILITY`). Those are application compatibility concerns and should remain local until each call site is migrated intentionally.

## Changes

- Restored Tasche's wrapper API surface as the local compatibility layer.
- Added comments clarifying that this file is the app-local adapter during gasket migration.
- Keeps generic extraction direction without forcing gasket to contain Tasche-specific binding names or behavior.

## Follow-up migration plan

1. Add gasket as a pinned dependency.
2. Replace generic conversion internals with `gasket.ffi` helpers one function/class at a time.
3. Keep Tasche binding-name properties and `SafeReadability` in Tasche unless a truly generic service abstraction suffices.
4. Move call sites gradually from `wrappers` to `gasket.ffi` where no app semantics are required.
5. Run the full Tasche test suite after each increment.
6. Delete only the compatibility code that is no longer used.

## Validation

- This approach is designed to preserve the old test contract while allowing incremental extraction.
- No GitHub operations were performed.

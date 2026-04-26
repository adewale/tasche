# Incrementally migrate Tasche wrappers to CFBoundary

## Summary

This PR starts the safe migration from Tasche's application-local Cloudflare/Pyodide boundary helpers to [`cfboundary`](https://github.com/adewale/cfboundary), while preserving Tasche's existing `src/wrappers.py` API.

Tasche still owns app-specific wrapper behavior such as:

- `SafeReadability`
- Tasche's `HttpResponse` shape
- observability hooks
- binding-name properties (`DB`, `CONTENT`, `SESSIONS`, `ARTICLE_QUEUE`, `AI`, `READABILITY`)

CFBoundary owns generic boundary mechanics that are reusable across apps.

## Changes

- Adds `cfboundary @ git+https://github.com/adewale/cfboundary@v0.1.0` as a dependency.
- Delegates generic fallback/production-ready conversion helpers through CFBoundary where safe:
  - JS null helper fallback
  - JS/Python value conversion fallback
  - bytes conversion fallback
- Keeps Tasche's public wrapper API unchanged.
- Keeps Pyodide fake tests working by retaining Tasche's monkeypatchable local runtime globals.

## Why this shape

A thin wrapper replacement would be too aggressive because Tasche's app code depends on local semantics and observability. This PR intentionally migrates internals first, behind the stable local `wrappers.py` interface.

## Validation

```bash
uv lock
uv run --group test pytest tests/unit/test_wrappers.py tests/unit/test_wrappers_ffi.py -q
```

Result:

```text
173 passed
```

Full-suite validation should also be run before merge:

```bash
uv run --group test pytest -q
```

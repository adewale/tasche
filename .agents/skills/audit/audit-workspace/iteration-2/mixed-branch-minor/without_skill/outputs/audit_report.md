# Audit Report: feature/cache-improvements

## Branch Overview

**Branch:** `feature/cache-improvements`
**Base:** `main` (commit `cbacb08` -- "feat: add simple TTL cache")
**Commits to push (2):**
1. `d77b8c5` -- "Add cache improvements"
2. `2ace557` -- "fixup! forgot cache_keys function"

**Files in branch:**
- `src/cache.py`
- `tests/test_cache.py`

---

## Commit History Observations

### "fixup!" commit should be squashed before pushing

The second commit (`2ace557`) is a `fixup!` commit with the message "fixup! forgot cache_keys function." This suggests it was meant to be squashed into the first commit via interactive rebase before pushing. You should squash these two commits together so the history is clean. Pushing a `fixup!` commit looks unfinished and may confuse reviewers.

---

## Code Review: src/cache.py

### 1. Unused import: `lru_cache` (line 1)

```python
from functools import lru_cache
```

`lru_cache` is imported but never used anywhere in the file. This is dead code and should be removed. It may indicate a feature that was started and abandoned, or something copied in by mistake.

### 2. FIXME left in code: `cache_stats()` doesn't account for expired entries (line 31)

```python
# FIXME: this doesn't account for expired entries
```

The `cache_stats()` function reports `"size": len(_store)` which includes entries that have expired but haven't been evicted yet (eviction only happens on access via `cache_get`). This is a known bug flagged by the author. If this is going into production, this should either be fixed now or tracked as a known issue. A simple fix would be to filter expired entries when calculating size.

### 3. TODO comments left in code (lines 20 and 24)

```python
# TODO: add logging for cache misses
# TODO: should we track deletes in stats?
```

Two TODO comments are present. These are not blocking issues, but they represent open questions and unfinished work. Consider whether these should be addressed before merging or captured as follow-up issues.

### 4. No thread safety

The module uses global mutable state (`_store`, `_hits`, `_misses`) without any locking. If this cache is used in a multi-threaded context, there will be race conditions. This may be fine for the intended use case, but it is worth noting.

### 5. `cache_keys()` returns potentially stale keys (lines 45-46)

```python
def cache_keys() -> list[str]:
    return list(_store.keys())
```

This function returns all keys including those whose TTL has expired. Consumers may expect only valid (non-expired) keys. This is related to the FIXME on `cache_stats()`.

---

## Code Review: tests/test_cache.py

### 6. Skipped test: `test_ttl_expiration` (line 19)

```python
@pytest.mark.skip(reason="need to mock time.time for this")
```

This test is skipped with a note about needing to mock `time.time`. A core feature of this cache (TTL expiration) has no working test. This should ideally be fixed before merging -- using `unittest.mock.patch` or `freezegun` to mock time would be straightforward.

### 7. `cache_keys` is not imported or tested

The test file imports `cache_set, cache_get, cache_delete, cache_stats, cache_clear` but does **not** import or test `cache_keys`. Given that the second commit was specifically about adding `cache_keys`, the lack of a test for it is a gap.

### 8. No test for `cache_delete` returning `False`

The `test_delete` test only checks the case where a key exists and is successfully deleted. There is no test for attempting to delete a non-existent key (which should return `False`).

---

## Summary

| # | Severity | Issue |
|---|----------|-------|
| 1 | Minor | `fixup!` commit should be squashed before pushing |
| 2 | Minor | Unused import: `from functools import lru_cache` |
| 3 | Low | FIXME: `cache_stats()` size includes expired entries |
| 4 | Low | TODO comments represent unfinished work |
| 5 | Info | No thread safety on global state |
| 6 | Low | `cache_keys()` returns expired keys |
| 7 | Low | `test_ttl_expiration` is skipped |
| 8 | Minor | `cache_keys` has no test despite being added in this branch |
| 9 | Info | No test for `cache_delete` returning `False` |

**Nothing dangerous or "weird" was found** -- no secrets, no credentials, no suspicious code, no destructive operations. The code is a straightforward in-memory TTL cache implementation.

**Recommended actions before pushing:**
1. Squash the `fixup!` commit into the first commit.
2. Remove the unused `lru_cache` import.
3. Add a test for `cache_keys()`.

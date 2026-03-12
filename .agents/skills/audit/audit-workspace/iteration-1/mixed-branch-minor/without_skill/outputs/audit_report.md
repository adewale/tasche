# Audit Report: feature/cache-improvements

## Branch Overview

- **Branch:** `feature/cache-improvements`
- **Base:** `main` (commit `cbacb08` - "feat: add simple TTL cache")
- **Commits on branch (2):**
  1. `d77b8c5` - "Add cache improvements"
  2. `2ace557` - "fixup! forgot cache_keys function"

## Files Changed

- `src/cache.py` -- cache implementation (modified)
- `tests/test_cache.py` -- test file (added or modified)

## Summary of Changes

The branch adds several enhancements to a simple TTL cache module:

**src/cache.py:**
- Hit/miss tracking via module-level `_hits` and `_misses` counters
- `cache_delete()` function to remove individual keys
- `cache_stats()` function returning size, hits, misses, and hit rate
- `cache_clear()` function to reset the store and counters
- `cache_keys()` function to list all keys in the store (added in the fixup commit)
- An unused `from functools import lru_cache` import at the top of the file

**tests/test_cache.py:**
- Tests for set/get, cache miss, delete, stats
- A `test_ttl_expiration` test that is skipped (`@pytest.mark.skip`) with the reason "need to mock time.time for this"
- Note: `cache_keys` is not imported or tested despite being added

## Issues Found

### 1. Unused Import
**File:** `src/cache.py`, line 1
**Severity:** Minor (code quality)

```python
from functools import lru_cache
```

`lru_cache` is imported but never used anywhere in the file. This should be removed.

### 2. Un-squashed Fixup Commit
**Severity:** Minor (git hygiene)

The second commit (`2ace557`) is titled "fixup! forgot cache_keys function". This is a fixup commit that should be squashed into the first commit before pushing/merging. Pushing fixup commits to a shared branch clutters the history.

### 3. FIXME in cache_stats()
**File:** `src/cache.py`, line 31
**Severity:** Minor (known bug)

```python
# FIXME: this doesn't account for expired entries
```

The `cache_stats()` function reports `size` as `len(_store)`, which includes entries that have expired but have not yet been accessed (and thus not evicted). This means the reported cache size can be inaccurate. Consider either purging expired entries before counting, or documenting this as a known limitation.

### 4. TODO Comments Left In
**File:** `src/cache.py`, lines 20 and 24
**Severity:** Minor (code quality)

Two TODO comments remain:
- Line 20: `# TODO: add logging for cache misses`
- Line 24: `# TODO: should we track deletes in stats?`

These are fine if they represent future work, but worth confirming they are intentional and not forgotten tasks that should be addressed before merging.

### 5. Skipped Test
**File:** `tests/test_cache.py`, line 19
**Severity:** Minor (test coverage)

```python
@pytest.mark.skip(reason="need to mock time.time for this")
def test_ttl_expiration():
```

The TTL expiration test is skipped. This is one of the core features of the cache and should ideally have a working test before merging. Using `unittest.mock.patch` or `freezegun` to mock `time.time()` would be straightforward.

### 6. Missing Test for cache_keys()
**File:** `tests/test_cache.py`
**Severity:** Minor (test coverage)

The `cache_keys()` function was added but is neither imported in the test file nor tested. The import on line 2 only brings in `cache_set, cache_get, cache_delete, cache_stats, cache_clear` -- `cache_keys` is missing.

### 7. No .gitignore
**Severity:** Minor (repo hygiene)

There is no `.gitignore` file in the repository. Python projects typically need one to exclude `__pycache__/`, `.pyc` files, `.egg-info/`, virtual environment directories, etc.

## Security Check

- No secrets, API keys, passwords, or credentials found in the code.
- No `.env` files or key files present.
- No sensitive data detected.

## Verdict

**Nothing alarming -- safe to push with minor cleanup recommended.** The code changes are straightforward cache utility enhancements. There are no security concerns or dangerous patterns. The main items to address before pushing:

1. **Squash the fixup commit** into the first commit for a clean history.
2. **Remove the unused `lru_cache` import.**
3. **Add `cache_keys` to the test imports** and write a test for it.

The remaining items (TODOs, FIXME, skipped test, missing .gitignore) are lower priority and could be addressed in follow-up work.

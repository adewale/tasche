# Pre-Push Audit: `feature/cache-improvements`

**Base branch:** `main`
**Commits on branch:** 2

1. `Add cache improvements`
2. `fixup! forgot cache_keys function`

---

## 1. Unintended changes

1. **Unused import:** `from functools import lru_cache` is imported at `src/cache.py:1` but never used anywhere in the code. This appears to be a leftover from a removed or never-implemented approach.

## 3. Debug artifacts

2. **TODO comment:** `# TODO: add logging for cache misses` at `src/cache.py:20` -- introduced in this branch.
3. **TODO comment:** `# TODO: should we track deletes in stats?` at `src/cache.py:24` -- introduced in this branch.
4. **FIXME comment:** `# FIXME: this doesn't account for expired entries` at `src/cache.py:31` -- introduced in this branch. This one in particular flags a real correctness concern with `cache_stats()`: the reported `size` includes entries that have expired but haven't been evicted yet.

## 4. Test coverage

5. **Skipped test:** `test_ttl_expiration` is decorated with `@pytest.mark.skip(reason="need to mock time.time for this")` at `tests/test_cache.py:19`. TTL expiration is a core behavior of this cache; shipping without this test means the TTL path is unverified.
6. **No tests for `cache_keys()`:** The function `cache_keys()` added at `src/cache.py:45-46` is not imported or tested in `tests/test_cache.py`.

## 6. Commit hygiene

7. **Fixup commit not squashed:** The second commit is `fixup! forgot cache_keys function`. This should be squashed into the first commit before pushing. Fixup commits are meant to be rebased/squashed and should not appear in the final branch history.

## 7. Integration check

8. **Unused import -- `lru_cache`:** `from functools import lru_cache` at `src/cache.py:1` is imported but not applied to any function. Either use it or remove it.

---

## Verdict: **Minor**

No secrets, no conflict markers, no broken tests (the skipped test doesn't run, so nothing fails). The issues found are low-risk but worth addressing before push:

- **Squash the fixup commit** (commit hygiene -- `fixup! forgot cache_keys function`).
- **Remove the unused `lru_cache` import** (`src/cache.py:1`).
- **Address or consciously accept the TODO/FIXME comments** (`src/cache.py:20, 24, 31`).
- **Unskip or implement `test_ttl_expiration`** (`tests/test_cache.py:19`) -- consider using `unittest.mock.patch` for `time.time` instead of `time.sleep`.
- **Add a test for `cache_keys()`** -- it's new code with no coverage.

None of these are blocking, but the fixup commit in particular should be squashed before the branch is shared. Push at your discretion.

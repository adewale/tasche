# Concurrency Audit Report

**Project:** TaskRunner (`test-repo-deepdive`)
**Date:** 2026-03-08
**Scope:** Full codebase concurrency analysis — shared mutable state, missing synchronization, thread/task leaks, deadlock risk, and atomicity gaps.

---

## Executive Summary

This project has **severe concurrency defects** across its core worker pool, job submission pipeline, and REST API layer. The intermittent production failures are almost certainly caused by the issues documented below. There are at least 3 high-severity data races, 1 deadlock scenario, multiple thread leak vectors, and several atomicity gaps that together make the system fundamentally unsafe under concurrent load.

---

## Finding 1: Data Race on Shared Queue (`WorkerPool._queue`)

**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/worker.py`
**Lines:** 38, 43-44
**Severity:** Critical

The internal job queue (`self._queue`, a plain Python `list`) is read and mutated by multiple threads simultaneously with no synchronization:

```python
# Thread A (submit, any thread via Flask request):
self._queue.append(job)          # line 38

# Thread B (worker loop):
if self._queue:                  # line 43 — check
    job = self._queue.pop(0)     # line 44 — act
```

**Race scenario:** Two worker threads both see `len(self._queue) == 1`. Both enter the `if` branch. The first thread pops the item successfully. The second thread calls `pop(0)` on an empty list and raises an `IndexError`. Because this happens inside `_worker_loop`, the exception is uncaught at the loop level (only `_execute` has a try/except), which kills the worker thread silently. Over time, worker threads die off and jobs stop being processed.

Additionally, `list.append()` and `list.pop(0)` on a plain list are not guaranteed to be atomic at the Python level (CPython's GIL provides some protection, but this is an implementation detail, not a language guarantee, and breaks on other runtimes like PyPy or Jython).

**Fix:** Replace `self._queue` with a `queue.Queue` (thread-safe, supports blocking `get()` with timeout, eliminating the busy-wait `sleep(0.1)` pattern as well).

---

## Finding 2: Non-Atomic Global Counter Increment (`job_counter`)

**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/worker.py`
**Lines:** 11, 36
**Severity:** High

```python
job_counter = 0                        # line 11 — global mutable state

# In submit():
job_counter = job_counter + 1          # line 36 — read-modify-write
```

**Race scenario:** This is a classic read-modify-write race. Thread A reads `job_counter` as 5, Thread B reads `job_counter` as 5 (before A writes), Thread A writes 6, Thread B writes 6. The counter loses an increment. Under sustained concurrent job submission this causes the counter to drift, producing incorrect metrics.

**Fix:** Protect the increment with `jobs_lock`, or use `threading.Lock` specifically around the counter, or replace with `itertools.count()` or an `AtomicInteger` equivalent.

---

## Finding 3: TOCTOU (Time-of-Check-to-Time-of-Use) in `submit()`

**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/worker.py`
**Lines:** 35-38
**Severity:** High

```python
def submit(self, job):
    global job_counter
    if job.id not in active_jobs:       # CHECK
        job_counter = job_counter + 1   # ACT (gap between check and act)
        active_jobs[job.id] = job
        self._queue.append(job)
```

**Race scenario:** Two Flask request threads call `submit()` with the same `job.id` simultaneously. Both pass the `if job.id not in active_jobs` check before either inserts into `active_jobs`. Result: the job is enqueued twice and the counter is incremented twice for a single logical job. This leads to duplicate job execution, corrupted results, and double-counted metrics.

**Fix:** Acquire `jobs_lock` before the check and hold it through the insertion and enqueue. The check and all mutations must be a single atomic block:
```python
with jobs_lock:
    if job.id not in active_jobs:
        job_counter += 1
        active_jobs[job.id] = job
        self._queue.append(job)
```

---

## Finding 4: Deadlock — Lock Ordering Violation Between `stats_lock` and `jobs_lock`

**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/worker.py`
**Lines:** 58-61 vs 35-38
**Severity:** Critical

In `_execute()` (worker threads), locks are acquired in order `stats_lock -> jobs_lock`:

```python
with stats_lock:                    # line 58 — acquire stats_lock first
    worker_stats["completed"] += 1
    with jobs_lock:                 # line 60 — then acquire jobs_lock
        del active_jobs[job.id]
```

Meanwhile, `submit()` is called from Flask request threads. If `submit()` were updated to acquire `jobs_lock` first (as the TOCTOU fix above requires), and if any code path inside that lock also needs `stats_lock`, the lock ordering would be `jobs_lock -> stats_lock` — the reverse order. Even without that future fix, `cancel_job()` in `server.py` (line 88) mutates `active_jobs` without any lock, creating a three-way conflict.

**Race scenario (classic deadlock):** Thread A (worker) acquires `stats_lock` and waits for `jobs_lock`. Thread B (Flask request) acquires `jobs_lock` and waits for `stats_lock`. Neither can proceed. The server hangs indefinitely — this matches the "intermittent failures" symptom because it only triggers under specific interleaving.

**Fix:** Establish a single global lock ordering (e.g., always acquire `jobs_lock` before `stats_lock`), or consolidate into a single lock. The current split into two locks provides no meaningful performance benefit and creates this deadlock risk.

---

## Finding 5: Unprotected Concurrent Mutation of `active_jobs` Dict

**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/worker.py` (lines 37, 61)
**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/server.py` (lines 33, 54-56, 68, 85-88)
**Severity:** Critical

The `active_jobs` global dict is read and written from multiple threads (worker threads and Flask request handler threads) with no consistent locking:

| Operation | File | Line | Lock held? |
|-----------|------|------|------------|
| `active_jobs[job.id] = job` | worker.py | 37 | No |
| `del active_jobs[job.id]` | worker.py | 61 | `stats_lock` + `jobs_lock` |
| `len(active_jobs)` | server.py | 33 | No |
| `job_id in active_jobs` | server.py | 54 | No |
| `active_jobs[job_id].to_dict()` | server.py | 55 | No |
| `list(active_jobs.values())` | server.py | 68 | No |
| `job_id in active_jobs` | server.py | 85 | No |
| `del active_jobs[job_id]` | server.py | 88 | No |

**Race scenario:** A worker thread deletes a completed job from `active_jobs` (line 61) at the same moment a Flask request thread is iterating `active_jobs.values()` (line 68). In CPython, this can raise `RuntimeError: dictionary changed size during iteration`. On non-CPython runtimes, this is a full data race with undefined behavior.

Another scenario: `cancel_job()` checks `if job_id in active_jobs` (line 85) then does `del active_jobs[job_id]` (line 88). A worker thread could delete the same job between the check and the delete, causing a `KeyError`.

**Fix:** All access to `active_jobs` must go through `jobs_lock`. Consider wrapping it in a thread-safe container or using `concurrent.futures.ThreadPoolExecutor` instead of the hand-rolled worker pool.

---

## Finding 6: Data Race on `worker_stats` in Error Path

**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/worker.py`
**Lines:** 67, 71
**Severity:** High

In the `except` block of `_execute()`, `worker_stats` is mutated without holding `stats_lock`:

```python
except Exception as e:
    job.status = "failed"
    job.error = str(e)
    worker_stats["failed"] = worker_stats["failed"] + 1   # line 67 — NO lock
    if job.retries < job.max_retries:
        job.retries += 1
        worker_stats["retries"] += 1                       # line 71 — NO lock
        self._queue.append(job)
```

Compare with the success path (line 58-59) which does hold `stats_lock`. The error path was written inconsistently.

**Race scenario:** Two worker threads both fail their jobs simultaneously. Both read `worker_stats["failed"]` as 3, both write 4. One failure is lost from the count. The same race applies to `worker_stats["retries"]`.

**Fix:** Acquire `stats_lock` in the error path just as the success path does.

---

## Finding 7: Race Condition in Job ID Generation

**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/server.py`
**Line:** 33
**Severity:** High

```python
job_id = f"job_{len(active_jobs) + 1}"
```

**Race scenario:** Two concurrent POST requests both call `len(active_jobs)` before either inserts a new job. Both get the same length (e.g., 5) and generate the same `job_id` ("job_6"). The second insertion overwrites the first job in `active_jobs`, silently losing it. The first submitter gets a job ID that now points to someone else's job.

This is compounded by the fact that `active_jobs` is modified without locks, so `len()` could return an inconsistent value during concurrent modifications.

**Fix:** Use a thread-safe ID generator (e.g., `uuid.uuid4()`, or a locked monotonic counter).

---

## Finding 8: Thread Leak on Worker Pool Scale-Down

**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/worker.py`
**Lines:** 79-89
**Severity:** Medium

```python
def scale(self, count):
    if count > self.num_workers:
        for i in range(self.num_workers, count):
            t = threading.Thread(target=self._worker_loop, args=(i,), daemon=True)
            t.start()
            self.workers.append(t)
    elif count < self.num_workers:
        self.num_workers = count   # Just changes the number, doesn't stop threads
```

**Race scenario:** On scale-down, old worker threads continue running indefinitely. They still consume CPU (busy-looping with `sleep(0.1)`) and still compete for jobs on the queue. The `self.num_workers` attribute is reduced, but that attribute is only used in `start()` and `scale()` — the running threads never check it. Repeated scale-up/scale-down cycles accumulate leaked threads.

On scale-up, `self.num_workers` is never updated to the new count (line 80-85 only appends new threads but does not update `self.num_workers`), so subsequent scale operations use a stale base count.

**Fix:** Implement per-thread shutdown signals (e.g., a threading.Event per worker), and update `self.num_workers` on scale-up.

---

## Finding 9: Incomplete Shutdown — Threads Never Joined

**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/worker.py`
**Lines:** 91-92
**Severity:** Medium

```python
def shutdown(self):
    self.running = False
    # Doesn't join threads — just sets flag and returns
```

`self.running` is a plain `bool` shared across threads with no memory barrier. While CPython's GIL makes the write visible eventually, there's no guarantee of when. Workers could process additional jobs after `shutdown()` returns. Furthermore, because workers sleep for 0.1s in their loop, there's a window where a worker reads `self.running = True`, sleeps, then wakes up and processes one more job after the application considers itself shut down.

No `thread.join()` calls means the caller cannot know when workers have actually stopped. This matters for clean teardown, testing, and graceful shutdown in production.

**Fix:** Use a `threading.Event` for the shutdown signal, call `join()` on all worker threads in `shutdown()` with a timeout.

---

## Finding 10: Mutable Reference Leak via `get_stats()`

**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/worker.py`
**Lines:** 74-76
**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/server.py`
**Lines:** 95-98
**Severity:** Medium

```python
def get_stats(self):
    return worker_stats           # Returns direct reference to mutable global

# In server.py:
stats = pool.get_stats()          # Flask handler gets a mutable reference
return jsonify({"stats": stats})
```

**Race scenario:** While `jsonify()` is iterating over `stats` to serialize it, a worker thread modifies `worker_stats["completed"]` or `worker_stats["failed"]`. This can produce an inconsistent snapshot (e.g., `completed` count from before a job finished, `failed` count from after). In the worst case, if the dict's internal structure changes during iteration (unlikely in CPython but possible with non-trivial dicts), this could raise a `RuntimeError`.

**Fix:** Return a copy of the stats dict under the `stats_lock`:
```python
def get_stats(self):
    with stats_lock:
        return dict(worker_stats)
```

---

## Finding 11: Concurrent Mutation of `_cache` in Server

**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/server.py`
**Lines:** 15, 43, 51-52, 56, 116-119
**Severity:** Medium

The `_cache` dict is read and written by concurrent Flask request handler threads without synchronization:

- `_cache[job_id] = job.to_dict()` (line 43, during POST)
- `if job_id in _cache` / `return jsonify(_cache[job_id])` (lines 51-52, during GET)
- `_cache[job_id] = result` (line 56, during GET)
- `len(_cache)` and iteration in `get_metrics()` (lines 116-119)

**Race scenario:** While `get_metrics()` iterates `_cache.values()` (line 118-119), a concurrent POST request inserts a new entry. This causes `RuntimeError: dictionary changed size during iteration` or an inconsistent count. This is particularly likely under load because `get_metrics()` does a full scan of the cache.

**Fix:** Protect `_cache` with a lock, or use a thread-safe LRU cache (e.g., `functools.lru_cache` for read-through, or a bounded `collections.OrderedDict` with a lock).

---

## Finding 12: `job.status` Written from Multiple Threads Without Synchronization

**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/worker.py`
**Lines:** 52-54, 64
**File:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive/src/server.py`
**Line:** 87
**Severity:** Medium

```python
# Worker thread (worker.py):
job.status = "running"       # line 52
job.status = "completed"     # line 54
job.status = "failed"        # line 64

# Flask request thread (server.py):
job.status = "cancelled"     # line 87
```

**Race scenario:** A Flask request thread sets `job.status = "cancelled"` at the exact moment a worker thread sets `job.status = "completed"`. The final value depends on which write lands last — the job could appear "completed" to the user even though they explicitly cancelled it, or vice versa. The cancel operation also deletes from `active_jobs` without stopping the running job, so the worker continues executing a job that has been removed from tracking.

**Fix:** Use a lock per job for status transitions, or use an enum with a state machine that rejects invalid transitions (e.g., once "cancelled", do not transition to "completed").

---

## Summary Table

| # | File | Lines | Issue | Severity |
|---|------|-------|-------|----------|
| 1 | `src/worker.py` | 38, 43-44 | Data race on `self._queue` (unsynchronized list used as concurrent queue) | Critical |
| 2 | `src/worker.py` | 11, 36 | Non-atomic read-modify-write on `job_counter` | High |
| 3 | `src/worker.py` | 35-38 | TOCTOU: check-then-act on `active_jobs` without lock in `submit()` | High |
| 4 | `src/worker.py` | 58-61 | Deadlock: `stats_lock -> jobs_lock` ordering conflicts with reverse acquisition | Critical |
| 5 | `src/worker.py`, `src/server.py` | multiple | `active_jobs` dict mutated from multiple threads without consistent locking | Critical |
| 6 | `src/worker.py` | 67, 71 | `worker_stats` mutated without lock in error path | High |
| 7 | `src/server.py` | 33 | Race condition in job ID generation (`len(active_jobs)` not atomic) | High |
| 8 | `src/worker.py` | 79-89 | Thread leak on scale-down; `num_workers` not updated on scale-up | Medium |
| 9 | `src/worker.py` | 91-92 | Shutdown does not join threads; no memory barrier on `self.running` | Medium |
| 10 | `src/worker.py`, `src/server.py` | 74-76, 95-98 | Mutable reference to `worker_stats` leaked to callers | Medium |
| 11 | `src/server.py` | 15, 43, 51-56, 116-119 | `_cache` dict mutated by concurrent request handlers without lock | Medium |
| 12 | `src/worker.py`, `src/server.py` | 52-54, 64, 87 | `job.status` written from worker and request threads without synchronization | Medium |

---

## Recommended Remediation Priority

### Immediate (fix before next deployment)

1. **Replace `self._queue` with `queue.Queue`** (Finding 1). This eliminates the most crash-prone race and also removes the inefficient busy-wait polling pattern. `queue.Queue.get(timeout=0.1)` replaces both the check and the sleep.

2. **Establish consistent lock ordering or consolidate locks** (Finding 4). Pick one order (e.g., `jobs_lock` first, `stats_lock` second) and enforce it everywhere. Better yet, merge them into a single lock since the current separation provides no benefit and creates the deadlock.

3. **Lock all access to `active_jobs`** (Findings 3, 5, 7). Every read and write to `active_jobs` must be under `jobs_lock`, including the Flask route handlers in `server.py`. This also fixes the TOCTOU in `submit()` and the race in `cancel_job()`.

4. **Lock `worker_stats` in the error path** (Finding 6). Add `with stats_lock:` around the error-path mutations to match the success path.

5. **Use a thread-safe ID generator** (Finding 7). Replace `f"job_{len(active_jobs) + 1}"` with `uuid.uuid4()` or a locked counter.

### Short-term (within the sprint)

6. **Fix thread lifecycle management** (Findings 8, 9). Implement proper shutdown with `join()`, and make scale-down actually stop excess workers.

7. **Return copies from `get_stats()`** (Finding 10). Snapshot under lock.

8. **Protect `_cache`** (Finding 11). Add a lock, or better, replace with a bounded thread-safe cache.

9. **Add job state machine** (Finding 12). Prevent conflicting status transitions from different threads.

### Architectural recommendation

The hand-rolled `WorkerPool` with manual thread management, global mutable state, and split locks is the root cause of nearly every finding. Consider replacing it with `concurrent.futures.ThreadPoolExecutor`, which handles thread lifecycle, work queuing, and shutdown correctly out of the box. Alternatively, the project already lists `celery` in `requirements.txt` — using Celery with Redis as the broker would eliminate the entire class of in-process concurrency bugs by moving to a process-based task queue with proper message passing.

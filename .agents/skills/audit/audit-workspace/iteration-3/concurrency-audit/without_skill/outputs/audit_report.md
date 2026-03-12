# Concurrency Audit Report: TaskRunner

**Project:** TaskRunner (Python task queue with worker pools)
**Audit Date:** 2026-03-08
**Audited Files:**
- `src/worker.py`
- `src/server.py`
- `src/job.py`
- `src/utils.py`
- `tests/test_worker.py`
- `tests/test_server.py`
- `tests/test_utils.py`

---

## Executive Summary

The TaskRunner codebase contains **multiple critical concurrency bugs** that directly explain the intermittent production failures. The most severe issues are: a potential deadlock from inconsistent lock ordering, race conditions on shared mutable state accessed by multiple threads without synchronization, a non-thread-safe queue implementation, and non-atomic counter increments. Additionally, the Flask server accesses shared worker state without any locking, creating further data races in the HTTP request path.

**Critical issues found:** 7
**High-severity issues found:** 5
**Medium-severity issues found:** 4
**Low-severity issues found:** 3

---

## Critical Issues

### CRIT-1: Deadlock from Inconsistent Lock Ordering

**File:** `src/worker.py`, lines 56-61 and 33-38
**Severity:** Critical

The `_execute` method acquires `stats_lock` first, then `jobs_lock` (lines 58-61):

```python
with stats_lock:
    worker_stats["completed"] += 1
    with jobs_lock:
        del active_jobs[job.id]
```

Meanwhile, the `submit` method accesses `active_jobs` (which is guarded by `jobs_lock` in `_execute`) without any lock, but could be extended to also touch `worker_stats`. More importantly, the `cancel_job` endpoint in `server.py` (line 88) directly mutates `active_jobs` without holding `jobs_lock` at all, while a worker thread may hold `stats_lock` and be waiting for `jobs_lock`. If any code path ever acquires `jobs_lock` then `stats_lock` (the reverse order), a classic ABBA deadlock occurs. Even as-is, the inconsistency of sometimes locking and sometimes not locking `jobs_lock` creates undefined behavior.

**Impact:** Complete application hang. Workers stop processing jobs, no new jobs are accepted. Requires process restart.

**Fix:** Establish a single, consistent lock ordering across the entire codebase. Better yet, use a single lock for all shared state, or use thread-safe data structures like `queue.Queue`.

---

### CRIT-2: Race Condition on Shared Queue (`self._queue`)

**File:** `src/worker.py`, lines 24, 38, 42-44, 72
**Severity:** Critical

`self._queue` is a plain Python list used as a work queue shared across all worker threads with zero synchronization:

```python
self._queue = []  # shared list, no synchronization
```

Multiple threads call `self._queue.pop(0)` (line 44) and `self._queue.append(job)` (lines 38, 72). The check-then-act pattern on lines 42-44 is a textbook TOCTOU (time-of-check-time-of-use) race:

```python
if self._queue:                    # Thread A checks: queue has 1 item
    job = self._queue.pop(0)       # Thread B already popped it -> IndexError
```

While CPython's GIL makes individual list operations atomic, the compound check-then-pop is **not** atomic. When two workers both see `self._queue` as non-empty and both attempt `pop(0)`, one will get an `IndexError`.

**Impact:** Worker threads crash with `IndexError`. With `daemon=True` threads, these crashes are silent -- the worker simply disappears, reducing throughput with no error reporting. Under load, this progressively kills workers until the pool is depleted.

**Fix:** Replace `self._queue` with `queue.Queue` from Python's standard library, which provides thread-safe `get()` and `put()` with proper blocking semantics.

---

### CRIT-3: Non-Atomic Increment of `job_counter`

**File:** `src/worker.py`, lines 11, 36
**Severity:** Critical

```python
job_counter = 0           # non-atomic counter (line 11)
...
job_counter = job_counter + 1  # non-atomic increment (line 36)
```

The read-modify-write on `job_counter` is not protected by any lock. Under concurrent submissions, two threads can read the same value, both increment to the same result, and one increment is lost. This is a classic lost-update race condition.

**Impact:** Inaccurate job counting, potential duplicate job IDs if `job_counter` is used for ID generation elsewhere, and unreliable metrics.

**Fix:** Protect with a lock, or use `itertools.count()` or `threading.Lock` around the increment.

---

### CRIT-4: TOCTOU Race in `submit()`

**File:** `src/worker.py`, lines 34-38
**Severity:** Critical

```python
def submit(self, job):
    global job_counter
    if job.id not in active_jobs:       # check
        job_counter = job_counter + 1   # act
        active_jobs[job.id] = job       # act
        self._queue.append(job)         # act
```

The check (`job.id not in active_jobs`) and the subsequent mutations are not atomic. Two threads can simultaneously check for the same `job.id`, both find it absent, and both insert it -- leading to duplicate job processing and a corrupted counter.

**Impact:** Duplicate job execution, wasted resources, potential data corruption if jobs have side effects (file writes, API calls, etc.).

**Fix:** Wrap the entire check-and-insert block in a lock.

---

### CRIT-5: Unsynchronized Mutation of `active_jobs` from Flask Request Threads

**File:** `src/server.py`, lines 33, 54, 68, 85-88
**Severity:** Critical

The Flask server accesses the global `active_jobs` dict from HTTP request handler threads without any locking:

- `create_job` reads `len(active_jobs)` (line 33) and calls `pool.submit()` which writes to it
- `get_job` reads from `active_jobs` (line 54)
- `list_jobs` iterates over `active_jobs.values()` (line 68)
- `cancel_job` reads, mutates, and deletes from `active_jobs` (lines 85-88)

Meanwhile, worker threads in `WorkerPool._execute` also mutate `active_jobs` (line 61). Dictionary mutation during iteration can raise `RuntimeError: dictionary changed size during iteration`. The `cancel_job` endpoint's `del active_jobs[job_id]` (line 88) can conflict with a worker thread's `del active_jobs[job.id]` (line 61), potentially causing a `KeyError` or corrupted dictionary state.

**Impact:** `RuntimeError` crashes on list endpoint, `KeyError` on cancel, and data corruption. These are exactly the kind of "intermittent failures" reported.

**Fix:** All access to `active_jobs` must be synchronized. Use `jobs_lock` consistently, or replace with a thread-safe data structure.

---

### CRIT-6: Race Condition on `job.status` Field

**File:** `src/worker.py`, lines 52-54, 64; `src/server.py`, line 87
**Severity:** Critical

`job.status` is read and written from multiple threads without synchronization:

- Worker threads set `job.status = "running"` (line 52), `"completed"` (line 54), or `"failed"` (line 64)
- Flask threads read `job.status` via `to_dict()` and set `job.status = "cancelled"` (server.py line 87)

There is no memory barrier or lock protecting these accesses. While CPython's GIL makes individual attribute assignments atomic at the bytecode level, the semantic race is still problematic: a Flask thread could cancel a job that a worker thread has already started executing, leading to a job that is both "cancelled" and actively running.

**Impact:** Jobs continue running after cancellation. Status reads return stale or inconsistent values.

**Fix:** Use a lock or `threading.Event` for status transitions. Implement proper cancellation with a cooperative check inside the job execution path.

---

### CRIT-7: Race Condition on `worker_stats` in Exception Handler

**File:** `src/worker.py`, lines 67, 71
**Severity:** Critical

```python
except Exception as e:
    job.status = "failed"
    job.error = str(e)
    worker_stats["failed"] = worker_stats["failed"] + 1  # no lock
    if job.retries < job.max_retries:
        job.retries += 1
        worker_stats["retries"] += 1  # no lock
        self._queue.append(job)  # no lock on queue
```

In the exception handler, `worker_stats` is updated without holding `stats_lock`, while the success path (line 59) does use `stats_lock`. This means the success path and the failure path can concurrently modify `worker_stats`, leading to lost updates.

**Impact:** Inaccurate failure and retry metrics. Under high error rates, significant count drift.

**Fix:** Acquire `stats_lock` in the exception handler as well, and use the same lock ordering.

---

## High-Severity Issues

### HIGH-1: Race Condition in Job ID Generation

**File:** `src/server.py`, line 33
**Severity:** High

```python
job_id = f"job_{len(active_jobs) + 1}"
```

Job IDs are generated based on the current length of `active_jobs`. Since jobs are removed from `active_jobs` upon completion (worker.py line 61), this creates duplicate job IDs. For example: submit 3 jobs (IDs: job_1, job_2, job_3), job_1 completes and is removed, next submission gets `len(active_jobs) == 2`, producing `job_3` again.

Additionally, concurrent requests can read the same `len()` and generate the same ID.

**Impact:** Duplicate job IDs cause cache collisions in `_cache`, job lookup returns wrong data, and the TOCTOU check in `submit()` may reject legitimate new jobs or allow duplicates.

**Fix:** Use a monotonically increasing, thread-safe counter (e.g., `itertools.count()` or `uuid.uuid4()`).

---

### HIGH-2: Worker Thread Leak on Scale-Down

**File:** `src/worker.py`, lines 79-88
**Severity:** High

```python
def scale(self, count):
    if count > self.num_workers:
        for i in range(self.num_workers, count):
            t = threading.Thread(target=self._worker_loop, args=(i,), daemon=True)
            t.start()
            self.workers.append(t)
    elif count < self.num_workers:
        self.num_workers = count  # Only updates count, threads keep running
```

Scaling down only reduces `self.num_workers` but does not signal existing threads to stop. The old threads continue running their `_worker_loop`, consuming CPU and competing for queue items. Scaling down from 10 to 2 leaves 8 ghost worker threads still active.

On scale-up, `self.num_workers` is never updated either (line 81 starts new threads but doesn't change `self.num_workers`), so subsequent scale operations use a stale count.

**Impact:** Resource leak -- unbounded thread growth over repeated scale operations. Ghost threads process jobs they shouldn't, and thread count reported by the API is incorrect.

**Fix:** Implement cooperative shutdown per-thread using per-thread `Event` objects. Update `self.num_workers` on scale-up.

---

### HIGH-3: `shutdown()` Does Not Join Threads

**File:** `src/worker.py`, lines 90-92
**Severity:** High

```python
def shutdown(self):
    self.running = False
    # Doesn't join threads -- just sets flag and returns
```

Setting `self.running = False` signals threads to stop, but without `join()`, the caller has no guarantee that threads have actually terminated. If the process exits immediately after shutdown, in-flight jobs may be silently dropped with no error or retry.

Also, `self.running` is a plain bool read by multiple threads. While CPython's GIL makes this safe at the bytecode level, on other Python implementations (PyPy, Jython) this would be a data race. The idiomatic approach is `threading.Event`.

**Impact:** Data loss on shutdown -- jobs that were mid-execution are abandoned. In tests, this causes flaky behavior because threads from one test leak into the next.

**Fix:** Call `t.join(timeout=X)` for each worker thread. Use `threading.Event` instead of a bool flag.

---

### HIGH-4: `get_stats()` Returns Mutable Reference

**File:** `src/worker.py`, lines 74-76; `src/server.py`, lines 94-98
**Severity:** High

```python
def get_stats(self):
    return worker_stats  # Returns reference to mutable global
```

Any caller (including Flask request handlers) receives a direct reference to the shared `worker_stats` dict. The Flask handler at `server.py:98` passes this reference into `jsonify()`, which iterates over it. If a worker thread modifies `worker_stats` during this iteration, `jsonify` can see inconsistent data or crash.

**Impact:** Intermittent `RuntimeError` or inconsistent metrics in the `/api/workers` endpoint.

**Fix:** Return a copy: `return dict(worker_stats)`, ideally while holding `stats_lock`.

---

### HIGH-5: Unsynchronized Mutation of `_cache` in Server

**File:** `src/server.py`, lines 15, 43, 51-56, 116-119
**Severity:** High

The `_cache` dict is read and written by Flask request handler threads without any synchronization:

- `create_job` writes to `_cache` (line 43)
- `get_job` reads and writes `_cache` (lines 51-56)
- `get_metrics` iterates `_cache.values()` (lines 118-119)

Under concurrent requests, `_cache` can be mutated during iteration in `get_metrics`, causing a `RuntimeError`.

**Impact:** Crashes on the `/api/metrics` endpoint under concurrent load.

**Fix:** Use `threading.Lock` around all `_cache` access, or use a thread-safe cache implementation.

---

## Medium-Severity Issues

### MED-1: Resource Leak -- Unclosed File Handle

**File:** `src/job.py`, lines 41-48
**Severity:** Medium

```python
def process_file(payload):
    f = open(filepath, "r")  # Never closed
    content = f.read()
    ...
    return result
```

Every `process_file` job leaks a file descriptor. Under sustained load, this will exhaust the OS file descriptor limit (typically 1024 or 4096), causing `OSError: [Errno 24] Too many open files` for all subsequent file operations, network connections, and even new thread creation.

**Fix:** Use `with open(filepath, "r") as f:`.

---

### MED-2: Zombie Subprocess in `run_transform`

**File:** `src/job.py`, lines 53-62
**Severity:** Medium

```python
proc = subprocess.Popen(["bash", script], ...)
# proc is never waited on
return {"pid": proc.pid, "started": True}
```

The subprocess is spawned but never waited on (`proc.wait()` or `proc.communicate()` is never called). This creates zombie processes that accumulate in the process table. Eventually, the system hits the maximum process limit.

**Fix:** Either call `proc.communicate()` (blocking) or implement async process tracking with cleanup.

---

### MED-3: Unbounded In-Memory Cache

**File:** `src/server.py`, lines 15, 43
**Severity:** Medium

```python
_cache = {}
```

Every job submission adds to `_cache`, and entries are never evicted. Over time, this grows without bound, consuming all available memory and eventually causing an `OutOfMemoryError`.

**Fix:** Use an LRU cache with a size limit (e.g., `functools.lru_cache` or `cachetools.LRUCache`).

---

### MED-4: No Timeout on HTTP Request in `send_notification`

**File:** `src/job.py`, line 69
**Severity:** Medium

```python
resp = requests.post(payload["url"], json=payload["data"])  # No timeout
```

If the callback URL is slow or unresponsive, this blocks the worker thread indefinitely. Since the worker pool has a fixed number of threads, a few slow callbacks can starve the entire pool.

**Fix:** Add a timeout: `requests.post(url, json=data, timeout=30)`.

---

## Low-Severity Issues

### LOW-1: Off-By-One in Pagination (`list_jobs`)

**File:** `src/server.py`, line 71
**Severity:** Low

```python
start = page * per_page  # should be (page - 1) * per_page
```

Page 1 skips the first `per_page` items, returning the second page of results. Page 0 would be needed to get the first page, which is non-standard.

**Fix:** Change to `start = (page - 1) * per_page`.

---

### LOW-2: Temp File Leak in `generate_report`

**File:** `src/job.py`, lines 77-82
**Severity:** Low

Temporary files are created with `delete=False` and never cleaned up. Over time, this fills the temp directory.

**Fix:** Track temp files for cleanup, or use `delete=True` and return the content instead of the path.

---

### LOW-3: Silent Validation Failure in `validate_payload`

**File:** `src/utils.py`, lines 47-52
**Severity:** Low

```python
try:
    json.dumps(payload)
except (TypeError, ValueError):
    pass  # Silently swallows validation failure
```

Non-serializable payloads are silently accepted, causing failures later during processing or serialization.

**Fix:** Raise an exception or return an error indicator on validation failure.

---

## Test Coverage Gaps (Concurrency-Related)

The existing test suite has significant gaps that would allow concurrency bugs to go undetected:

1. **No concurrent access tests:** No test submits jobs from multiple threads simultaneously to expose race conditions (all tests in `test_worker.py` and `test_server.py`).

2. **Sleep-based synchronization:** `test_job_completes` uses `time.sleep(2)` to wait for completion (test_worker.py line 42), making it inherently flaky and unable to reliably detect timing-dependent bugs.

3. **Shared mutable state across tests:** `test_jobs` list (test_worker.py line 8) and global `active_jobs`/`worker_stats` are mutated by tests without cleanup, causing test ordering dependencies.

4. **Trivial assertions:** `test_worker_stats` only checks `isinstance(stats, dict)` (test_worker.py line 52) -- this passes even if stats are completely wrong.

5. **Missing cancellation/scale-down tests:** No test for `DELETE /api/jobs/<id>` or `POST /api/workers/scale` (test_server.py lines 53-58).

6. **No stress tests:** No test exercises the system under load to expose race conditions, deadlocks, or resource exhaustion.

---

## Architecture Observations

1. **README claims Redis-backed queue, code uses in-memory list:** The README mentions "Redis-backed persistent queue" but the implementation uses a plain Python list. The `redis` package is in `requirements.txt` but never imported. This means there is no persistence -- all jobs are lost on restart.

2. **README mentions WebSocket notifications -- not implemented:** No WebSocket code exists anywhere in the codebase.

3. **README mentions `WEBHOOK_SECRET` -- never read:** The environment variable is documented but the code never accesses it.

4. **Celery in requirements but unused:** `celery==5.3.0` is listed in requirements.txt but never imported. The project has a hand-rolled worker pool that lacks the thread safety Celery provides.

---

## Summary of Recommended Fixes (Priority Order)

| Priority | Issue | Fix |
|----------|-------|-----|
| 1 | CRIT-2: Unsafe queue | Replace `self._queue` (list) with `queue.Queue` |
| 2 | CRIT-1: Deadlock risk | Establish single lock ordering; consider one lock for all shared state |
| 3 | CRIT-5: Unsynchronized `active_jobs` access from Flask | Protect all `active_jobs` access with `jobs_lock` |
| 4 | CRIT-4: TOCTOU in `submit()` | Wrap check-and-insert in lock |
| 5 | CRIT-3: Non-atomic counter | Use `itertools.count()` or protect with lock |
| 6 | CRIT-7: Unprotected `worker_stats` in error path | Acquire `stats_lock` in exception handler |
| 7 | CRIT-6: Race on `job.status` | Add synchronization for status transitions |
| 8 | HIGH-1: Duplicate job IDs | Use UUID or atomic counter for ID generation |
| 9 | HIGH-2: Thread leak on scale | Implement cooperative per-thread shutdown |
| 10 | HIGH-3: No thread join on shutdown | Join threads with timeout in `shutdown()` |
| 11 | HIGH-4: Mutable stats reference | Return `dict(worker_stats)` under lock |
| 12 | HIGH-5: Unsynchronized `_cache` | Add locking or use thread-safe cache |
| 13 | MED-1-4: Resource leaks and timeouts | Fix file handles, subprocesses, cache bounds, request timeouts |

---

## Conclusion

The codebase has fundamental thread safety problems throughout. The worker pool uses a plain list as a shared queue, global mutable dictionaries are accessed from multiple threads without locking, and the locking that does exist uses inconsistent ordering that risks deadlock. These issues are the most likely root cause of the intermittent production failures reported.

The recommended approach is to:
1. Replace the hand-rolled queue with `queue.Queue` (immediate fix for CRIT-2).
2. Consolidate all shared mutable state behind a single lock or use thread-safe data structures (fixes CRIT-1, CRIT-3 through CRIT-7).
3. Fix the Flask server to synchronize access to `active_jobs` and `_cache` (fixes CRIT-5, HIGH-5).
4. Consider adopting Celery (already in requirements.txt) instead of maintaining a custom worker pool -- it handles all of these concurrency concerns out of the box.

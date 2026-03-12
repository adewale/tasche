# Performance Audit Report: TaskRunner

**Project:** `/Users/ade/Documents/projects/audit-skill/audit-workspace/test-repo-deepdive`
**Date:** 2026-03-08
**Scope:** Full codebase performance review -- N+1 queries, unbounded caches, unnecessary allocations, repeated I/O, blocking operations, resource leaks, concurrency bugs

---

## Executive Summary

The TaskRunner codebase contains **18 distinct performance and reliability issues** across four source files. The most critical problems are: an unbounded in-memory cache that will cause memory exhaustion under load, multiple race conditions in the worker pool that will corrupt shared state under concurrent access, leaked file handles and zombie processes in task handlers, and repeated disk I/O on every API request. Several of these issues interact -- for example, the cache never evicts entries and the metrics endpoint iterates over the entire cache on every call, meaning response latency degrades linearly as jobs accumulate.

**Severity breakdown:** 5 Critical, 7 High, 4 Medium, 2 Low

---

## Critical Issues

### PERF-01: Unbounded In-Memory Cache (Memory Leak)
- **File:** `src/server.py`, lines 15, 43, 56
- **Pattern:** Unbounded cache / memory leak
- **Description:** The module-level `_cache` dict grows without limit. Every created job is cached (line 43), and every fetched job is also cached (line 56), but entries are never evicted or expired. Under sustained load this will exhaust process memory and cause OOM kills.
- **Impact:** Memory usage grows monotonically. With thousands of jobs per hour, the process will eventually be killed by the OS or container runtime.
- **Recommendation:** Replace with an LRU cache (`functools.lru_cache`, `cachetools.TTLCache`, or similar) with a bounded `maxsize`. Alternatively, offload to Redis (already listed in `requirements.txt` but unused).

### PERF-02: Race Conditions on Shared Mutable State (Data Corruption)
- **File:** `src/worker.py`, lines 10-12, 35-38, 43-44, 67, 72
- **Pattern:** Unsynchronized concurrent mutation
- **Description:** Multiple threads read and write `active_jobs`, `job_counter`, `worker_stats`, and `self._queue` without holding locks:
  - `active_jobs` is a plain dict mutated from multiple worker threads and the Flask request thread simultaneously (lines 37, 61, and `server.py` lines 54, 68, 85-88).
  - `job_counter` is incremented non-atomically (`job_counter = job_counter + 1`, line 36).
  - `self._queue` is a plain list used as a thread-shared queue with no synchronization. The check-then-pop on lines 42-44 is a classic TOCTOU race: one thread checks `if self._queue`, another pops the last element, and the first thread's `pop(0)` raises `IndexError`.
  - `worker_stats["failed"]` is incremented without a lock (line 67).
- **Impact:** Under concurrent load: corrupted job counts, lost jobs, `IndexError` crashes, and silently wrong metrics.
- **Recommendation:** Replace `self._queue` with `queue.Queue` (thread-safe). Protect `active_jobs` and `worker_stats` mutations with their respective locks consistently. Use `threading.Lock` or `threading.atomic` for `job_counter`.

### PERF-03: Potential Deadlock from Lock Ordering Violation
- **File:** `src/worker.py`, lines 58-61 vs. 35-37
- **Pattern:** Deadlock / inconsistent lock ordering
- **Description:** In `_execute()`, the code acquires `stats_lock` then `jobs_lock` (lines 58-61). But `submit()` accesses `active_jobs` (which should be guarded by `jobs_lock`) without any lock, and if locking were added there it would create an opposite acquisition order. Any future fix that adds locking in `submit()` in the natural order (jobs_lock then stats_lock) will deadlock with `_execute()`.
- **Impact:** Latent deadlock that will manifest when lock usage is corrected, causing complete worker pool stall.
- **Recommendation:** Establish a single global lock ordering (always acquire `jobs_lock` before `stats_lock`), or consolidate into a single lock. Better yet, use `queue.Queue` to eliminate the need for manual locking on the queue.

### PERF-04: Leaked File Handles (Resource Exhaustion)
- **File:** `src/job.py`, lines 42-48
- **Pattern:** Resource leak
- **Description:** `process_file()` opens a file with `open(filepath, "r")` but never closes the handle. Each invocation leaks one file descriptor.
- **Impact:** Under load, the process will hit the OS file descriptor limit (`ulimit -n`, typically 1024), after which all file operations, socket creation, and new connections will fail with `OSError: [Errno 24] Too many open files`.
- **Recommendation:** Use a `with` statement: `with open(filepath, "r") as f:`.

### PERF-05: Zombie Processes (Process Table Exhaustion)
- **File:** `src/job.py`, lines 54-62
- **Pattern:** Resource leak / zombie processes
- **Description:** `run_transform()` spawns a subprocess via `Popen` but never calls `proc.wait()` or `proc.communicate()`. The child process becomes a zombie when it terminates, occupying a slot in the process table indefinitely.
- **Impact:** Accumulated zombie processes will eventually exhaust the process table, preventing any new processes from being spawned on the system.
- **Recommendation:** Either call `proc.communicate(timeout=...)` to wait for completion, or use `subprocess.run()` instead of `Popen` if the result is needed synchronously.

---

## High Severity Issues

### PERF-06: Repeated Config File I/O on Every Request
- **File:** `src/server.py`, lines 18-25, 31, 112
- **Pattern:** Repeated I/O / hot-path disk access
- **Description:** `get_config()` opens and parses `config.json` from disk on every single API call. It is called in both `create_job()` (line 31) and `get_metrics()` (line 112). Under load, this means hundreds or thousands of `open()` + `json.load()` syscalls per second.
- **Impact:** Unnecessary disk I/O adds latency to every request and contention on the filesystem.
- **Recommendation:** Load config once at startup and cache it. If hot-reload is needed, use a file-watcher or TTL-based reload (e.g., re-read at most once per 30 seconds).

### PERF-07: N+1 Query Pattern in batch_process
- **File:** `src/job.py`, lines 86-98
- **Pattern:** N+1 queries
- **Description:** `batch_process()` calls `_fetch_item_details(item["id"])` individually for each item in the payload, simulating one database query per item rather than fetching all items in a single batch.
- **Impact:** For a batch of N items, this makes N sequential round-trips (each with 10ms simulated latency), so a 1000-item batch takes ~10 seconds instead of ~10ms.
- **Recommendation:** Replace with a batch fetch: `_fetch_items_details([item["id"] for item in items])` that performs a single query.

### PERF-08: Blocking HTTP Call with No Timeout
- **File:** `src/job.py`, lines 66-70
- **Pattern:** Blocking I/O / no timeout
- **Description:** `send_notification()` calls `requests.post()` without a `timeout` parameter. If the remote webhook server is slow or unresponsive, the worker thread will block indefinitely.
- **Impact:** One slow webhook endpoint can permanently consume a worker thread. With only 4 workers by default, a few hanging webhooks will starve the entire pool.
- **Recommendation:** Add a timeout: `requests.post(url, json=data, timeout=(5, 30))` (5s connect, 30s read). Consider making webhook calls async or moving them to a separate pool.

### PERF-09: Metrics Endpoint Scans Entire Cache on Every Call
- **File:** `src/server.py`, lines 114-121
- **Pattern:** Unnecessary full-collection scan
- **Description:** `get_metrics()` iterates over all values in `_cache` twice (lines 118-119) to count completed and failed jobs. Since the cache is unbounded (see PERF-01), this scan becomes progressively more expensive.
- **Impact:** Metrics endpoint latency grows linearly with total job count. After millions of jobs, a single `/api/metrics` call could take seconds and block the Flask worker.
- **Recommendation:** Maintain running counters that are incremented when job status changes, rather than recomputing from the full collection.

### PERF-10: Full Collection Copy on list_jobs
- **File:** `src/server.py`, lines 67-74
- **Pattern:** Unnecessary allocation
- **Description:** `list_jobs()` calls `list(active_jobs.values())` which copies every job reference into a new list on every request, regardless of the requested page size. With thousands of active jobs, this creates a large temporary allocation on every paginated request.
- **Impact:** O(N) memory allocation per request even when only returning `per_page` (default 20) items.
- **Recommendation:** Use `itertools.islice` to avoid the full copy, or maintain an indexed data structure.

### PERF-11: String Concatenation in Loop
- **File:** `src/job.py`, lines 91-93
- **Pattern:** Quadratic string allocation
- **Description:** Inside `batch_process()`, the inner loop builds `log_msg` via repeated string concatenation (`log_msg = log_msg + f"..."`). In Python, this is O(n^2) because strings are immutable and each concatenation creates a new string object.
- **Impact:** For items with many keys, this creates significant GC pressure and wasted CPU.
- **Recommendation:** Use `"".join(...)` or an `io.StringIO` buffer.

### PERF-12: Temp Files Never Cleaned Up
- **File:** `src/job.py`, lines 74-82
- **Pattern:** Disk resource leak
- **Description:** `generate_report()` creates temporary files with `delete=False` but never deletes them. Each report generation leaves a file on disk permanently.
- **Impact:** Disk usage grows without bound. On systems with small `/tmp` partitions, this can eventually cause disk-full errors affecting the entire system.
- **Recommendation:** Either use `delete=True` (default), or track temp file paths and clean them up after the report is consumed.

---

## Medium Severity Issues

### PERF-13: Regex Compiled on Every Call
- **File:** `src/utils.py`, lines 9-10
- **Pattern:** Repeated work / unnecessary allocation
- **Description:** `parse_job_id()` calls `re.compile()` on every invocation. While CPython does cache compiled patterns internally (up to a limit), this is wasteful and the pattern should be a module-level constant.
- **Impact:** Minor CPU overhead per call, but adds up under high request rates.
- **Recommendation:** Move `pattern = re.compile(r"^job_(\d+)$")` to module level.

### PERF-14: Worker Threads Leaked on Scale-Down
- **File:** `src/worker.py`, lines 80-88
- **Pattern:** Thread leak
- **Description:** `scale()` reduces `self.num_workers` but never signals existing threads to stop. Old threads continue running their `_worker_loop`, consuming CPU and potentially processing jobs. The `workers` list also only grows (line 85), never shrinks.
- **Impact:** Repeated scale-up/scale-down cycles accumulate orphaned threads, wasting memory and CPU.
- **Recommendation:** Implement a mechanism to signal excess workers to exit (e.g., poison pill on the queue, or per-worker stop events).

### PERF-15: Shutdown Does Not Join Threads
- **File:** `src/worker.py`, lines 90-92
- **Pattern:** Unclean shutdown
- **Description:** `shutdown()` sets `self.running = False` but never calls `thread.join()`. This means in-flight jobs may be interrupted mid-execution, and the process may exit while workers are still running.
- **Impact:** Data corruption for in-flight jobs, potential resource leaks on unclean exit.
- **Recommendation:** After setting `self.running = False`, call `t.join(timeout=...)` for each thread in `self.workers`.

### PERF-16: Race Condition on Job ID Generation
- **File:** `src/server.py`, line 33
- **Pattern:** TOCTOU race
- **Description:** Job IDs are generated as `f"job_{len(active_jobs) + 1}"`. Under concurrent requests, two requests can read the same `len(active_jobs)` before either inserts, producing duplicate IDs. Additionally, since completed jobs are removed from `active_jobs`, IDs can be reused.
- **Impact:** Duplicate job IDs cause one job to silently overwrite another in `active_jobs` and `_cache`.
- **Recommendation:** Use `uuid.uuid4()` or a thread-safe atomic counter for ID generation.

---

## Low Severity Issues

### PERF-17: Off-by-One in list_jobs Pagination
- **File:** `src/server.py`, line 71
- **Pattern:** Logic bug
- **Description:** The pagination offset is calculated as `start = page * per_page` instead of `start = (page - 1) * per_page`. Page 1 skips the first `per_page` items entirely; page 0 would be needed to see the first page.
- **Impact:** Clients using standard 1-based pagination will miss the first page of results.
- **Recommendation:** Change to `start = (page - 1) * per_page` (the correct formula already exists in `utils.py:paginate()`).

### PERF-18: get_stats Returns Mutable Reference
- **File:** `src/worker.py`, lines 74-76
- **Pattern:** Broken encapsulation
- **Description:** `get_stats()` returns a direct reference to the global `worker_stats` dict. Any caller (including the Flask response serialization path) can accidentally mutate the live stats.
- **Impact:** Accidental mutation of worker statistics from request-handling code.
- **Recommendation:** Return a copy: `return dict(worker_stats)` or `return worker_stats.copy()`.

---

## Architecture-Level Concerns

### Documented Features Not Implemented
- **README.md** advertises "Redis-backed persistent queue" but the code uses an in-memory list. Redis is in `requirements.txt` but never imported.
- **README.md** advertises "WebSocket notifications on job completion" but no WebSocket code exists.
- **README.md** documents `WEBHOOK_SECRET`, `REDIS_URL`, `MAX_RETRIES`, `JOB_TIMEOUT`, and `LOG_LEVEL` environment variables, but only `WORKER_COUNT` and `CONFIG_PATH` are actually read.
- Celery is listed in `requirements.txt` but never used.

### Test Quality Gaps
The test suite has significant gaps that allow these performance issues to go undetected:
- No concurrency tests (would expose race conditions in PERF-02, PERF-03, PERF-16)
- No load/stress tests (would expose PERF-01, PERF-04, PERF-05, PERF-12)
- Sleep-based timing assertions (`time.sleep(2)`) make tests flaky
- Shared mutable state between tests (`test_jobs`, `active_jobs`, `worker_stats` globals) means test results depend on execution order
- Missing negative test cases (invalid input, error paths)

---

## Summary Table

| ID | Severity | File | Issue | Pattern |
|---|---|---|---|---|
| PERF-01 | Critical | server.py:15 | Unbounded cache, no eviction | Memory leak |
| PERF-02 | Critical | worker.py:10-44 | Unsynchronized shared state | Race condition |
| PERF-03 | Critical | worker.py:58-61 | Lock ordering violation | Deadlock |
| PERF-04 | Critical | job.py:42-48 | File handle never closed | Resource leak |
| PERF-05 | Critical | job.py:54-62 | Subprocess never waited on | Zombie process |
| PERF-06 | High | server.py:18-25 | Config re-read on every request | Repeated I/O |
| PERF-07 | High | job.py:86-98 | Per-item fetch instead of batch | N+1 query |
| PERF-08 | High | job.py:66-70 | HTTP POST with no timeout | Blocking I/O |
| PERF-09 | High | server.py:114-121 | Full cache scan for metrics | Unnecessary scan |
| PERF-10 | High | server.py:67-74 | Full dict copy on paginated list | Unnecessary allocation |
| PERF-11 | High | job.py:91-93 | String concat in loop | Quadratic allocation |
| PERF-12 | High | job.py:74-82 | Temp files with delete=False | Disk leak |
| PERF-13 | Medium | utils.py:9-10 | Regex compiled per call | Repeated work |
| PERF-14 | Medium | worker.py:80-88 | Threads not stopped on scale-down | Thread leak |
| PERF-15 | Medium | worker.py:90-92 | Shutdown doesn't join threads | Unclean shutdown |
| PERF-16 | Medium | server.py:33 | Job ID from len() is racy | TOCTOU race |
| PERF-17 | Low | server.py:71 | Off-by-one in pagination | Logic bug |
| PERF-18 | Low | worker.py:74-76 | get_stats returns mutable ref | Broken encapsulation |

---

## Recommended Priority Order for Fixes

1. **PERF-02 + PERF-03:** Replace `self._queue` with `queue.Queue` and establish consistent locking. This is the single highest-impact fix because it eliminates crashes and deadlocks.
2. **PERF-04 + PERF-05:** Fix resource leaks (file handles, zombie processes). These cause cascading failures under sustained load.
3. **PERF-01:** Add eviction to the cache (LRU or TTL). Without this, the process will eventually OOM.
4. **PERF-08:** Add timeouts to `requests.post()`. Without this, a single slow webhook can starve all workers.
5. **PERF-07 + PERF-11:** Fix the N+1 pattern and string concatenation in `batch_process()` for order-of-magnitude improvement on batch workloads.
6. **PERF-06 + PERF-09:** Cache config reads and maintain incremental metrics counters.
7. **PERF-12 through PERF-18:** Address remaining medium and low severity issues.

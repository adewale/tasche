# Performance Audit Report: TaskRunner

**Project:** TaskRunner (`test-repo-deepdive`)
**Audit type:** Deep-dive performance audit (static analysis)
**Date:** 2026-03-08

---

## Executive Summary

The TaskRunner project is a Flask-based task queue with a threaded worker pool. Static analysis reveals **multiple severe performance issues** that directly explain the slowdowns under load. The problems span five categories: hot-path allocations, N+1 processing patterns, unbounded memory growth, blocking/unnecessary work on every request, and resource leaks that cause degradation over time. Several of these compound under concurrency, meaning the system degrades non-linearly as load increases.

---

## Finding 1: Unbounded In-Memory Cache With No Eviction

**File:** `src/server.py`, lines 15, 43, 56
**Impact:** Memory -- unbounded growth leading to OOM under sustained load
**Severity:** Critical

The module-level `_cache` dict grows without limit. Every job created via `POST /api/jobs` adds an entry (line 43), and every cache miss on `GET /api/jobs/<id>` adds another (line 56). There is no eviction policy, no TTL, and no size cap.

```python
_cache = {}  # line 15 -- never evicted

# In create_job():
_cache[job_id] = job.to_dict()  # line 43 -- every job is cached forever

# In get_job():
_cache[job_id] = result  # line 56 -- cache misses also stored forever
```

Under sustained load (thousands of jobs per hour), this dict will consume increasing amounts of memory until the process is killed. The `GET /api/metrics` endpoint (line 118) also iterates the entire `_cache` on every request, meaning metrics latency degrades linearly with total historical job count.

**Recommendation:** Replace with an LRU cache (e.g., `functools.lru_cache` for simple cases, or `cachetools.TTLCache`) with a bounded size and TTL. Alternatively, move completed job data to a persistent store and evict from memory.

---

## Finding 2: Config File Re-Read and Re-Parsed on Every Request

**File:** `src/server.py`, lines 18-25, 31, 112
**Impact:** Latency -- unnecessary disk I/O on every request
**Severity:** High

The `get_config()` function opens, reads, and parses `config.json` from disk on every invocation. It is called on every `POST /api/jobs` (line 31) and every `GET /api/metrics` (line 112).

```python
def get_config():
    config_path = os.environ.get("CONFIG_PATH", "config.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
```

Under load, this means hundreds of file opens, reads, and JSON parses per second for a config that rarely (if ever) changes at runtime.

**Recommendation:** Load config once at startup and store it in a module-level variable. If hot-reloading is needed, use a file watcher or re-read at most once every N seconds using a cached timestamp check.

---

## Finding 3: N+1 Processing Pattern in `batch_process`

**File:** `src/job.py`, lines 86-98
**Impact:** Latency -- linear scaling with item count, completely avoidable
**Severity:** High

The `batch_process` task handler iterates over items and calls `_fetch_item_details()` once per item. Each call simulates a database query with a 10ms sleep. For a batch of 1,000 items, this means 10 seconds of serial waiting.

```python
for item in items:
    # ...
    result = _fetch_item_details(item["id"])  # one "query" per item
    results.append(result)
```

This is the classic N+1 query problem. Even if the simulated sleep were replaced with a real DB call, the pattern remains: serial per-item fetches instead of a single batch query.

**Recommendation:** Collect all item IDs upfront and fetch details in a single batch operation. If the downstream data source requires individual calls, use concurrent fetching (e.g., `concurrent.futures.ThreadPoolExecutor`) to parallelize them.

---

## Finding 4: String Concatenation in Loop

**File:** `src/job.py`, lines 91-93
**Impact:** Latency + Memory -- quadratic time complexity for string building
**Severity:** Medium

Inside the `batch_process` inner loop, a log message is built via repeated string concatenation:

```python
log_msg = ""
for key, value in item.items():
    log_msg = log_msg + f"{key}={value}, "
```

Python string concatenation in a loop creates a new string object on every iteration, resulting in O(n^2) time complexity for n key-value pairs. For items with many fields processed in large batches, this compounds with the N+1 issue above.

**Recommendation:** Use `", ".join(f"{k}={v}" for k, v in item.items())` for O(n) string construction. Also note that this log message is built but never actually used anywhere -- it could be removed entirely.

---

## Finding 5: Regex Compiled on Every Call

**File:** `src/utils.py`, lines 9-10
**Impact:** Latency -- unnecessary CPU work on every job ID parse
**Severity:** Medium

The `parse_job_id` function compiles a regex pattern on every invocation:

```python
def parse_job_id(raw_id):
    pattern = re.compile(r"^job_(\d+)$")  # compiled every call
    match = pattern.match(raw_id)
```

Regex compilation is expensive relative to matching. If `parse_job_id` is called on every incoming API request (e.g., for job lookup), this adds unnecessary CPU overhead.

**Recommendation:** Move the `re.compile()` call to module level:
```python
_JOB_ID_PATTERN = re.compile(r"^job_(\d+)$")

def parse_job_id(raw_id):
    match = _JOB_ID_PATTERN.match(raw_id)
```

---

## Finding 6: Full Dict Copy on Every List Request

**File:** `src/server.py`, lines 67-74
**Impact:** Latency + Memory -- O(n) allocation on every list request
**Severity:** High

The `list_jobs` endpoint materializes the entire `active_jobs` dict into a list on every request, regardless of the requested page size:

```python
all_jobs = list(active_jobs.values())  # copies ALL jobs
start = page * per_page
end = start + per_page
jobs = [j.to_dict() for j in all_jobs[start:end]]
```

If there are 100,000 active jobs and the client requests page 1 with 20 items per page, the server still allocates a list of 100,000 references, then slices out 20. Under concurrent requests, multiple copies of this list exist simultaneously.

Additionally, there is a pagination bug: `start = page * per_page` should be `start = (page - 1) * per_page`. Page 1 skips the first `per_page` items entirely.

**Recommendation:** Avoid copying the entire dict. Use an ordered data structure (e.g., an `OrderedDict` or a list maintained in insertion order) and implement cursor-based or offset-based pagination without materializing all values. Fix the off-by-one in the page calculation.

---

## Finding 7: Metrics Recomputed From Scratch on Every Request

**File:** `src/server.py`, lines 114-121
**Impact:** Latency -- O(n) scan of entire cache on every metrics call
**Severity:** Medium

The `/api/metrics` endpoint iterates the entire `_cache` dict twice (once for completed, once for failed) on every request:

```python
"completed": sum(1 for j in _cache.values() if j.get("status") == "completed"),
"failed": sum(1 for j in _cache.values() if j.get("status") == "failed"),
```

Combined with the unbounded cache (Finding 1), this means metrics response time grows linearly with the total number of jobs ever submitted. After running for days with thousands of jobs, each metrics request scans the entire history.

**Recommendation:** Maintain running counters that are updated atomically when job status changes, rather than recomputing from the full dataset on every request. The `worker_stats` dict in `worker.py` already tracks completed/failed counts (albeit with its own concurrency issues).

---

## Finding 8: Leaked File Handles in `process_file`

**File:** `src/job.py`, lines 41-48
**Impact:** Throughput -- file descriptor exhaustion under sustained load
**Severity:** Critical

The `process_file` task handler opens a file but never closes it:

```python
def process_file(payload):
    f = open(filepath, "r")  # Never closed
    content = f.read()
    # ...
    return result
    # f is never closed -- leaked file handle
```

Each invocation leaks one file descriptor. Operating systems have a per-process file descriptor limit (typically 1024 on Linux, 256 on some macOS configurations). Under sustained load, the process will hit this limit and all subsequent file operations -- including the `get_config()` call on every request -- will fail with `OSError: [Errno 24] Too many open files`.

**Recommendation:** Use a context manager: `with open(filepath, "r") as f:`.

---

## Finding 9: Zombie Subprocess in `run_transform`

**File:** `src/job.py`, lines 52-62
**Impact:** Memory + OS resources -- zombie process accumulation
**Severity:** High

The `run_transform` handler spawns a subprocess but never calls `proc.wait()` or `proc.communicate()`:

```python
proc = subprocess.Popen(
    ["bash", script],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
# Returns immediately -- proc is never waited on
return {"pid": proc.pid, "started": True}
```

This creates zombie processes (defunct entries in the process table). Additionally, because `stdout=subprocess.PIPE` is set but the pipes are never read, the child process may block if it writes enough output to fill the OS pipe buffer, causing it to hang indefinitely.

**Recommendation:** Either use `subprocess.run()` (which waits for completion) or store the `Popen` object and call `proc.communicate()` before returning. If the intent is fire-and-forget, remove the PIPE redirects.

---

## Finding 10: HTTP Request Without Timeout in `send_notification`

**File:** `src/job.py`, lines 65-70
**Impact:** Throughput -- worker thread blocked indefinitely
**Severity:** High

The `send_notification` handler makes an HTTP POST with no timeout:

```python
resp = requests.post(payload["url"], json=payload["data"])  # no timeout
```

If the remote webhook endpoint is slow or unresponsive, the worker thread will block indefinitely. Since the `WorkerPool` has a fixed number of threads (default 4), a single unresponsive webhook can eventually block all workers, causing complete queue starvation.

**Recommendation:** Always set a timeout: `requests.post(url, json=data, timeout=(5, 30))` (5-second connect, 30-second read timeout). Consider using the retry pattern with exponential backoff for transient failures.

---

## Finding 11: Temporary Files Never Cleaned Up

**File:** `src/job.py`, lines 73-82
**Impact:** Disk -- slow disk space exhaustion
**Severity:** Medium

The `generate_report` handler creates a temporary file with `delete=False` and never removes it:

```python
tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
# ... writes data ...
return {"path": tmp.name}
# File is never deleted
```

Each report generation leaves a file on disk. Under sustained load, this gradually fills the temporary directory. On systems where `/tmp` is a tmpfs (RAM-backed), this also consumes memory.

**Recommendation:** If the temp file must persist for the caller to read, implement a cleanup mechanism (e.g., a cleanup task that runs periodically, or delete the file after the caller confirms receipt). If the file is only needed transiently, use `delete=True` (the default) or use a context manager.

---

## Finding 12: Unsynchronized Shared Queue Causes Worker Contention

**File:** `src/worker.py`, lines 24, 38, 42-44
**Impact:** Throughput + Correctness -- lost jobs under concurrency
**Severity:** Critical

The worker pool uses a plain Python list as a shared queue with no synchronization:

```python
self._queue = []  # shared list, no synchronization

# In submit():
self._queue.append(job)  # unsynchronized write

# In _worker_loop():
if self._queue:                    # check
    job = self._queue.pop(0)       # act -- race between check and pop
```

Multiple worker threads call `pop(0)` concurrently. While CPython's GIL makes individual list operations atomic, the check-then-act pattern (`if self._queue` followed by `self._queue.pop(0)`) is not atomic. Two threads can both see a non-empty queue, but only one item remains, causing an `IndexError` on the second pop.

Additionally, `list.pop(0)` is O(n) because it shifts all remaining elements. For a large queue, this adds significant overhead on every dequeue.

**Recommendation:** Replace with `queue.Queue`, which provides thread-safe, blocking dequeue with O(1) amortized performance. This also eliminates the busy-wait `time.sleep(0.1)` loop.

---

## Finding 13: Blocking Event Loop via `time.sleep` in Worker Loop

**File:** `src/worker.py`, lines 41-47
**Impact:** Latency -- up to 100ms delay before processing each job
**Severity:** Medium

The worker loop uses a polling pattern with a 100ms sleep:

```python
def _worker_loop(self, worker_id):
    while self.running:
        if self._queue:
            job = self._queue.pop(0)
            self._execute(job, worker_id)
        else:
            time.sleep(0.1)
```

When the queue is empty, each worker sleeps for 100ms before checking again. This means a newly submitted job can wait up to 100ms before any worker picks it up, adding unnecessary latency. Under bursty load patterns, this delay compounds.

**Recommendation:** Use `queue.Queue.get(timeout=0.1)` or a `threading.Event` to wake workers immediately when new work is available.

---

## Summary Table

| # | File | Lines | Issue | Impact | Severity |
|---|------|-------|-------|--------|----------|
| 1 | `src/server.py` | 15, 43, 56 | Unbounded cache with no eviction | Memory (OOM) | Critical |
| 2 | `src/server.py` | 18-25 | Config re-read/re-parsed every request | Latency (disk I/O) | High |
| 3 | `src/job.py` | 86-98 | N+1 processing in batch_process | Latency (linear scaling) | High |
| 4 | `src/job.py` | 91-93 | String concatenation in loop (quadratic) | Latency + Memory | Medium |
| 5 | `src/utils.py` | 9-10 | Regex compiled on every call | Latency (CPU) | Medium |
| 6 | `src/server.py` | 67-74 | Full dict copy on every list request | Latency + Memory | High |
| 7 | `src/server.py` | 114-121 | Metrics recomputed via full scan each request | Latency (linear in job count) | Medium |
| 8 | `src/job.py` | 41-48 | File handle never closed | Throughput (fd exhaustion) | Critical |
| 9 | `src/job.py` | 52-62 | Zombie subprocess (never waited on) | Memory + OS resources | High |
| 10 | `src/job.py` | 65-70 | HTTP request with no timeout | Throughput (worker starvation) | High |
| 11 | `src/job.py` | 73-82 | Temp files never cleaned up | Disk exhaustion | Medium |
| 12 | `src/worker.py` | 24, 38, 42-44 | Unsynchronized shared list as queue | Throughput + Correctness | Critical |
| 13 | `src/worker.py` | 41-47 | Polling with sleep instead of blocking queue | Latency (up to 100ms delay) | Medium |

---

## Priority Recommendations

### Immediate (Critical -- will cause failures under load)

1. **Replace the shared list queue with `queue.Queue`** (Finding 12). This fixes the race condition, eliminates the O(n) pop, and removes the busy-wait sleep (Finding 13) in one change.
2. **Close file handles using context managers** (Finding 8). Without this, the process will hit file descriptor limits and crash under sustained file-processing workloads.
3. **Add eviction to the in-memory cache** (Finding 1). Use `cachetools.LRUCache` or `cachetools.TTLCache` with a reasonable max size. This also makes the metrics scan (Finding 7) bounded.

### Short-Term (High -- causes degradation under moderate load)

4. **Add timeouts to all outbound HTTP requests** (Finding 10). A single unresponsive webhook should not be able to starve the entire worker pool.
5. **Wait on subprocesses** (Finding 9). Use `subprocess.run()` or call `communicate()` on the Popen object.
6. **Load config once at startup** (Finding 2). Store in a module-level variable.
7. **Batch the N+1 processing pattern** (Finding 3). Collect IDs and fetch in one call.
8. **Fix pagination to avoid full-dict materialization** (Finding 6). Also fix the off-by-one bug.

### Medium-Term (Medium -- causes gradual degradation)

9. **Compile regex at module level** (Finding 5).
10. **Use `str.join()` instead of concatenation in loops** (Finding 4). Or remove the dead code entirely since `log_msg` is never used.
11. **Clean up temporary files** (Finding 11).
12. **Maintain running counters for metrics** (Finding 7) instead of scanning the full cache.

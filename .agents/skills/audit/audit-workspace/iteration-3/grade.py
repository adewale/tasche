#!/usr/bin/env python3
"""Grade iteration-3 deep-dive audit reports against assertions."""
import json
import os

BASE = "/Users/ade/Documents/projects/audit-skill/audit-workspace/iteration-3"

evals = {
    "concurrency-audit": {
        "assertions": [
            {"name": "detects-shared-mutable-state", "type": "contains_any", "values": ["active_jobs", "job_counter", "worker_stats", "shared", "global"]},
            {"name": "detects-race-condition", "type": "contains_any", "values": ["race", "TOCTOU", "check-then-act", "non-atomic", "without lock"]},
            {"name": "detects-deadlock-risk", "type": "contains_any", "values": ["deadlock", "lock order", "stats_lock", "jobs_lock"]},
            {"name": "detects-thread-leak", "type": "contains_any", "values": ["thread leak", "never stopped", "never joined", "scale-down", "daemon"]},
            {"name": "detects-queue-race", "type": "contains_any", "values": ["_queue", "pop", "IndexError", "thread-safe"]},
            {"name": "has-severity-ratings", "type": "contains_any", "values": ["Critical", "High", "Medium", "Severity"]},
            {"name": "has-fix-recommendations", "type": "contains_any", "values": ["Fix:", "Recommendation:", "Replace", "queue.Queue"]},
        ],
        "configs": ["with_skill", "without_skill"],
    },
    "test-quality-audit": {
        "assertions": [
            {"name": "detects-weak-assertions", "type": "contains_any", "values": ["no assertion", "trivial", "isinstance", "meaningless", "weak"]},
            {"name": "detects-flaky-patterns", "type": "contains_any", "values": ["sleep", "flaky", "timing", "time.sleep"]},
            {"name": "detects-test-isolation", "type": "contains_any", "values": ["shared", "test_jobs", "isolation", "mutable state", "module-level"]},
            {"name": "detects-missing-negative-tests", "type": "contains_any", "values": ["negative", "error path", "invalid", "missing test"]},
            {"name": "detects-missing-endpoint-tests", "type": "contains_any", "values": ["DELETE", "scale", "untested"]},
            {"name": "detects-tautological-tests", "type": "contains_any", "values": ["tautol", "accepts both", "both outcomes", "enshrines", "confirms a bug", "false confidence"]},
            {"name": "has-bug-inventory", "type": "contains_any", "values": ["bugs", "caught", "0 caught", "zero"]},
        ],
        "configs": ["with_skill", "without_skill"],
    },
    "performance-audit": {
        "assertions": [
            {"name": "detects-n-plus-1", "type": "contains_any", "values": ["N+1", "per-item", "batch", "_fetch_item_details"]},
            {"name": "detects-unbounded-cache", "type": "contains_any", "values": ["unbounded", "_cache", "eviction", "no limit", "OOM"]},
            {"name": "detects-repeated-config-read", "type": "contains_any", "values": ["get_config", "re-read", "every request", "every call", "config"]},
            {"name": "detects-string-concat-in-loop", "type": "contains_any", "values": ["concatenat", "quadratic", "log_msg", "O(n"]},
            {"name": "detects-regex-recompilation", "type": "contains_any", "values": ["re.compile", "regex", "compiled", "every call"]},
            {"name": "detects-file-handle-leak", "type": "contains_any", "values": ["file handle", "file descriptor", "never closed", "open("]},
            {"name": "has-impact-analysis", "type": "contains_any", "values": ["Impact:", "Latency", "Memory", "Throughput"]},
        ],
        "configs": ["with_skill", "without_skill"],
    },
}

for eval_name, eval_data in evals.items():
    for config in eval_data["configs"]:
        report_path = os.path.join(BASE, eval_name, config, "outputs", "audit_report.md")
        with open(report_path) as f:
            content = f.read()

        results = []
        for assertion in eval_data["assertions"]:
            name = assertion["name"]
            atype = assertion["type"]
            values = assertion["values"]

            if atype == "contains_any":
                matches = [v for v in values if v.lower() in content.lower()]
                passed = len(matches) > 0
                evidence = f"Found: {', '.join(matches)}" if passed else f"None of {values} found"

            results.append({"text": name, "passed": passed, "evidence": evidence})

        grading = {
            "eval_name": eval_name,
            "config": config,
            "expectations": results,
            "pass_count": sum(1 for r in results if r["passed"]),
            "total_count": len(results),
        }

        grading_path = os.path.join(BASE, eval_name, config, "grading.json")
        with open(grading_path, "w") as f:
            json.dump(grading, f, indent=2)

        passed = sum(1 for r in results if r["passed"])
        total = len(results)
        print(f"{eval_name}/{config}: {passed}/{total} passed")
        for r in results:
            status = "PASS" if r["passed"] else "FAIL"
            print(f"  [{status}] {r['text']}: {r['evidence']}")
        print()

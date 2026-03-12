#!/usr/bin/env python3
"""Grade audit reports against assertions."""
import json
import os
import re

BASE = "/Users/ade/Documents/projects/audit-skill/audit-workspace/iteration-1"

evals = {
    "dirty-branch-blocking": {
        "assertions": [
            {"name": "verdict-is-blocking", "type": "contains_any", "values": ["**Blocking**", "Blocking"]},
            {"name": "detects-api-key", "type": "contains_any", "values": ["sk_live", "API key", "secret key", "credential", "Stripe"]},
            {"name": "detects-debug-prints", "type": "contains_any", "values": ["print(", "debug", "DEBUG"]},
            {"name": "detects-missing-tests", "type": "contains_any", "values": ["No tests", "missing test", "untested", "test coverage"]},
            {"name": "detects-unrelated-changes", "type": "contains_any", "values": ["README", "unrelated", "unintended"]},
        ],
        "configs": ["with_skill", "without_skill"],
    },
    "clean-branch-clean": {
        "assertions": [
            {"name": "verdict-is-clean", "type": "contains_any", "values": ["**Clean**"]},
            {"name": "no-false-positives", "type": "not_contains_any", "values": ["**Blocking**", "**Minor**"]},
        ],
        "configs": ["with_skill", "without_skill"],
    },
    "mixed-branch-minor": {
        "assertions": [
            {"name": "verdict-is-minor", "type": "contains_any", "values": ["**Minor**"]},
            {"name": "detects-todo-comments", "type": "contains_any", "values": ["TODO", "FIXME"]},
            {"name": "detects-skipped-test", "type": "contains_any", "values": ["skip", "@pytest.mark.skip", "skipped"]},
            {"name": "detects-fixup-commit", "type": "contains_any", "values": ["fixup", "squash"]},
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
                if passed:
                    evidence = f"Found: {', '.join(matches)}"
                else:
                    evidence = f"None of {values} found in report"
            elif atype == "not_contains_any":
                matches = [v for v in values if v in content]  # case-sensitive for bold markers
                passed = len(matches) == 0
                if passed:
                    evidence = f"Correctly absent: none of {values} found"
                else:
                    evidence = f"Incorrectly present: {', '.join(matches)}"

            results.append({
                "text": name,
                "passed": passed,
                "evidence": evidence,
            })

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

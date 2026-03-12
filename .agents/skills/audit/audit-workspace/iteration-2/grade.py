#!/usr/bin/env python3
"""Grade iteration-2 audit reports against assertions."""
import json
import os
import re

BASE = "/Users/ade/Documents/projects/audit-skill/audit-workspace/iteration-2"

evals = {
    "dirty-branch-blocking": {
        "assertions": [
            {"name": "verdict-is-blocking", "type": "contains_any", "values": ["**Blocking**", "Verdict: Blocking"]},
            {"name": "detects-api-key", "type": "contains_any", "values": ["sk_live", "secret key", "Stripe"]},
            {"name": "detects-debug-prints", "type": "contains_any", "values": ["print(", "DEBUG"]},
            {"name": "detects-missing-tests", "type": "contains_any", "values": ["No tests", "no test", "no corresponding test", "zero coverage"]},
            {"name": "detects-unrelated-changes", "type": "custom_readme", "values": []},
            {"name": "has-branch-summary", "type": "contains_any", "values": ["Branch Summary", "## Branch"]},
            {"name": "omits-empty-categories", "type": "not_contains_any", "values": ["No findings"]},
        ],
        "configs": ["with_skill", "without_skill"],
    },
    "clean-branch-clean": {
        "assertions": [
            {"name": "verdict-is-clean", "type": "contains_any", "values": ["**Clean**", "Verdict: Clean"]},
            {"name": "no-false-positives", "type": "not_contains_any", "values": ["**Blocking**", "Verdict: Blocking", "Verdict: Minor"]},
            {"name": "has-branch-summary", "type": "contains_any", "values": ["Branch Summary", "## Branch"]},
        ],
        "configs": ["with_skill", "without_skill"],
    },
    "mixed-branch-minor": {
        "assertions": [
            {"name": "verdict-is-minor", "type": "contains_any", "values": ["**Minor**", "Verdict: Minor"]},
            {"name": "detects-todo-comments", "type": "contains_any", "values": ["TODO", "FIXME"]},
            {"name": "detects-skipped-test", "type": "contains_any", "values": ["@pytest.mark.skip", "skipped"]},
            {"name": "detects-fixup-commit", "type": "contains_any", "values": ["fixup", "squash"]},
            {"name": "has-branch-summary", "type": "contains_any", "values": ["Branch Summary", "## Branch"]},
            {"name": "no-duplicate-findings", "type": "custom_no_dup", "values": []},
        ],
        "configs": ["with_skill", "without_skill"],
    },
}

def check_readme_unrelated(content):
    """Check if report flags README as an unrelated/unintended change."""
    content_lower = content.lower()
    # Must mention README in context of being unrelated/unintended
    if "readme" in content_lower:
        for word in ["unrelated", "unintended", "not relate", "doesn't relate", "not relevant"]:
            if word in content_lower:
                return True, "Report flags README as unrelated"
    return False, "README not flagged as unrelated change"

def check_no_duplicates(content):
    """Check that lru_cache finding doesn't appear in multiple sections."""
    sections = re.split(r'^### ', content, flags=re.MULTILINE)
    lru_count = sum(1 for s in sections if 'lru_cache' in s)
    if lru_count <= 1:
        return True, f"lru_cache mentioned in {lru_count} section(s) — no duplication"
    return False, f"lru_cache mentioned in {lru_count} sections — duplicated"

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
            elif atype == "not_contains_any":
                matches = [v for v in values if v in content]
                passed = len(matches) == 0
                evidence = f"Correctly absent" if passed else f"Incorrectly present: {', '.join(matches)}"
            elif atype == "custom_readme":
                passed, evidence = check_readme_unrelated(content)
            elif atype == "custom_no_dup":
                passed, evidence = check_no_duplicates(content)

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

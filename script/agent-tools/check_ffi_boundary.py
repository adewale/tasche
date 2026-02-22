#!/usr/bin/env python3
"""Verify that all FFI operations go through the Safe* boundary layer.

Scans all .py files under src/ (excluding the boundary modules) and tests/
(excluding test_wrappers.py) for imports or calls to raw FFI functions that
should only be used inside the boundary layer.

Exit 0 if clean, exit 1 if any unsafe patterns found.

Usage:
    python script/agent-tools/check_ffi_boundary.py
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
TEST_DIR = PROJECT_ROOT / "tests"

# Files that ARE the boundary — allowed to use raw FFI functions
BOUNDARY_FILES = {
    SRC_DIR / "wrappers.py",
    SRC_DIR / "entry.py",  # queue message body conversion at JS→Python boundary
}

TEST_BOUNDARY_FILES = {
    TEST_DIR / "unit" / "test_wrappers.py",
}

# Raw FFI function names that must NOT be imported or called outside boundary files
FORBIDDEN_NAMES = {
    "d1_first": "use SafeD1Statement.first() instead",
    "d1_rows": "use SafeD1Statement.all() instead",
    "d1_null": "SafeD1Statement.bind() handles this",
    "r2_put": "use SafeR2.put() instead",
    "_to_js_value": "use SafeQueue.send()/SafeAI.run()",
    "to_js_bytes": "use SafeR2.put() instead",
    "get_js_null": "SafeD1Statement.bind() handles this",
    "_to_py_safe": "Safe* wrappers handle conversion",
}

# Patterns that detect actual usage (imports and calls), not mentions in
# comments, docstrings, or string literals.
_IMPORT_RE = re.compile(
    r"^\s*from\s+(?:src\.)?wrappers\s+import\s+(.+)"
)
_CALL_RE = re.compile(
    r"(?<![\"'`\w])(" + "|".join(re.escape(n) for n in FORBIDDEN_NAMES) + r")\s*\("
)


def _is_comment_or_docstring(line: str) -> bool:
    """Heuristic: skip lines that are comments or look like docstring content."""
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return True
    # Lines inside triple-quoted strings (docstrings) typically start with
    # plain text or triple quotes.  We detect triple-quote openers/closers
    # and lines that have no leading code tokens.
    if stripped.startswith(('"""', "'''")):
        return True
    return False


def scan_file(path: Path) -> list[str]:
    """Scan a single file for forbidden FFI patterns. Returns violation messages."""
    violations = []
    try:
        text = path.read_text()
        lines = text.splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    rel_path = path.relative_to(PROJECT_ROOT)
    in_docstring = False

    for lineno, line in enumerate(lines, start=1):
        stripped = line.lstrip()

        # Track triple-quoted docstrings (simple toggle — good enough for scanning)
        triple_count = stripped.count('"""') + stripped.count("'''")
        if triple_count % 2 == 1:
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue

        # Skip comments
        if stripped.startswith("#"):
            continue

        # Check 1: import statements pulling raw FFI functions from wrappers
        m = _IMPORT_RE.match(line)
        if m:
            imported = {name.strip() for name in m.group(1).split(",")}
            for name in imported & FORBIDDEN_NAMES.keys():
                fix = FORBIDDEN_NAMES[name]
                violations.append(f"  {rel_path}:{lineno}: import {name} — {fix}")
                violations.append(f"    {line.rstrip()}")

        # Check 2: direct function calls
        for call_match in _CALL_RE.finditer(line):
            name = call_match.group(1)
            fix = FORBIDDEN_NAMES[name]
            violations.append(f"  {rel_path}:{lineno}: call {name}() — {fix}")
            violations.append(f"    {line.rstrip()}")

    return violations


def main() -> int:
    all_violations: list[str] = []

    # Scan src/ (excluding boundary files)
    for py_file in sorted(SRC_DIR.rglob("*.py")):
        if py_file.resolve() in {f.resolve() for f in BOUNDARY_FILES}:
            continue
        all_violations.extend(scan_file(py_file))

    # Scan tests/ (excluding boundary test files)
    for py_file in sorted(TEST_DIR.rglob("*.py")):
        if py_file.resolve() in {f.resolve() for f in TEST_BOUNDARY_FILES}:
            continue
        all_violations.extend(scan_file(py_file))

    if all_violations:
        print("FFI boundary violations found:\n")
        for v in all_violations:
            print(v)
        print(f"\n{len(all_violations) // 2} violation(s) found.")
        print("All FFI operations must go through the Safe* layer in src/wrappers.py.")
        return 1

    print("FFI boundary check passed — no unsafe patterns found outside boundary modules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

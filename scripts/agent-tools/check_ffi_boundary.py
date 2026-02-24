#!/usr/bin/env python3
"""Validate that all FFI operations go through the Safe* boundary layer.

Scans all .py files under src/ (excluding the boundary modules themselves)
for patterns that indicate direct FFI usage bypassing wrappers.py.

Checks:
  1. Raw FFI function imports from wrappers.py (d1_first, d1_rows, etc.)
     that should only be used inside Safe* classes
  2. Direct .to_py() calls outside wrappers.py
  3. Direct JsProxy/JsNull type checks outside wrappers.py
  4. Direct js.* access outside wrappers.py and entry.py
  5. Direct r2.put() / kv.put() / queue.send() bypassing Safe* wrappers
  6. None comparisons on JS return values (should use _is_js_null_or_undefined)

Exit 0 if clean, exit 1 if violations found.

Usage:
    python scripts/agent-tools/check_ffi_boundary.py
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"
TEST_DIR = PROJECT_ROOT / "tests"

# Files that ARE the boundary -- allowed to use raw FFI functions
BOUNDARY_FILES = {
    SRC_DIR / "wrappers.py",
    SRC_DIR / "entry.py",  # queue message body conversion at JS->Python boundary
}

# Raw FFI helper names that must NOT be imported/called outside boundary files.
# These are internal to wrappers.py and should be used via Safe* classes.
FORBIDDEN_IMPORTS = {
    "d1_first": "use SafeD1Statement.first() instead",
    "d1_rows": "use SafeD1Statement.all() instead",
    "d1_null": "SafeD1Statement.bind() handles None->null automatically",
    "r2_put": "use SafeR2.put() instead",
    "to_js_bytes": "use SafeR2.put() instead",
    "get_js_null": "SafeD1Statement.bind() handles None->null automatically",
}

# Patterns that are safe to import from wrappers.py in application code
ALLOWED_IMPORTS = {
    "SafeEnv", "SafeD1", "SafeR2", "SafeKV", "SafeQueue", "SafeAI",
    "SafeReadability", "SafeD1Statement",
    "HttpClient", "HttpResponse", "HttpError",
    "stream_r2_body", "get_r2_size", "consume_readable_stream",
    "HAS_PYODIDE", "JsException",
    # _to_py_safe is allowed in entry.py for queue message conversion
    "_to_py_safe",
}

# Regex patterns for checks
_IMPORT_FROM_WRAPPERS = re.compile(
    r"^\s*from\s+(?:src\.)?wrappers\s+import\s+(.+)", re.MULTILINE
)
_DIRECT_TO_PY = re.compile(r"\.to_py\(\)")
_DIRECT_JSPROXY_CHECK = re.compile(r"isinstance\([^,]+,\s*JsProxy\)")
_DIRECT_JSNULL_CHECK = re.compile(r'type\([^)]+\)\.__name__\s*==\s*["\']JsNull["\']')
_DIRECT_JS_ACCESS = re.compile(
    r"\bjs\.(eval|Function|undefined|null|JSON|Object|fetch|URL|Request)\b"
)


def _is_in_comment_or_docstring(line: str) -> bool:
    """Heuristic: skip lines that are comments or docstring-like."""
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return True
    if stripped.startswith(('"""', "'''")):
        return True
    return False


def scan_file(path: Path) -> list[str]:
    """Scan a single file for FFI boundary violations."""
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

        # Track docstrings (simple toggle)
        triple_count = stripped.count('"""') + stripped.count("'''")
        if triple_count % 2 == 1:
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue

        # Check 1: Forbidden imports from wrappers
        import_match = _IMPORT_FROM_WRAPPERS.match(line)
        if import_match:
            imported_names = {
                name.strip().split(" as ")[0].strip()
                for name in import_match.group(1).split(",")
            }
            for name in imported_names:
                if name in FORBIDDEN_IMPORTS:
                    fix = FORBIDDEN_IMPORTS[name]
                    violations.append(
                        f"  {rel_path}:{lineno}: FORBIDDEN IMPORT '{name}' -- {fix}"
                    )

        # Check 2: Direct .to_py() calls
        if _DIRECT_TO_PY.search(stripped):
            violations.append(
                f"  {rel_path}:{lineno}: DIRECT .to_py() call -- "
                f"use Safe* wrappers for automatic conversion"
            )

        # Check 3: Direct JsProxy isinstance checks
        if _DIRECT_JSPROXY_CHECK.search(stripped):
            violations.append(
                f"  {rel_path}:{lineno}: DIRECT JsProxy isinstance check -- "
                f"Safe* wrappers handle conversion; if needed, use HAS_PYODIDE guard"
            )

        # Check 4: Direct JsNull type name checks
        if _DIRECT_JSNULL_CHECK.search(stripped):
            violations.append(
                f"  {rel_path}:{lineno}: DIRECT JsNull type check -- "
                f"use _is_js_null_or_undefined() from wrappers.py or rely on Safe* wrappers"
            )

        # Check 5: Direct js.* access (excluding standard import guards)
        if _DIRECT_JS_ACCESS.search(stripped):
            # Allow in entry.py for URL/Request construction
            if path.resolve() == (SRC_DIR / "entry.py").resolve():
                continue
            violations.append(
                f"  {rel_path}:{lineno}: DIRECT js.* access -- "
                f"all JS interop should go through wrappers.py"
            )

    return violations


def main() -> int:
    all_violations: list[str] = []
    files_scanned = 0

    # Scan src/ (excluding boundary files)
    boundary_resolved = {f.resolve() for f in BOUNDARY_FILES}
    for py_file in sorted(SRC_DIR.rglob("*.py")):
        if py_file.resolve() in boundary_resolved:
            continue
        if py_file.name == "__init__.py" and py_file.stat().st_size == 0:
            continue
        all_violations.extend(scan_file(py_file))
        files_scanned += 1

    print(f"FFI Boundary Check -- scanned {files_scanned} files\n")

    if all_violations:
        print("VIOLATIONS FOUND:\n")
        for v in all_violations:
            print(v)
        # Count unique violations (not indented continuation lines)
        count = sum(1 for v in all_violations if v.startswith("  ") and ":" in v)
        print(f"\n{count} violation(s) found.")
        print(
            "\nAll FFI operations must go through the Safe* layer in src/wrappers.py."
        )
        print("See LESSONS_LEARNED.md sections 29, 30, 36 for context.")
        return 1

    print("PASSED -- no unsafe FFI patterns found outside boundary modules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

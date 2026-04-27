#!/usr/bin/env python3
"""Detect common Pyodide/Workers runtime pitfalls in Python source files.

Checks for patterns that work in CPython (pytest) but fail in the Pyodide
runtime inside Cloudflare Workers V8 isolates.

Checks:
  1. Sync route handlers (must be async def -- sync causes RuntimeError)
  2. eval() or Function() calls (blocked in Workers V8 isolates)
  3. Module-level PRNG usage (breaks Wasm snapshot for fast cold starts)
  4. Direct 'import js' outside boundary modules (should go through boundary)
  5. Threading/multiprocessing imports (unavailable in Pyodide)
  6. C-extension library imports (incompatible with Pyodide/WebAssembly)
  7. Sync HTTP libraries (requests, urllib3 -- must use async httpx or HttpClient)

Exit 0 if clean, exit 1 if problems found.

Usage:
    python scripts/agent-tools/check_pyodide_pitfalls.py
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"

# Files allowed to use 'import js' directly
JS_IMPORT_ALLOWED = {
    SRC_DIR / "boundary",
    SRC_DIR / "entry.py",
}

# FastAPI route decorators that indicate a handler function
_ROUTE_DECORATOR = re.compile(
    r"^\s*@\w+\.(get|post|put|patch|delete|head|options)\("
)

# Sync def (not async def) following a route decorator
_SYNC_DEF = re.compile(r"^\s*def\s+\w+\s*\(")
_ASYNC_DEF = re.compile(r"^\s*async\s+def\s+\w+\s*\(")

# eval/Function patterns
_EVAL_PATTERN = re.compile(r"\beval\s*\(|\bjs\.eval\s*\(|\bjs\.Function\s*\(")

# Module-level PRNG -- only flag CALLS at module level, not bare imports.
# Importing the module is fine; calling random.random() or secrets.token_urlsafe()
# at module level breaks the Wasm snapshot.
_PRNG_CALL_PATTERN = re.compile(
    r"^(?:\w+\s*=\s*)?(?:random|secrets)\.\w+\("
)
_PRNG_FROM_IMPORT_CALL = re.compile(
    r"^(?:from\s+random\s+import\s+(?:random|randint|choice|shuffle|sample|getrandbits|randbytes|seed)|"
    r"from\s+secrets\s+import\s+(?:token_urlsafe|token_hex|token_bytes|randbelow))"
)

# Direct js import
_JS_IMPORT = re.compile(r"^\s*(?:import\s+js\b|from\s+js\s+import)")

# Threading/multiprocessing
_THREADING_IMPORT = re.compile(
    r"^\s*(?:import\s+(?:threading|multiprocessing|concurrent\.futures)|"
    r"from\s+(?:threading|multiprocessing|concurrent\.futures)\s+import)"
)

# C-extension libraries known to fail in Pyodide
_C_EXTENSION_LIBS = {
    "lxml", "readability", "readability_lxml",
    "numpy", "pandas", "scipy", "pillow", "PIL",
    "cryptography", "cffi", "greenlet", "gevent",
    "uvloop", "aiohttp",
}
_C_EXTENSION_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(" + "|".join(_C_EXTENSION_LIBS) + r")\b"
)

# Sync HTTP libraries
_SYNC_HTTP = re.compile(
    r"^\s*(?:import\s+(?:requests|urllib3)|from\s+(?:requests|urllib3)\s+import)"
)


class Violation:
    """A single detected violation."""

    def __init__(
        self, path: Path, lineno: int,
        category: str, message: str, line_text: str,
    ):
        self.path = path
        self.lineno = lineno
        self.category = category
        self.message = message
        self.line_text = line_text

    def __str__(self) -> str:
        rel = self.path.relative_to(PROJECT_ROOT)
        return (
            f"  {rel}:{self.lineno}: [{self.category}] "
            f"{self.message}\n    {self.line_text.rstrip()}"
        )


def scan_file(path: Path) -> list[Violation]:
    """Scan a single file for Pyodide pitfalls."""
    violations = []
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    in_docstring = False
    prev_was_route_decorator = False

    for lineno, line in enumerate(lines, start=1):
        stripped = line.lstrip()

        # Track docstrings
        triple_count = stripped.count('"""') + stripped.count("'''")
        if triple_count % 2 == 1:
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        if stripped.startswith("#") or not stripped:
            continue

        # Track indentation for module-level detection
        current_indent = len(line) - len(line.lstrip())

        # Check 1: Sync route handlers
        if _ROUTE_DECORATOR.match(stripped):
            prev_was_route_decorator = True
            continue

        if prev_was_route_decorator:
            prev_was_route_decorator = False
            if _SYNC_DEF.match(stripped) and not _ASYNC_DEF.match(stripped):
                violations.append(Violation(
                    path, lineno, "SYNC_HANDLER",
                    "Route handler must be 'async def' -- sync handlers cause "
                    "'RuntimeError: can't start new thread' in Pyodide",
                    stripped,
                ))

        # Check 2: eval() / Function() calls
        if _EVAL_PATTERN.search(stripped):
            violations.append(Violation(
                path, lineno, "EVAL_BLOCKED",
                "eval()/Function() is blocked in Workers V8 isolates with "
                "'EvalError: Code generation from strings disallowed'",
                stripped,
            ))

        # Check 3: Module-level PRNG calls (only at indent level 0)
        # Bare 'import random' / 'import secrets' are fine -- only flag actual
        # function calls like random.random() or secrets.token_urlsafe() at
        # module scope, or 'from random import randint' which enables bare calls.
        if current_indent == 0:
            if _PRNG_CALL_PATTERN.match(stripped):
                violations.append(Violation(
                    path, lineno, "MODULE_PRNG",
                    "Module-level random/secrets function call breaks the Wasm snapshot "
                    "for fast cold starts -- call inside functions instead",
                    stripped,
                ))
            elif _PRNG_FROM_IMPORT_CALL.match(stripped):
                violations.append(Violation(
                    path, lineno, "MODULE_PRNG",
                    "Importing random/secrets functions at module level enables accidental "
                    "module-level calls -- import the module and call inside functions instead",
                    stripped,
                ))

        # Check 4: Direct 'import js' outside boundary
        if _JS_IMPORT.match(stripped):
            if path.resolve() not in {f.resolve() for f in JS_IMPORT_ALLOWED}:
                violations.append(Violation(
                    path, lineno, "DIRECT_JS_IMPORT",
                    "Direct 'import js' should only appear in boundary and entry.py -- "
                    "use Safe* wrappers or http_fetch() from boundary instead",
                    stripped,
                ))

        # Check 5: Threading/multiprocessing imports
        if _THREADING_IMPORT.match(stripped):
            violations.append(Violation(
                path, lineno, "THREADING",
                "threading/multiprocessing/concurrent.futures are unavailable in Pyodide -- "
                "use async/await patterns instead",
                stripped,
            ))

        # Check 6: C-extension library imports
        if _C_EXTENSION_IMPORT.match(stripped):
            violations.append(Violation(
                path, lineno, "C_EXTENSION",
                "This library requires C extensions incompatible "
                "with Pyodide -- see LESSONS_LEARNED.md section 33",
                stripped,
            ))

        # Check 7: Sync HTTP libraries
        if _SYNC_HTTP.match(stripped):
            violations.append(Violation(
                path, lineno, "SYNC_HTTP",
                "Sync HTTP libraries (requests, urllib3) don't work in Pyodide -- "
                "use HttpClient from boundary or async httpx",
                stripped,
            ))

    return violations


def main() -> int:
    all_violations: list[Violation] = []
    files_scanned = 0

    for py_file in sorted(SRC_DIR.rglob("*.py")):
        if py_file.name == "__init__.py" and py_file.stat().st_size == 0:
            continue
        all_violations.extend(scan_file(py_file))
        files_scanned += 1

    print(f"Pyodide Pitfalls Check -- scanned {files_scanned} files\n")

    if all_violations:
        # Group by category
        categories: dict[str, list[Violation]] = {}
        for v in all_violations:
            categories.setdefault(v.category, []).append(v)

        for category, vs in sorted(categories.items()):
            print(f"{category} ({len(vs)}):")
            for v in vs:
                print(str(v))
            print()

        print(f"Total: {len(all_violations)} pitfall(s) found.")
        print("\nSee LESSONS_LEARNED.md sections 27, 32, 33, 35 for context.")
        return 1

    print("PASSED -- no Pyodide pitfalls detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

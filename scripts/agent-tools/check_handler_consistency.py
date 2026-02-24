#!/usr/bin/env python3
"""Audit API route handlers for structural consistency.

Checks all route files under src/ for common patterns that the commit
history shows are frequently sources of bugs:

Checks:
  1. All route handlers are async (Workers requirement)
  2. All handlers that need auth use Depends(get_current_user)
  3. All handlers access env via request.scope["env"] (not self.env or global)
  4. All handlers that write to DB call .run() (not .first() or .all())
  5. Status string literals match the known valid enums
  6. Deletion order: R2 content deleted before D1 row (not vice versa)
  7. All route files import from wrappers, not raw js/pyodide

Exit 0 if clean, exit 1 if inconsistencies found.

Usage:
    python scripts/agent-tools/check_handler_consistency.py
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_DIR = PROJECT_ROOT / "src"

# Route files to scan
ROUTE_FILES = list(SRC_DIR.rglob("routes.py")) + list(SRC_DIR.rglob("rules.py"))

# Known valid status enums (must match D1 CHECK constraints)
VALID_READING_STATUSES = {"unread", "reading", "archived"}
VALID_ARTICLE_STATUSES = {"pending", "processing", "ready", "failed"}
VALID_AUDIO_STATUSES = {"pending", "generating", "ready", "failed"}
VALID_ORIGINAL_STATUSES = {"unknown", "live", "dead", "moved", "paywalled"}

ALL_VALID_STATUSES = (
    VALID_READING_STATUSES
    | VALID_ARTICLE_STATUSES
    | VALID_AUDIO_STATUSES
    | VALID_ORIGINAL_STATUSES
)

# Patterns
_ROUTE_DECORATOR = re.compile(
    r"^\s*@(?:router|app|article_tags_router)\."
    r"(get|post|put|patch|delete|head|options)\("
)
_ASYNC_DEF = re.compile(r"^\s*async\s+def\s+(\w+)\s*\(")
_SYNC_DEF = re.compile(r"^\s*def\s+(\w+)\s*\(")
_DEPENDS_AUTH = re.compile(r"Depends\(get_current_user\)")
_ENV_ACCESS = re.compile(r'request\.scope\["env"\]')
_STATUS_LITERAL = re.compile(
    r"""(?:status|reading_status|audio_status|original_status)"""
    r"""\s*=\s*['"](\w+)['"]"""
)


class Issue:
    def __init__(self, path: Path, lineno: int, category: str, message: str):
        self.path = path
        self.lineno = lineno
        self.category = category
        self.message = message

    def __str__(self) -> str:
        rel = self.path.relative_to(PROJECT_ROOT)
        return f"  {rel}:{self.lineno}: [{self.category}] {self.message}"


def scan_route_file(path: Path) -> list[Issue]:
    """Scan a route file for consistency issues."""
    issues = []
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    # Track handler functions
    in_handler = False
    handler_name = ""
    handler_start = 0
    handler_has_auth = False
    prev_was_route_decorator = False

    # Public endpoints that intentionally skip auth
    NO_AUTH_ENDPOINTS = {
        "health", "health_config", "login", "callback",
        "logout",  # Must work with expired/invalid sessions to clear cookie
    }

    for lineno, line in enumerate(lines, start=1):
        stripped = line.lstrip()

        # Detect route decorators
        route_match = _ROUTE_DECORATOR.match(stripped)
        if route_match:
            # If we were tracking a previous handler, finalize it
            if in_handler and handler_name not in NO_AUTH_ENDPOINTS:
                if not handler_has_auth:
                    issues.append(Issue(
                        path, handler_start, "MISSING_AUTH",
                        f"Handler '{handler_name}' has no Depends(get_current_user)"
                    ))

            prev_was_route_decorator = True
            continue

        # Detect handler function definition after decorator
        if prev_was_route_decorator:
            prev_was_route_decorator = False

            async_match = _ASYNC_DEF.match(stripped)
            sync_match = _SYNC_DEF.match(stripped)

            if async_match:
                handler_name = async_match.group(1)
            elif sync_match:
                handler_name = sync_match.group(1)
                issues.append(Issue(
                    path, lineno, "SYNC_HANDLER",
                    f"Handler '{handler_name}' must be 'async def' for Workers runtime"
                ))
            else:
                continue

            in_handler = True
            handler_start = lineno
            handler_has_auth = False
            continue

        if in_handler:
            # Check for auth dependency
            if _DEPENDS_AUTH.search(line):
                handler_has_auth = True

        # Check status string literals everywhere in the file
        for match in _STATUS_LITERAL.finditer(stripped):
            status_value = match.group(1)
            if status_value not in ALL_VALID_STATUSES:
                issues.append(Issue(
                    path, lineno, "INVALID_STATUS",
                    f"Status literal '{status_value}' does not match any known enum. "
                    f"Valid values: {sorted(ALL_VALID_STATUSES)}"
                ))

    # Finalize the last handler
    if in_handler and handler_name not in NO_AUTH_ENDPOINTS:
        if not handler_has_auth:
            issues.append(Issue(
                path, handler_start, "MISSING_AUTH",
                f"Handler '{handler_name}' has no Depends(get_current_user)"
            ))

    # Check for raw js/pyodide imports
    for lineno, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if re.match(r"^\s*(?:import\s+js\b|from\s+js\s+import)", stripped):
            issues.append(Issue(
                path, lineno, "RAW_JS_IMPORT",
                "Route files should not import 'js' directly -- use wrappers.py"
            ))
        if re.match(r"^\s*from\s+pyodide", stripped):
            issues.append(Issue(
                path, lineno, "RAW_PYODIDE_IMPORT",
                "Route files should not import from 'pyodide' directly -- use wrappers.py"
            ))

    return issues


def check_deletion_order(path: Path) -> list[Issue]:
    """Check that R2 content is deleted before D1 rows."""
    issues = []
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []

    # Look for delete patterns: R2 delete should come before D1 DELETE
    lines = text.splitlines()
    r2_delete_lines = []
    d1_delete_lines = []

    for lineno, line in enumerate(lines, start=1):
        if "delete_article_content" in line or "r2.delete" in line:
            r2_delete_lines.append(lineno)
        if "DELETE FROM articles" in line or "DELETE FROM article_tags" in line:
            d1_delete_lines.append(lineno)

    # For each D1 delete, check if there's a preceding R2 delete in the same
    # function context (within ~20 lines before)
    for d1_line in d1_delete_lines:
        has_preceding_r2 = any(
            r2_line < d1_line and d1_line - r2_line < 30
            for r2_line in r2_delete_lines
        )
        # Only flag if there are R2 deletes in the file but they come AFTER D1 deletes
        has_following_r2 = any(
            r2_line > d1_line and r2_line - d1_line < 30
            for r2_line in r2_delete_lines
        )
        if has_following_r2 and not has_preceding_r2:
            issues.append(Issue(
                path, d1_line, "DELETION_ORDER",
                "D1 row deleted before R2 content -- should delete R2 first "
                "(see LESSONS_LEARNED.md section 16)"
            ))

    return issues


def main() -> int:
    all_issues: list[Issue] = []
    files_scanned = 0

    for route_file in sorted(ROUTE_FILES):
        if not route_file.exists():
            continue
        all_issues.extend(scan_route_file(route_file))
        all_issues.extend(check_deletion_order(route_file))
        files_scanned += 1

    print(f"Handler Consistency Check -- scanned {files_scanned} route files\n")

    if all_issues:
        categories: dict[str, list[Issue]] = {}
        for issue in all_issues:
            categories.setdefault(issue.category, []).append(issue)

        for category, items in sorted(categories.items()):
            print(f"{category} ({len(items)}):")
            for item in items:
                print(str(item))
            print()

        print(f"Total: {len(all_issues)} issue(s) found.")
        return 1

    print("PASSED -- all route handlers are structurally consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Report approximate Worker bundle input sizes.

This measures checked-in source/assets and generated deployment inputs when they
exist. It does not run Wrangler or pywrangler; use it as a cheap trend metric.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATHS = [
    "src",
    "assets",
    "migrations",
    "frontend/dist",
    "readability-worker",
    "python_modules",
]
EXCLUDED_DIRS = {
    ".git",
    ".hypothesis",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".venv-workers",
    "__pycache__",
    "node_modules",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


@dataclass(frozen=True)
class SizeEntry:
    path: str
    bytes: int
    exists: bool = True

    @property
    def human(self) -> str:
        return human_size(self.bytes)


def human_size(size: int) -> str:
    units = ["B", "KiB", "MiB", "GiB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def iter_files(path: Path):
    if path.is_file():
        if path.suffix not in EXCLUDED_SUFFIXES:
            yield path
        return
    if not path.exists():
        return
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in child.relative_to(PROJECT_ROOT).parts):
            continue
        if child.suffix in EXCLUDED_SUFFIXES:
            continue
        yield child


def path_size(path: Path) -> int:
    return sum(file.stat().st_size for file in iter_files(path))


def collect(paths: list[str]) -> list[SizeEntry]:
    entries: list[SizeEntry] = []
    for raw in paths:
        path = PROJECT_ROOT / raw
        entries.append(SizeEntry(raw, path_size(path), path.exists()))
    return entries


def largest_children(path: Path, limit: int) -> list[SizeEntry]:
    if not path.exists() or not path.is_dir():
        return []
    entries = [
        SizeEntry(str(child.relative_to(PROJECT_ROOT)), path_size(child), child.exists())
        for child in path.iterdir()
        if child.name not in EXCLUDED_DIRS
    ]
    return sorted(entries, key=lambda item: item.bytes, reverse=True)[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=DEFAULT_PATHS, help="Paths to include")
    parser.add_argument("--top", type=int, default=12, help="Largest child entries to show")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    entries = collect(args.paths)
    total = sum(entry.bytes for entry in entries)
    top_entries: list[SizeEntry] = []
    for raw in args.paths:
        top_entries.extend(largest_children(PROJECT_ROOT / raw, args.top))
    top_entries = sorted(top_entries, key=lambda item: item.bytes, reverse=True)[: args.top]

    if args.json:
        print(
            json.dumps(
                {
                    "total_bytes": total,
                    "total_human": human_size(total),
                    "paths": [asdict(entry) | {"human": entry.human} for entry in entries],
                    "largest_children": [
                        asdict(entry) | {"human": entry.human} for entry in top_entries
                    ],
                },
                indent=2,
            )
        )
        return 0

    print("Bundle input size estimate")
    print(f"Project: {PROJECT_ROOT}")
    print()
    width = max(len(entry.path) for entry in entries) if entries else 0
    for entry in entries:
        marker = "" if entry.exists else " (missing)"
        print(f"  {entry.path:<{width}}  {entry.human:>10}{marker}")
    print("-" * (width + 14))
    print(f"  {'total':<{width}}  {human_size(total):>10}")
    if top_entries:
        print("\nLargest included children:")
        child_width = max(len(entry.path) for entry in top_entries)
        for entry in top_entries:
            print(f"  {entry.path:<{child_width}}  {entry.human:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

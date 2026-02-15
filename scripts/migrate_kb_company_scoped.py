#!/usr/bin/env python3
"""Migrate Knowledge Repository to company-scoped structure.

This project expects KB files to live under:

  {Section}/{Company}/...

for Sections:
- 00_Glossary
- 10_Style_Guide
- 20_Domain_Knowledge
- 40_Templates

This script moves *unscoped* items directly under those sections into the
selected company folder. It does not modify 30_Reference.

Run with --dry-run first.
"""

from __future__ import annotations

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path


SECTIONS = ["00_Glossary", "10_Style_Guide", "20_Domain_Knowledge", "40_Templates"]


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


def _unique_dest(dest: Path) -> Path:
    if not dest.exists():
        return dest
    return dest.with_name(f"{dest.stem}_{_timestamp()}{dest.suffix}")


def _move(src: Path, dest: Path, *, dry_run: bool) -> Path:
    dest = _unique_dest(dest)
    if dry_run:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    return Path(shutil.move(str(src), str(dest)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate KB into company-scoped folders.")
    parser.add_argument("--kb-root", required=True, help="Path to Knowledge Repository")
    parser.add_argument("--company", required=True, help="Company folder name to migrate into")
    parser.add_argument("--dry-run", action="store_true", help="Print planned moves without modifying files")
    parser.add_argument(
        "--include-dirs",
        action="store_true",
        help="Also move unscoped directories (default: only move files to avoid moving other companies)",
    )
    args = parser.parse_args()

    kb_root = Path(args.kb_root).expanduser().resolve()
    company = str(args.company or "").strip()
    if not company:
        raise SystemExit("ERROR: --company is required")

    planned = 0
    moved = 0

    for section in SECTIONS:
        section_root = kb_root / section
        if not section_root.exists() or not section_root.is_dir():
            continue
        company_root = section_root / company
        if not args.dry_run:
            company_root.mkdir(parents=True, exist_ok=True)

        for child in sorted(section_root.iterdir(), key=lambda p: p.name.lower()):
            if child.name.startswith("."):
                continue
            if child.name == company:
                continue
            if child.is_dir() and not args.include_dirs:
                continue
            planned += 1
            dest = company_root / child.name
            resolved = _move(child, dest, dry_run=args.dry_run)
            if args.dry_run:
                print(f"[dry-run] {child} -> {resolved}")
            else:
                moved += 1
                print(f"[moved] {child} -> {resolved}")

    if args.dry_run:
        print(f"Planned moves: {planned}")
    else:
        print(f"Moved items: {moved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

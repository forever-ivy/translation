#!/usr/bin/env python3
"""Build a task bundle by discovering and classifying translation task documents."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

KNOWN_SUBFOLDERS = {
    "arabic_source": "Arabic Source",
    "glossary": "Glossery",
    "previously_translated": "Previously Translated",
    "translated_en": "Translated -EN",
}


def is_arabic_name(name: str) -> bool:
    return bool(re.search(r"[\u0600-\u06ff]", name))


def infer_language(path: Path) -> str:
    name = path.name
    lowered = name.lower()
    if is_arabic_name(name):
        return "ar"
    if any(token in lowered for token in ("arabic", "ar_", "_ar", "ar-")):
        return "ar"
    return "en"


def infer_version(path: Path) -> str:
    lowered = path.name.lower()
    if re.search(r"(^|[\s_\-\[\(])v1([\s_\-\]\)]|$)", lowered):
        return "v1"
    if re.search(r"(^|[\s_\-\[\(])v2([\s_\-\]\)]|$)", lowered):
        return "v2"
    if re.search(r"(^|[\s_\-\[\(])v3([\s_\-\]\)]|$)", lowered):
        return "v3"
    return "unknown"


def infer_role(path: Path) -> str:
    lowered_full = str(path).lower()
    lowered_name = path.name.lower()
    if "/_review/" in lowered_full or "/.system/" in lowered_full:
        return "generated"
    if "glossery" in lowered_full or "glossary" in lowered_full:
        return "glossary"
    if "previously translated" in lowered_full:
        return "reference_translation"
    if "translated -en" in lowered_full:
        return "translated_output"
    if "source" in lowered_full:
        return "source"
    if any(t in lowered_name for t in ("survey", "questionnaire", "استبانة")):
        return "survey"
    return "general"


def classify_legacy_slot(path: Path) -> str | None:
    name = path.name.lower()
    arabic = is_arabic_name(path.name)

    if arabic and "v2" in name:
        return "arabic_v2"
    if arabic and "v1" in name:
        return "arabic_v1"
    if ("english" in name or "ai readiness" in name or "quantitative" in name) and "v1" in name:
        return "english_v1"
    return None


def discover_docx(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for p in root.rglob("*.docx"):
        if "~$" in p.name:
            continue
        lowered = str(p).lower()
        if "/_review/" in lowered or "/.system/" in lowered:
            continue
        candidates.append(p)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates


def build_bundle(root: Path, job_id: str) -> dict[str, Any]:
    files = discover_docx(root)
    legacy_mapping: dict[str, Path] = {}

    candidate_files: list[dict[str, Any]] = []
    for doc in files:
        stat = doc.stat()
        language = infer_language(doc)
        version = infer_version(doc)
        role = infer_role(doc)
        source_folder = "root"
        for key, folder in KNOWN_SUBFOLDERS.items():
            if f"/{folder.lower()}/" in str(doc).lower():
                source_folder = key
                break

        candidate_files.append(
            {
                "path": str(doc.resolve()),
                "name": doc.name,
                "language": language,
                "version": version,
                "role": role,
                "source_folder": source_folder,
                "mtime_ns": stat.st_mtime_ns,
                "size_bytes": stat.st_size,
            }
        )

        legacy_slot = classify_legacy_slot(doc)
        if legacy_slot and legacy_slot not in legacy_mapping:
            legacy_mapping[legacy_slot] = doc

    required_legacy = ["arabic_v1", "arabic_v2", "english_v1"]
    missing_legacy = [k for k in required_legacy if k not in legacy_mapping]

    bundle_files: dict[str, Any] = {}
    for key in required_legacy:
        if key in legacy_mapping:
            bundle_files[key] = {
                "path": str(legacy_mapping[key].resolve()),
                "name": legacy_mapping[key].name,
            }
        else:
            bundle_files[key] = None

    language_counts = {
        "ar": sum(1 for x in candidate_files if x["language"] == "ar"),
        "en": sum(1 for x in candidate_files if x["language"] == "en"),
    }

    role_counts: dict[str, int] = {}
    for item in candidate_files:
        role = item["role"]
        role_counts[role] = role_counts.get(role, 0) + 1

    return {
        "job_id": job_id,
        "root": str(root.resolve()),
        "valid": len(candidate_files) > 0,
        "missing": [] if candidate_files else ["no_docx_found"],
        "files": bundle_files,
        "legacy_missing": missing_legacy,
        "candidate_files": candidate_files,
        "stats": {
            "doc_count": len(candidate_files),
            "language_counts": language_counts,
            "role_counts": role_counts,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, help="Task root folder")
    parser.add_argument("--job-id", required=True, help="Job ID")
    parser.add_argument("--output", help="Output JSON file")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(json.dumps({"ok": False, "error": f"Missing root: {root}"}), file=sys.stderr)
        return 2

    payload = build_bundle(root, args.job_id)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"ok": True, "data": payload}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

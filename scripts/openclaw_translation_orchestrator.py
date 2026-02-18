#!/usr/bin/env python3
"""OpenClaw V5.2 translation orchestrator (LLM intent + real Codex/Gemini rounds)."""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# Configure logging
log = logging.getLogger(__name__)

# Allow running this file directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_delta_pack import build_delta, flatten_blocks
from scripts.docx_preserver import extract_units as extract_docx_units
from scripts.docx_preserver import units_to_payload as docx_units_to_payload
from scripts.revision_pack import RevisionPack, build_revision_pack, format_revision_context_for_prompt
from scripts.extract_docx_structure import extract_structure
from scripts.openclaw_artifact_writer import write_artifacts
from scripts.openclaw_quality_gate import QualityThresholds, compute_runtime_timeout, evaluate_quality
from scripts.output_sanity import scan_markdown_in_translation_maps
from scripts.v4_kb import extract_text
from scripts.xlsx_preserver import extract_translatable_cells
from scripts.xlsx_preserver import units_to_payload as xlsx_units_to_payload


def _resolve_openclaw_bin() -> str:
    """Find the openclaw binary, checking PATH and known install locations."""
    found = shutil.which("openclaw")
    if found:
        return found
    # Check common install locations
    for candidate in [
        Path.home() / ".npm-global" / "bin" / "openclaw",
        Path.home() / ".local" / "bin" / "openclaw",
    ]:
        if candidate.exists():
            return str(candidate)
    # Fallback: let subprocess try and fail with a clear error
    return "openclaw"


OPENCLAW_BIN = _resolve_openclaw_bin()
FORMAT_QA_ENABLED = os.getenv("OPENCLAW_FORMAT_QA_ENABLED", "0").strip() == "1"
DOCX_QA_ENABLED = os.getenv("OPENCLAW_DOCX_QA_ENABLED", "0").strip() == "1"


def _env_flag(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default)).strip().lower() not in {"0", "false", "off", "no", ""}


def _pipeline_version() -> str:
    override = str(os.getenv("OPENCLAW_PIPELINE_VERSION", "")).strip()
    if override:
        return override
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        sha = proc.stdout.strip() if proc.returncode == 0 else ""
        if not sha:
            return "unknown"
        dirty = ""
        proc2 = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(PROJECT_ROOT),
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if proc2.returncode == 0 and proc2.stdout.strip():
            dirty = "+dirty"
        return f"{sha}{dirty}"
    except Exception:
        return "unknown"


TASK_TYPES = {
    "REVISION_UPDATE",
    "NEW_TRANSLATION",
    "BILINGUAL_REVIEW",
    "BILINGUAL_PROOFREADING",
    "EN_ONLY_EDIT",
    "MULTI_FILE_BATCH",
    "TERMINOLOGY_ENFORCEMENT",
    "LOW_CONTEXT_TASK",
    "FORMAT_CRITICAL_TASK",
    "SPREADSHEET_TRANSLATION",
}

REQUIRED_INPUTS_BY_TASK: dict[str, list[str]] = {
    "REVISION_UPDATE": ["source_old", "source_new", "target_baseline"],
    "NEW_TRANSLATION": ["source_document"],
    "BILINGUAL_REVIEW": ["source_document", "target_document"],
    "BILINGUAL_PROOFREADING": ["source_document", "target_document"],
    "EN_ONLY_EDIT": ["target_document"],
    "MULTI_FILE_BATCH": ["batch_documents"],
    "TERMINOLOGY_ENFORCEMENT": ["target_document", "glossary"],
    "LOW_CONTEXT_TASK": [],
    "FORMAT_CRITICAL_TASK": ["source_document"],
    "SPREADSHEET_TRANSLATION": ["source_document"],
}

CODEX_AGENT = os.getenv("OPENCLAW_CODEX_AGENT", "translator-core")

TASK_TOOL_INSTRUCTIONS: dict[str, str] = {
    "REVISION_UPDATE": (
        "REVISION UPDATE: Update the English document to match Arabic V2 changes. "
        "CRITICAL: You will receive a revision_pack with sections marked as PRESERVE EXACTLY or TRANSLATE. "
        "For PRESERVE EXACTLY sections: Copy the English text verbatim - no changes whatsoever. "
        "For TRANSLATE sections: Translate the updated Arabic V2 text. "
        "For NEW sections: Translate the new Arabic V2 content. "
        "The revision_pack.preserved_text_map contains the exact texts you must copy without modification. "
        "Your docx_translation_map must include ALL unit IDs from the template."
    ),
    "NEW_TRANSLATION": (
        "Translate the source document from scratch. Maintain paragraph and heading structure. "
        "Use formal register unless context indicates otherwise. Preserve all numbering, "
        "bullet formatting, and table layouts."
    ),
    "BILINGUAL_PROOFREADING": (
        "Compare the source document against the target translation. Correct errors in the "
        "target language only — do not alter the source. Fix grammar, terminology, and "
        "mistranslations while preserving the target language throughout."
    ),
    "BILINGUAL_REVIEW": (
        "Evaluate translation quality by comparing source and target. Produce a detailed "
        "review with specific findings: mistranslations, omissions, terminology issues, "
        "and style inconsistencies. Suggest concrete improvements."
    ),
    "EN_ONLY_EDIT": (
        "Edit the target-language document for grammar, clarity, and consistency. Do not translate. "
        "Preserve the original meaning and structure. Fix punctuation, spelling, and "
        "awkward phrasing."
    ),
    "SPREADSHEET_TRANSLATION": (
        "Translate cell by cell, preserving the spreadsheet structure exactly. Do not alter "
        "numbers, formulas, or cell references. Keep column/row alignment intact. "
        "Translate headers and text cells only."
    ),
    "FORMAT_CRITICAL_TASK": (
        "Structural fidelity is the top priority. Preserve all headings, numbering, "
        "bullet hierarchies, table structures, and page breaks exactly. Translation "
        "accuracy is secondary to format preservation."
    ),
    "TERMINOLOGY_ENFORCEMENT": (
        "Apply the provided glossary strictly. Every glossary term must be translated "
        "using the specified equivalent — no synonyms or alternatives. Flag any source "
        "terms not covered by the glossary."
    ),
    "MULTI_FILE_BATCH": (
        "Process each file independently but maintain cross-file consistency for shared "
        "terminology and style. Use the same translation choices across all files. "
        "Report per-file status."
    ),
    "LOW_CONTEXT_TASK": (
        "Best-effort translation with limited context. Flag any uncertainties or ambiguous "
        "passages explicitly. Prefer literal translation when context is insufficient "
        "to determine intent."
    ),
}
GEMINI_AGENT = os.getenv("OPENCLAW_GEMINI_AGENT", "review-core")
OPENCLAW_CMD_TIMEOUT = int(os.getenv("OPENCLAW_AGENT_CALL_TIMEOUT_SECONDS", "700"))
DOC_CONTEXT_CHARS = int(os.getenv("OPENCLAW_DOC_CONTEXT_CHARS", "45000"))
VALID_THINKING_LEVELS = {"off", "minimal", "low", "medium", "high"}
OPENCLAW_TRANSLATION_THINKING = os.getenv("OPENCLAW_TRANSLATION_THINKING", "high").strip().lower()
if OPENCLAW_TRANSLATION_THINKING not in VALID_THINKING_LEVELS:
    OPENCLAW_TRANSLATION_THINKING = "high"

GLM_AGENT = os.getenv("OPENCLAW_GLM_AGENT", "glm-reviewer")
GLM_GENERATOR_AGENT = os.getenv("OPENCLAW_GLM_GENERATOR_AGENT", GLM_AGENT)
GLM_ENABLED = os.getenv("OPENCLAW_GLM_ENABLED", "0").strip() == "1"
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
GLM_API_BASE_URL = os.getenv("GLM_API_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
GLM_MODEL = os.getenv("OPENCLAW_GLM_MODEL", "zai/glm-5")


def _normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_docx_translation_map_ids(entries: Any) -> set[str]:
    ids: set[str] = set()
    if not entries:
        return ids
    if isinstance(entries, dict):
        for k in entries.keys():
            key = str(k or "").strip()
            if key:
                ids.add(key)
        return ids
    if not isinstance(entries, list):
        return ids
    for item in entries:
        if not isinstance(item, dict):
            continue
        unit_id = str(item.get("id") or item.get("unit_id") or item.get("block_id") or item.get("cell_id") or "").strip()
        if unit_id:
            ids.add(unit_id)
    return ids


def _normalize_xlsx_translation_map_keys(entries: Any, *, xlsx_files: list[str]) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    if not entries:
        return keys

    # Convenience shape for single-file jobs: {"Sheet!B2": "..."}
    if isinstance(entries, dict) and len(xlsx_files) == 1:
        file_name = xlsx_files[0]
        for k in entries.keys():
            raw = str(k or "")
            if "!" not in raw:
                continue
            sheet, cell = raw.split("!", 1)
            sheet = sheet.strip()
            cell = cell.strip().upper()
            if sheet and cell:
                keys.add((file_name, sheet, cell))
        return keys

    if not isinstance(entries, list):
        return keys
    for item in entries:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file") or "").strip()
        sheet = str(item.get("sheet") or "").strip()
        cell = str(item.get("cell") or "").strip().upper()
        if file_name and sheet and cell:
            keys.add((file_name, sheet, cell))
    return keys


def _validate_format_preserve_coverage(context: dict[str, Any], draft: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Return (findings, meta) for missing/incomplete preserve maps."""
    findings: list[str] = []
    meta: dict[str, Any] = {}
    preserve = (context.get("format_preserve") or {}) if isinstance(context.get("format_preserve"), dict) else {}

    docx = preserve.get("docx_template") if isinstance(preserve.get("docx_template"), dict) else None
    if docx and isinstance(docx.get("units"), list):
        expected = {str(u.get("id") or "").strip() for u in docx.get("units") if isinstance(u, dict) and str(u.get("id") or "").strip()}
        got = _normalize_docx_translation_map_ids(draft.get("docx_translation_map"))
        missing = sorted(expected - got)
        meta["docx_expected"] = len(expected)
        meta["docx_got"] = len(got)
        if expected and not got:
            findings.append("docx_translation_map_missing")
        elif missing:
            findings.append(f"docx_translation_map_incomplete:missing={len(missing)}")
            meta["docx_missing_sample"] = missing[:8]

    xlsx_sources = preserve.get("xlsx_sources") if isinstance(preserve.get("xlsx_sources"), list) else []
    if xlsx_sources:
        expected_keys: set[tuple[str, str, str]] = set()
        xlsx_files: list[str] = []
        for src in xlsx_sources:
            if not isinstance(src, dict):
                continue
            file_name = str(src.get("file") or "").strip()
            if file_name:
                xlsx_files.append(file_name)
            for unit in (src.get("cell_units") or []):
                if not isinstance(unit, dict):
                    continue
                sheet = str(unit.get("sheet") or "").strip()
                cell = str(unit.get("cell") or "").strip().upper()
                file_val = str(unit.get("file") or file_name).strip()
                if file_val and sheet and cell:
                    expected_keys.add((file_val, sheet, cell))

        got_keys = _normalize_xlsx_translation_map_keys(draft.get("xlsx_translation_map"), xlsx_files=xlsx_files)
        missing = sorted(expected_keys - got_keys)
        meta["xlsx_expected"] = len(expected_keys)
        meta["xlsx_got"] = len(got_keys)
        if expected_keys and not got_keys:
            findings.append("xlsx_translation_map_missing")
        elif missing:
            findings.append(f"xlsx_translation_map_incomplete:missing={len(missing)}")
            meta["xlsx_missing_sample"] = [
                {"file": f, "sheet": s, "cell": c}
                for (f, s, c) in missing[:8]
            ]

    return findings, meta


def _load_meta(args: argparse.Namespace) -> dict[str, Any]:
    if args.meta_json:
        return json.loads(args.meta_json)
    if args.meta_json_file:
        return json.loads(Path(args.meta_json_file).read_text(encoding="utf-8"))
    if args.meta_json_base64:
        decoded = base64.b64decode(args.meta_json_base64.encode("utf-8")).decode("utf-8")
        return json.loads(decoded)
    raise ValueError("One of --meta-json / --meta-json-file / --meta-json-base64 is required")


def _result_path(review_dir: str) -> Path:
    return Path(review_dir) / ".system" / "openclaw_result.json"


def _write_result(review_dir: str, payload: dict[str, Any]) -> None:
    if not review_dir:
        return
    result_path = _result_path(review_dir)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _collect_candidates(meta: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in meta.get("candidate_files") or []:
        if not isinstance(item, dict):
            continue
        if not item.get("path"):
            continue
        out.append(item)

    files = meta.get("files") or {}

    def _build_legacy_slot_candidates() -> list[tuple[str, str, str]]:
        """Dynamically build (slot_name, language, version) from files dict.

        Supports both legacy names (arabic_v1, english_v1) and dynamic names (fr_v1, zh_v2).
        """
        result: list[tuple[str, str, str]] = []
        legacy_map = {
            "arabic_v1": ("ar", "v1"),
            "arabic_v2": ("ar", "v2"),
            "english_v1": ("en", "v1"),
        }
        # Add legacy ar/en slots first for backward compatibility
        for key, (lang, ver) in legacy_map.items():
            if key in files and files.get(key):
                result.append((key, lang, ver))
        # Then add any dynamic slots from files (e.g., fr_v1, zh_v2)
        for key in files:
            if key in legacy_map:
                continue
            # Parse dynamic slot names like "fr_v1", "zh_v2", "de_v1"
            parts = key.rsplit("_", 1)
            if len(parts) == 2 and len(parts[0]) == 2 and parts[1] in {"v1", "v2", "v3"}:
                lang, ver = parts
                result.append((key, lang, ver))
        return result

    legacy_candidates = _build_legacy_slot_candidates()
    seen = {Path(x["path"]).resolve() for x in out if x.get("path")}
    for key, lang, version in legacy_candidates:
        data = files.get(key)
        if not data:
            continue
        p = Path(data["path"]).resolve()
        if p in seen:
            continue
        out.append(
            {
                "path": str(p),
                "name": Path(p).name,
                "language": lang,
                "version": version,
                "role": "source",
                "source_folder": "legacy",
            }
        )
        seen.add(p)
    return out


def _enrich_structures(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in candidates:
        path = Path(item["path"])
        if not path.exists():
            continue
        if item.get("structure"):
            structure = item["structure"]
        else:
            if path.suffix.lower() == ".docx":
                structure = extract_structure(path)
            else:
                parser, text = extract_text(path)
                lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
                blocks = [{"kind": "paragraph", "text": ln} for ln in lines]
                structure = {
                    "path": str(path.resolve()),
                    "name": path.name,
                    "paragraph_count": len(lines),
                    "table_count": 1 if parser in {"xlsx", "csv"} else 0,
                    "block_count": len(blocks),
                    "blocks": blocks,
                    "parser": parser,
                }
        out.append({**item, "structure": structure, "path": str(path.resolve())})
    return out


def _pick_file(candidates: list[dict[str, Any]], *, language: str, version: str | None = None) -> str | None:
    for item in candidates:
        if item.get("language") != language:
            continue
        if version and item.get("version") != version:
            continue
        return item["path"]
    return None


def _select_docx_template(candidates: list[dict[str, Any]], *, target_language: str) -> str | None:
    target_lang = str(target_language or "").strip().lower()
    template_path: str | None = None
    if target_lang and target_lang not in {"unknown", "multi"}:
        template_path = _pick_file(candidates, language=target_lang, version="v1") or _pick_file(candidates, language=target_lang)

    if template_path and Path(template_path).suffix.lower() == ".docx":
        return template_path

    return next(
        (
            str(item.get("path"))
            for item in candidates
            if str(item.get("path") or "").lower().endswith(".docx")
        ),
        None,
    )


def _structure_text(struct: dict[str, Any], max_chars: int = DOC_CONTEXT_CHARS) -> str:
    lines: list[str] = []
    for block in struct.get("blocks", []):
        if block.get("kind") == "paragraph":
            text = _normalize_text(block.get("text", ""))
            if text:
                lines.append(text)
        elif block.get("kind") == "table":
            rows = block.get("rows") or []
            for row in rows:
                cells = [_normalize_text(str(cell)) for cell in row if _normalize_text(str(cell))]
                if cells:
                    lines.append(" | ".join(cells))
    joined = "\n".join(lines)
    if len(joined) <= max_chars:
        return joined
    return joined[:max_chars]


def _candidate_payload(candidates: list[dict[str, Any]], *, include_text: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in candidates:
        struct = item.get("structure") or {}
        row: dict[str, Any] = {
            "name": item.get("name"),
            "path": item.get("path"),
            "language": item.get("language"),
            "version": item.get("version"),
            "role": item.get("role"),
            "paragraph_count": struct.get("paragraph_count", 0),
            "table_count": struct.get("table_count", 0),
            "block_count": struct.get("block_count", 0),
        }
        if include_text:
            row["content"] = _structure_text(struct, max_chars=DOC_CONTEXT_CHARS)
        rows.append(row)
    return rows


def _extract_json_from_text(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty response text")

    def _coerce_first_dict(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    return item
        return None

    # 1) Strict JSON only.
    try:
        coerced = _coerce_first_dict(json.loads(raw))
        if coerced is not None:
            return coerced
    except json.JSONDecodeError:
        pass

    # 2) Allow trailing garbage after a valid JSON value.
    decoder = json.JSONDecoder()
    try:
        value, _end = decoder.raw_decode(raw)
        coerced = _coerce_first_dict(value)
        if coerced is not None:
            return coerced
    except json.JSONDecodeError:
        pass

    # 3) Best-effort scan: pick the dict that most resembles one of our expected schemas.
    dict_candidates: list[dict[str, Any]] = []
    for cand in _iter_json_candidates(raw, limit=24):
        coerced = _coerce_first_dict(cand)
        if coerced is not None:
            dict_candidates.append(coerced)
    if dict_candidates:
        keysets = [
            {
                "final_text",
                "final_reflow_text",
                "draft_a_text",
                "draft_b_text",
                "docx_translation_map",
                "docx_translation_blocks",
                "docx_table_cells",
                "xlsx_translation_map",
                "review_brief_points",
                "change_log_points",
                "resolved",
                "unresolved",
                "codex_pass",
                "reasoning_summary",
            },
            {
                "task_type",
                "task_label",
                "source_language",
                "target_language",
                "required_inputs",
                "missing_inputs",
                "confidence",
                "reasoning_summary",
                "estimated_minutes",
                "complexity_score",
            },
            {
                "findings",
                "resolved",
                "unresolved",
                "pass",
                "terminology_rate",
                "structure_complete_rate",
                "target_language_purity",
                "numbering_consistency",
                "reasoning_summary",
            },
            {
                "findings",
                "pass",
                "terminology_score",
                "completeness_score",
                "naturalness_score",
                "reasoning_summary",
            },
        ]

        def _score(obj: dict[str, Any]) -> tuple[int, int]:
            keys = set(obj.keys())
            best_hits = 0
            for ks in keysets:
                hits = sum(1 for k in ks if k in keys)
                if hits > best_hits:
                    best_hits = hits
            return (best_hits, len(keys))

        return max(dict_candidates, key=_score)

    raise ValueError("no JSON object in model output")


def _iter_json_candidates(raw: str, *, limit: int = 12) -> list[Any]:
    """Extract JSON values from mixed stdout that may contain log lines.

    We scan for the first few JSON objects/arrays that `json.JSONDecoder.raw_decode`
    can parse starting at a `{` or `[` character. This is more robust than
    slicing `first_brace:last_brace` because OpenClaw may emit extra braces in
    strings or append additional debug output after the JSON blob.
    """
    text = (raw or "").strip()
    if not text:
        return []
    decoder = json.JSONDecoder()
    out: list[Any] = []
    for idx, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            value, _end = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        out.append(value)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _extract_openclaw_payload_text(payload: Any) -> str:
    """Best-effort extraction of the first text payload from OpenClaw agent output."""
    if isinstance(payload, dict):
        # Common gateway format: {"result": {"payloads": [{"text": "..."}]}}
        result = payload.get("result")
        for container in (result, payload):
            if not isinstance(container, dict):
                continue
            items = container.get("payloads")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        return text
        # Fallback: some runtimes may return a direct text field.
        direct = payload.get("text")
        if isinstance(direct, str) and direct.strip():
            return direct

    if isinstance(payload, list):
        for item in payload:
            text = _extract_openclaw_payload_text(item)
            if text:
                return text
    return ""


def _agent_call(agent_id: str, message: str, timeout_seconds: int = OPENCLAW_CMD_TIMEOUT) -> dict[str, Any]:
    cmd = [
        OPENCLAW_BIN,
        "agent",
        "--agent",
        agent_id,
        "--message",
        message,
        "--json",
        "--thinking",
        OPENCLAW_TRANSLATION_THINKING,
        "--timeout",
        str(max(30, int(timeout_seconds))),
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": f"agent_call_failed:{agent_id}",
            "stderr": proc.stderr.strip(),
            "stdout": proc.stdout.strip(),
            "returncode": proc.returncode,
        }

    payload: Any | None = None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        # OpenClaw may emit non-JSON log lines before/after the actual JSON blob
        # (e.g. "[agent/embedded] ..."). Extract the first decodable JSON value.
        candidates = _iter_json_candidates(proc.stdout, limit=12)
        if not candidates:
            return {
                "ok": False,
                "error": f"agent_json_invalid:{agent_id}",
                "detail": "no JSON value found in stdout",
                "stdout": proc.stdout[:2000],
            }

        # Prefer the candidate that actually contains payload text.
        payload = candidates[0]
        for cand in candidates:
            if _extract_openclaw_payload_text(cand):
                payload = cand
                break

    text = _extract_openclaw_payload_text(payload)
    return {"ok": True, "agent_id": agent_id, "payload": payload, "text": text}


LEGACY_REQUIRED_INPUTS_MAP = {
    # Legacy ar→en mappings
    "arabic_old": "source_old",
    "arabic_new": "source_new",
    "english_baseline": "target_baseline",
    "english_document": "target_document",
    # Generic aliases for any language pair
    "source_old": "source_old",
    "source_new": "source_new",
    "target_baseline": "target_baseline",
    "target_document": "target_document",
}


def _normalize_required_inputs(values: list[Any]) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        text = str(raw or "").strip().lower()
        if not text:
            continue
        out.append(LEGACY_REQUIRED_INPUTS_MAP.get(text, text))
    seen: set[str] = set()
    deduped: list[str] = []
    for item in out:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _available_slots(
    candidates: list[dict[str, Any]],
    *,
    source_language: str,
    target_language: str,
) -> dict[str, bool]:
    has_any_file = len(candidates) > 0
    languages = {str(x.get("language") or "").strip().lower() for x in candidates if str(x.get("language") or "").strip()}
    has_multiple_langs = len(languages) >= 2
    has_glossary = any((x.get("role") == "glossary") for x in candidates)

    src = str(source_language or "unknown").strip().lower()
    tgt = str(target_language or "unknown").strip().lower()

    def _has_lang(lang: str) -> bool:
        if not lang or lang in {"unknown", "multi"}:
            return False
        return any((str(x.get("language") or "").strip().lower() == lang) for x in candidates)

    def _has_lang_version(lang: str, version: str) -> bool:
        if not lang or lang in {"unknown", "multi"}:
            return False
        return any(
            (
                str(x.get("language") or "").strip().lower() == lang
                and str(x.get("version") or "").strip().lower() == version
            )
            for x in candidates
        )

    has_source = _has_lang(src)
    has_target = _has_lang(tgt)
    has_source_v1 = _has_lang_version(src, "v1")
    has_source_v2 = _has_lang_version(src, "v2")
    has_source_v3 = _has_lang_version(src, "v3")
    has_target_v1 = _has_lang_version(tgt, "v1")

    source_document = has_any_file if src in {"unknown", "multi"} else (has_source or has_any_file)
    target_document = has_target if tgt not in {"unknown", "multi"} else has_multiple_langs
    target_baseline = has_target_v1 or has_target

    return {
        "source_old": has_source_v1,
        "source_new": has_source_v2 or has_source_v3,  # V2 or V3 counts as new source
        "target_baseline": target_baseline,
        "source_document": source_document,
        "target_document": target_document,
        "batch_documents": len(candidates) >= 2,
        "glossary": has_glossary,
    }


def _llm_intent(meta: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    message_blob = " ".join(
        [
            str(meta.get("subject") or ""),
            str(meta.get("message_text") or ""),
            str(meta.get("message") or ""),
        ]
    ).strip()
    files_payload = _candidate_payload(candidates, include_text=False)
    prompt = f"""
You are classifying a translation job. Return strict JSON only.

Allowed task_type values:
REVISION_UPDATE, NEW_TRANSLATION, BILINGUAL_REVIEW, BILINGUAL_PROOFREADING, EN_ONLY_EDIT, MULTI_FILE_BATCH, TERMINOLOGY_ENFORCEMENT, LOW_CONTEXT_TASK, FORMAT_CRITICAL_TASK, SPREADSHEET_TRANSLATION

Canonical required_inputs values:
source_old, source_new, target_baseline, source_document, target_document, batch_documents, glossary

Given:
- subject/message: {message_blob}
- files: {json.dumps(files_payload, ensure_ascii=False)}

Output JSON schema:
{{
  "task_type": "...",
  "task_label": "Brief human-friendly task description, e.g. 'Proofread French → English translation of Teachers Survey'",
  "source_language": "ar|en|fr|es|de|pt|zh|tr|multi|unknown",
  "target_language": "ar|en|fr|es|de|pt|zh|tr|multi|unknown",
  "required_inputs": ["..."],
  "missing_inputs": ["..."],
  "confidence": 0.0,
  "reasoning_summary": "...",
  "estimated_minutes": 1,
  "complexity_score": 1.0
}}

Rules:
- confidence must be between 0 and 1.
- estimated_minutes must be integer.
- If information is insufficient, choose LOW_CONTEXT_TASK.
- missing_inputs must be inferred from files.
- For proofreading/review tasks where user provides source + translation in any language pair, use BILINGUAL_PROOFREADING.
- If any file is .xlsx or .csv, strongly prefer SPREADSHEET_TRANSLATION.
- Infer source_language and target_language from the user message (e.g. "translate French to English" → source_language: "fr", target_language: "en").
- task_label must always be a short human-readable description of the task, never empty.
""".strip()
    call = _agent_call(CODEX_AGENT, prompt)
    if not call.get("ok"):
        return {"ok": False, "error": call.get("error"), "detail": call}
    try:
        parsed = _extract_json_from_text(str(call.get("text", "")))
    except Exception as exc:
        return {"ok": False, "error": "intent_json_parse_failed", "detail": str(exc), "raw_text": call.get("text", "")}

    task_type = str(parsed.get("task_type") or "").strip().upper()
    if task_type not in TASK_TYPES:
        task_type = "LOW_CONTEXT_TASK"
    confidence = float(parsed.get("confidence", 0.0) or 0.0)
    confidence = max(0.0, min(1.0, confidence))
    estimated_minutes = int(parsed.get("estimated_minutes", 12) or 12)
    estimated_minutes = max(1, estimated_minutes)
    complexity_score = float(parsed.get("complexity_score", 30.0) or 30.0)
    complexity_score = max(1.0, min(100.0, complexity_score))
    required = _normalize_required_inputs(list(parsed.get("required_inputs") or []))
    if not required:
        required = REQUIRED_INPUTS_BY_TASK.get(task_type, [])

    slots = _available_slots(
        candidates,
        source_language=str(parsed.get("source_language") or "unknown"),
        target_language=str(parsed.get("target_language") or "unknown"),
    )
    missing = [x for x in required if not slots.get(x, False)]

    return {
        "ok": True,
        "intent": {
            "task_type": task_type,
            "task_label": str(parsed.get("task_label") or ""),
            "source_language": str(parsed.get("source_language") or "unknown"),
            "target_language": str(parsed.get("target_language") or "unknown"),
            "required_inputs": required,
            "missing_inputs": missing,
            "confidence": confidence,
            "reasoning_summary": str(parsed.get("reasoning_summary") or ""),
        },
        "estimated_minutes": estimated_minutes,
        "complexity_score": complexity_score,
        "raw": parsed,
    }


def _build_delta_pack(
    *,
    job_id: str,
    task_type: str,
    candidates: list[dict[str, Any]],
    source_language: str = "unknown",
) -> dict[str, Any]:
    if task_type == "REVISION_UPDATE":
        lang = str(source_language or "unknown").strip().lower()

        v1_path = _pick_file(candidates, language=lang, version="v1") if lang not in {"unknown", "multi"} else None
        v2_path = _pick_file(candidates, language=lang, version="v2") if lang not in {"unknown", "multi"} else None

        if not v1_path:
            v1_path = next((x.get("path") for x in candidates if x.get("version") == "v1"), None)
        if not v2_path:
            v2_path = next((x.get("path") for x in candidates if x.get("version") == "v2"), None)

        if v1_path and v2_path:
            s1 = next((x["structure"] for x in candidates if x["path"] == v1_path), {})
            s2 = next((x["structure"] for x in candidates if x["path"] == v2_path), {})
            return build_delta(
                job_id=job_id,
                v1_rows=flatten_blocks(s1),
                v2_rows=flatten_blocks(s2),
            )

    changes = []
    for item in candidates[:20]:
        struct = item.get("structure") or {}
        name = item.get("name", "")
        sample_lines = []
        for block in struct.get("blocks", []):
            if block.get("kind") != "paragraph":
                continue
            text = _normalize_text(block.get("text", ""))
            if text:
                sample_lines.append(text)
            if len(sample_lines) >= 2:
                break
        preview = " | ".join(sample_lines)[:200]
        changes.append(
            {
                "section": name or "General",
                "changes": [f"Input considered for {item.get('role', 'task')}: {preview or '(no text preview)'}"],
            }
        )

    return {
        "job_id": job_id,
        "added": [],
        "removed": [],
        "modified": [],
        "summary_by_section": changes,
        "stats": {"added_count": 0, "removed_count": 0, "modified_count": 0},
    }


def _build_execution_context(
    meta: dict[str, Any],
    candidates: list[dict[str, Any]],
    intent: dict[str, Any],
    kb_hits: list[dict[str, Any]],
    revision_pack: RevisionPack | None = None,
) -> dict[str, Any]:
    task_type = str(intent.get("task_type", "LOW_CONTEXT_TASK"))
    context: dict[str, Any] = {
        "job_id": meta.get("job_id"),
        "subject": meta.get("subject", ""),
        "message_text": meta.get("message_text", ""),
        "task_intent": intent,
        "selected_tool": task_type,
        "candidate_files": _candidate_payload(candidates, include_text=True),
        "knowledge_context": kb_hits[:12],
        "cross_job_memories": list(meta.get("cross_job_memories") or []),
        "rules": {
            "preserve_structure_first": True,
            "keep_unmodified_content": True,
            "target_language_purity_required": True,
            "manual_delivery_only": True,
        },
    }
    # Add revision pack for REVISION_UPDATE tasks
    if revision_pack:
        context["revision_pack"] = revision_pack.to_dict()
        context["revision_context_prompt"] = format_revision_context_for_prompt(revision_pack)
    return context


def _codex_generate(context: dict[str, Any], previous_draft: dict[str, Any] | None, findings: list[str], round_index: int) -> dict[str, Any]:
    task_type = str((context.get("task_intent") or {}).get("task_type", "LOW_CONTEXT_TASK"))
    tool_instructions = TASK_TOOL_INSTRUCTIONS.get(task_type, TASK_TOOL_INSTRUCTIONS["LOW_CONTEXT_TASK"])

    # Build revision context section for REVISION_UPDATE tasks
    revision_context_section = ""
    if task_type == "REVISION_UPDATE" and context.get("revision_context_prompt"):
        revision_context_section = f"""
CRITICAL REVISION CONTEXT:
{context.get("revision_context_prompt")}

PRESERVED_TEXT_MAP (copy these texts EXACTLY for the corresponding unit IDs):
{json.dumps(context.get("revision_pack", {}).get("preserved_text_map", {}), ensure_ascii=False)}
"""

    prompt = f"""
You are Codex translator. Work on this translation job and return strict JSON only.

Round: {round_index}
Previous unresolved findings: {json.dumps(findings, ensure_ascii=False)}
{revision_context_section}
Execution context:
{json.dumps(context, ensure_ascii=False)}

Previous draft (if any):
{json.dumps(previous_draft or {}, ensure_ascii=False)}

Output JSON:
{{
  "final_text": "string",
  "final_reflow_text": "string",
  "docx_translation_map": [{{"id": "p:12", "text": "..."}}],
  "xlsx_translation_map": [{{"file": "file.xlsx", "sheet": "Sheet1", "cell": "B2", "text": "..."}}],
  "review_brief_points": ["..."],
  "change_log_points": ["..."],
  "resolved": ["..."],
  "unresolved": ["..."],
  "codex_pass": true,
  "reasoning_summary": "string"
}}

Task type: {task_type}
Tool instructions: {tool_instructions}

Rules:
- Follow the tool instructions above for this specific task type.
- Produce complete output text for the selected task.
- If execution_context.format_preserve.docx_template is present, you MUST fill docx_translation_map for every unit id provided.
- If execution_context.format_preserve.xlsx_sources is present, you MUST fill xlsx_translation_map for every provided (file, sheet, cell).
- FOR REVISION_UPDATE: You MUST copy texts from PRESERVED_TEXT_MAP exactly for unchanged sections. Do not modify preserved texts.
- Do NOT output Markdown anywhere (no ``` fenced blocks, no **bold**, no headings like #/##, no "- " markdown bullets, no [text](url)).
  Use plain text only. For lists use "• " or "1) " style, not Markdown.
- If context is insufficient, keep "codex_pass": false and explain in unresolved.
- JSON only.
""".strip()
    call = _agent_call(CODEX_AGENT, prompt)
    if not call.get("ok"):
        return {"ok": False, "error": call.get("error"), "detail": call}
    try:
        parsed = _extract_json_from_text(str(call.get("text", "")))
    except Exception as exc:
        return {"ok": False, "error": "codex_json_parse_failed", "detail": str(exc), "raw_text": call.get("text", "")}

    return {
        "ok": True,
        "data": {
            "final_text": str(parsed.get("final_text") or parsed.get("draft_a_text") or parsed.get("draft_b_text") or ""),
            "final_reflow_text": str(
                parsed.get("final_reflow_text")
                or parsed.get("draft_b_text")
                or parsed.get("final_text")
                or parsed.get("draft_a_text")
                or ""
            ),
            "docx_translation_map": parsed.get("docx_translation_map") or parsed.get("docx_translation_blocks") or parsed.get("docx_table_cells") or [],
            "xlsx_translation_map": parsed.get("xlsx_translation_map") or [],
            "review_brief_points": [str(x) for x in (parsed.get("review_brief_points") or [])],
            "change_log_points": [str(x) for x in (parsed.get("change_log_points") or [])],
            "resolved": [str(x) for x in (parsed.get("resolved") or [])],
            "unresolved": [str(x) for x in (parsed.get("unresolved") or [])],
            "codex_pass": bool(parsed.get("codex_pass")),
            "reasoning_summary": str(parsed.get("reasoning_summary") or ""),
        },
        "raw": parsed,
    }


def _gemini_review(context: dict[str, Any], draft: dict[str, Any], round_index: int) -> dict[str, Any]:
    task_type = str((context.get("task_intent") or {}).get("task_type", "LOW_CONTEXT_TASK"))
    prompt = f"""
You are Gemini reviewer. Validate translation quality and return strict JSON only.

Round: {round_index}
Task type: {task_type}
Context:
{json.dumps(context, ensure_ascii=False)}

Draft candidate:
{json.dumps(draft, ensure_ascii=False)}

Output JSON:
{{
  "findings": ["..."],
  "resolved": ["..."],
  "unresolved": ["..."],
  "pass": true,
  "terminology_rate": 0.0,
  "structure_complete_rate": 0.0,
  "target_language_purity": 0.0,
  "numbering_consistency": 0.0,
  "reasoning_summary": "string"
}}

Rules:
- pass=true only if no critical issue remains.
- Scores are between 0 and 1.
- JSON only.
""".strip()
    call = _agent_call(GEMINI_AGENT, prompt)
    if not call.get("ok"):
        return {"ok": False, "error": call.get("error"), "detail": call}
    try:
        parsed = _extract_json_from_text(str(call.get("text", "")))
    except Exception as exc:
        return {"ok": False, "error": "gemini_json_parse_failed", "detail": str(exc), "raw_text": call.get("text", "")}

    def _clamp(v: Any, fallback: float) -> float:
        try:
            val = float(v)
        except (TypeError, ValueError):
            return fallback
        return max(0.0, min(1.0, val))

    return {
        "ok": True,
        "data": {
            "findings": [str(x) for x in (parsed.get("findings") or [])],
            "resolved": [str(x) for x in (parsed.get("resolved") or [])],
            "unresolved": [str(x) for x in (parsed.get("unresolved") or [])],
            "pass": bool(parsed.get("pass")),
            "terminology_rate": _clamp(parsed.get("terminology_rate"), 0.0),
            "structure_complete_rate": _clamp(parsed.get("structure_complete_rate"), 0.0),
            "target_language_purity": _clamp(parsed.get("target_language_purity"), 0.0),
            "numbering_consistency": _clamp(parsed.get("numbering_consistency"), 0.0),
            "reasoning_summary": str(parsed.get("reasoning_summary") or ""),
        },
        "raw": parsed,
    }


def _glm_direct_api_call(prompt: str) -> dict[str, Any]:
    """Fallback: call Zhipu GLM API directly when OpenClaw agent unavailable."""
    import urllib.request
    if not GLM_API_KEY:
        return {"ok": False, "error": "glm_api_key_not_set"}
    url = f"{GLM_API_BASE_URL}/chat/completions"
    body = json.dumps({
        "model": GLM_MODEL.split("/")[-1] if "/" in GLM_MODEL else GLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GLM_API_KEY}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return {"ok": True, "text": text, "source": "direct_api"}
    except Exception as exc:
        return {"ok": False, "error": f"glm_direct_api_failed: {exc}"}


def _glm_generate(context: dict[str, Any], previous_draft: dict[str, Any] | None, findings: list[str], round_index: int) -> dict[str, Any]:
    task_type = str((context.get("task_intent") or {}).get("task_type", "LOW_CONTEXT_TASK"))
    tool_instructions = TASK_TOOL_INSTRUCTIONS.get(task_type, TASK_TOOL_INSTRUCTIONS["LOW_CONTEXT_TASK"])

    # Build revision context section for REVISION_UPDATE tasks
    revision_context_section = ""
    if task_type == "REVISION_UPDATE" and context.get("revision_context_prompt"):
        revision_context_section = f"""
CRITICAL REVISION CONTEXT:
{context.get("revision_context_prompt")}

PRESERVED_TEXT_MAP (copy these texts EXACTLY for the corresponding unit IDs):
{json.dumps(context.get("revision_pack", {}).get("preserved_text_map", {}), ensure_ascii=False)}
"""

    prompt = f"""
You are a translation generator (GLM). Work on this translation job and return strict JSON only.

Round: {round_index}
Previous unresolved findings: {json.dumps(findings, ensure_ascii=False)}
{revision_context_section}
Execution context:
{json.dumps(context, ensure_ascii=False)}

Previous draft (if any):
{json.dumps(previous_draft or {}, ensure_ascii=False)}

Output JSON:
{{
  "final_text": "string",
  "final_reflow_text": "string",
  "docx_translation_map": [{{"id": "p:12", "text": "..."}}],
  "xlsx_translation_map": [{{"file": "file.xlsx", "sheet": "Sheet1", "cell": "B2", "text": "..."}}],
  "review_brief_points": ["..."],
  "change_log_points": ["..."],
  "resolved": ["..."],
  "unresolved": ["..."],
  "codex_pass": true,
  "reasoning_summary": "string"
}}

Task type: {task_type}
Tool instructions: {tool_instructions}

Rules:
- Follow the tool instructions above for this specific task type.
- Produce complete output text for the selected task.
- If execution_context.format_preserve.docx_template is present, you MUST fill docx_translation_map for every unit id provided.
- If execution_context.format_preserve.xlsx_sources is present, you MUST fill xlsx_translation_map for every provided (file, sheet, cell).
- FOR REVISION_UPDATE: You MUST copy texts from PRESERVED_TEXT_MAP exactly for unchanged sections. Do not modify preserved texts.
- Do NOT output Markdown anywhere (no ``` fenced blocks, no **bold**, no headings like #/##, no "- " markdown bullets, no [text](url)).
  Use plain text only. For lists use "• " or "1) " style, not Markdown.
- If context is insufficient, keep "codex_pass": false and explain in unresolved.
- JSON only.
""".strip()

    call = _agent_call(GLM_GENERATOR_AGENT, prompt)
    if not call.get("ok"):
        call = _glm_direct_api_call(prompt)
    if not call.get("ok"):
        return {"ok": False, "error": call.get("error"), "detail": call}

    try:
        parsed = _extract_json_from_text(str(call.get("text", "")))
    except Exception as exc:
        return {"ok": False, "error": "glm_generate_json_parse_failed", "detail": str(exc), "raw_text": call.get("text", "")}

    return {
        "ok": True,
        "data": {
            "final_text": str(parsed.get("final_text") or parsed.get("draft_a_text") or parsed.get("draft_b_text") or ""),
            "final_reflow_text": str(
                parsed.get("final_reflow_text")
                or parsed.get("draft_b_text")
                or parsed.get("final_text")
                or parsed.get("draft_a_text")
                or ""
            ),
            "docx_translation_map": parsed.get("docx_translation_map") or parsed.get("docx_translation_blocks") or parsed.get("docx_table_cells") or [],
            "xlsx_translation_map": parsed.get("xlsx_translation_map") or [],
            "review_brief_points": [str(x) for x in (parsed.get("review_brief_points") or [])],
            "change_log_points": [str(x) for x in (parsed.get("change_log_points") or [])],
            "resolved": [str(x) for x in (parsed.get("resolved") or [])],
            "unresolved": [str(x) for x in (parsed.get("unresolved") or [])],
            "codex_pass": bool(parsed.get("codex_pass")),
            "reasoning_summary": str(parsed.get("reasoning_summary") or ""),
        },
        "raw": parsed,
    }


def _glm_review(context: dict[str, Any], draft: dict[str, Any], round_index: int) -> dict[str, Any]:
    """Independent third-party review by GLM-5. Advisory only."""
    task_type = str((context.get("task_intent") or {}).get("task_type", "LOW_CONTEXT_TASK"))
    prompt = f"""
You are an independent third-party translation reviewer (GLM-5). Evaluate this translation for:
1. Terminology accuracy — are domain-specific terms translated correctly?
2. Semantic completeness — is any meaning lost or added?
3. Target language naturalness — does the translation read naturally?

Round: {round_index}
Task type: {task_type}

Draft:
{json.dumps(draft, ensure_ascii=False)}

Return strict JSON:
{{
  "findings": ["..."],
  "pass": true,
  "terminology_score": 0.0,
  "completeness_score": 0.0,
  "naturalness_score": 0.0,
  "reasoning_summary": "string"
}}
""".strip()

    call = _agent_call(GLM_AGENT, prompt)
    if not call.get("ok"):
        call = _glm_direct_api_call(prompt)

    if not call.get("ok"):
        return {"ok": False, "error": call.get("error", "glm_review_failed")}

    try:
        parsed = _extract_json_from_text(str(call.get("text", "")))
    except Exception as exc:
        return {"ok": False, "error": "glm_json_parse_failed", "detail": str(exc)}

    def _clamp(v: Any, fallback: float) -> float:
        try:
            val = float(v)
        except (TypeError, ValueError):
            return fallback
        return max(0.0, min(1.0, val))

    return {
        "ok": True,
        "data": {
            "findings": [str(x) for x in (parsed.get("findings") or [])],
            "pass": bool(parsed.get("pass")),
            "terminology_score": _clamp(parsed.get("terminology_score"), 0.0),
            "completeness_score": _clamp(parsed.get("completeness_score"), 0.0),
            "naturalness_score": _clamp(parsed.get("naturalness_score"), 0.0),
            "reasoning_summary": str(parsed.get("reasoning_summary") or ""),
        },
    }


def _write_json(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.resolve())


def _round_record(
    *,
    round_idx: int,
    codex_path: str,
    gemini_path: str,
    codex_data: dict[str, Any],
    gemini_data: dict[str, Any],
) -> dict[str, Any]:
    codex_pass = bool(codex_data.get("codex_pass"))
    gemini_pass = bool(gemini_data.get("pass"))
    return {
        "round": round_idx,
        "codex_output_ref": codex_path,
        "gemini_findings_ref": gemini_path,
        "resolved": list(codex_data.get("resolved") or []),
        "unresolved": list(gemini_data.get("unresolved") or codex_data.get("unresolved") or []),
        "codex_pass": codex_pass,
        "gemini_pass": gemini_pass,
        "pass": codex_pass and gemini_pass,
        "metrics": {
            "terminology_rate": float(gemini_data.get("terminology_rate", 0.0)),
            "structure_complete_rate": float(gemini_data.get("structure_complete_rate", 0.0)),
            "target_language_purity": float(gemini_data.get("target_language_purity", 0.0)),
            "numbering_consistency": float(gemini_data.get("numbering_consistency", 0.0)),
            "hard_fail_items": [],
        },
    }


def _model_scores(job_id: str, last_round: dict[str, Any], gemini_enabled: bool) -> dict[str, Any]:
    m = last_round.get("metrics", {})
    codex_total = (
        0.45 * float(m.get("terminology_rate", 0.0))
        + 0.25 * float(m.get("structure_complete_rate", 0.0))
        + 0.15 * float(m.get("target_language_purity", 0.0))
        + 0.1 * float(m.get("numbering_consistency", 0.0))
        + 0.05 * 0.95
    )
    gemini_total = max(0.0, codex_total - 0.01) if gemini_enabled else 0.0
    scores = {
        "codex_primary": {"total": round(codex_total, 4)},
        "gemini_reviewer": {"total": round(gemini_total, 4)},
    }
    glm_scores = last_round.get("glm_scores", {})
    if glm_scores:
        glm_total = (
            0.4 * float(glm_scores.get("terminology", 0.0))
            + 0.3 * float(glm_scores.get("completeness", 0.0))
            + 0.3 * float(glm_scores.get("naturalness", 0.0))
        )
        scores["glm_reviewer"] = {"total": round(glm_total, 4)}
    return {
        "job_id": job_id,
        "winner": "codex_primary",
        "judge_margin": round(max(0.0, codex_total - gemini_total), 4),
        "term_hit": round(float(m.get("terminology_rate", 0.0)), 4),
        "scores": scores,
    }


def _weighted_review_score(review: dict[str, Any]) -> float:
    """Compute a comparable score from Gemini review metrics (0..1)."""
    return (
        0.45 * float(review.get("terminology_rate", 0.0))
        + 0.25 * float(review.get("structure_complete_rate", 0.0))
        + 0.15 * float(review.get("target_language_purity", 0.0))
        + 0.1 * float(review.get("numbering_consistency", 0.0))
    )


def _markdown_findings_from_sanity(sanity: dict[str, Any]) -> list[str]:
    if not sanity or not sanity.get("has_markdown"):
        return []
    by_field = sanity.get("by_field") if isinstance(sanity.get("by_field"), dict) else {}
    findings: list[str] = []
    for field in ["final_text", "final_reflow_text", "docx_translation_map", "xlsx_translation_map"]:
        field_data = by_field.get(field)
        if not isinstance(field_data, dict) or not field_data.get("has_markdown"):
            continue
        patterns = ",".join([str(p) for p in (field_data.get("patterns") or [])]) or "unknown"
        example = ""
        examples = field_data.get("examples") or []
        if isinstance(examples, list) and examples:
            ex0 = examples[0]
            if isinstance(ex0, dict):
                example = str(ex0.get("example") or "")
        example = example[:220].strip()
        if example:
            findings.append(f"markdown_detected:{field}:patterns={patterns}:example={example}")
        else:
            findings.append(f"markdown_detected:{field}:patterns={patterns}")

    if not findings:
        patterns = ",".join([str(p) for p in (sanity.get("patterns") or [])]) or "unknown"
        findings.append(f"markdown_detected:patterns={patterns}")
    return findings[:8]


def _vision_findings_from_xlsx_qa(qa_result: dict[str, Any], *, file_name: str) -> tuple[list[str], list[str]]:
    findings: list[str] = []
    warnings: list[str] = []
    status = str(qa_result.get("status") or "")
    fidelity = float(qa_result.get("format_fidelity_min", qa_result.get("format_fidelity_score", 0.0)) or 0.0)
    threshold = float(qa_result.get("threshold", 0.85) or 0.85)
    if status != "passed":
        findings.append(f"xlsx_format_fidelity_failed:{file_name}:min={round(fidelity,4)}<thr={round(threshold,4)}")
        if qa_result.get("sheet_count_mismatch"):
            findings.append(f"xlsx_sheet_count_mismatch:{file_name}")
        for d in (qa_result.get("discrepancies") or [])[:4]:
            if not isinstance(d, dict):
                continue
            loc = str(d.get("location") or d.get("sheet_index") or "unknown")
            issue = str(d.get("issue") or "discrepancy")[:140]
            sev = str(d.get("severity") or "unknown")
            findings.append(f"xlsx_discrepancy:{file_name}:{sev}:{loc}:{issue}")
    else:
        if qa_result.get("aesthetics_warning"):
            warnings.append(f"xlsx_aesthetics_warning:{file_name}:min={round(float(qa_result.get('aesthetics_min',0.0) or 0.0),4)}")
    return findings, warnings


def _vision_findings_from_docx_qa(qa_result: dict[str, Any], *, file_name: str) -> tuple[list[str], list[str]]:
    findings: list[str] = []
    warnings: list[str] = []
    status = str(qa_result.get("status") or "")
    fidelity = float(qa_result.get("format_fidelity_min", qa_result.get("format_fidelity_score", 0.0)) or 0.0)
    threshold = float(qa_result.get("fidelity_threshold", 0.85) or 0.85)
    if status != "passed":
        findings.append(f"docx_format_fidelity_failed:{file_name}:min={round(fidelity,4)}<thr={round(threshold,4)}")
        for d in (qa_result.get("discrepancies") or [])[:4]:
            if not isinstance(d, dict):
                continue
            loc = str(d.get("location") or d.get("page_index") or "unknown")
            issue = str(d.get("issue") or "discrepancy")[:140]
            sev = str(d.get("severity") or "unknown")
            findings.append(f"docx_discrepancy:{file_name}:{sev}:{loc}:{issue}")
    else:
        if qa_result.get("aesthetics_warning"):
            warnings.append(f"docx_aesthetics_warning:{file_name}:min={round(float(qa_result.get('aesthetics_min',0.0) or 0.0),4)}")
    return findings, warnings


def _run_vision_trials(
    *,
    review_dir: str,
    context: dict[str, Any],
    draft: dict[str, Any],
    round_idx: int,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Generate temporary preserve outputs and run vision QA. Returns (findings, warnings, results)."""
    findings: list[str] = []
    warnings: list[str] = []
    results: dict[str, Any] = {"round": round_idx, "xlsx": {}, "docx": {}}

    preserve = context.get("format_preserve") if isinstance(context.get("format_preserve"), dict) else {}
    if not preserve:
        return findings, warnings, results

    trial_root = Path(review_dir) / ".system" / "vision_trial" / f"round_{round_idx}"
    trial_root.mkdir(parents=True, exist_ok=True)
    results["trial_root"] = str(trial_root.resolve())

    # DOCX trial
    docx_template = preserve.get("docx_template") if isinstance(preserve.get("docx_template"), dict) else None
    if DOCX_QA_ENABLED and docx_template and docx_template.get("path") and draft.get("docx_translation_map"):
        try:
            from scripts.docx_preserver import apply_translation_map as apply_docx_translation_map
            from scripts.docx_qa_vision import run_docx_qa

            original_docx = Path(str(docx_template["path"])).expanduser().resolve()
            trial_docx = trial_root / f"{original_docx.stem}_trial.docx"
            apply_docx_translation_map(
                template_docx=original_docx,
                output_docx=trial_docx,
                translation_map_entries=draft.get("docx_translation_map"),
            )
            max_pages = int(os.getenv("OPENCLAW_DOCX_QA_PAGES_MAX", "6"))
            fidelity_threshold = float(
                os.getenv(
                    "OPENCLAW_DOCX_QA_THRESHOLD",
                    os.getenv("OPENCLAW_FORMAT_QA_THRESHOLD", "0.85"),
                )
            )
            aesthetics_warn = float(os.getenv("OPENCLAW_VISION_AESTHETICS_WARN_THRESHOLD", "0.7"))
            qa = run_docx_qa(
                original_docx=original_docx,
                translated_docx=trial_docx,
                review_dir=trial_root / "docx_qa",
                max_pages=max_pages,
                fidelity_threshold=fidelity_threshold,
                aesthetics_warn_threshold=aesthetics_warn,
            )
            results["docx"][trial_docx.name] = qa
            f, w = _vision_findings_from_docx_qa(qa, file_name=trial_docx.name)
            findings.extend(f)
            warnings.extend(w)
        except Exception as exc:
            results["docx_error"] = str(exc)

    # XLSX trials
    xlsx_sources = preserve.get("xlsx_sources") if isinstance(preserve.get("xlsx_sources"), list) else []
    if FORMAT_QA_ENABLED and xlsx_sources and draft.get("xlsx_translation_map"):
        try:
            from scripts.xlsx_preserver import apply_translation_map as apply_xlsx_translation_map
            from scripts.format_qa_vision import run_format_qa_loop

            max_retries = int(os.getenv("OPENCLAW_VISION_QA_TRIAL_MAX_RETRIES", "0"))
            for src in xlsx_sources:
                if not isinstance(src, dict) or not src.get("path"):
                    continue
                original_xlsx = Path(str(src["path"])).expanduser().resolve()
                trial_xlsx = trial_root / f"{original_xlsx.stem}_trial.xlsx"
                apply_xlsx_translation_map(
                    source_xlsx=original_xlsx,
                    output_xlsx=trial_xlsx,
                    translation_map_entries=draft.get("xlsx_translation_map"),
                    beautify=True,
                )
                qa = run_format_qa_loop(
                    original_xlsx,
                    trial_xlsx,
                    trial_root / "xlsx_qa" / original_xlsx.stem,
                    max_retries=max(0, int(max_retries)),
                )
                results["xlsx"][trial_xlsx.name] = qa
                f, w = _vision_findings_from_xlsx_qa(qa, file_name=trial_xlsx.name)
                findings.extend(f)
                warnings.extend(w)
        except Exception as exc:
            results["xlsx_error"] = str(exc)

    return findings, warnings, results


def _compute_hard_gates(
    *,
    review_dir: str,
    context: dict[str, Any],
    draft: dict[str, Any],
    round_idx: int,
    disallow_markdown: bool,
    vision_in_round: bool,
) -> tuple[list[str], list[str], dict[str, Any]]:
    markdown_sanity: dict[str, Any] | None = None
    markdown_findings: list[str] = []
    if disallow_markdown:
        markdown_sanity = scan_markdown_in_translation_maps(draft)
        if markdown_sanity.get("has_markdown"):
            markdown_findings = _markdown_findings_from_sanity(markdown_sanity)

    preserve_findings, preserve_meta = _validate_format_preserve_coverage(context, draft)

    findings: list[str] = []
    findings.extend(markdown_findings)
    findings.extend(preserve_findings)
    warnings: list[str] = []

    vision_results: dict[str, Any] = {}
    if vision_in_round and (FORMAT_QA_ENABLED or DOCX_QA_ENABLED) and not findings:
        vf, vw, vr = _run_vision_trials(review_dir=review_dir, context=context, draft=draft, round_idx=round_idx)
        findings.extend(vf)
        warnings.extend(vw)
        vision_results = vr
    else:
        reason = "vision_disabled"
        if vision_in_round and (FORMAT_QA_ENABLED or DOCX_QA_ENABLED) and findings:
            reason = "blocked_by_markdown_or_preserve_coverage"
        elif vision_in_round and not (FORMAT_QA_ENABLED or DOCX_QA_ENABLED):
            reason = "qa_disabled"
        vision_results = {"skipped": True, "reason": reason}

    meta = {
        "markdown_sanity": markdown_sanity,
        "preserve_coverage": {"findings": preserve_findings, "meta": preserve_meta},
        "vision_trial": vision_results,
    }
    return findings, warnings, meta


def run(meta: dict[str, Any], *, plan_only: bool = False) -> dict[str, Any]:
    started = time.time()
    thresholds = QualityThresholds(max_rounds=3)

    job_id = str(meta.get("job_id") or f"job_{int(time.time())}")
    root_path = str(meta.get("root_path") or "")
    review_dir = str(meta.get("review_dir") or "")
    if not review_dir and root_path:
        review_dir = str(Path(root_path) / "Translated -EN" / "_VERIFY" / job_id)

    try:
        pipeline_version = _pipeline_version()
        candidates = _enrich_structures(_collect_candidates(meta))
        router_mode = str(meta.get("router_mode") or "strict")
        token_guard_applied = bool(meta.get("token_guard_applied", False))

        # Log questionnaire detection for each candidate
        for candidate in candidates:
            struct = candidate.get("structure")
            if struct:
                checksums = struct.get("checksums", {})
                questionnaire_info = struct.get("questionnaire_info")
                if questionnaire_info and questionnaire_info.get("is_questionnaire"):
                    log.info(
                        "Detected questionnaire in %s: %d questions, %d domains",
                        candidate.get("name", "unknown"),
                        questionnaire_info.get("total_questions", 0),
                        len(questionnaire_info.get("domains", [])),
                    )
                elif checksums.get("question_count", 0) > 0:
                    log.info(
                        "Document %s has %d questions detected",
                        candidate.get("name", "unknown"),
                        checksums.get("question_count", 0),
                    )

        if not candidates:
            response = {
                "ok": False,
                "job_id": job_id,
                "pipeline_version": pipeline_version,
                "status": "incomplete_input",
                "review_dir": review_dir,
                "errors": ["no_input_documents_found"],
                "iteration_count": 0,
                "double_pass": False,
                "estimated_minutes": 0,
                "runtime_timeout_minutes": 0,
                "actual_duration_minutes": round((time.time() - started) / 60.0, 3),
                "status_flags": [],
                "thinking_level": OPENCLAW_TRANSLATION_THINKING,
                "router_mode": router_mode,
                "token_guard_applied": token_guard_applied,
            }
            _write_result(review_dir, response)
            return response

        intent_result = _llm_intent(meta, candidates)
        if not intent_result.get("ok"):
            response = {
                "ok": False,
                "job_id": job_id,
                "pipeline_version": pipeline_version,
                "status": "failed",
                "review_dir": review_dir,
                "errors": [str(intent_result.get("error"))],
                "iteration_count": 0,
                "double_pass": False,
                "estimated_minutes": 0,
                "runtime_timeout_minutes": 0,
                "actual_duration_minutes": round((time.time() - started) / 60.0, 3),
                "status_flags": ["hard_fail"],
                "thinking_level": OPENCLAW_TRANSLATION_THINKING,
                "router_mode": router_mode,
                "token_guard_applied": token_guard_applied,
                "debug": intent_result,
            }
            _write_result(review_dir, response)
            return response

        intent = intent_result["intent"]
        estimated_minutes = int(intent_result.get("estimated_minutes", 12))
        complexity_score = float(intent_result.get("complexity_score", 30.0))
        runtime_timeout_minutes, timeout_flags = compute_runtime_timeout(estimated_minutes, thresholds)
        status_flags = list(meta.get("status_flags_seed") or []) + list(timeout_flags or [])

        plan = {
            "task_type": intent.get("task_type", "LOW_CONTEXT_TASK"),
            "confidence": float(intent.get("confidence", 0.0)),
            "estimated_minutes": estimated_minutes,
            "complexity_score": complexity_score,
            "time_budget_minutes": runtime_timeout_minutes,
            "pipeline_version": pipeline_version,
            "thinking_level": OPENCLAW_TRANSLATION_THINKING,
            "router_mode": router_mode,
            "token_guard_applied": token_guard_applied,
        }

        missing_inputs = list(intent.get("missing_inputs") or [])
        if missing_inputs:
            response = {
                "ok": False,
                "job_id": job_id,
                "pipeline_version": pipeline_version,
                "status": "missing_inputs",
                "review_dir": review_dir,
                "intent": intent,
                "plan": plan,
                "iteration_count": 0,
                "double_pass": False,
                "estimated_minutes": estimated_minutes,
                "runtime_timeout_minutes": runtime_timeout_minutes,
                "actual_duration_minutes": round((time.time() - started) / 60.0, 3),
                "status_flags": status_flags + ["missing_inputs"],
                "thinking_level": OPENCLAW_TRANSLATION_THINKING,
                "router_mode": router_mode,
                "token_guard_applied": token_guard_applied,
                "errors": [f"missing:{x}" for x in missing_inputs],
            }
            _write_result(review_dir, response)
            return response

        if plan_only:
            response = {
                "ok": True,
                "job_id": job_id,
                "pipeline_version": pipeline_version,
                "status": "planned",
                "review_dir": review_dir,
                "intent": intent,
                "plan": plan,
                "iteration_count": 0,
                "double_pass": False,
                "estimated_minutes": estimated_minutes,
                "runtime_timeout_minutes": runtime_timeout_minutes,
                "actual_duration_minutes": round((time.time() - started) / 60.0, 3),
                "status_flags": status_flags,
                "thinking_level": OPENCLAW_TRANSLATION_THINKING,
                "router_mode": router_mode,
                "token_guard_applied": token_guard_applied,
                "errors": [],
            }
            _write_result(review_dir, response)
            return response

        task_type = str(intent.get("task_type", "LOW_CONTEXT_TASK"))
        delta_pack = _build_delta_pack(
            job_id=job_id,
            task_type=task_type,
            candidates=candidates,
            source_language=str(intent.get("source_language") or "unknown"),
        )
        kb_hits = list(meta.get("knowledge_context") or [])

        # Build revision pack for REVISION_UPDATE tasks
        revision_pack: RevisionPack | None = None
        if task_type == "REVISION_UPDATE":
            source_language = str(intent.get("source_language") or "ar")
            target_language = str(intent.get("target_language") or "en")

            # Find the three required structures
            arabic_v1_struct = None
            arabic_v2_struct = None
            english_v1_struct = None

            for c in candidates:
                lang = str(c.get("language") or "").lower()
                ver = str(c.get("version") or "").lower()
                struct = c.get("structure", {})

                if lang == source_language and ver == "v1":
                    arabic_v1_struct = struct
                elif lang == source_language and ver == "v2":
                    arabic_v2_struct = struct
                elif lang == target_language and ver == "v1":
                    english_v1_struct = struct

            # Build revision pack if all three structures are available
            if arabic_v1_struct and arabic_v2_struct and english_v1_struct:
                try:
                    revision_pack = build_revision_pack(
                        arabic_v1_structure=arabic_v1_struct,
                        arabic_v2_structure=arabic_v2_struct,
                        english_v1_structure=english_v1_struct,
                        job_id=job_id,
                    )
                    status_flags.append("revision_pack_built")

                    # Check for structure integrity issues
                    if revision_pack.structure_issues:
                        for issue in revision_pack.structure_issues:
                            severity = issue.get("severity", "warning")
                            issue_type = issue.get("type", "unknown")
                            status_flags.append(f"structure_issue:{issue_type}")

                            if severity == "error":
                                log.error(
                                    "Structure integrity issue [%s]: %s",
                                    issue_type,
                                    issue.get("message", ""),
                                )
                                # For question count mismatch, warn but continue
                                if issue_type == "question_count_mismatch":
                                    status_flags.append("structure_drift_detected")
                                    errors.append(issue.get("message", ""))
                            else:
                                log.warning(
                                    "Structure integrity issue [%s]: %s",
                                    issue_type,
                                    issue.get("message", ""),
                                )

                except Exception as exc:
                    status_flags.append("revision_pack_error")
                    errors.append(f"revision_pack_build_failed: {exc}")
            else:
                status_flags.append("revision_pack_incomplete_inputs")

                # Log which structures are missing for REVISION_UPDATE
                missing = []
                if not arabic_v1_struct:
                    missing.append(f"{source_language} V1")
                if not arabic_v2_struct:
                    missing.append(f"{source_language} V2")
                if not english_v1_struct:
                    missing.append(f"{target_language} V1 (baseline)")
                log.warning(
                    "REVISION_UPDATE missing required structures: %s",
                    ", ".join(missing),
                )

        execution_context = _build_execution_context(meta, candidates, intent, kb_hits, revision_pack=revision_pack)

        # --- Format-preserving payloads (XLSX/DOCX) ---
        format_preserve: dict[str, Any] = {}
        try:
            # XLSX sources
            xlsx_sources = [
                Path(str(c.get("path") or "")).expanduser().resolve()
                for c in candidates
                if str(c.get("path") or "") and Path(str(c.get("path") or "")).suffix.lower() == ".xlsx"
            ]
            if xlsx_sources:
                max_cells = int(os.getenv("OPENCLAW_XLSX_TRANSLATION_MAX_CELLS", "2000"))
                max_chars = int(os.getenv("OPENCLAW_XLSX_MAX_CHARS_PER_CELL", "400"))
                sources_payload: list[dict[str, Any]] = []
                for src in xlsx_sources:
                    units, meta_info = extract_translatable_cells(src, max_cells=max_cells)
                    sources_payload.append(
                        {
                            "file": src.name,
                            "path": str(src),
                            "cell_units": xlsx_units_to_payload(units, max_chars_per_cell=max_chars),
                            "meta": meta_info,
                        }
                    )
                format_preserve["xlsx_sources"] = sources_payload

            # DOCX template units (used for preserve output)
            template_docx = _select_docx_template(candidates, target_language=str(intent.get("target_language") or ""))
            if template_docx and Path(template_docx).suffix.lower() == ".docx":
                max_units = int(os.getenv("OPENCLAW_DOCX_TRANSLATION_MAX_UNITS", "1200"))
                max_chars = int(os.getenv("OPENCLAW_DOCX_MAX_CHARS_PER_UNIT", "800"))
                units, meta_info = extract_docx_units(Path(template_docx), max_units=max_units, max_chars_per_unit=max_chars)
                format_preserve["docx_template"] = {
                    "file": Path(template_docx).name,
                    "path": str(Path(template_docx).expanduser().resolve()),
                    "units": docx_units_to_payload(units),
                    "meta": meta_info,
                }
        except Exception as exc:
            status_flags.append("format_preserve_payload_error")
            format_preserve["error"] = str(exc)

        if format_preserve:
            execution_context["format_preserve"] = format_preserve

        rounds: list[dict[str, Any]] = []
        previous_findings: list[str] = []
        current_draft: dict[str, Any] | None = None
        errors: list[str] = []
        gemini_enabled = bool(meta.get("gemini_available", True))
        system_round_root = Path(review_dir) / ".system" / "rounds"
        system_round_root.mkdir(parents=True, exist_ok=True)
        disallow_markdown = _env_flag("OPENCLAW_DISALLOW_MARKDOWN", "1")
        vision_in_round = _env_flag("OPENCLAW_VISION_QA_IN_ROUND", "1")
        vision_fix_limit = max(0, int(os.getenv("OPENCLAW_VISION_QA_MAX_RETRIES", "2")))
        vision_fix_used = 0
        markdown_sanity_by_round: dict[str, Any] = {}
        preserve_coverage_by_round: dict[str, Any] = {}
        vision_trials_by_round: dict[str, Any] = {}

        def _write_raw_error_artifacts(round_dir: Path, name: str, result: dict[str, Any]) -> None:
            if result.get("ok"):
                return
            payload: dict[str, Any] = {"ok": False, "error": str(result.get("error") or "")}
            detail = result.get("detail")
            if detail is not None:
                payload["detail"] = detail
            _write_json(round_dir / f"{name}_error.json", payload)

            raw_text = str(result.get("raw_text") or "").strip()
            if not raw_text and isinstance(detail, dict):
                for k in ("raw_text", "text", "stdout", "stderr"):
                    val = detail.get(k)
                    if isinstance(val, str) and val.strip():
                        raw_text = val
                        break
            if raw_text:
                cap = int(os.getenv("OPENCLAW_RAW_MODEL_OUTPUT_MAX_CHARS", "200000"))
                safe_text = raw_text[: max(1000, cap)]
                (round_dir / f"raw_{name}.txt").write_text(safe_text, encoding="utf-8", errors="replace")

        for round_idx in range(1, thresholds.max_rounds + 1):
            round_dir = system_round_root / f"round_{round_idx}"
            round_dir.mkdir(parents=True, exist_ok=True)

            codex_gen = _codex_generate(execution_context, current_draft, previous_findings, round_idx)
            glm_gen = _glm_generate(execution_context, current_draft, previous_findings, round_idx)

            codex_data: dict[str, Any] | None = codex_gen.get("data") if codex_gen.get("ok") else None
            glm_data: dict[str, Any] | None = glm_gen.get("data") if glm_gen.get("ok") else None

            _write_raw_error_artifacts(round_dir, "codex_generate", codex_gen)
            _write_raw_error_artifacts(round_dir, "glm_generate", glm_gen)

            generation_errors: dict[str, str] = {}
            if not codex_data:
                generation_errors["codex"] = str(codex_gen.get("error", "codex_generation_failed"))
            if not glm_data:
                generation_errors["glm"] = str(glm_gen.get("error", "glm_generation_failed"))
            if not codex_data and not glm_data:
                errors.append(f"no_generator_candidates:{generation_errors}")
                _write_json(round_dir / "generation_errors.json", {"errors": generation_errors})
                break

            codex_review: dict[str, Any] | None = None
            glm_review: dict[str, Any] | None = None
            gemini_review_errors: list[str] = []

            if gemini_enabled:
                if codex_data:
                    rev = _gemini_review(execution_context, codex_data, round_idx)
                    if rev.get("ok"):
                        codex_review = rev["data"]
                    else:
                        _write_raw_error_artifacts(round_dir, "gemini_review_codex", rev)
                        gemini_review_errors.append(str(rev.get("error", "gemini_review_failed:codex")))
                if glm_data:
                    rev = _gemini_review(execution_context, glm_data, round_idx)
                    if rev.get("ok"):
                        glm_review = rev["data"]
                    else:
                        _write_raw_error_artifacts(round_dir, "gemini_review_glm", rev)
                        gemini_review_errors.append(str(rev.get("error", "gemini_review_failed:glm")))

                if not codex_review and not glm_review:
                    gemini_enabled = False
                    status_flags.append("degraded_single_model")

            def _candidate_info(source: str, draft: dict[str, Any] | None, review: dict[str, Any] | None) -> dict[str, Any]:
                if not draft:
                    return {
                        "source": source,
                        "ok": False,
                        "generator_pass": False,
                        "reviewed": False,
                        "gemini_pass": False,
                        "pass": False,
                        "score": -1.0,
                    }
                generator_pass = bool(draft.get("codex_pass"))
                reviewed = bool(review)
                gemini_pass = bool(review.get("pass")) if review else False
                pass_flag = generator_pass and (gemini_pass if gemini_enabled else True)
                score = _weighted_review_score(review) if review else -1.0
                return {
                    "source": source,
                    "ok": True,
                    "generator_pass": generator_pass,
                    "reviewed": reviewed,
                    "gemini_pass": gemini_pass,
                    "pass": pass_flag,
                    "score": score,
                }

            codex_info = _candidate_info("codex", codex_data, codex_review)
            glm_info = _candidate_info("glm", glm_data, glm_review)

            selected_source = "codex" if codex_data else "glm"
            selected_draft = codex_data or glm_data or {}
            selected_review = codex_review if selected_source == "codex" else glm_review
            selection_reason = "fallback"

            if gemini_enabled:
                scored = [x for x in [codex_info, glm_info] if x.get("ok")]
                passing = [x for x in scored if x.get("pass")]
                pool = passing or scored
                if pool:
                    best = max(
                        pool,
                        key=lambda x: (bool(x.get("pass")), float(x.get("score", -1.0)), 1 if x.get("source") == "codex" else 0),
                    )
                    selected_source = str(best.get("source"))
                    selected_draft = codex_data if selected_source == "codex" else (glm_data or {})
                    selected_review = codex_review if selected_source == "codex" else glm_review
                    selection_reason = "pass_and_score" if passing else "score_only"

            review_findings: list[str] = []
            if selected_review:
                review_findings = list(selected_review.get("findings") or selected_review.get("unresolved") or [])
            if not review_findings:
                review_findings = list(selected_draft.get("unresolved") or [])

            did_fix = False
            if review_findings:
                codex_fix = _codex_generate(execution_context, selected_draft, review_findings, round_idx)
                if codex_fix.get("ok"):
                    selected_draft = codex_fix["data"]
                    did_fix = True

            if gemini_enabled:
                if did_fix:
                    gemini_final = _gemini_review(execution_context, selected_draft, round_idx)
                    if gemini_final.get("ok"):
                        gemini_data = gemini_final["data"]
                    else:
                        _write_raw_error_artifacts(round_dir, "gemini_review_selected", gemini_final)
                        gemini_enabled = False
                        status_flags.append("degraded_single_model")
                        gemini_data = {
                            "findings": review_findings,
                            "resolved": list(selected_draft.get("resolved") or []),
                            "unresolved": list(selected_draft.get("unresolved") or []),
                            "pass": bool(selected_draft.get("codex_pass")),
                            "terminology_rate": 0.9,
                            "structure_complete_rate": 0.9,
                            "target_language_purity": 0.9,
                            "numbering_consistency": 0.9,
                            "reasoning_summary": "Gemini second-pass failed; degraded single model path.",
                        }
                else:
                    gemini_data = selected_review or {
                        "findings": list(selected_draft.get("unresolved") or []),
                        "resolved": list(selected_draft.get("resolved") or []),
                        "unresolved": list(selected_draft.get("unresolved") or []),
                        "pass": bool(selected_draft.get("codex_pass")),
                        "terminology_rate": 0.9,
                        "structure_complete_rate": 0.9,
                        "target_language_purity": 0.9,
                        "numbering_consistency": 0.9,
                        "reasoning_summary": "Gemini review unavailable for selected candidate; degraded metrics.",
                    }
            else:
                gemini_data = {
                    "findings": list(selected_draft.get("unresolved") or []),
                    "resolved": list(selected_draft.get("resolved") or []),
                    "unresolved": list(selected_draft.get("unresolved") or []),
                    "pass": bool(selected_draft.get("codex_pass")),
                    "terminology_rate": 0.9,
                    "structure_complete_rate": 0.9,
                    "target_language_purity": 0.9,
                    "numbering_consistency": 0.9,
                    "reasoning_summary": "Gemini disabled by runtime settings.",
                }

            def _fix_findings_for_retry() -> list[str]:
                items: list[str] = []
                if isinstance(gemini_data, dict):
                    items.extend([str(x) for x in (gemini_data.get("unresolved") or [])])
                    if not items:
                        items.extend([str(x) for x in (gemini_data.get("findings") or [])])
                if not items:
                    items.extend([str(x) for x in (selected_draft.get("unresolved") or [])])
                return [x for x in items if str(x).strip()]

            hard_fix_attempts: list[dict[str, Any]] = []
            hard_findings, hard_warnings, hard_meta = _compute_hard_gates(
                review_dir=review_dir,
                context=execution_context,
                draft=selected_draft,
                round_idx=round_idx,
                disallow_markdown=disallow_markdown,
                vision_in_round=vision_in_round,
            )

            while hard_findings and vision_fix_used < vision_fix_limit:
                vision_fix_used += 1
                retry_findings = sorted(set(_fix_findings_for_retry() + hard_findings))
                retry_findings = [x for x in retry_findings if str(x).strip()][:40]
                hard_fix_attempts.append({"attempt": vision_fix_used, "findings": retry_findings})

                codex_fix = _codex_generate(execution_context, selected_draft, retry_findings, round_idx)
                if not codex_fix.get("ok"):
                    errors.append(f"hard_gate_fix_failed:{codex_fix.get('error')}")
                    break
                selected_draft = codex_fix["data"]
                did_fix = True

                if gemini_enabled:
                    gemini_final = _gemini_review(execution_context, selected_draft, round_idx)
                    if gemini_final.get("ok"):
                        gemini_data = gemini_final["data"]
                    else:
                        _write_raw_error_artifacts(round_dir, "gemini_review_selected", gemini_final)
                        gemini_enabled = False
                        status_flags.append("degraded_single_model")
                        gemini_data = {
                            "findings": retry_findings,
                            "resolved": list(selected_draft.get("resolved") or []),
                            "unresolved": list(selected_draft.get("unresolved") or retry_findings),
                            "pass": bool(selected_draft.get("codex_pass")),
                            "terminology_rate": 0.9,
                            "structure_complete_rate": 0.9,
                            "target_language_purity": 0.9,
                            "numbering_consistency": 0.9,
                            "reasoning_summary": "Gemini unavailable during hard-gate retry; degraded single model path.",
                        }

                hard_findings, hard_warnings, hard_meta = _compute_hard_gates(
                    review_dir=review_dir,
                    context=execution_context,
                    draft=selected_draft,
                    round_idx=round_idx,
                    disallow_markdown=disallow_markdown,
                    vision_in_round=vision_in_round,
                )

            candidate_refs: dict[str, str] = {}
            review_refs: dict[str, str] = {}
            if codex_data:
                candidate_refs["codex"] = _write_json(round_dir / "candidate_codex.json", codex_data)
            if glm_data:
                candidate_refs["glm"] = _write_json(round_dir / "candidate_glm.json", glm_data)
            if codex_review:
                review_refs["codex"] = _write_json(round_dir / "gemini_review_codex.json", codex_review)
            if glm_review:
                review_refs["glm"] = _write_json(round_dir / "gemini_review_glm.json", glm_review)

            selection_meta = {
                "selected": selected_source,
                "reason": selection_reason,
                "did_fix": did_fix,
                "generation_errors": generation_errors,
                "gemini_review_errors": gemini_review_errors,
                "candidates": {
                    "codex": codex_info,
                    "glm": glm_info,
                },
            }
            selection_meta["hard_gate"] = {
                "findings": hard_findings,
                "warnings": hard_warnings,
                "attempts": hard_fix_attempts,
            }
            _write_json(round_dir / "selection.json", selection_meta)

            selected_ref = _write_json(round_dir / "selected_output.json", selected_draft)
            gemini_ref = _write_json(round_dir / "gemini_review_selected.json", gemini_data)
            rec = _round_record(
                round_idx=round_idx,
                codex_path=selected_ref,
                gemini_path=gemini_ref,
                codex_data=selected_draft,
                gemini_data=gemini_data,
            )
            rec["selected_candidate"] = selected_source
            rec["candidate_refs"] = candidate_refs
            rec["candidate_review_refs"] = review_refs
            rec["selection"] = selection_meta
            rec["hard_findings"] = hard_findings
            rec["warnings"] = hard_warnings
            rec["hard_fix_attempts"] = hard_fix_attempts
            rec["metrics"]["hard_fail_items"] = list(hard_findings)
            if hard_findings:
                rec["unresolved"] = sorted(set([str(x) for x in (rec.get("unresolved") or [])] + [str(x) for x in hard_findings]))
                rec["pass"] = False
            rounds.append(rec)

            markdown_sanity_by_round[str(round_idx)] = hard_meta.get("markdown_sanity")
            preserve_coverage_by_round[str(round_idx)] = hard_meta.get("preserve_coverage")
            vision_trials_by_round[str(round_idx)] = hard_meta.get("vision_trial")

            current_draft = selected_draft
            previous_findings = list(rec.get("unresolved") or [])
            if rec.get("pass"):
                break

        # --- GLM-5 advisory review (after rounds loop) ---
        if GLM_ENABLED and rounds and current_draft:
            glm_result = _glm_review(execution_context, current_draft, len(rounds))
            if glm_result.get("ok"):
                glm_data = glm_result["data"]
                rounds[-1]["glm_findings"] = glm_data.get("findings", [])
                rounds[-1]["glm_pass"] = glm_data.get("pass", False)
                rounds[-1]["glm_scores"] = {
                    "terminology": glm_data.get("terminology_score", 0.0),
                    "completeness": glm_data.get("completeness_score", 0.0),
                    "naturalness": glm_data.get("naturalness_score", 0.0),
                }
            else:
                status_flags.append("glm_review_failed")

        if not rounds:
            status = "failed"
            double_pass = False
            iteration_count = 0
            status_flags.append("hard_fail")
        else:
            last_round = rounds[-1]
            double_pass = bool(last_round.get("pass"))
            iteration_count = len(rounds)
            if double_pass:
                status = "review_ready"
            else:
                status = "needs_attention"
                status_flags.append("non_converged")

        quality_report = {
            "rounds": rounds,
            "convergence_reached": bool(rounds and rounds[-1].get("pass")),
            "stop_reason": "double_pass" if rounds and rounds[-1].get("pass") else ("max_rounds" if rounds else "hard_fail"),
            "pipeline_version": pipeline_version,
            "markdown_policy": {"disallow_markdown": disallow_markdown},
            "vision_policy": {
                "vision_in_round": vision_in_round,
                "hard_gate_max_retries": vision_fix_limit,
                "format_qa_enabled": FORMAT_QA_ENABLED,
                "docx_qa_enabled": DOCX_QA_ENABLED,
            },
            "thinking_level": OPENCLAW_TRANSLATION_THINKING,
            "router_mode": router_mode,
            "token_guard_applied": token_guard_applied,
            "knowledge_backend": str(meta.get("knowledge_backend") or "local"),
            "markdown_sanity_by_round": markdown_sanity_by_round,
            "preserve_coverage_by_round": preserve_coverage_by_round,
            "vision_trials_by_round": vision_trials_by_round,
        }

        last_round = rounds[-1] if rounds else {"metrics": {}}
        model_scores = _model_scores(job_id, last_round, gemini_enabled=gemini_enabled)
        quality = evaluate_quality(model_scores=model_scores, delta_pack=delta_pack, thresholds=thresholds)

        if not current_draft:
            current_draft = {
                "final_text": "",
                "final_reflow_text": "",
                "review_brief_points": [],
                "change_log_points": [],
            }

        target_lang = str(intent.get("target_language") or "").strip().lower()
        _template_candidate = None
        if target_lang and target_lang not in {"unknown", "multi"}:
            _template_candidate = _pick_file(candidates, language=target_lang, version="v1") or _pick_file(candidates, language=target_lang)
        if not _template_candidate:
            _template_candidate = next(
                (
                    str(item.get("path"))
                    for item in candidates
                    if str(item.get("path") or "").lower().endswith(".docx")
                ),
                None,
            )
        # Only use template if it's a .docx — xlsx/csv can't be opened by python-docx
        if _template_candidate and Path(_template_candidate).suffix.lower() != ".docx":
            _template_candidate = None

        xlsx_sources = [
            Path(str(c.get("path") or ""))
            for c in candidates
            if str(c.get("path") or "") and Path(str(c.get("path") or "")).suffix.lower() == ".xlsx"
        ]

        artifacts = write_artifacts(
            review_dir=review_dir,
            draft_a_template_path=_template_candidate,
            delta_pack=delta_pack,
            model_scores=model_scores,
            quality=quality,
            quality_report=quality_report,
            job_id=job_id,
            task_type=task_type,
            confidence=float(intent.get("confidence", 0.0)),
            estimated_minutes=estimated_minutes,
            runtime_timeout_minutes=runtime_timeout_minutes,
            iteration_count=iteration_count,
            double_pass=double_pass,
            status_flags=status_flags,
            candidate_files=candidates,
            review_questions=[str(x) for x in current_draft.get("review_brief_points", [])],
            draft_payload=current_draft,
            generate_final_xlsx=(task_type == "SPREADSHEET_TRANSLATION" or any(Path(c.get("path", "")).suffix.lower() in {".xlsx", ".csv"} for c in candidates)),
            plan_payload={
                "intent": intent,
                "plan": plan,
                "meta": {
                    "sender": meta.get("sender", ""),
                    "message_id": meta.get("message_id", ""),
                    "raw_message_ref": meta.get("raw_message_ref", ""),
                    "pipeline_version": pipeline_version,
                    "markdown_policy": {
                        "disallow_markdown": disallow_markdown,
                    },
                    "vision_policy": {
                        "vision_in_round": vision_in_round,
                        "hard_gate_max_retries": vision_fix_limit,
                        "format_qa_enabled": FORMAT_QA_ENABLED,
                        "docx_qa_enabled": DOCX_QA_ENABLED,
                    },
                    "thinking_level": OPENCLAW_TRANSLATION_THINKING,
                    "router_mode": router_mode,
                    "token_guard_applied": token_guard_applied,
                    "knowledge_backend": str(meta.get("knowledge_backend") or "local"),
                },
            },
        )

        # --- Optional format QA (Gemini Vision) for spreadsheet outputs ---
        if FORMAT_QA_ENABLED:
            format_qa_results: dict[str, Any] = {}
            xlsx_jobs: list[tuple[Path, Path]] = []
            for entry in (artifacts.get("xlsx_files") or []):
                if not isinstance(entry, dict):
                    continue
                src = str(entry.get("source_path") or "").strip()
                out = str(entry.get("path") or "").strip()
                if not src or not out:
                    continue
                src_path = Path(src).expanduser()
                out_path = Path(out).expanduser()
                if src_path.suffix.lower() != ".xlsx" or out_path.suffix.lower() != ".xlsx":
                    continue
                xlsx_jobs.append((src_path, out_path))

            # Back-compat: single Final.xlsx without xlsx_files entries.
            final_xlsx_path = str(artifacts.get("final_xlsx") or "").strip()
            if not xlsx_jobs and xlsx_sources and final_xlsx_path:
                xlsx_jobs.append((xlsx_sources[0].expanduser(), Path(final_xlsx_path).expanduser()))

            if xlsx_jobs:
                try:
                    from scripts.format_qa_vision import run_format_qa_loop

                    max_retries = int(os.getenv("OPENCLAW_FORMAT_QA_MAX_RETRIES", "2"))
                    qa_root = Path(review_dir) / ".system" / "format_qa"
                    qa_root.mkdir(parents=True, exist_ok=True)

                    any_failed = False
                    any_aesthetic_warning = False
                    for original_xlsx, translated_xlsx in xlsx_jobs:
                        if not original_xlsx.exists() or not translated_xlsx.exists():
                            status_flags.append("format_qa_skipped_missing_files")
                            continue
                        qa_result = run_format_qa_loop(
                            original_xlsx.resolve(),
                            translated_xlsx.resolve(),
                            qa_root / original_xlsx.stem,
                            max_retries=max_retries,
                        )
                        format_qa_results[translated_xlsx.name] = qa_result
                        if qa_result.get("status") != "passed":
                            any_failed = True
                        if qa_result.get("aesthetics_warning"):
                            any_aesthetic_warning = True

                    if format_qa_results:
                        quality_report["format_qa"] = format_qa_results
                        quality = evaluate_quality(
                            model_scores=model_scores,
                            delta_pack=delta_pack,
                            thresholds=thresholds,
                            format_qa_results=format_qa_results,
                        )
                    if any_aesthetic_warning:
                        status_flags.append("format_qa_aesthetics_warning")
                    if any_failed:
                        status_flags.append("format_qa_failed")
                        if status == "review_ready":
                            status = "needs_attention"
                except Exception as exc:
                    status_flags.append("format_qa_error")
                    quality_report["format_qa_error"] = str(exc)
            elif xlsx_sources and not final_xlsx_path:
                status_flags.append("format_qa_skipped_no_final_xlsx")

        # --- Optional DOCX vision QA (Gemini Vision) for layout fidelity + aesthetics ---
        if DOCX_QA_ENABLED:
            try:
                from scripts.docx_qa_vision import run_docx_qa

                original_docx_value = str(_template_candidate or "").strip()
                translated_docx_value = str(artifacts.get("final_docx") or "").strip()
                if not original_docx_value or not translated_docx_value:
                    status_flags.append("docx_qa_skipped_no_template")
                else:
                    original_docx_path = Path(original_docx_value).expanduser()
                    translated_docx_path = Path(translated_docx_value).expanduser()

                    if original_docx_path.suffix.lower() != ".docx" or translated_docx_path.suffix.lower() != ".docx":
                        status_flags.append("docx_qa_skipped_not_docx")
                    elif original_docx_path.exists() and translated_docx_path.exists():
                        max_pages = int(os.getenv("OPENCLAW_DOCX_QA_PAGES_MAX", "6"))
                        fidelity_threshold = float(
                            os.getenv(
                                "OPENCLAW_DOCX_QA_THRESHOLD",
                                os.getenv("OPENCLAW_FORMAT_QA_THRESHOLD", "0.85"),
                            )
                        )
                        aesthetics_warn = float(os.getenv("OPENCLAW_VISION_AESTHETICS_WARN_THRESHOLD", "0.7"))
                        qa_root = Path(review_dir) / ".system" / "docx_qa"
                        qa_root.mkdir(parents=True, exist_ok=True)
                        docx_qa = run_docx_qa(
                            original_docx=original_docx_path.resolve(),
                            translated_docx=translated_docx_path.resolve(),
                            review_dir=qa_root / original_docx_path.stem,
                            max_pages=max_pages,
                            fidelity_threshold=fidelity_threshold,
                            aesthetics_warn_threshold=aesthetics_warn,
                        )
                        quality_report["docx_vision_qa"] = {translated_docx_path.name: docx_qa}
                        if docx_qa.get("aesthetics_warning"):
                            status_flags.append("docx_layout_ugly")
                        if docx_qa.get("status") != "passed":
                            status_flags.append("docx_qa_failed")
                            if status == "review_ready":
                                status = "needs_attention"
                    else:
                        status_flags.append("docx_qa_skipped_missing_files")
            except Exception as exc:
                status_flags.append("docx_qa_error")
                quality_report["docx_qa_error"] = str(exc)

        # --- Optional Detail Validation (structure-based format checking) ---
        detail_validation_enabled = os.getenv("OPENCLAW_DETAIL_VALIDATION", "1").strip() not in {"0", "false", "no"}
        if detail_validation_enabled:
            try:
                from scripts.detail_validator import validate_file_pair, ValidationReportGenerator

                detail_results: dict[str, Any] = {}
                original_files: list[Path] = []
                translated_files: list[Path] = []

                # Collect DOCX pairs
                if _template_candidate and artifacts.get("final_docx"):
                    orig_docx = Path(str(_template_candidate)).expanduser()
                    trans_docx = Path(str(artifacts["final_docx"])).expanduser()
                    if orig_docx.exists() and trans_docx.exists():
                        original_files.append(orig_docx)
                        translated_files.append(trans_docx)

                # Collect XLSX pairs
                for entry in (artifacts.get("xlsx_files") or []):
                    src = str(entry.get("source_path") or "").strip()
                    out = str(entry.get("path") or "").strip()
                    if src and out:
                        src_path = Path(src).expanduser()
                        out_path = Path(out).expanduser()
                        if src_path.exists() and out_path.exists():
                            original_files.append(src_path)
                            translated_files.append(out_path)

                # Run validation on each pair
                for orig, trans in zip(original_files, translated_files):
                    try:
                        result = validate_file_pair(orig, trans)
                        detail_results[trans.name] = result.to_dict()
                    except Exception as ve:
                        detail_results[trans.name] = {"error": str(ve)}

                if detail_results:
                    quality_report["detail_validation"] = detail_results

                    # Generate markdown report
                    from scripts.detail_validator import ValidationResult
                    results_list = []
                    for name, data in detail_results.items():
                        if "error" not in data:
                            results_list.append(ValidationResult(
                                file_name=name,
                                file_path=data.get("file_path", ""),
                                format_type=data.get("format_type", "unknown"),
                                valid=data.get("valid", False),
                                total_checks=data.get("total_checks", 0),
                                passed=data.get("passed", 0),
                                warnings=data.get("warnings", 0),
                                failed=data.get("failed", 0),
                                issues=[],
                                format_fidelity_score=data.get("score", 1.0),
                            ))

                    if results_list:
                        generator = ValidationReportGenerator()
                        report_md = generator.generate_markdown(results_list, job_id=job_id)
                        detail_report_path = Path(review_dir) / ".system" / "detail_validation_report.md"
                        detail_report_path.parent.mkdir(parents=True, exist_ok=True)
                        detail_report_path.write_text(report_md, encoding="utf-8")

                        # Add summary to status flags if score is low
                        summary = generator.generate_summary(results_list)
                        if summary.get("score", 1.0) < 0.85:
                            status_flags.append("detail_validation_low_score")
                        if summary.get("failed", 0) > 0:
                            status_flags.append("detail_validation_failed")

            except Exception as exc:
                status_flags.append("detail_validation_error")
                quality_report["detail_validation_error"] = str(exc)

        # Keep the on-disk quality report consistent with the returned payload.
        try:
            quality_report_json = str(artifacts.get("quality_report_json") or "").strip()
            if quality_report_json:
                report_path = Path(quality_report_json).expanduser()
                _write_json(report_path, quality_report)
        except Exception:
            pass

        response = {
            "ok": status == "review_ready",
            "job_id": job_id,
            "pipeline_version": pipeline_version,
            "status": status,
            "review_dir": review_dir,
            "artifacts": artifacts,
            "quality": quality,
            "quality_report": quality_report,
            "intent": intent,
            "plan": plan,
            "iteration_count": iteration_count,
            "double_pass": double_pass,
            "estimated_minutes": estimated_minutes,
            "runtime_timeout_minutes": runtime_timeout_minutes,
            "actual_duration_minutes": round((time.time() - started) / 60.0, 3),
            "status_flags": status_flags,
            "thinking_level": OPENCLAW_TRANSLATION_THINKING,
            "router_mode": router_mode,
            "token_guard_applied": token_guard_applied,
            "errors": errors if errors else ([] if status == "review_ready" else ["double_pass_not_reached"]),
        }
        _write_result(review_dir, response)
        return response
    except Exception as exc:  # pragma: no cover
        response = {
            "ok": False,
            "job_id": job_id,
            "pipeline_version": pipeline_version,
            "status": "failed",
            "review_dir": review_dir,
            "errors": [str(exc)],
            "trace": traceback.format_exc(limit=8),
            "iteration_count": 0,
            "double_pass": False,
            "estimated_minutes": 0,
            "runtime_timeout_minutes": 0,
            "actual_duration_minutes": round((time.time() - started) / 60.0, 3),
            "status_flags": ["hard_fail"],
            "thinking_level": OPENCLAW_TRANSLATION_THINKING,
            "router_mode": str(meta.get("router_mode") or "strict"),
            "token_guard_applied": bool(meta.get("token_guard_applied", False)),
        }
        _write_result(review_dir, response)
        return response


def main() -> int:
    # Configure logging for CLI usage
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--meta-json")
    parser.add_argument("--meta-json-file")
    parser.add_argument("--meta-json-base64")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    meta = _load_meta(args)
    result = run(meta, plan_only=args.plan_only)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

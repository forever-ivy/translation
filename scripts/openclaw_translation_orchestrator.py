#!/usr/bin/env python3
"""OpenClaw V5.2 translation orchestrator (LLM intent + real Codex/Gemini rounds)."""

from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable

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
CODEX_FALLBACK_AGENT = os.getenv("OPENCLAW_CODEX_FALLBACK_AGENT", "qa-gate").strip()
INTENT_AGENT = (
    os.getenv("OPENCLAW_INTENT_AGENT", "").strip()
    or os.getenv("OPENCLAW_TASK_ROUTER_AGENT", "").strip()
    or "task-router"
)

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
        "Translate headers and text cells only. "
        "CRITICAL: If execution_context.format_preserve.xlsx_sources is present, you MUST translate "
        "every cell listed in xlsx_sources[].cell_units OR xlsx_sources[].rows "
        "(even if the sheet notes say a column \"auto-populates\"). "
        "In rows mode, each row is [sheet, cell, source_text]. "
        "Do not omit any provided (file, sheet, cell). "
        "CRITICAL OUTPUT COMPLETENESS: Every cell translation MUST be COMPLETE — translate the "
        "ENTIRE source text from beginning to end. NEVER stop mid-sentence, NEVER truncate, "
        "NEVER abbreviate, NEVER summarize. If the source text is 500 characters, the translation "
        "must cover ALL 500 characters of meaning. A partial translation is WRONG. "
        "If a cell contains a question, translate it as a question — do NOT answer it. "
        "It is better to translate fewer cells completely than to translate all cells partially."
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
GEMINI_FALLBACK_AGENTS = [
    a.strip()
    for a in os.getenv("OPENCLAW_GEMINI_FALLBACK_AGENTS", "translator-core,qa-gate").split(",")
    if a.strip()
]
OPENCLAW_CMD_TIMEOUT = int(os.getenv("OPENCLAW_AGENT_CALL_TIMEOUT_SECONDS", "600"))
OPENCLAW_AGENT_CALL_MAX_ATTEMPTS = max(1, int(os.getenv("OPENCLAW_AGENT_CALL_MAX_ATTEMPTS", "3")))
OPENCLAW_AGENT_CALL_RETRY_BACKOFF_SECONDS = max(0.5, float(os.getenv("OPENCLAW_AGENT_CALL_RETRY_BACKOFF_SECONDS", "3")))
OPENCLAW_AGENT_CALL_RETRY_MAX_BACKOFF_SECONDS = max(
    OPENCLAW_AGENT_CALL_RETRY_BACKOFF_SECONDS,
    float(os.getenv("OPENCLAW_AGENT_CALL_RETRY_MAX_BACKOFF_SECONDS", "20")),
)
OPENCLAW_LOCK_RECOVERY_ENABLED = _env_flag("OPENCLAW_LOCK_RECOVERY_ENABLED", "1")
OPENCLAW_LOCK_STALE_SECONDS = max(30, int(os.getenv("OPENCLAW_LOCK_STALE_SECONDS", "150")))
OPENCLAW_DIRECT_API_MAX_ATTEMPTS = max(1, int(os.getenv("OPENCLAW_DIRECT_API_MAX_ATTEMPTS", "4")))
OPENCLAW_DIRECT_API_BACKOFF_SECONDS = max(0.5, float(os.getenv("OPENCLAW_DIRECT_API_BACKOFF_SECONDS", "2.5")))
OPENCLAW_DIRECT_API_MAX_BACKOFF_SECONDS = max(
    OPENCLAW_DIRECT_API_BACKOFF_SECONDS,
    float(os.getenv("OPENCLAW_DIRECT_API_MAX_BACKOFF_SECONDS", "20")),
)
OPENCLAW_COOLDOWN_FRIENDLY_MODE = _env_flag("OPENCLAW_COOLDOWN_FRIENDLY_MODE", "1")
OPENCLAW_COOLDOWN_RETRY_SECONDS = max(30, int(os.getenv("OPENCLAW_COOLDOWN_RETRY_SECONDS", "300")))
DOC_CONTEXT_CHARS = int(os.getenv("OPENCLAW_DOC_CONTEXT_CHARS", "45000"))
VALID_THINKING_LEVELS = {"off", "minimal", "low", "medium", "high"}
OPENCLAW_TRANSLATION_THINKING = os.getenv("OPENCLAW_TRANSLATION_THINKING", "high").strip().lower()
if OPENCLAW_TRANSLATION_THINKING not in VALID_THINKING_LEVELS:
    OPENCLAW_TRANSLATION_THINKING = "high"
INTENT_CLASSIFIER_MODE = os.getenv("OPENCLAW_INTENT_CLASSIFIER_MODE", "hybrid").strip().lower() or "hybrid"
OPENCLAW_AGENT_MESSAGE_MAX_BYTES = max(300000, int(os.getenv("OPENCLAW_AGENT_MESSAGE_MAX_BYTES", "1800000")))
OPENCLAW_PROVIDER_MESSAGE_LIMIT_BYTES = max(500000, int(os.getenv("OPENCLAW_PROVIDER_MESSAGE_LIMIT_BYTES", "2097152")))
OPENCLAW_AGENT_MESSAGE_OVERHEAD_BYTES = max(0, int(os.getenv("OPENCLAW_AGENT_MESSAGE_OVERHEAD_BYTES", "1300000")))
OPENCLAW_AGENT_PROMPT_MAX_BYTES = max(
    20000,
    min(
        OPENCLAW_AGENT_MESSAGE_MAX_BYTES,
        OPENCLAW_PROVIDER_MESSAGE_LIMIT_BYTES - OPENCLAW_AGENT_MESSAGE_OVERHEAD_BYTES,
    ),
)
OPENCLAW_KB_CONTEXT_MAX_HITS = max(0, int(os.getenv("OPENCLAW_KB_CONTEXT_MAX_HITS", "6")))
OPENCLAW_KB_CONTEXT_MAX_CHARS = max(120, int(os.getenv("OPENCLAW_KB_CONTEXT_MAX_CHARS", "1200")))
OPENCLAW_XLSX_PROMPT_TEXT_MAX_CHARS = max(40, int(os.getenv("OPENCLAW_XLSX_PROMPT_TEXT_MAX_CHARS", "2000")))
OPENCLAW_PREVIOUS_MAP_MAX_ENTRIES = max(100, int(os.getenv("OPENCLAW_PREVIOUS_MAP_MAX_ENTRIES", "1200")))
OPENCLAW_XLSX_TRUNCATED_SAMPLE_LIMIT = max(1, int(os.getenv("OPENCLAW_XLSX_TRUNCATED_SAMPLE_LIMIT", "50")))
SOURCE_TRUNCATED_MARKER = str(os.getenv("OPENCLAW_SOURCE_TRUNCATED_MARKER", "[SOURCE TRUNCATED]")).strip() or "[SOURCE TRUNCATED]"

GLM_AGENT = os.getenv("OPENCLAW_GLM_AGENT", "glm-reviewer")
GLM_GENERATOR_AGENT = os.getenv("OPENCLAW_GLM_GENERATOR_AGENT", GLM_AGENT)
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
GLM_API_BASE_URL = os.getenv("GLM_API_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
GLM_MODEL = os.getenv("OPENCLAW_GLM_MODEL", "zai/glm-5")
KIMI_CODING_MODEL = os.getenv("OPENCLAW_KIMI_CODING_MODEL", os.getenv("ANTHROPIC_MODEL", "kimi-coding/k2p5"))
KIMI_CODING_API_BASE_URL = os.getenv(
    "OPENCLAW_KIMI_CODING_BASE_URL",
    os.getenv("ANTHROPIC_BASE_URL", "https://api.kimi.com/coding/v1"),
)


def _glm_enabled() -> bool:
    return os.getenv("OPENCLAW_GLM_ENABLED", "0").strip() == "1"


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


def _has_terminal_punctuation(text: str, *, allow_source_marker: bool = True) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if allow_source_marker and value.endswith(SOURCE_TRUNCATED_MARKER):
        return True
    return value[-1] in ".!?\"'):]}>"


def _looks_like_truncated_source(text: str) -> bool:
    """Best-effort heuristic for *source* truncation (not translation truncation).

    We keep this conservative: false negatives are preferable to mislabeling intact
    source data as truncated. Marker usage is gated on this.
    """
    value = str(text or "").strip()
    if not value:
        return False
    if value.endswith(("...", "…")):
        return True
    if value[-1] in ",،;؛:":
        return True

    tokens = value.split()
    if not tokens:
        return False
    last = tokens[-1]
    # Common Arabic connector tokens that strongly suggest a cut-off when trailing.
    if last in {"و", "ب", "ل", "في", "على", "من", "عن", "إلى", "الى", "حتى", "ثم", "كما", "أو", "او"}:
        return True
    if len(tokens) >= 2 and tokens[-2] == "كما" and tokens[-1] == "و":
        return True
    return False


def _xlsx_marker_count(draft: dict[str, Any] | None) -> int:
    if not isinstance(draft, dict):
        return 0
    entries = draft.get("xlsx_translation_map")
    if not isinstance(entries, list):
        return 0
    count = 0
    for item in entries:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and SOURCE_TRUNCATED_MARKER and SOURCE_TRUNCATED_MARKER in text:
            count += 1
    return count


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
        expected_text_by_key: dict[tuple[str, str, str], str] = {}
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
                    key = (file_val, sheet, cell)
                    expected_keys.add(key)
                    text_val = unit.get("text")
                    if isinstance(text_val, str) and text_val.strip():
                        expected_text_by_key[key] = text_val

        got_keys = _normalize_xlsx_translation_map_keys(draft.get("xlsx_translation_map"), xlsx_files=xlsx_files)
        missing = sorted(expected_keys - got_keys)
        meta["xlsx_expected"] = len(expected_keys)
        meta["xlsx_got"] = len(got_keys)
        if expected_keys and not got_keys:
            findings.append("xlsx_translation_map_missing")
            missing_list = sorted(expected_keys)
        else:
            missing_list = missing

        if missing_list:
            if not got_keys:
                pass
            else:
                findings.append(f"xlsx_translation_map_incomplete:missing={len(missing_list)}")

            meta["xlsx_missing_sample"] = [
                {"file": f, "sheet": s, "cell": c}
                for (f, s, c) in missing_list[:8]
            ]
            max_units = 20
            if len(missing_list) <= 60:
                max_units = len(missing_list)
            meta["xlsx_missing_units_sample"] = [
                {"file": f, "sheet": s, "cell": c, "text": expected_text_by_key.get((f, s, c), "")}
                for (f, s, c) in missing_list[:max_units]
            ]

    # --- Truncation detection for xlsx_translation_map ---
    x_entries = draft.get("xlsx_translation_map")
    if isinstance(x_entries, list):
        translation_truncated_cells: list[dict[str, Any]] = []
        source_truncated_cells: list[dict[str, Any]] = []
        for entry in x_entries:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            file_name = str(entry.get("file") or "").strip()
            cell = str(entry.get("cell") or "")
            sheet = str(entry.get("sheet") or "")
            key = (file_name, sheet, str(cell).upper())
            src_text = expected_text_by_key.get(key, "")
            source_has_terminal = _has_terminal_punctuation(src_text)
            marker_present = bool(SOURCE_TRUNCATED_MARKER and text.endswith(SOURCE_TRUNCATED_MARKER))
            translated_has_terminal = _has_terminal_punctuation(text, allow_source_marker=False) and not marker_present
            if text.endswith("...") or text.endswith("…"):
                reason = "ellipsis"
            elif marker_present:
                reason = "marker"
            elif len(text) > 100 and not translated_has_terminal:
                reason = "no_terminal_punct"
            else:
                continue
            row = {
                "file": file_name,
                "cell": cell,
                "sheet": sheet,
                "tail": text[-60:],
                "reason": reason,
                "source_has_terminal": source_has_terminal,
                "marker_present": marker_present,
            }
            if marker_present:
                if src_text and (not source_has_terminal) and _looks_like_truncated_source(src_text):
                    source_truncated_cells.append(row)
                else:
                    translation_truncated_cells.append(row)
            elif src_text and not source_has_terminal:
                source_truncated_cells.append(row)
            else:
                translation_truncated_cells.append(row)
        if translation_truncated_cells:
            findings.append(f"xlsx_translation_truncated:cells={len(translation_truncated_cells)}")
            meta["xlsx_translation_truncated_sample"] = translation_truncated_cells[:OPENCLAW_XLSX_TRUNCATED_SAMPLE_LIMIT]
            # Backward compatibility for existing readers.
            meta["xlsx_truncated_sample"] = translation_truncated_cells[:OPENCLAW_XLSX_TRUNCATED_SAMPLE_LIMIT]
        if source_truncated_cells:
            meta["xlsx_source_truncated_count"] = len(source_truncated_cells)
            meta["xlsx_source_truncated_sample"] = source_truncated_cells[:OPENCLAW_XLSX_TRUNCATED_SAMPLE_LIMIT]
            meta["xlsx_source_truncated_marker_missing_count"] = sum(
                1 for item in source_truncated_cells if not bool(item.get("marker_present"))
            )

    return findings, meta


def _validate_glossary_enforcer(context: dict[str, Any], draft: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Return (findings, meta) for strict glossary enforcement.

    Enforcer payload shape:
      execution_context.glossary_enforcer = {
        "enabled": true,
        "terms": [{"ar": "...", "en": "..."}],
        ...
      }

    We only validate when format_preserve payloads are available (DOCX units / XLSX cells),
    so we can enforce per-unit/per-cell, not just "somewhere in the output".
    """
    findings: list[str] = []
    meta: dict[str, Any] = {
        "enabled": False,
        "terms": 0,
        "docx_checked_units": 0,
        "xlsx_checked_cells": 0,
        "violations": 0,
        "skipped_reason": "",
    }

    enforcer = context.get("glossary_enforcer") if isinstance(context.get("glossary_enforcer"), dict) else {}
    if not enforcer or not bool(enforcer.get("enabled", False)):
        meta["skipped_reason"] = "disabled"
        return findings, meta

    terms_raw = enforcer.get("terms")
    if not isinstance(terms_raw, list) or not terms_raw:
        meta["enabled"] = True
        meta["skipped_reason"] = "no_terms"
        return findings, meta

    preserve = (context.get("format_preserve") or {}) if isinstance(context.get("format_preserve"), dict) else {}
    if not preserve:
        meta["enabled"] = True
        meta["skipped_reason"] = "no_format_preserve"
        return findings, meta

    try:
        from scripts.kb_glossary_enforcer import contains_arabic_term, normalize_arabic, normalize_english
    except Exception as exc:  # pragma: no cover - optional in some minimal runtimes
        meta["enabled"] = True
        meta["skipped_reason"] = f"import_failed:{exc}"
        return findings, meta

    # Normalize and de-dupe terms by Arabic key.
    terms: list[tuple[str, str, str, str]] = []  # (ar_norm, en_norm, ar_display, en_display)
    seen_ar: set[str] = set()
    for item in terms_raw:
        ar = ""
        en = ""
        if isinstance(item, dict):
            ar = str(item.get("ar") or item.get("arabic") or "").strip()
            en = str(item.get("en") or item.get("english") or "").strip()
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            ar = str(item[0] or "").strip()
            en = str(item[1] or "").strip()
        if not ar or not en:
            continue
        ar_norm = normalize_arabic(ar)
        en_norm = normalize_english(en)
        if not ar_norm or not en_norm:
            continue
        if ar_norm in seen_ar:
            continue
        seen_ar.add(ar_norm)
        terms.append((ar_norm, en_norm, ar, en))

    meta["enabled"] = True
    meta["terms"] = len(terms)
    if not terms:
        meta["skipped_reason"] = "no_valid_terms"
        return findings, meta

    # Normalize translation maps for lookup.
    docx_map: dict[str, str] = {}
    d_entries = draft.get("docx_translation_map")
    if isinstance(d_entries, dict):
        for k, v in d_entries.items():
            key = str(k or "").strip()
            if key:
                docx_map[key] = str(v or "")
    elif isinstance(d_entries, list):
        for row in d_entries:
            if not isinstance(row, dict):
                continue
            unit_id = str(row.get("id") or row.get("unit_id") or row.get("block_id") or row.get("cell_id") or "").strip()
            if unit_id:
                docx_map[unit_id] = str(row.get("text") or "")

    xlsx_map: dict[tuple[str, str, str], str] = {}
    x_entries = draft.get("xlsx_translation_map")
    xlsx_sources = preserve.get("xlsx_sources") if isinstance(preserve.get("xlsx_sources"), list) else []
    xlsx_files = [
        str(src.get("file") or "").strip()
        for src in xlsx_sources
        if isinstance(src, dict) and str(src.get("file") or "").strip()
    ]
    default_xlsx_file = xlsx_files[0] if len(xlsx_files) == 1 else ""
    if isinstance(x_entries, dict) and default_xlsx_file:
        for k, v in x_entries.items():
            raw = str(k or "")
            if "!" not in raw:
                continue
            sheet, cell = raw.split("!", 1)
            sheet = sheet.strip()
            cell = cell.strip().upper()
            if sheet and cell:
                xlsx_map[(default_xlsx_file, sheet, cell)] = str(v or "")
    elif isinstance(x_entries, list):
        for row in x_entries:
            if not isinstance(row, dict):
                continue
            file_name = str(row.get("file") or "").strip()
            sheet = str(row.get("sheet") or "").strip()
            cell = str(row.get("cell") or "").strip().upper()
            if file_name and sheet and cell:
                xlsx_map[(file_name, sheet, cell)] = str(row.get("text") or "")

    max_violations = max(1, int(os.getenv("OPENCLAW_GLOSSARY_ENFORCER_MAX_VIOLATIONS", "60")))

    prefer_longest = _env_flag("OPENCLAW_GLOSSARY_ENFORCER_PREFER_LONGEST", "1")

    def _active_terms_for_source(src_norm: str) -> list[tuple[str, str, str, str]]:
        matched = [t for t in terms if t[0] and contains_arabic_term(src_norm, t[0])]
        if not prefer_longest or len(matched) <= 1:
            return matched
        out: list[tuple[str, str, str, str]] = []
        for term in matched:
            ar_norm = term[0]
            shadowed = False
            for other in matched:
                if other is term:
                    continue
                other_ar = other[0]
                if len(other_ar) <= len(ar_norm):
                    continue
                if ar_norm in other_ar and other_ar in src_norm:
                    shadowed = True
                    break
            if not shadowed:
                out.append(term)
        return out or matched

    def _check_unit(*, where: str, unit_key: str, src_text: str, out_text: str) -> None:
        nonlocal findings
        if len(findings) >= max_violations:
            return
        src_norm = normalize_arabic(src_text)
        out_norm = normalize_english(out_text)
        if not src_norm:
            return
        for ar_norm, en_norm, ar_disp, en_disp in _active_terms_for_source(src_norm):
            if en_norm and en_norm in out_norm:
                continue
            findings.append(f"glossary_enforcer_missing:{where}:{unit_key}:{ar_disp}=>{en_disp}")
            if len(findings) >= max_violations:
                return

    # DOCX units
    docx = preserve.get("docx_template") if isinstance(preserve.get("docx_template"), dict) else None
    if docx and isinstance(docx.get("units"), list) and docx_map:
        for unit in docx.get("units") or []:
            if not isinstance(unit, dict):
                continue
            unit_id = str(unit.get("id") or "").strip()
            src_text = str(unit.get("text") or "")
            if not unit_id or not src_text:
                continue
            meta["docx_checked_units"] += 1
            out_text = docx_map.get(unit_id, "")
            _check_unit(where="docx", unit_key=unit_id, src_text=src_text, out_text=out_text)

    # XLSX cells
    if xlsx_sources and xlsx_map:
        for src in xlsx_sources:
            if not isinstance(src, dict):
                continue
            file_name = str(src.get("file") or "").strip()
            for unit in (src.get("cell_units") or []):
                if not isinstance(unit, dict):
                    continue
                sheet = str(unit.get("sheet") or "").strip()
                cell = str(unit.get("cell") or "").strip().upper()
                src_text = str(unit.get("text") or "")
                if not file_name or not sheet or not cell or not src_text:
                    continue
                meta["xlsx_checked_cells"] += 1
                out_text = xlsx_map.get((file_name, sheet, cell), "")
                _check_unit(where="xlsx", unit_key=f"{file_name}:{sheet}!{cell}", src_text=src_text, out_text=out_text)

    meta["violations"] = len(findings)
    if not findings:
        meta["skipped_reason"] = ""
    elif len(findings) >= max_violations:
        meta["skipped_reason"] = "violations_truncated"
    return findings, meta


def _strip_redundant_glossary_suffixes(context: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    """Remove repeated glossary labels appended at sentence/cell tail.

    Conservative behavior: only remove a trailing glossary English term when
    the same term already appears earlier in that output text.
    """
    meta: dict[str, Any] = {
        "enabled": False,
        "cleaned_docx_units": 0,
        "cleaned_xlsx_cells": 0,
        "skipped_reason": "",
    }
    if not _env_flag("OPENCLAW_GLOSSARY_SUFFIX_STRIP_ENABLED", "1"):
        meta["skipped_reason"] = "disabled"
        return meta

    enforcer = context.get("glossary_enforcer") if isinstance(context.get("glossary_enforcer"), dict) else {}
    terms_raw = enforcer.get("terms")
    if not bool(enforcer.get("enabled")) or not isinstance(terms_raw, list) or not terms_raw:
        meta["skipped_reason"] = "no_enforcer_terms"
        return meta

    preserve = (context.get("format_preserve") or {}) if isinstance(context.get("format_preserve"), dict) else {}
    if not preserve:
        meta["skipped_reason"] = "no_format_preserve"
        return meta

    try:
        from scripts.kb_glossary_enforcer import contains_arabic_term, normalize_arabic
    except Exception as exc:  # pragma: no cover
        meta["skipped_reason"] = f"import_failed:{exc}"
        return meta

    terms: list[tuple[str, str]] = []
    for item in terms_raw:
        if not isinstance(item, dict):
            continue
        ar = str(item.get("ar") or item.get("arabic") or "").strip()
        en = str(item.get("en") or item.get("english") or "").strip()
        if not ar or not en:
            continue
        ar_norm = normalize_arabic(ar)
        if not ar_norm:
            continue
        terms.append((ar_norm, en))
    if not terms:
        meta["skipped_reason"] = "no_valid_terms"
        return meta

    def _clean_tail_for_source(*, out_text: str, src_text: str) -> str:
        src_norm = normalize_arabic(src_text)
        if not src_norm:
            return out_text
        cleaned = str(out_text or "")
        for ar_norm, en in terms:
            if not contains_arabic_term(src_norm, ar_norm):
                continue
            needle = en.strip()
            if not needle:
                continue
            lower_text = cleaned.lower()
            lower_term = needle.lower()
            pattern = re.compile(
                rf"(?:\s*[\|\-–—,:;，、。؟!\(\)\[\]]+\s*|\s+){re.escape(needle)}\s*$",
                re.IGNORECASE,
            )
            m = pattern.search(cleaned)
            if not m:
                continue
            prefix = cleaned[: m.start()]
            # Primary signal: full glossary term already appears earlier.
            duplicated_full_term = lower_text.count(lower_term) >= 2
            # Secondary signal: trailing term has acronym "(AI)" and acronym appears earlier.
            acronym_match = re.search(r"\(([^()]{1,12})\)\s*$", needle)
            acronym = acronym_match.group(1).strip() if acronym_match else ""
            duplicated_acronym = False
            if acronym:
                duplicated_acronym = bool(re.search(rf"\b{re.escape(acronym)}\b", prefix, re.IGNORECASE))
            if not duplicated_full_term and not duplicated_acronym:
                continue
            cleaned = (prefix + cleaned[m.end() :]).rstrip()
        return cleaned

    meta["enabled"] = True

    docx_source_map: dict[str, str] = {}
    docx_t = preserve.get("docx_template") if isinstance(preserve.get("docx_template"), dict) else None
    if docx_t and isinstance(docx_t.get("units"), list):
        for unit in docx_t.get("units") or []:
            if not isinstance(unit, dict):
                continue
            uid = str(unit.get("id") or "").strip()
            src = str(unit.get("text") or "")
            if uid and src:
                docx_source_map[uid] = src

    xlsx_source_map: dict[tuple[str, str, str], str] = {}
    xlsx_sources = preserve.get("xlsx_sources") if isinstance(preserve.get("xlsx_sources"), list) else []
    default_file = ""
    if len(xlsx_sources) == 1 and isinstance(xlsx_sources[0], dict):
        default_file = str(xlsx_sources[0].get("file") or "").strip()
    for src in xlsx_sources:
        if not isinstance(src, dict):
            continue
        file_name = str(src.get("file") or "").strip()
        for cell_unit in (src.get("cell_units") or []):
            if not isinstance(cell_unit, dict):
                continue
            sheet = str(cell_unit.get("sheet") or "").strip()
            cell = str(cell_unit.get("cell") or "").strip().upper()
            text = str(cell_unit.get("text") or "")
            if file_name and sheet and cell and text:
                xlsx_source_map[(file_name, sheet, cell)] = text
        for row in (src.get("rows") or []):
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            sheet = str(row[0] or "").strip()
            cell = str(row[1] or "").strip().upper()
            text = str(row[2] or "")
            if file_name and sheet and cell and text:
                xlsx_source_map[(file_name, sheet, cell)] = text

    d_entries = draft.get("docx_translation_map")
    if isinstance(d_entries, list):
        for row in d_entries:
            if not isinstance(row, dict):
                continue
            uid = str(row.get("id") or row.get("unit_id") or row.get("block_id") or row.get("cell_id") or "").strip()
            if not uid or uid not in docx_source_map:
                continue
            before = str(row.get("text") or "")
            after = _clean_tail_for_source(out_text=before, src_text=docx_source_map[uid])
            if after != before:
                row["text"] = after
                meta["cleaned_docx_units"] = int(meta["cleaned_docx_units"]) + 1

    x_entries = draft.get("xlsx_translation_map")
    if isinstance(x_entries, list):
        for row in x_entries:
            if not isinstance(row, dict):
                continue
            file_name = str(row.get("file") or "").strip() or default_file
            sheet = str(row.get("sheet") or "").strip()
            cell = str(row.get("cell") or "").strip().upper()
            key = (file_name, sheet, cell)
            if not file_name or not sheet or not cell or key not in xlsx_source_map:
                continue
            before = str(row.get("text") or "")
            after = _clean_tail_for_source(out_text=before, src_text=xlsx_source_map[key])
            if after != before:
                row["text"] = after
                meta["cleaned_xlsx_cells"] = int(meta["cleaned_xlsx_cells"]) + 1

    return meta


def _merge_docx_translation_map(prev_val: Any, new_val: Any) -> Any:
    """Merge docx translation maps by unit id, preferring new entries on conflict."""
    if not prev_val:
        return new_val
    if not new_val:
        return prev_val
    if isinstance(prev_val, list) and not isinstance(new_val, list):
        # Avoid wiping a valid map with an unexpected shape.
        return prev_val
    if not isinstance(prev_val, list) and isinstance(new_val, list):
        return new_val
    if not isinstance(prev_val, list) or not isinstance(new_val, list):
        return new_val

    merged: dict[str, dict[str, Any]] = {}
    for item in prev_val:
        if not isinstance(item, dict):
            continue
        unit_id = str(item.get("id") or item.get("unit_id") or item.get("block_id") or item.get("cell_id") or "").strip()
        if unit_id:
            merged[unit_id] = item
    for item in new_val:
        if not isinstance(item, dict):
            continue
        unit_id = str(item.get("id") or item.get("unit_id") or item.get("block_id") or item.get("cell_id") or "").strip()
        if unit_id:
            merged[unit_id] = item
    return list(merged.values())


def _merge_xlsx_translation_map(prev_val: Any, new_val: Any) -> Any:
    """Merge xlsx translation maps by (file,sheet,cell), preferring new entries on conflict."""
    if not prev_val:
        return new_val
    if not new_val:
        return prev_val

    if isinstance(prev_val, list) and not isinstance(new_val, list):
        # Avoid wiping a valid map with an unexpected shape.
        return prev_val
    if not isinstance(prev_val, list) and isinstance(new_val, list):
        return new_val
    if not isinstance(prev_val, list) or not isinstance(new_val, list):
        return new_val

    def _norm_key(item: dict[str, Any]) -> tuple[str, str, str] | None:
        file_name = str(item.get("file") or "").strip()
        sheet = str(item.get("sheet") or "").strip()
        cell = str(item.get("cell") or "").strip().upper()
        if not (file_name and sheet and cell):
            return None
        return (file_name, sheet, cell)

    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in prev_val:
        if not isinstance(item, dict):
            continue
        key = _norm_key(item)
        if key is None:
            continue
        merged[key] = item
    for item in new_val:
        if not isinstance(item, dict):
            continue
        key = _norm_key(item)
        if key is None:
            continue
        merged[key] = item
    return list(merged.values())


def _preserve_nonempty_translation_maps(previous: dict[str, Any], updated: dict[str, Any]) -> dict[str, Any]:
    """Avoid wiping preserve maps when applying a fix or an incremental draft.

    For spreadsheet jobs, models sometimes emit *partial* translation maps (e.g. "remaining 4 files"),
    expecting the orchestrator to carry forward earlier file maps. Treat missing entries as "unchanged",
    not as deletions.
    """
    if not isinstance(previous, dict) or not isinstance(updated, dict):
        return updated
    prev_docx = previous.get("docx_translation_map")
    new_docx = updated.get("docx_translation_map")
    updated["docx_translation_map"] = _merge_docx_translation_map(prev_docx, new_docx)

    prev_xlsx = previous.get("xlsx_translation_map")
    new_xlsx = updated.get("xlsx_translation_map")
    updated["xlsx_translation_map"] = _merge_xlsx_translation_map(prev_xlsx, new_xlsx)
    return updated


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


def _truncate_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars)] + "…"


def _compact_knowledge_context(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if OPENCLAW_KB_CONTEXT_MAX_HITS <= 0:
        return []
    out: list[dict[str, Any]] = []
    for hit in list(hits or [])[:OPENCLAW_KB_CONTEXT_MAX_HITS]:
        if not isinstance(hit, dict):
            out.append({"snippet": _truncate_text(hit, max_chars=OPENCLAW_KB_CONTEXT_MAX_CHARS)})
            continue
        row: dict[str, Any] = {}
        for key in ("id", "source", "path", "title", "score", "company", "doc_id", "chunk_id"):
            if key in hit:
                row[key] = hit.get(key)
        snippet = hit.get("snippet")
        if not snippet:
            snippet = hit.get("text")
        if not snippet:
            snippet = hit.get("content")
        if snippet:
            row["snippet"] = _truncate_text(snippet, max_chars=OPENCLAW_KB_CONTEXT_MAX_CHARS)
        if not row:
            row["snippet"] = _truncate_text(json.dumps(hit, ensure_ascii=False), max_chars=OPENCLAW_KB_CONTEXT_MAX_CHARS)
        out.append(row)
    return out


def _trim_xlsx_prompt_text(context_payload: dict[str, Any], *, max_chars_per_cell: int) -> int:
    preserve = context_payload.get("format_preserve")
    if not isinstance(preserve, dict):
        return 0
    sources = preserve.get("xlsx_sources")
    if not isinstance(sources, list):
        return 0
    trimmed = 0
    for src in sources:
        if not isinstance(src, dict):
            continue
        units = src.get("cell_units")
        if isinstance(units, list):
            for unit in units:
                if not isinstance(unit, dict):
                    continue
                text = unit.get("text")
                if not isinstance(text, str):
                    continue
                if len(text) > max_chars_per_cell:
                    unit["text"] = _truncate_text(text, max_chars=max_chars_per_cell)
                    trimmed += 1
        rows = src.get("rows")
        if isinstance(rows, list):
            for idx, row in enumerate(rows):
                if isinstance(row, list) and len(row) >= 3 and isinstance(row[2], str):
                    if len(row[2]) > max_chars_per_cell:
                        rows[idx][2] = _truncate_text(row[2], max_chars=max_chars_per_cell)
                        trimmed += 1
                elif isinstance(row, dict) and isinstance(row.get("text"), str):
                    text = str(row.get("text") or "")
                    if len(text) > max_chars_per_cell:
                        row["text"] = _truncate_text(text, max_chars=max_chars_per_cell)
                        trimmed += 1
    return trimmed


def _normalize_xlsx_key(file_name: str, sheet: str, cell: str) -> tuple[str, str, str]:
    return (
        str(file_name or "").strip(),
        str(sheet or "").strip(),
        str(cell or "").strip().upper(),
    )


def _collect_translated_xlsx_keys(previous_payload: dict[str, Any] | None) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    if not isinstance(previous_payload, dict):
        return keys
    entries = previous_payload.get("xlsx_translation_map")
    if not isinstance(entries, list):
        return keys
    for item in entries:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file") or "").strip()
        sheet = str(item.get("sheet") or "").strip()
        cell = str(item.get("cell") or "").strip()
        if not sheet or not cell:
            continue
        keys.add(_normalize_xlsx_key(file_name, sheet, cell))
    return keys


def _estimate_xlsx_source_chars(context_payload: dict[str, Any]) -> int:
    """Estimate total source text characters in xlsx_sources for output size prediction."""
    preserve = context_payload.get("format_preserve")
    if not isinstance(preserve, dict):
        return 0
    sources = preserve.get("xlsx_sources")
    if not isinstance(sources, list):
        return 0
    total = 0
    for src in sources:
        if not isinstance(src, dict):
            continue
        rows = src.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, list) and len(row) >= 3:
                    total += len(str(row[2] or ""))
                elif isinstance(row, dict):
                    total += len(str(row.get("text") or ""))
        units = src.get("cell_units")
        if isinstance(units, list):
            for unit in units:
                if isinstance(unit, dict):
                    total += len(str(unit.get("text") or ""))
    return total


def _count_xlsx_prompt_rows(context_payload: dict[str, Any]) -> int:
    preserve = context_payload.get("format_preserve")
    if not isinstance(preserve, dict):
        return 0
    sources = preserve.get("xlsx_sources")
    if not isinstance(sources, list):
        return 0
    total = 0
    for src in sources:
        if not isinstance(src, dict):
            continue
        rows = src.get("rows")
        if isinstance(rows, list):
            total += len(rows)
            continue
        units = src.get("cell_units")
        if isinstance(units, list):
            total += len(units)
    return total


def _cap_xlsx_prompt_rows(context_payload: dict[str, Any], *, max_rows: int) -> int:
    preserve = context_payload.get("format_preserve")
    if not isinstance(preserve, dict):
        return 0
    sources = preserve.get("xlsx_sources")
    if not isinstance(sources, list):
        return 0

    keep = max(1, int(max_rows))
    kept = 0
    for src in sources:
        if not isinstance(src, dict):
            continue
        bucket_key = ""
        items: list[Any] = []
        rows = src.get("rows")
        if isinstance(rows, list):
            bucket_key = "rows"
            items = rows
        else:
            units = src.get("cell_units")
            if isinstance(units, list):
                bucket_key = "cell_units"
                items = units
        if not bucket_key:
            continue
        remaining = keep - kept
        if remaining <= 0:
            src[bucket_key] = []
            continue
        if len(items) > remaining:
            src[bucket_key] = items[:remaining]
        kept += min(len(src.get(bucket_key) or []), remaining)
    return kept


def _compact_xlsx_prompt_payload(
    context_payload: dict[str, Any],
    *,
    previous_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preserve = context_payload.get("format_preserve")
    if not isinstance(preserve, dict):
        return {"changed": False, "total_rows": 0, "kept_rows": 0, "skipped_existing": 0}
    sources = preserve.get("xlsx_sources")
    if not isinstance(sources, list):
        return {"changed": False, "total_rows": 0, "kept_rows": 0, "skipped_existing": 0}

    translated_keys = _collect_translated_xlsx_keys(previous_payload)
    compact_sources: list[dict[str, Any]] = []
    total_rows = 0
    kept_rows = 0
    skipped_existing = 0
    changed = False

    for src in sources:
        if not isinstance(src, dict):
            continue
        file_name = str(src.get("file") or "").strip()
        rows_out: list[list[str]] = []
        pending_rows: list[list[str]] = []
        existing_rows: list[list[str]] = []

        rows = src.get("rows")
        if isinstance(rows, list):
            iter_rows = rows
        else:
            iter_rows = src.get("cell_units") if isinstance(src.get("cell_units"), list) else []

        for item in iter_rows:
            sheet = ""
            cell = ""
            text = ""
            if isinstance(item, list) and len(item) >= 3:
                sheet = str(item[0] or "").strip()
                cell = str(item[1] or "").strip()
                text = str(item[2] or "")
            elif isinstance(item, dict):
                sheet = str(item.get("sheet") or "").strip()
                cell = str(item.get("cell") or "").strip()
                text = str(item.get("text") or "")
                if not file_name:
                    file_name = str(item.get("file") or "").strip()
            if not sheet or not cell:
                continue
            total_rows += 1
            row = [sheet, cell.upper(), text]
            key_with_file = _normalize_xlsx_key(file_name, row[0], row[1])
            key_no_file = _normalize_xlsx_key("", row[0], row[1])
            if translated_keys and (key_with_file in translated_keys or key_no_file in translated_keys):
                existing_rows.append(row)
                skipped_existing += 1
            else:
                pending_rows.append(row)

        selected_rows = pending_rows if pending_rows else existing_rows
        if selected_rows:
            rows_out.extend(selected_rows)
            kept_rows += len(selected_rows)

        compact_source = {"file": file_name, "rows": rows_out}
        if rows_out:
            compact_sources.append(compact_source)
        if "path" in src or "meta" in src or "cell_units" in src or "rows" in src:
            changed = True

    preserve["xlsx_sources"] = compact_sources
    return {
        "changed": changed,
        "total_rows": total_rows,
        "kept_rows": kept_rows,
        "skipped_existing": skipped_existing,
    }


def _compact_previous_draft_for_prompt(previous_payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if not isinstance(previous_payload, dict):
        return {}, False
    out = copy.deepcopy(previous_payload)
    changed = False
    for key in ("final_text", "final_reflow_text"):
        if isinstance(out.get(key), str) and len(out[key]) > 12000:
            out[key] = _truncate_text(out[key], max_chars=12000)
            changed = True
    for key in ("docx_translation_map", "xlsx_translation_map"):
        entries = out.get(key)
        if isinstance(entries, list) and len(entries) > OPENCLAW_PREVIOUS_MAP_MAX_ENTRIES:
            out[key] = entries[:OPENCLAW_PREVIOUS_MAP_MAX_ENTRIES]
            changed = True
    return out, changed


def _flatten_xlsx_prompt_rows(context_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten xlsx_sources into canonical row dicts for batch planning."""
    preserve = context_payload.get("format_preserve")
    if not isinstance(preserve, dict):
        return []
    sources = preserve.get("xlsx_sources")
    if not isinstance(sources, list):
        return []

    rows: list[dict[str, Any]] = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        file_name = str(src.get("file") or "").strip()
        path = str(src.get("path") or "").strip()
        meta = src.get("meta") if isinstance(src.get("meta"), dict) else {}
        units = src.get("cell_units")
        if isinstance(units, list):
            for unit in units:
                if not isinstance(unit, dict):
                    continue
                sheet = str(unit.get("sheet") or "").strip()
                cell = str(unit.get("cell") or "").strip().upper()
                text = str(unit.get("text") or "")
                if not sheet or not cell:
                    continue
                row_file = str(unit.get("file") or file_name).strip()
                rows.append(
                    {
                        "file": row_file,
                        "path": path,
                        "meta": meta,
                        "sheet": sheet,
                        "cell": cell,
                        "text": text,
                    }
                )
            continue

        data_rows = src.get("rows")
        if isinstance(data_rows, list):
            for row in data_rows:
                if isinstance(row, list) and len(row) >= 3:
                    sheet = str(row[0] or "").strip()
                    cell = str(row[1] or "").strip().upper()
                    text = str(row[2] or "")
                elif isinstance(row, dict):
                    sheet = str(row.get("sheet") or "").strip()
                    cell = str(row.get("cell") or "").strip().upper()
                    text = str(row.get("text") or "")
                else:
                    continue
                if not sheet or not cell:
                    continue
                rows.append(
                    {
                        "file": file_name,
                        "path": path,
                        "meta": meta,
                        "sheet": sheet,
                        "cell": cell,
                        "text": text,
                    }
                )
    return rows


def _group_xlsx_rows_as_sources(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        file_name = str(row.get("file") or "").strip()
        if not file_name:
            continue
        if file_name not in grouped:
            grouped[file_name] = {
                "file": file_name,
                "rows": [],
            }
            order.append(file_name)
        grouped[file_name]["rows"].append(
            [
                str(row.get("sheet") or "").strip(),
                str(row.get("cell") or "").strip().upper(),
                str(row.get("text") or ""),
            ]
        )
    out: list[dict[str, Any]] = []
    for file_name in order:
        src = grouped[file_name]
        if src.get("rows"):
            out.append(src)
    return out


def _chunk_xlsx_rows_for_translation(
    rows: list[dict[str, Any]],
    *,
    max_cells: int,
    max_source_chars: int,
) -> list[list[dict[str, Any]]]:
    if not rows:
        return []
    cell_cap = max(1, int(max_cells))
    char_cap = max(200, int(max_source_chars))
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for row in rows:
        text_len = len(str(row.get("text") or ""))
        if current and (len(current) >= cell_cap or current_chars + text_len > char_cap):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(row)
        current_chars += text_len
    if current:
        chunks.append(current)
    return chunks


def _xlsx_batch_key_set(rows: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        file_name = str(row.get("file") or "").strip()
        sheet = str(row.get("sheet") or "").strip()
        cell = str(row.get("cell") or "").strip().upper()
        if file_name and sheet and cell:
            keys.add(_normalize_xlsx_key(file_name, sheet, cell))
    return keys


def _filter_xlsx_map_for_keys(entries: Any, keys: set[tuple[str, str, str]]) -> list[dict[str, Any]]:
    if not isinstance(entries, list) or not keys:
        return []
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file") or "").strip()
        sheet = str(item.get("sheet") or "").strip()
        cell = str(item.get("cell") or "").strip().upper()
        if not (file_name and sheet and cell):
            continue
        key = _normalize_xlsx_key(file_name, sheet, cell)
        if key not in keys or key in seen:
            continue
        out.append(
            {
                "file": file_name,
                "sheet": sheet,
                "cell": cell,
                "text": str(item.get("text") or ""),
            }
        )
        seen.add(key)
    return out


LANGUAGE_ALIASES: dict[str, str] = {
    "ar": "ar",
    "arabic": "ar",
    "arab": "ar",
    "en": "en",
    "english": "en",
    "eng": "en",
    "englsih": "en",
    "englsh": "en",
    "inglish": "en",
    "fr": "fr",
    "french": "fr",
    "es": "es",
    "spanish": "es",
    "de": "de",
    "german": "de",
    "pt": "pt",
    "portuguese": "pt",
    "zh": "zh",
    "chinese": "zh",
    "tr": "tr",
    "turkish": "tr",
}


def _normalize_language_token(token: str) -> str:
    raw = str(token or "").strip().lower()
    if not raw:
        return "unknown"
    return LANGUAGE_ALIASES.get(raw, "unknown")


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = str(value or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _has_english_target_hint(text: str) -> bool:
    if not text:
        return False
    return bool(
        re.search(
            r"\b(?:to|into|->|=>)\s*(?:english|eng|englsih|englsh|inglish)\b",
            text,
        )
    )


def _infer_language_pair_from_context(message_blob: str, candidates: list[dict[str, Any]]) -> tuple[str, str]:
    text = (message_blob or "").strip().lower()
    if text:
        # Most common phrasing: "<source> to <target>" or "<source> -> <target>".
        pattern = (
            r"\b(arabic|arab|english|eng|englsih|englsh|inglish|french|spanish|german|"
            r"portuguese|chinese|turkish|ar|en|fr|es|de|pt|zh|tr)\b\s*(?:to|into|->|=>)\s*"
            r"\b(arabic|arab|english|eng|englsih|englsh|inglish|french|spanish|german|"
            r"portuguese|chinese|turkish|ar|en|fr|es|de|pt|zh|tr)\b"
        )
        match = re.search(pattern, text)
        if match:
            src = _normalize_language_token(match.group(1))
            tgt = _normalize_language_token(match.group(2))
            if src == tgt and src not in {"unknown", "en"} and _has_english_target_hint(text):
                tgt = "en"
            return src, tgt

        if _has_english_target_hint(text):
            tokens = re.findall(
                r"\b(arabic|arab|french|spanish|german|portuguese|chinese|turkish|ar|fr|es|de|pt|zh|tr)\b",
                text,
            )
            src = _normalize_language_token(tokens[0]) if tokens else "unknown"
            return src, "en"

    langs = [str(x.get("language") or "").strip().lower() for x in candidates]
    known_langs = _ordered_unique([x for x in langs if x and x not in {"unknown", "multi"}])
    if len(known_langs) >= 2:
        src = known_langs[0]
        tgt = known_langs[1]
        if src == tgt and src not in {"unknown", "en"}:
            return src, "en"
        return src, tgt
    if len(known_langs) == 1:
        src = known_langs[0]
        tgt = "en" if src != "en" else "unknown"
        return src, tgt

    return "unknown", "en"


def _estimate_spreadsheet_minutes_from_candidates(candidates: list[dict[str, Any]]) -> int:
    """Heuristic ETA for spreadsheet jobs based on parsed structure size."""
    files = 0
    units = 0
    chars = 0
    for item in candidates:
        if not isinstance(item, dict):
            continue
        suffix = Path(str(item.get("path") or "")).suffix.lower()
        if suffix not in {".xlsx", ".csv", ".tsv"}:
            continue
        files += 1
        struct = item.get("structure") if isinstance(item.get("structure"), dict) else {}
        blocks = struct.get("blocks") if isinstance(struct.get("blocks"), list) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            text = str(block.get("text") or "").strip()
            if text:
                units += 1
                chars += len(text)
            rows = block.get("rows")
            if isinstance(rows, list):
                for row in rows:
                    row_text = str(row.get("text") or "").strip() if isinstance(row, dict) else str(row or "").strip()
                    if row_text:
                        units += 1
                        chars += len(row_text)
        if units <= 0:
            block_count = int(struct.get("block_count") or 0)
            para_count = int(struct.get("paragraph_count") or 0)
            units += max(block_count, para_count)

    if files <= 0:
        return 0

    # Small sheets stay near 10-20m; larger interview sheets rise to 35-60m.
    estimate = 6.0 + (files * 2.0) + (units / 7.5) + (chars / 2400.0)
    return int(max(8, min(180, round(estimate))))


def _fallback_intent(meta: dict[str, Any], candidates: list[dict[str, Any]], *, reason: str, raw_text: str = "") -> dict[str, Any]:
    message_blob = " ".join(
        [
            str(meta.get("subject") or ""),
            str(meta.get("message_text") or ""),
            str(meta.get("message") or ""),
        ]
    ).strip()
    source_language, target_language = _infer_language_pair_from_context(message_blob, candidates)
    suffixes = {Path(str(x.get("path") or "")).suffix.lower() for x in candidates}
    lowered_message = message_blob.lower()
    if any(ext in suffixes for ext in {".xlsx", ".csv", ".tsv"}):
        task_type = "SPREADSHEET_TRANSLATION"
    elif "proofread" in lowered_message:
        task_type = "BILINGUAL_PROOFREADING"
    elif len(candidates) >= 2:
        task_type = "MULTI_FILE_BATCH"
    else:
        task_type = "LOW_CONTEXT_TASK"

    required = _normalize_required_inputs(REQUIRED_INPUTS_BY_TASK.get(task_type, []))
    slots = _available_slots(candidates, source_language=source_language, target_language=target_language)
    missing = [x for x in required if not slots.get(x, False)]
    task_label = {
        "SPREADSHEET_TRANSLATION": "Translate spreadsheet content",
        "BILINGUAL_PROOFREADING": "Proofread bilingual translation",
        "MULTI_FILE_BATCH": "Translate multiple files",
    }.get(task_type, "Translation task")

    log.warning("Intent classifier fallback engaged: %s", reason)
    estimated_minutes = 45 if task_type == "SPREADSHEET_TRANSLATION" else 20
    complexity_score = 60.0 if task_type == "SPREADSHEET_TRANSLATION" else 30.0
    if task_type == "SPREADSHEET_TRANSLATION":
        dynamic_eta = _estimate_spreadsheet_minutes_from_candidates(candidates)
        if dynamic_eta > 0:
            estimated_minutes = max(estimated_minutes, dynamic_eta)
            complexity_score = max(complexity_score, min(100.0, 20.0 + dynamic_eta * 1.2))
    return {
        "ok": True,
        "intent": {
            "task_type": task_type,
            "task_label": task_label,
            "source_language": source_language,
            "target_language": target_language,
            "required_inputs": required,
            "missing_inputs": missing,
            "confidence": 0.35,
            "reasoning_summary": f"Fallback classification used because intent model was unavailable ({reason}).",
        },
        "estimated_minutes": estimated_minutes,
        "complexity_score": complexity_score,
        "raw": {
            "fallback": True,
            "reason": reason,
            "raw_text": raw_text[:1200],
        },
    }


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


def _extract_openclaw_payload_model(payload: Any) -> dict[str, str]:
    """Best-effort extraction of the model used from OpenClaw agent output payload.

    This is intentionally heuristic: OpenClaw gateway payload formats may vary across
    versions and backends. We prefer returning OpenClaw-style model keys
    (e.g. "kimi-coding/k2p5") when present, but will fall back to any model-like
    string.
    """
    def _is_model_key(s: str) -> bool:
        text = (s or "").strip()
        if not text or "://" in text:
            return False
        if "/" not in text:
            return False
        provider, model = text.split("/", 1)
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", provider):
            return False
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", model):
            return False
        return True

    def _walk(obj: Any, path: tuple[str, ...]) -> list[tuple[int, str, tuple[str, ...]]]:
        out: list[tuple[int, str, tuple[str, ...]]] = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                key = str(k)
                out.extend(_walk(v, path + (key,)))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                out.extend(_walk(v, path + (f"[{i}]",)))
        elif isinstance(obj, str):
            val = obj.strip()
            if not val:
                return out
            key_hint = " ".join(path).lower()
            score = 0
            if "model" in key_hint:
                score += 5
            if "resolved" in key_hint or "selected" in key_hint or "route" in key_hint:
                score += 3
            if _is_model_key(val):
                score += 10
            elif "model" in key_hint and len(val) <= 80:
                score += 2
            if score > 0:
                out.append((score, val, path))
        return out

    # Fast path: read structured agentMeta from OpenClaw response.
    if isinstance(payload, dict):
        result = payload.get("result")
        if isinstance(result, dict):
            meta = result.get("meta")
            if isinstance(meta, dict):
                agent_meta = meta.get("agentMeta")
                if isinstance(agent_meta, dict):
                    am_model = str(agent_meta.get("model") or "").strip()
                    am_provider = str(agent_meta.get("provider") or "").strip()
                    if am_model:
                        model_key = f"{am_provider}/{am_model}" if am_provider and "/" not in am_model else am_model
                        provider = model_key.split("/", 1)[0] if "/" in model_key else am_provider
                        return {"model": model_key, "provider": provider}

    # Fallback: heuristic walk.
    best: tuple[int, str, tuple[str, ...]] | None = None
    if isinstance(payload, dict):
        result = payload.get("result")
        for container in (result, payload):
            candidates = _walk(container, tuple())
            for cand in candidates:
                if best is None or cand[0] > best[0]:
                    best = cand

    if best is None:
        return {"model": "", "provider": ""}

    model = best[1]
    provider = model.split("/", 1)[0] if "/" in model else ""
    return {"model": model, "provider": provider}


def _is_retryable_agent_failure(error: str, detail: str) -> bool:
    text = f"{error}\n{detail}".lower()
    hard_non_retry_markers = (
        "agent_request_too_large:",
        "prompt_too_large",
        "invalid_api_key",
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "not found",
    )
    if any(m in text for m in hard_non_retry_markers):
        return False
    retry_markers = (
        "rate limit",
        "429",
        "cooldown",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "overloaded",
        "server error",
        "5xx",
        "connection reset",
        "gateway",
        "try again",
    )
    return any(m in text for m in retry_markers)


def _is_cooldown_provider_error(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        token in lowered
        for token in (
            "cooldown",
            "rate_limit",
            "rate limit",
            "too many requests",
            "429",
            "all profiles unavailable",
            "api rate limit reached",
        )
    )


def _recover_stale_agent_lock(agent_id: str) -> None:
    if not OPENCLAW_LOCK_RECOVERY_ENABLED:
        return
    try:
        lock_dir = Path.home() / ".openclaw" / "agents" / str(agent_id).strip() / "sessions"
        if not lock_dir.exists():
            return
        now = dt.datetime.now(dt.timezone.utc)
        for lock_path in lock_dir.glob("health-check*.lock"):
            try:
                payload = json.loads(lock_path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            pid_raw = payload.get("pid")
            created_raw = str(payload.get("createdAt") or "").strip()
            try:
                pid = int(pid_raw)
            except Exception:
                continue
            if not created_raw:
                continue
            created_iso = created_raw.replace("Z", "+00:00")
            try:
                created = dt.datetime.fromisoformat(created_iso)
            except Exception:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=dt.timezone.utc)
            age_seconds = (now - created).total_seconds()
            if age_seconds < OPENCLAW_LOCK_STALE_SECONDS:
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                try:
                    lock_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            except Exception:
                continue
            try:
                os.kill(pid, 15)
                time.sleep(1.2)
            except Exception:
                continue
            alive = True
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
            except Exception:
                alive = True
            if alive:
                try:
                    os.kill(pid, 9)
                    time.sleep(0.3)
                except Exception:
                    pass
            try:
                os.kill(pid, 0)
                still_alive = True
            except ProcessLookupError:
                still_alive = False
            except Exception:
                still_alive = True
            if not still_alive:
                try:
                    lock_path.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        return


def _agent_call(agent_id: str, message: str, timeout_seconds: int = OPENCLAW_CMD_TIMEOUT) -> dict[str, Any]:
    timeout_s = max(30, int(timeout_seconds))
    max_attempts = OPENCLAW_AGENT_CALL_MAX_ATTEMPTS
    backoff_s = OPENCLAW_AGENT_CALL_RETRY_BACKOFF_SECONDS
    _recover_stale_agent_lock(agent_id)
    session_id = f"runtime-{agent_id}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
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
        str(timeout_s),
        "--session-id",
        session_id,
    ]
    last_error: dict[str, Any] | None = None
    for attempt in range(1, max_attempts + 1):
        # Apply a local hard timeout as a safety net in addition to OpenClaw's own
        # --timeout argument; this prevents orphaned hangs from blocking the worker.
        hard_timeout_s = timeout_s + 15
        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=hard_timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            failure = {
                "ok": False,
                "error": f"agent_call_timeout:{agent_id}",
                "detail": f"subprocess_timeout:{hard_timeout_s}s",
                "stdout": str(getattr(exc, "stdout", "") or "").strip()[:2000],
                "stderr": str(getattr(exc, "stderr", "") or "").strip()[:2000],
                "attempt": attempt,
                "max_attempts": max_attempts,
            }
            last_error = failure
            if attempt < max_attempts and _is_retryable_agent_failure(str(failure.get("error")), str(failure.get("detail"))):
                time.sleep(min(backoff_s * (2 ** (attempt - 1)), OPENCLAW_AGENT_CALL_RETRY_MAX_BACKOFF_SECONDS))
                continue
            return failure
        if proc.returncode != 0:
            failure = {
                "ok": False,
                "error": f"agent_call_failed:{agent_id}",
                "stderr": proc.stderr.strip(),
                "stdout": proc.stdout.strip(),
                "returncode": proc.returncode,
                "attempt": attempt,
                "max_attempts": max_attempts,
            }
            last_error = failure
            detail = f"{failure.get('stderr', '')}\n{failure.get('stdout', '')}"
            if attempt < max_attempts and _is_retryable_agent_failure(str(failure.get("error")), detail):
                time.sleep(min(backoff_s * (2 ** (attempt - 1)), OPENCLAW_AGENT_CALL_RETRY_MAX_BACKOFF_SECONDS))
                continue
            return failure

        payload: Any | None = None
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            # OpenClaw may emit non-JSON log lines before/after the actual JSON blob
            # (e.g. "[agent/embedded] ..."). Extract the first decodable JSON value.
            candidates = _iter_json_candidates(proc.stdout, limit=12)
            if not candidates:
                failure = {
                    "ok": False,
                    "error": f"agent_json_invalid:{agent_id}",
                    "detail": "no JSON value found in stdout",
                    "stdout": proc.stdout[:2000],
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                }
                last_error = failure
                if attempt < max_attempts and _is_retryable_agent_failure(str(failure.get("error")), str(failure.get("stdout", ""))):
                    time.sleep(min(backoff_s * (2 ** (attempt - 1)), OPENCLAW_AGENT_CALL_RETRY_MAX_BACKOFF_SECONDS))
                    continue
                return failure

            # Prefer the candidate that actually contains payload text.
            payload = candidates[0]
            for cand in candidates:
                if _extract_openclaw_payload_text(cand):
                    payload = cand
                    break

        text = _extract_openclaw_payload_text(payload)
        meta = _extract_openclaw_payload_model(payload)
        if _looks_like_model_request_too_large(text):
            return {
                "ok": False,
                "error": f"agent_request_too_large:{agent_id}",
                "detail": text[:2000],
                "raw_text": text,
                "agent_id": agent_id,
                "payload": payload,
                "meta": meta,
                "attempt": attempt,
                "max_attempts": max_attempts,
            }
        return {
            "ok": True,
            "agent_id": agent_id,
            "payload": payload,
            "text": text,
            "meta": meta,
            "attempt": attempt,
            "max_attempts": max_attempts,
        }
    return last_error or {"ok": False, "error": f"agent_call_failed:{agent_id}", "detail": "unknown_failure"}


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
    if INTENT_CLASSIFIER_MODE in {"heuristic", "local"}:
        return _fallback_intent(meta, candidates, reason="intent_classifier_mode_forced_heuristic")

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
    call = _agent_call(INTENT_AGENT, prompt)
    if not call.get("ok"):
        return _fallback_intent(
            meta,
            candidates,
            reason=str(call.get("error") or "intent_agent_call_failed"),
            raw_text=str(call.get("stdout") or call.get("stderr") or ""),
        )
    try:
        parsed = _extract_json_from_text(str(call.get("text", "")))
    except Exception as exc:
        return _fallback_intent(
            meta,
            candidates,
            reason="intent_json_parse_failed",
            raw_text=str(call.get("text", "") or str(exc)),
        )

    task_type = str(parsed.get("task_type") or "").strip().upper()
    if task_type not in TASK_TYPES:
        task_type = "LOW_CONTEXT_TASK"
    confidence = float(parsed.get("confidence", 0.0) or 0.0)
    confidence = max(0.0, min(1.0, confidence))
    estimated_minutes = int(parsed.get("estimated_minutes", 12) or 12)
    estimated_minutes = max(1, estimated_minutes)
    complexity_score = float(parsed.get("complexity_score", 30.0) or 30.0)
    complexity_score = max(1.0, min(100.0, complexity_score))
    if task_type == "SPREADSHEET_TRANSLATION":
        dynamic_eta = _estimate_spreadsheet_minutes_from_candidates(candidates)
        if dynamic_eta > 0:
            estimated_minutes = max(estimated_minutes, dynamic_eta)
            complexity_score = max(complexity_score, min(100.0, 20.0 + dynamic_eta * 1.2))
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
    include_candidate_text = task_type != "SPREADSHEET_TRANSLATION"
    context: dict[str, Any] = {
        "job_id": meta.get("job_id"),
        "subject": meta.get("subject", ""),
        "message_text": meta.get("message_text", ""),
        "task_intent": intent,
        "selected_tool": task_type,
        "candidate_files": _candidate_payload(candidates, include_text=include_candidate_text),
        "knowledge_context": _compact_knowledge_context(kb_hits),
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

    revision_context_section = ""
    if task_type == "REVISION_UPDATE" and context.get("revision_context_prompt"):
        revision_context_section = f"""
CRITICAL REVISION CONTEXT:
{context.get("revision_context_prompt")}

PRESERVED_TEXT_MAP (copy these texts EXACTLY for the corresponding unit IDs):
{json.dumps(context.get("revision_pack", {}).get("preserved_text_map", {}), ensure_ascii=False)}
"""

    def _render_prompt(
        *,
        context_payload: dict[str, Any],
        previous_payload: dict[str, Any] | None,
        revision_section: str,
        local_findings: list[str],
        xlsx_batch_mode: bool = False,
        xlsx_batch_hint: str = "",
    ) -> str:
        batch_rules = ""
        if xlsx_batch_mode:
            batch_rules = f"""
- This is XLSX BATCH MODE. Translate ONLY the cells present in this batch payload.
- You MUST return xlsx_translation_map for every cell in this batch; do not skip any cell.
- Return JSON only with complete per-cell translations; no summarization.
- If you detect a source cell itself is truncated/incomplete, append {SOURCE_TRUNCATED_MARKER} at the end of that cell's translation.
- XLSX batch hint: {xlsx_batch_hint}
"""

        return f"""
You are Codex translator. Work on this translation job and return strict JSON only.

Round: {round_index}
Previous unresolved findings: {json.dumps(local_findings, ensure_ascii=False)}
{revision_section}
Execution context:
{json.dumps(context_payload, ensure_ascii=False)}

Previous draft (if any):
{json.dumps(previous_payload or {}, ensure_ascii=False)}

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
- If execution_context.format_preserve.xlsx_sources is present, you MUST fill xlsx_translation_map for every provided (file, sheet, cell), whether entries are in cell_units objects or rows tuples [sheet, cell, source_text].
- XLSX COMPLETENESS: For each cell in xlsx_translation_map, your translated text MUST cover the ENTIRE source text.
  Compare your translation against the source: if the source has 5 sentences, your translation must have 5 sentences.
  A translation that only covers the first half of the source is WRONG and will be rejected.
  If you cannot fit all cells completely, translate FEWER cells but translate each one FULLY.
{batch_rules}
- If execution_context.glossary_enforcer.terms is present, you MUST apply those term mappings strictly (Arabic => English).
  For every unit/cell whose source text contains a glossary Arabic term, the corresponding translated text MUST contain the required English translation.
  Never append standalone glossary labels at the end (e.g., "... Artificial Intelligence (AI)").
  Integrate terminology naturally in-place inside the translated sentence/cell.
- FOR REVISION_UPDATE: You MUST copy texts from PRESERVED_TEXT_MAP exactly for unchanged sections. Do not modify preserved texts.
- Do NOT output Markdown anywhere (no ``` fenced blocks, no **bold**, no headings like #/##, no "- " markdown bullets, no [text](url)).
  Use plain text only. For lists use "• " or "1) " style, not Markdown.
- If context is insufficient, keep "codex_pass": false and explain in unresolved.
- JSON only.
""".strip()

    def _execute_prompt(prompt: str, *, prefer_direct: bool = False) -> dict[str, Any]:
        call: dict[str, Any] = {"ok": False, "error": "not_called"}
        if prefer_direct:
            if _env_flag("OPENCLAW_GLM_DIRECT_FALLBACK_ENABLED", "1"):
                glm_call = _glm_direct_api_call(prompt)
                if glm_call.get("ok"):
                    call = glm_call
                else:
                    call = {"ok": False, "error": str(glm_call.get("error") or "glm_direct_failed")}
            if not call.get("ok") and _env_flag("OPENCLAW_KIMI_CODING_DIRECT_FALLBACK_ENABLED", "1"):
                kimi_call = _kimi_coding_direct_api_call(prompt)
                if kimi_call.get("ok"):
                    call = kimi_call
                else:
                    call = {"ok": False, "error": str(kimi_call.get("error") or "kimi_coding_direct_failed")}

        if not call.get("ok"):
            call = _agent_call(CODEX_AGENT, prompt)
        raw_text = str(call.get("text") or call.get("stdout") or call.get("stderr") or "")
        if call.get("ok") and _looks_like_provider_schema_error(raw_text):
            call = {
                "ok": False,
                "error": "agent_provider_schema_rejected",
                "detail": raw_text[:2000],
                "raw_text": raw_text,
            }

        if not call.get("ok"):
            fallback_errors: dict[str, str] = {}

            if CODEX_FALLBACK_AGENT and CODEX_FALLBACK_AGENT != CODEX_AGENT:
                fallback_call = _agent_call(CODEX_FALLBACK_AGENT, prompt)
                fallback_text = str(
                    fallback_call.get("text") or fallback_call.get("stdout") or fallback_call.get("stderr") or ""
                )
                if fallback_call.get("ok") and _looks_like_provider_schema_error(fallback_text):
                    fallback_call = {
                        "ok": False,
                        "error": "agent_provider_schema_rejected",
                        "detail": fallback_text[:2000],
                        "raw_text": fallback_text,
                    }
                if fallback_call.get("ok"):
                    call = fallback_call
                else:
                    fallback_errors["agent"] = str(
                        fallback_call.get("error") or f"agent_call_failed:{CODEX_FALLBACK_AGENT}"
                    )

            if not call.get("ok") and _env_flag("OPENCLAW_GLM_DIRECT_FALLBACK_ENABLED", "1"):
                glm_call = _glm_direct_api_call(prompt)
                if glm_call.get("ok"):
                    call = glm_call
                else:
                    fallback_errors["glm"] = str(glm_call.get("error") or "glm_direct_failed")

            if not call.get("ok") and _env_flag("OPENCLAW_KIMI_CODING_DIRECT_FALLBACK_ENABLED", "1"):
                kimi_call = _kimi_coding_direct_api_call(prompt)
                if kimi_call.get("ok"):
                    call = kimi_call
                else:
                    fallback_errors["kimi_coding"] = str(kimi_call.get("error") or "kimi_coding_direct_failed")

            if not call.get("ok"):
                detail = dict(call)
                if fallback_errors:
                    detail["fallback_errors"] = fallback_errors
                return {"ok": False, "error": call.get("error"), "detail": detail, "raw_text": raw_text}

        parse_error: str | None = None
        try:
            parsed = _extract_json_from_text(str(call.get("text", "")))
        except Exception as exc:
            parse_error = str(exc)
            parsed = None

        if parsed is None and _env_flag("OPENCLAW_GLM_DIRECT_FALLBACK_ENABLED", "1"):
            glm_call = _glm_direct_api_call(prompt)
            if glm_call.get("ok"):
                try:
                    parsed = _extract_json_from_text(str(glm_call.get("text", "")))
                    call = glm_call
                except Exception as glm_exc:
                    parse_error = (
                        f"{parse_error}; glm_direct_parse_failed:{glm_exc}"
                        if parse_error
                        else f"glm_direct_parse_failed:{glm_exc}"
                    )
            else:
                glm_err = str(glm_call.get("error") or "glm_direct_failed")
                parse_error = f"{parse_error}; glm_direct_failed:{glm_err}" if parse_error else f"glm_direct_failed:{glm_err}"

        if parsed is None and _env_flag("OPENCLAW_KIMI_CODING_DIRECT_FALLBACK_ENABLED", "1"):
            kimi_call = _kimi_coding_direct_api_call(prompt)
            if kimi_call.get("ok"):
                try:
                    parsed = _extract_json_from_text(str(kimi_call.get("text", "")))
                    call = kimi_call
                except Exception as kimi_exc:
                    parse_error = (
                        f"{parse_error}; kimi_coding_direct_parse_failed:{kimi_exc}"
                        if parse_error
                        else f"kimi_coding_direct_parse_failed:{kimi_exc}"
                    )
            else:
                kimi_err = str(kimi_call.get("error") or "kimi_coding_direct_failed")
                parse_error = (
                    f"{parse_error}; kimi_coding_direct_failed:{kimi_err}"
                    if parse_error
                    else f"kimi_coding_direct_failed:{kimi_err}"
                )

        if parsed is None:
            return {
                "ok": False,
                "error": "codex_json_parse_failed",
                "detail": parse_error or "invalid_json_payload",
                "raw_text": call.get("text", ""),
            }

        call_meta = call.get("meta") if isinstance(call.get("meta"), dict) else {}
        if isinstance(call, dict) and call.get("agent_id"):
            call_meta = {"agent_id": str(call.get("agent_id") or ""), **call_meta}
        if isinstance(call, dict) and call.get("source") == "direct_api_kimi_coding":
            call_meta = {
                "agent_id": "direct_api_kimi_coding",
                "provider": "kimi-coding",
                "model": str(call.get("model") or KIMI_CODING_MODEL or "kimi-coding/k2p5"),
                **call_meta,
            }
        if isinstance(call, dict) and call.get("source") == "direct_api":
            call_meta = {
                "agent_id": "direct_api_glm",
                "provider": str(GLM_MODEL or "").split("/", 1)[0] if "/" in str(GLM_MODEL or "") else "zai",
                "model": str(GLM_MODEL or "zai/glm-5"),
                **call_meta,
            }

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
                "docx_translation_map": parsed.get("docx_translation_map")
                or parsed.get("docx_translation_blocks")
                or parsed.get("docx_table_cells")
                or [],
                "xlsx_translation_map": parsed.get("xlsx_translation_map") or [],
                "review_brief_points": [str(x) for x in (parsed.get("review_brief_points") or [])],
                "change_log_points": [str(x) for x in (parsed.get("change_log_points") or [])],
                "resolved": [str(x) for x in (parsed.get("resolved") or [])],
                "unresolved": [str(x) for x in (parsed.get("unresolved") or [])],
                "codex_pass": bool(parsed.get("codex_pass")),
                "reasoning_summary": str(parsed.get("reasoning_summary") or ""),
            },
            "raw": parsed,
            "call_meta": call_meta,
        }

    def _run_single_generation(
        *,
        context_payload: dict[str, Any],
        previous_payload: dict[str, Any],
        local_findings: list[str],
        xlsx_batch_mode: bool = False,
        xlsx_batch_hint: str = "",
    ) -> dict[str, Any]:
        prompt_max_bytes = OPENCLAW_AGENT_PROMPT_MAX_BYTES
        if task_type == "SPREADSHEET_TRANSLATION":
            safe_spreadsheet_max = max(120000, int(os.getenv("OPENCLAW_XLSX_PROMPT_SAFE_MAX_BYTES", "700000")))
            prompt_max_bytes = min(prompt_max_bytes, safe_spreadsheet_max)
            # Keep spreadsheet prompts lean; large context fields do not improve cell-by-cell quality.
            context_payload["knowledge_context"] = []
            context_payload["cross_job_memories"] = []
            context_payload["subject"] = _truncate_text(context_payload.get("subject") or "", max_chars=200)
            context_payload["message_text"] = _truncate_text(context_payload.get("message_text") or "", max_chars=1200)
            candidate_rows = context_payload.get("candidate_files")
            if isinstance(candidate_rows, list):
                slim_rows: list[dict[str, Any]] = []
                for row in candidate_rows[:8]:
                    if not isinstance(row, dict):
                        continue
                    slim_rows.append(
                        {
                            "name": row.get("name"),
                            "path": row.get("path"),
                            "language": row.get("language"),
                            "version": row.get("version"),
                            "role": row.get("role"),
                        }
                    )
                context_payload["candidate_files"] = slim_rows
            preserve = context_payload.get("format_preserve")
            if isinstance(preserve, dict) and "docx_template" in preserve:
                preserve.pop("docx_template", None)

        prompt = _render_prompt(
            context_payload=context_payload,
            previous_payload=previous_payload,
            revision_section=revision_context_section,
            local_findings=local_findings,
            xlsx_batch_mode=xlsx_batch_mode,
            xlsx_batch_hint=xlsx_batch_hint,
        )
        prompt_bytes = len(prompt.encode("utf-8"))
        compactions: list[str] = []

        if prompt_bytes > prompt_max_bytes:
            context_payload["knowledge_context"] = []
            context_payload["cross_job_memories"] = []
            compactions.append("drop_knowledge_context")
            prompt = _render_prompt(
                context_payload=context_payload,
                previous_payload=previous_payload,
                revision_section=revision_context_section,
                local_findings=local_findings,
                xlsx_batch_mode=xlsx_batch_mode,
                xlsx_batch_hint=xlsx_batch_hint,
            )
            prompt_bytes = len(prompt.encode("utf-8"))

        if prompt_bytes > prompt_max_bytes and task_type == "SPREADSHEET_TRANSLATION":
            trimmed_cells = _trim_xlsx_prompt_text(
                context_payload,
                max_chars_per_cell=OPENCLAW_XLSX_PROMPT_TEXT_MAX_CHARS,
            )
            if trimmed_cells > 0:
                compactions.append(f"trim_xlsx_text:{trimmed_cells}")
                prompt = _render_prompt(
                    context_payload=context_payload,
                    previous_payload=previous_payload,
                    revision_section=revision_context_section,
                    local_findings=local_findings,
                    xlsx_batch_mode=xlsx_batch_mode,
                    xlsx_batch_hint=xlsx_batch_hint,
                )
                prompt_bytes = len(prompt.encode("utf-8"))

        if prompt_bytes > prompt_max_bytes and task_type == "SPREADSHEET_TRANSLATION":
            compact_stats = _compact_xlsx_prompt_payload(
                context_payload,
                previous_payload=previous_payload,
            )
            if compact_stats.get("changed"):
                compactions.append(
                    "compact_xlsx_rows:"
                    f"{int(compact_stats.get('kept_rows') or 0)}/{int(compact_stats.get('total_rows') or 0)}"
                )
                prompt = _render_prompt(
                    context_payload=context_payload,
                    previous_payload=previous_payload,
                    revision_section=revision_context_section,
                    local_findings=local_findings,
                    xlsx_batch_mode=xlsx_batch_mode,
                    xlsx_batch_hint=xlsx_batch_hint,
                )
                prompt_bytes = len(prompt.encode("utf-8"))

        if prompt_bytes > prompt_max_bytes and task_type == "SPREADSHEET_TRANSLATION":
            total_rows = _count_xlsx_prompt_rows(context_payload)
            shrink_round = 0
            while prompt_bytes > prompt_max_bytes and total_rows > 1 and shrink_round < 8:
                target_rows = max(1, int(total_rows * 0.7))
                kept = _cap_xlsx_prompt_rows(context_payload, max_rows=target_rows)
                compactions.append(f"cap_xlsx_rows:{kept}/{total_rows}")
                prompt = _render_prompt(
                    context_payload=context_payload,
                    previous_payload=previous_payload,
                    revision_section=revision_context_section,
                    local_findings=local_findings,
                    xlsx_batch_mode=xlsx_batch_mode,
                    xlsx_batch_hint=xlsx_batch_hint,
                )
                prompt_bytes = len(prompt.encode("utf-8"))
                total_rows = _count_xlsx_prompt_rows(context_payload)
                shrink_round += 1

        if prompt_bytes > prompt_max_bytes and previous_payload:
            compact_prev, changed = _compact_previous_draft_for_prompt(previous_payload)
            if changed:
                previous_payload = compact_prev
                compactions.append("compact_previous_draft")
                prompt = _render_prompt(
                    context_payload=context_payload,
                    previous_payload=previous_payload,
                    revision_section=revision_context_section,
                    local_findings=local_findings,
                    xlsx_batch_mode=xlsx_batch_mode,
                    xlsx_batch_hint=xlsx_batch_hint,
                )
                prompt_bytes = len(prompt.encode("utf-8"))

        if compactions:
            context_payload["prompt_compaction"] = {
                "applied": compactions,
                "max_bytes": prompt_max_bytes,
            }
            prompt = _render_prompt(
                context_payload=context_payload,
                previous_payload=previous_payload,
                revision_section=revision_context_section,
                local_findings=local_findings,
                xlsx_batch_mode=xlsx_batch_mode,
                xlsx_batch_hint=xlsx_batch_hint,
            )
            prompt_bytes = len(prompt.encode("utf-8"))

        if prompt_bytes > prompt_max_bytes:
            return {
                "ok": False,
                "error": "prompt_too_large",
                "detail": {
                    "bytes": prompt_bytes,
                    "max_bytes": prompt_max_bytes,
                    "configured_agent_message_max_bytes": OPENCLAW_AGENT_MESSAGE_MAX_BYTES,
                    "provider_total_limit_bytes": OPENCLAW_PROVIDER_MESSAGE_LIMIT_BYTES,
                    "reserved_overhead_bytes": OPENCLAW_AGENT_MESSAGE_OVERHEAD_BYTES,
                    "compactions": compactions,
                },
                "raw_text": "",
            }

        prefer_direct = False
        if task_type == "SPREADSHEET_TRANSLATION":
            direct_min = max(10000, int(os.getenv("OPENCLAW_XLSX_DIRECT_FIRST_MIN_PROMPT_BYTES", "50000")))
            prefer_direct = prompt_bytes >= direct_min and _env_flag("OPENCLAW_XLSX_PREFER_DIRECT_API_ON_LARGE_PROMPT", "1")
        return _execute_prompt(prompt, prefer_direct=prefer_direct)

    context_for_prompt = copy.deepcopy(context)
    previous_for_prompt = copy.deepcopy(previous_draft or {})

    if task_type != "SPREADSHEET_TRANSLATION":
        return _run_single_generation(
            context_payload=context_for_prompt,
            previous_payload=previous_for_prompt,
            local_findings=list(findings or []),
        )

    # Quality-first spreadsheet path: split into small batches and backfill misses.
    all_rows = _flatten_xlsx_prompt_rows(context_for_prompt)
    if not all_rows:
        return _run_single_generation(
            context_payload=context_for_prompt,
            previous_payload=previous_for_prompt,
            local_findings=list(findings or []),
        )

    batch_max_cells = max(1, int(os.getenv("OPENCLAW_XLSX_BATCH_MAX_CELLS", "6")))
    batch_max_chars = max(200, int(os.getenv("OPENCLAW_XLSX_BATCH_MAX_SOURCE_CHARS", "8000")))
    batch_retry = max(0, int(os.getenv("OPENCLAW_XLSX_BATCH_RETRY", "1")))
    chunks = _chunk_xlsx_rows_for_translation(
        all_rows,
        max_cells=batch_max_cells,
        max_source_chars=batch_max_chars,
    )
    chunk_queue: list[dict[str, Any]] = [
        {"label": str(i), "rows": list(chunk)}
        for i, chunk in enumerate(chunks, start=1)
    ]

    merged_xlsx_map: Any = previous_for_prompt.get("xlsx_translation_map") if isinstance(previous_for_prompt, dict) else []
    merged_docx_map: Any = previous_for_prompt.get("docx_translation_map") if isinstance(previous_for_prompt, dict) else []
    aggregate_data: dict[str, Any] = {
        "final_text": "",
        "final_reflow_text": "",
        "docx_translation_map": merged_docx_map if isinstance(merged_docx_map, list) else [],
        "xlsx_translation_map": merged_xlsx_map if isinstance(merged_xlsx_map, list) else [],
        "review_brief_points": [],
        "change_log_points": [],
        "resolved": [],
        "unresolved": [],
        "codex_pass": True,
        "reasoning_summary": "",
    }
    first_call_meta: dict[str, Any] = {}
    failed_batches: list[dict[str, Any]] = []
    xlsx_files = sorted({str(row.get("file") or "").strip() for row in all_rows if str(row.get("file") or "").strip()})

    while chunk_queue:
        chunk_item = chunk_queue.pop(0)
        chunk_label = str(chunk_item.get("label") or "?")
        chunk_rows = chunk_item.get("rows") if isinstance(chunk_item.get("rows"), list) else []
        if not chunk_rows:
            continue
        expected_keys = _xlsx_batch_key_set(chunk_rows)
        batch_context = copy.deepcopy(context_for_prompt)
        preserve = batch_context.get("format_preserve")
        if not isinstance(preserve, dict):
            preserve = {}
            batch_context["format_preserve"] = preserve
        preserve["xlsx_sources"] = _group_xlsx_rows_as_sources(chunk_rows)

        batch_previous = copy.deepcopy(previous_for_prompt)
        batch_previous["xlsx_translation_map"] = _filter_xlsx_map_for_keys(
            aggregate_data.get("xlsx_translation_map"),
            expected_keys,
        )

        attempt = 0
        batch_result: dict[str, Any] | None = None
        local_findings = list(findings or [])
        batch_collected_map: Any = batch_previous.get("xlsx_translation_map") if isinstance(batch_previous, dict) else []
        split_for_size = False
        while attempt <= batch_retry:
            attempt += 1
            hint = f"batch={chunk_label} cells={len(chunk_rows)} attempt={attempt} queue={len(chunk_queue)}"
            batch_result = _run_single_generation(
                context_payload=copy.deepcopy(batch_context),
                previous_payload=copy.deepcopy(batch_previous),
                local_findings=local_findings,
                xlsx_batch_mode=True,
                xlsx_batch_hint=hint,
            )
            if not batch_result.get("ok"):
                err = str(batch_result.get("error") or "")
                detail = str(batch_result.get("detail") or "")
                raw_text = str(batch_result.get("raw_text") or "")
                if len(chunk_rows) > 1 and (
                    err.startswith("agent_request_too_large:")
                    or _looks_like_model_request_too_large(detail)
                    or _looks_like_model_request_too_large(raw_text)
                ):
                    split_for_size = True
                    mid = max(1, len(chunk_rows) // 2)
                    left_rows = list(chunk_rows[:mid])
                    right_rows = list(chunk_rows[mid:])
                    chunk_queue = [
                        {"label": f"{chunk_label}a", "rows": left_rows},
                        {"label": f"{chunk_label}b", "rows": right_rows},
                        *chunk_queue,
                    ]
                    failed_batches.append(
                        {
                            "batch": chunk_label,
                            "error": "agent_request_too_large",
                            "action": "split_retry",
                            "cells": len(chunk_rows),
                            "split_cells": [len(left_rows), len(right_rows)],
                        }
                    )
                    break
                continue
            data = batch_result.get("data") if isinstance(batch_result.get("data"), dict) else {}
            batch_collected_map = _merge_xlsx_translation_map(batch_collected_map, data.get("xlsx_translation_map"))
            if isinstance(batch_collected_map, list):
                data["xlsx_translation_map"] = batch_collected_map
            got_keys = _normalize_xlsx_translation_map_keys(batch_collected_map, xlsx_files=xlsx_files)
            missing = sorted(expected_keys - got_keys)
            if not missing:
                break
            missing_sample = [{"file": f, "sheet": s, "cell": c} for (f, s, c) in missing[:50]]
            local_findings = list(findings or []) + [
                f"xlsx_missing_batch_cells:{json.dumps(missing_sample, ensure_ascii=False)}"
            ]
            batch_previous["xlsx_translation_map"] = batch_collected_map if isinstance(batch_collected_map, list) else []

        if split_for_size:
            continue

        if not batch_result or not batch_result.get("ok"):
            failed_batches.append({"batch": chunk_label, "error": str((batch_result or {}).get("error") or "batch_failed")})
            continue

        if not first_call_meta:
            first_call_meta = batch_result.get("call_meta") if isinstance(batch_result.get("call_meta"), dict) else {}

        data = batch_result.get("data") if isinstance(batch_result.get("data"), dict) else {}
        aggregate_data["docx_translation_map"] = _merge_docx_translation_map(
            aggregate_data.get("docx_translation_map"),
            data.get("docx_translation_map"),
        )
        aggregate_data["xlsx_translation_map"] = _merge_xlsx_translation_map(
            aggregate_data.get("xlsx_translation_map"),
            data.get("xlsx_translation_map"),
        )
        aggregate_data["review_brief_points"] = sorted(
            set([str(x) for x in (aggregate_data.get("review_brief_points") or [])] + [str(x) for x in (data.get("review_brief_points") or [])])
        )
        aggregate_data["change_log_points"] = sorted(
            set([str(x) for x in (aggregate_data.get("change_log_points") or [])] + [str(x) for x in (data.get("change_log_points") or [])])
        )
        aggregate_data["resolved"] = sorted(
            set([str(x) for x in (aggregate_data.get("resolved") or [])] + [str(x) for x in (data.get("resolved") or [])])
        )
        aggregate_data["unresolved"] = sorted(
            set([str(x) for x in (aggregate_data.get("unresolved") or [])] + [str(x) for x in (data.get("unresolved") or [])])
        )
        aggregate_data["codex_pass"] = bool(aggregate_data.get("codex_pass")) and bool(data.get("codex_pass", True))
        if not aggregate_data.get("final_text") and str(data.get("final_text") or "").strip():
            aggregate_data["final_text"] = str(data.get("final_text") or "")
        if not aggregate_data.get("final_reflow_text") and str(data.get("final_reflow_text") or "").strip():
            aggregate_data["final_reflow_text"] = str(data.get("final_reflow_text") or "")
        aggregate_data["reasoning_summary"] = str(data.get("reasoning_summary") or aggregate_data.get("reasoning_summary") or "")

    expected_all_keys = _xlsx_batch_key_set(all_rows)
    got_all_keys = _normalize_xlsx_translation_map_keys(aggregate_data.get("xlsx_translation_map"), xlsx_files=xlsx_files)
    missing_all = sorted(expected_all_keys - got_all_keys)
    if missing_all:
        aggregate_data["unresolved"] = sorted(
            set([str(x) for x in (aggregate_data.get("unresolved") or [])] + [f"xlsx_translation_map_incomplete:missing={len(missing_all)}"])
        )
        aggregate_data["codex_pass"] = False
        failed_batches.append({"batch": "backfill", "missing_cells": len(missing_all)})

    if failed_batches and not aggregate_data.get("reasoning_summary"):
        aggregate_data["reasoning_summary"] = "Spreadsheet batch translation partially failed."

    if not aggregate_data.get("xlsx_translation_map") and failed_batches:
        return {
            "ok": False,
            "error": "xlsx_batch_generation_failed",
            "detail": {"failed_batches": failed_batches},
            "raw_text": "",
        }

    return {
        "ok": True,
        "data": aggregate_data,
        "raw": {"batch_mode": True, "failed_batches": failed_batches},
        "call_meta": first_call_meta,
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
    if not call.get("ok") and GEMINI_FALLBACK_AGENTS:
        for fb_agent in GEMINI_FALLBACK_AGENTS:
            if fb_agent == GEMINI_AGENT:
                continue
            call = _agent_call(fb_agent, prompt)
            if call.get("ok"):
                break
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

    data = {
        "findings": [str(x) for x in (parsed.get("findings") or [])],
        "resolved": [str(x) for x in (parsed.get("resolved") or [])],
        "unresolved": [str(x) for x in (parsed.get("unresolved") or [])],
        "pass": bool(parsed.get("pass")),
        "terminology_rate": _clamp(parsed.get("terminology_rate"), 0.0),
        "structure_complete_rate": _clamp(parsed.get("structure_complete_rate"), 0.0),
        "target_language_purity": _clamp(parsed.get("target_language_purity"), 0.0),
        "numbering_consistency": _clamp(parsed.get("numbering_consistency"), 0.0),
        "reasoning_summary": str(parsed.get("reasoning_summary") or ""),
    }

    call_meta = call.get("meta") if isinstance(call.get("meta"), dict) else {}
    if isinstance(call, dict) and call.get("agent_id"):
        call_meta = {"agent_id": str(call.get("agent_id") or ""), **call_meta}

    # Treat "empty but successful" reviews as unavailable. This happens when upstream
    # providers return placeholder zeros without any findings or explanation.
    if (
        (not data["pass"])
        and (not data["findings"])
        and (not data["unresolved"])
        and (not data["resolved"])
        and (not data["reasoning_summary"].strip())
        and float(data["terminology_rate"]) == 0.0
        and float(data["structure_complete_rate"]) == 0.0
        and float(data["target_language_purity"]) == 0.0
        and float(data["numbering_consistency"]) == 0.0
    ):
        return {
            "ok": False,
            "error": "gemini_review_empty",
            "detail": call,
            "raw_text": call.get("text", ""),
            "call_meta": call_meta,
        }

    return {"ok": True, "data": data, "raw": parsed, "call_meta": call_meta}


def _resolve_openclaw_auth_profiles_path() -> Path:
    override = os.environ.get("OPENCLAW_AUTH_PROFILES_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return Path("~/.openclaw/agents/main/agent/auth-profiles.json").expanduser()


def _read_openclaw_api_key(provider_id: str) -> str:
    provider = str(provider_id or "").strip()
    if not provider:
        return ""
    path = _resolve_openclaw_auth_profiles_path()
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""

    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}

    def _extract_key(profile_obj: Any) -> str:
        if not isinstance(profile_obj, dict):
            return ""
        if str(profile_obj.get("type") or "") != "api_key":
            return ""
        for key_name in ("key", "api_key", "apiKey", "token"):
            val = profile_obj.get(key_name)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ""

    last_good = data.get("lastGood")
    if isinstance(last_good, dict):
        profile_id = last_good.get(provider)
        if isinstance(profile_id, str) and profile_id.strip():
            key = _extract_key(profiles.get(profile_id.strip()))
            if key:
                return key

    key = _extract_key(profiles.get(f"{provider}:default"))
    if key:
        return key

    for profile_id, profile_obj in profiles.items():
        if not isinstance(profile_id, str) or not profile_id.startswith(f"{provider}:"):
            continue
        key = _extract_key(profile_obj)
        if key:
            return key
    return ""


def _looks_like_provider_schema_error(text: str) -> bool:
    lowered = str(text or "").lower()
    if "patternproperties" not in lowered:
        return False
    return any(
        token in lowered
        for token in (
            "invalid json payload",
            "cannot find field",
            "function_declarations",
        )
    )


def _looks_like_model_request_too_large(text: str) -> bool:
    lowered = str(text or "").lower()
    return (
        "llm request rejected" in lowered
        and "total message size" in lowered
        and "exceeds limit" in lowered
    )


def _kimi_coding_direct_api_call(prompt: str) -> dict[str, Any]:
    import urllib.request

    model_key = str(KIMI_CODING_MODEL or "kimi-coding/k2p5").strip()
    model = model_key.rsplit("/", 1)[-1] if "/" in model_key else model_key
    api_key = (
        str(os.getenv("OPENCLAW_KIMI_CODING_API_KEY") or "").strip()
        or str(os.getenv("ANTHROPIC_AUTH_TOKEN") or "").strip()
        or _read_openclaw_api_key("kimi-coding")
    )
    if not api_key:
        return {"ok": False, "error": "kimi_coding_api_key_not_set"}

    base_url = str(KIMI_CODING_API_BASE_URL or "").strip().rstrip("/")
    if base_url.endswith("/coding"):
        base_url = f"{base_url}/v1"
    url = f"{base_url}/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 4096,
            "stream": False,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        if not isinstance(content, str):
            content = str(content)
        return {
            "ok": True,
            "text": content,
            "source": "direct_api_kimi_coding",
            "provider": "kimi-coding",
            "model": model_key,
        }
    except Exception as exc:
        return {"ok": False, "error": f"kimi_coding_direct_api_failed:{exc}"}


def _glm_direct_api_call(prompt: str) -> dict[str, Any]:
    """Fallback: call Zhipu GLM API directly when OpenClaw agent unavailable."""
    import urllib.error
    import urllib.request

    api_key = str(os.getenv("GLM_API_KEY") or GLM_API_KEY or "").strip()
    if not api_key:
        api_key = _read_openclaw_api_key("zai")
    if not api_key:
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
            "Authorization": f"Bearer {api_key}",
        },
    )
    last_error = ""
    for attempt in range(1, OPENCLAW_DIRECT_API_MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return {
                "ok": True,
                "text": text,
                "source": "direct_api",
                "attempt": attempt,
                "max_attempts": OPENCLAW_DIRECT_API_MAX_ATTEMPTS,
            }
        except urllib.error.HTTPError as exc:
            last_error = f"glm_direct_api_failed: HTTP Error {exc.code}: {exc.reason}"
            retryable = exc.code in {408, 409, 425, 429, 500, 502, 503, 504}
            if attempt < OPENCLAW_DIRECT_API_MAX_ATTEMPTS and retryable:
                time.sleep(min(OPENCLAW_DIRECT_API_BACKOFF_SECONDS * (2 ** (attempt - 1)), OPENCLAW_DIRECT_API_MAX_BACKOFF_SECONDS))
                continue
            return {"ok": False, "error": last_error, "attempt": attempt, "max_attempts": OPENCLAW_DIRECT_API_MAX_ATTEMPTS}
        except Exception as exc:
            last_error = f"glm_direct_api_failed: {exc}"
            if attempt < OPENCLAW_DIRECT_API_MAX_ATTEMPTS:
                time.sleep(min(OPENCLAW_DIRECT_API_BACKOFF_SECONDS * (2 ** (attempt - 1)), OPENCLAW_DIRECT_API_MAX_BACKOFF_SECONDS))
                continue
            return {"ok": False, "error": last_error, "attempt": attempt, "max_attempts": OPENCLAW_DIRECT_API_MAX_ATTEMPTS}
    return {"ok": False, "error": last_error or "glm_direct_api_failed", "attempt": OPENCLAW_DIRECT_API_MAX_ATTEMPTS, "max_attempts": OPENCLAW_DIRECT_API_MAX_ATTEMPTS}


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
- If execution_context.format_preserve.xlsx_sources is present, you MUST fill xlsx_translation_map for every provided (file, sheet, cell), whether entries are in cell_units objects or rows tuples [sheet, cell, source_text].
- XLSX COMPLETENESS: For each cell in xlsx_translation_map, your translated text MUST cover the ENTIRE source text.
  Compare your translation against the source: if the source has 5 sentences, your translation must have 5 sentences.
  A translation that only covers the first half of the source is WRONG and will be rejected.
  If you cannot fit all cells completely, translate FEWER cells but translate each one FULLY.
- If execution_context.glossary_enforcer.terms is present, you MUST apply those term mappings strictly (Arabic => English).
  For every unit/cell whose source text contains a glossary Arabic term, the corresponding translated text MUST contain the required English translation.
  Never append standalone glossary labels at the end (e.g., "... Artificial Intelligence (AI)").
  Integrate terminology naturally in-place inside the translated sentence/cell.
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

    call_meta: dict[str, Any] = {}
    if isinstance(call, dict):
        call_meta = call.get("meta") if isinstance(call.get("meta"), dict) else {}
        if call.get("agent_id"):
            call_meta = {"agent_id": str(call.get("agent_id") or ""), **call_meta}
        if not call_meta.get("model") and call.get("source") == "direct_api":
            call_meta = {
                "agent_id": "direct_api",
                "model": str(GLM_MODEL or "").strip(),
                "provider": str(GLM_MODEL or "").split("/", 1)[0] if "/" in str(GLM_MODEL or "") else "",
            }
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
        "call_meta": call_meta,
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
    if status == "skipped":
        reason = str(qa_result.get("reason") or qa_result.get("error") or "").strip()
        msg = f"xlsx_qa_skipped:{file_name}"
        if reason:
            msg = f"{msg}:{reason[:180]}"
        warnings.append(msg)
        return findings, warnings
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
    if status == "skipped":
        reason = str(qa_result.get("reason") or qa_result.get("error") or "").strip()
        msg = f"docx_qa_skipped:{file_name}"
        if reason:
            msg = f"{msg}:{reason[:180]}"
        warnings.append(msg)
        return findings, warnings
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
    glossary_findings: list[str] = []
    glossary_meta: dict[str, Any] | None = None
    # Only validate glossary enforcement once preserve coverage is complete, otherwise we'd
    # flood findings due to missing translation map entries.
    if not preserve_findings:
        glossary_findings, glossary_meta = _validate_glossary_enforcer(context, draft)

    findings: list[str] = []
    findings.extend(markdown_findings)
    findings.extend(preserve_findings)
    findings.extend(glossary_findings)
    warnings: list[str] = []
    source_truncated_count = int(preserve_meta.get("xlsx_source_truncated_count", 0) or 0)
    if source_truncated_count > 0:
        warnings.append(f"xlsx_source_truncated:cells={source_truncated_count}")
        missing_marker_count = int(preserve_meta.get("xlsx_source_truncated_marker_missing_count", 0) or 0)
        if missing_marker_count > 0:
            warnings.append(f"xlsx_source_truncated_marker_missing:cells={missing_marker_count}")

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
        "glossary_enforcer": {"findings": glossary_findings, "meta": glossary_meta},
        "vision_trial": vision_results,
    }
    return findings, warnings, meta


def run(
    meta: dict[str, Any],
    *,
    plan_only: bool = False,
    on_round_complete: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
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
            log.info("format_preserve: xlsx_sources=%d candidates=%d", len(xlsx_sources), len(candidates))
            if xlsx_sources:
                max_cells = int(os.getenv("OPENCLAW_XLSX_TRANSLATION_MAX_CELLS", "2000"))
                max_chars = int(os.getenv("OPENCLAW_XLSX_MAX_CHARS_PER_CELL", "2000"))
                if task_type == "SPREADSHEET_TRANSLATION":
                    min_chars = int(os.getenv("OPENCLAW_XLSX_MIN_CHARS_PER_CELL_FOR_COMPLETENESS", "2000"))
                    if max_chars > 0 and max_chars < min_chars:
                        status_flags.append("xlsx_cell_text_cap_increased")
                        max_chars = min_chars
                src_lang = str(intent.get("source_language") or "").strip().lower()
                arabic_only = False
                if src_lang in {"ar", "arabic"}:
                    arabic_only = os.getenv("OPENCLAW_XLSX_ARABIC_ONLY", "1").strip().lower() not in {"0", "false", "no", "off"}

                sheet_include_regex = os.getenv("OPENCLAW_XLSX_SHEET_INCLUDE_REGEX", "").strip() or None
                sheet_exclude_regex = os.getenv("OPENCLAW_XLSX_SHEET_EXCLUDE_REGEX", "").strip() or None
                focus_interview = (
                    task_type == "SPREADSHEET_TRANSLATION"
                    and os.getenv("OPENCLAW_XLSX_FOCUS_INTERVIEW_SHEETS", "1").strip().lower() not in {"0", "false", "no", "off"}
                    and not sheet_include_regex
                )
                sources_payload: list[dict[str, Any]] = []
                for src in xlsx_sources:
                    units, meta_info = extract_translatable_cells(
                        src,
                        max_cells=max_cells,
                        arabic_only=arabic_only,
                        interview_only_if_present=focus_interview,
                        sheet_include_regex=sheet_include_regex,
                        sheet_exclude_regex=sheet_exclude_regex,
                    )
                    sources_payload.append(
                        {
                            "file": src.name,
                            "path": str(src),
                            "cell_units": xlsx_units_to_payload(units, max_chars_per_cell=max_chars),
                            "meta": meta_info,
                        }
                    )
                format_preserve["xlsx_sources"] = sources_payload
                log.info("format_preserve: xlsx_sources built with %d files, %d total units",
                         len(sources_payload),
                         sum(len(s.get("cell_units", [])) for s in sources_payload))

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
            log.error("format_preserve build failed: %s", exc, exc_info=True)

        if format_preserve:
            execution_context["format_preserve"] = format_preserve
            log.info("format_preserve attached to execution_context, keys=%s", list(format_preserve.keys()))
        else:
            log.warning("format_preserve is EMPTY — xlsx cell-level translation will not be available")

        # --- KB Glossary Enforcer (company-scoped) ---
        try:
            glossary_enabled = _env_flag("OPENCLAW_GLOSSARY_ENFORCER_ENABLED", "1")
            src_lang = str(intent.get("source_language") or "").strip().lower()
            tgt_lang = str(intent.get("target_language") or "").strip().lower()
            kb_root = str(meta.get("kb_root") or "").strip()
            kb_company = str(meta.get("kb_company") or "").strip()

            if (
                glossary_enabled
                and format_preserve
                and kb_root
                and kb_company
            ):
                from scripts.kb_glossary_enforcer import (
                    build_glossary_map,
                    load_company_glossary_pairs,
                    looks_arabic,
                    select_terms_for_sources,
                )

                source_texts: list[str] = []
                docx_template = format_preserve.get("docx_template") if isinstance(format_preserve.get("docx_template"), dict) else None
                if docx_template and isinstance(docx_template.get("units"), list):
                    for u in docx_template.get("units") or []:
                        if not isinstance(u, dict):
                            continue
                        t = u.get("text")
                        if isinstance(t, str) and t.strip():
                            source_texts.append(t)

                for src in (format_preserve.get("xlsx_sources") or []):
                    if not isinstance(src, dict):
                        continue
                    for u in (src.get("cell_units") or []):
                        if not isinstance(u, dict):
                            continue
                        t = u.get("text")
                        if isinstance(t, str) and t.strip():
                            source_texts.append(t)

                has_arabic_source = any(looks_arabic(t) for t in source_texts[:400])
                src_ok = (not src_lang) or src_lang in {"ar", "arabic", "unknown", "multi"}
                tgt_ok = (not tgt_lang) or tgt_lang in {"en", "english", "unknown", "multi"}

                if has_arabic_source and src_ok and tgt_ok:
                    max_files = int(os.getenv("OPENCLAW_GLOSSARY_ENFORCER_MAX_FILES", "80"))
                    max_terms = int(os.getenv("OPENCLAW_GLOSSARY_ENFORCER_MAX_TERMS", "80"))
                    min_ar_len = int(os.getenv("OPENCLAW_GLOSSARY_ENFORCER_MIN_AR_LEN", "2"))

                    pairs, pairs_meta = load_company_glossary_pairs(
                        kb_root=Path(kb_root),
                        company=kb_company,
                        max_files=max_files,
                    )
                    glossary_map, conflicts = build_glossary_map(pairs, min_arabic_len=min_ar_len)
                    selected, select_meta = select_terms_for_sources(
                        glossary_map=glossary_map,
                        source_texts=source_texts,
                        max_terms=max_terms,
                    )

                    if selected:
                        execution_context["glossary_enforcer"] = {
                            "enabled": True,
                            "company": kb_company,
                            "terms": [{"ar": p.arabic, "en": p.english} for p in selected],
                            "meta": {
                                "files_scanned": int(pairs_meta.get("files_scanned", 0) or 0),
                                "pairs_extracted": int(pairs_meta.get("pairs_extracted", 0) or 0),
                                "errors": len(pairs_meta.get("errors") or []),
                                "unique_terms": len(glossary_map),
                                "conflicts": len(conflicts),
                                "matched_terms": int(select_meta.get("matched_terms", 0) or 0),
                                "truncated": bool(select_meta.get("truncated", False)),
                            },
                        }
                        (execution_context.get("rules") or {})["glossary_enforcer_strict"] = True
                        status_flags.append("glossary_enforcer_active")
        except Exception as exc:
            status_flags.append("glossary_enforcer_error")
            execution_context["glossary_enforcer"] = {"enabled": False, "error": str(exc)}

        rounds: list[dict[str, Any]] = []
        previous_findings: list[str] = []
        current_draft: dict[str, Any] | None = None
        errors: list[str] = []
        gemini_enabled = bool(meta.get("gemini_available", True))
        gemini_initially_enabled = gemini_enabled
        glm_enabled = _glm_enabled()
        system_round_root = Path(review_dir) / ".system" / "rounds"
        system_round_root.mkdir(parents=True, exist_ok=True)
        disallow_markdown = _env_flag("OPENCLAW_DISALLOW_MARKDOWN", "1")
        vision_in_round = _env_flag("OPENCLAW_VISION_QA_IN_ROUND", "1")
        vision_fix_limit = max(0, int(os.getenv("OPENCLAW_VISION_QA_MAX_RETRIES", "2")))
        markdown_sanity_by_round: dict[str, Any] = {}
        preserve_coverage_by_round: dict[str, Any] = {}
        vision_trials_by_round: dict[str, Any] = {}
        queue_retry_recommended = False
        queue_retry_after_seconds = 0
        queue_retry_reason = ""

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
            # Re-enable gemini each round so a transient failure doesn't permanently
            # disable review when fallback agents are available.
            if gemini_initially_enabled and not gemini_enabled and GEMINI_FALLBACK_AGENTS:
                gemini_enabled = True
            # Hard-gate retries are per round; otherwise a noisy first round can
            # consume the entire budget and prevent convergence in later rounds.
            vision_fix_used = 0

            codex_gen = _codex_generate(execution_context, current_draft, previous_findings, round_idx)
            glm_gen = (
                _glm_generate(execution_context, current_draft, previous_findings, round_idx)
                if glm_enabled
                else {"ok": False, "error": "glm_disabled"}
            )

            codex_data: dict[str, Any] | None = codex_gen.get("data") if codex_gen.get("ok") else None
            glm_data: dict[str, Any] | None = glm_gen.get("data") if (glm_enabled and glm_gen.get("ok")) else None
            if current_draft:
                # Some generators emit incremental preserve maps across rounds. Treat missing entries
                # as "unchanged" and merge them forward.
                if codex_data:
                    codex_data = _preserve_nonempty_translation_maps(current_draft, codex_data)
                if glm_data:
                    glm_data = _preserve_nonempty_translation_maps(current_draft, glm_data)

            _write_raw_error_artifacts(round_dir, "codex_generate", codex_gen)
            if glm_enabled:
                _write_raw_error_artifacts(round_dir, "glm_generate", glm_gen)

            codex_gen_meta = codex_gen.get("call_meta") if isinstance(codex_gen.get("call_meta"), dict) else {}
            glm_gen_meta = glm_gen.get("call_meta") if isinstance(glm_gen.get("call_meta"), dict) else {}

            generation_errors: dict[str, str] = {}
            if not codex_data:
                generation_errors["codex"] = str(codex_gen.get("error", "codex_generation_failed"))
            if glm_enabled and not glm_data:
                generation_errors["glm"] = str(glm_gen.get("error", "glm_generation_failed"))
            if not codex_data and not glm_data:
                errors.append(f"no_generator_candidates:{generation_errors}")
                if generation_errors and all(_is_cooldown_provider_error(str(v or "")) for v in generation_errors.values()):
                    status_flags.append("all_providers_cooldown")
                    if OPENCLAW_COOLDOWN_FRIENDLY_MODE:
                        queue_retry_recommended = True
                        queue_retry_after_seconds = OPENCLAW_COOLDOWN_RETRY_SECONDS
                        queue_retry_reason = "all_providers_cooldown"
                _write_json(round_dir / "generation_errors.json", {"errors": generation_errors})
                break

            codex_review: dict[str, Any] | None = None
            glm_review: dict[str, Any] | None = None
            codex_review_meta: dict[str, Any] = {}
            glm_review_meta: dict[str, Any] = {}
            gemini_review_errors: list[str] = []

            if gemini_enabled:
                if codex_data:
                    rev = _gemini_review(execution_context, codex_data, round_idx)
                    if rev.get("ok"):
                        codex_review = rev["data"]
                        codex_review_meta = rev.get("call_meta") if isinstance(rev.get("call_meta"), dict) else {}
                    else:
                        _write_raw_error_artifacts(round_dir, "gemini_review_codex", rev)
                        gemini_review_errors.append(str(rev.get("error", "gemini_review_failed:codex")))
                        codex_review_meta = rev.get("call_meta") if isinstance(rev.get("call_meta"), dict) else {}
                if glm_data:
                    rev = _gemini_review(execution_context, glm_data, round_idx)
                    if rev.get("ok"):
                        glm_review = rev["data"]
                        glm_review_meta = rev.get("call_meta") if isinstance(rev.get("call_meta"), dict) else {}
                    else:
                        _write_raw_error_artifacts(round_dir, "gemini_review_glm", rev)
                        gemini_review_errors.append(str(rev.get("error", "gemini_review_failed:glm")))
                        glm_review_meta = rev.get("call_meta") if isinstance(rev.get("call_meta"), dict) else {}

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
                        "xlsx_marker_count": 0,
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
                    "xlsx_marker_count": _xlsx_marker_count(draft),
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
                        key=lambda x: (
                            bool(x.get("pass")),
                            float(x.get("score", -1.0)),
                            -int(x.get("xlsx_marker_count", 0) or 0),
                            1 if x.get("source") == "codex" else 0,
                        ),
                    )
                    selected_source = str(best.get("source"))
                    selected_draft = codex_data if selected_source == "codex" else (glm_data or {})
                    selected_review = codex_review if selected_source == "codex" else glm_review
                    selection_reason = "pass_and_score" if passing else "score_only"

            selected_generator_meta = codex_gen_meta if selected_source == "codex" else glm_gen_meta
            selected_review_meta = codex_review_meta if selected_source == "codex" else glm_review_meta

            review_findings: list[str] = []
            if selected_review:
                review_findings = list(selected_review.get("unresolved") or [])
                if not review_findings and not bool(selected_review.get("pass", False)):
                    review_findings = list(selected_review.get("findings") or [])
            if not review_findings:
                review_findings = list(selected_draft.get("unresolved") or [])

            did_fix = False
            if review_findings:
                prev_selected = selected_draft
                codex_fix = _codex_generate(execution_context, selected_draft, review_findings, round_idx)
                if codex_fix.get("ok"):
                    selected_draft = _preserve_nonempty_translation_maps(prev_selected, codex_fix["data"])
                    selected_generator_meta = (
                        codex_fix.get("call_meta") if isinstance(codex_fix.get("call_meta"), dict) else selected_generator_meta
                    )
                    did_fix = True

            glossary_suffix_cleanup = _strip_redundant_glossary_suffixes(execution_context, selected_draft)
            if gemini_enabled:
                if did_fix:
                    gemini_final = _gemini_review(execution_context, selected_draft, round_idx)
                    if gemini_final.get("ok"):
                        gemini_data = gemini_final["data"]
                        selected_review_meta = (
                            gemini_final.get("call_meta")
                            if isinstance(gemini_final.get("call_meta"), dict)
                            else selected_review_meta
                        )
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
                    if not items and not bool(gemini_data.get("pass", False)):
                        items.extend([str(x) for x in (gemini_data.get("findings") or [])])
                if not items:
                    items.extend([str(x) for x in (selected_draft.get("unresolved") or [])])
                return [x for x in items if str(x).strip()]

            def _hard_gate_retry_hints(meta: dict[str, Any]) -> list[str]:
                hints: list[str] = []
                preserve = meta.get("preserve_coverage") if isinstance(meta.get("preserve_coverage"), dict) else {}
                preserve_meta = preserve.get("meta") if isinstance(preserve.get("meta"), dict) else {}

                xlsx_units = preserve_meta.get("xlsx_missing_units_sample")
                if isinstance(xlsx_units, list) and xlsx_units:
                    hints.append(f"xlsx_missing_units_sample:{json.dumps(xlsx_units, ensure_ascii=False)}")
                else:
                    xlsx_sample = preserve_meta.get("xlsx_missing_sample")
                    if isinstance(xlsx_sample, list) and xlsx_sample:
                        hints.append(f"xlsx_missing_sample:{json.dumps(xlsx_sample, ensure_ascii=False)}")

                # Truncation hints — tell the model which cells need complete translation
                xlsx_truncated = preserve_meta.get("xlsx_translation_truncated_sample")
                if not isinstance(xlsx_truncated, list) or not xlsx_truncated:
                    xlsx_truncated = preserve_meta.get("xlsx_truncated_sample")
                if isinstance(xlsx_truncated, list) and xlsx_truncated:
                    hints.append(
                        f"xlsx_truncated_cells_need_complete_translation:{json.dumps(xlsx_truncated, ensure_ascii=False)}"
                    )

                docx_sample = preserve_meta.get("docx_missing_sample")
                if isinstance(docx_sample, list) and docx_sample:
                    hints.append(f"docx_missing_sample:{json.dumps(docx_sample, ensure_ascii=False)}")

                return hints

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
                retry_findings = sorted(set(_fix_findings_for_retry() + hard_findings + _hard_gate_retry_hints(hard_meta)))
                retry_findings = [x for x in retry_findings if str(x).strip()][:40]
                hard_fix_attempts.append({"attempt": vision_fix_used, "findings": retry_findings})

                codex_fix = _codex_generate(execution_context, selected_draft, retry_findings, round_idx)
                if not codex_fix.get("ok"):
                    errors.append(f"hard_gate_fix_failed:{codex_fix.get('error')}")
                    break
                selected_draft = _preserve_nonempty_translation_maps(selected_draft, codex_fix["data"])
                glossary_suffix_cleanup = _strip_redundant_glossary_suffixes(execution_context, selected_draft)
                if isinstance(codex_fix.get("call_meta"), dict):
                    selected_generator_meta = codex_fix["call_meta"]
                did_fix = True

                if gemini_enabled:
                    gemini_final = _gemini_review(execution_context, selected_draft, round_idx)
                    if gemini_final.get("ok"):
                        gemini_data = gemini_final["data"]
                        if isinstance(gemini_final.get("call_meta"), dict):
                            selected_review_meta = gemini_final["call_meta"]
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
            selection_meta["glossary_suffix_cleanup"] = glossary_suffix_cleanup
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
            rec["generator"] = selected_generator_meta
            rec["reviewer"] = selected_review_meta
            rec["generator_model"] = str((selected_generator_meta.get("model") or "")).strip()
            rec["review_model"] = str((selected_review_meta.get("model") or "")).strip()
            rec["generator_agent_id"] = str((selected_generator_meta.get("agent_id") or "")).strip()
            rec["review_agent_id"] = str((selected_review_meta.get("agent_id") or "")).strip()
            rec["selected_candidate"] = selected_source
            rec["candidate_refs"] = candidate_refs
            rec["candidate_review_refs"] = review_refs
            rec["selection"] = selection_meta
            rec["hard_findings"] = hard_findings
            rec["warnings"] = hard_warnings
            rec["hard_fix_attempts"] = hard_fix_attempts
            rec["metrics"]["hard_fail_items"] = list(hard_findings)
            if any(str(item).startswith("xlsx_translation_truncated:") for item in hard_findings):
                if "translation_truncation_detected" not in status_flags:
                    status_flags.append("translation_truncation_detected")
            if any(str(item).startswith("xlsx_source_truncated:") for item in hard_warnings):
                if "source_data_truncated_warning" not in status_flags:
                    status_flags.append("source_data_truncated_warning")
            if hard_findings:
                rec["unresolved"] = sorted(set([str(x) for x in (rec.get("unresolved") or [])] + [str(x) for x in hard_findings]))
                rec["pass"] = False
            rounds.append(rec)
            if on_round_complete is not None:
                try:
                    on_round_complete(rec)
                except Exception as exc:
                    log.warning("on_round_complete failed for job %s round %s: %s", job_id, rec.get("round"), exc)

            markdown_sanity_by_round[str(round_idx)] = hard_meta.get("markdown_sanity")
            preserve_coverage_by_round[str(round_idx)] = hard_meta.get("preserve_coverage")
            vision_trials_by_round[str(round_idx)] = hard_meta.get("vision_trial")

            current_draft = selected_draft
            previous_findings = list(rec.get("unresolved") or [])
            if rec.get("pass"):
                break

        # --- GLM-5 advisory review (after rounds loop) ---
        if glm_enabled and rounds and current_draft:
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
                    any_skipped = False
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
                        status_value = str(qa_result.get("status") or "").strip().lower()
                        if status_value == "skipped":
                            any_skipped = True
                            continue
                        if status_value != "passed":
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
                    if any_skipped:
                        status_flags.append("format_qa_skipped")
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
                        status_value = str(docx_qa.get("status") or "").strip().lower()
                        if status_value == "skipped":
                            status_flags.append("docx_qa_skipped")
                        elif status_value != "passed":
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
                        report_md = generator.generate_markdown(results_list)
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
            "status_flags": [f for f in status_flags if f != "degraded_single_model"] if gemini_enabled else status_flags,
            "thinking_level": OPENCLAW_TRANSLATION_THINKING,
            "router_mode": router_mode,
            "token_guard_applied": token_guard_applied,
            "errors": errors if errors else ([] if status == "review_ready" else ["double_pass_not_reached"]),
            "queue_retry_recommended": bool(queue_retry_recommended),
            "queue_retry_after_seconds": int(queue_retry_after_seconds) if queue_retry_recommended else 0,
            "queue_retry_reason": str(queue_retry_reason or "") if queue_retry_recommended else "",
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

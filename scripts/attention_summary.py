#!/usr/bin/env python3
"""Summarize why a job needs attention.

This module is intentionally small and dependency-free. It provides a short
"why" list (1-3 lines) derived from:
- status_flags (fast, already persisted in DB)
- quality_report.json (best-effort)
- errors (fallback)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

_NOISE_ERRORS = {"double_pass_not_reached"}


def _norm_items(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for v in values:
        s = str(v or "").strip()
        if not s:
            continue
        out.append(s)
    return out


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in values:
        s = str(item or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _system_path(review_dir: str, child: str) -> str:
    if not str(review_dir or "").strip():
        return ""
    return str((Path(review_dir) / ".system" / child).resolve())


def _flag_message(flag: str, *, review_dir: str) -> str | None:
    f = (flag or "").strip()
    if not f:
        return None
    if f == "format_qa_failed":
        base = _system_path(review_dir, "format_qa")
        msg = "Format QA failed (XLSX layout mismatch)."
        return f"{msg} Check: {base}" if base else msg
    if f == "format_qa_error":
        base = _system_path(review_dir, "format_qa")
        msg = "Format QA error (vision QA crashed)."
        return f"{msg} Check: {base}" if base else msg
    if f == "docx_qa_failed":
        base = _system_path(review_dir, "docx_qa")
        msg = "DOCX QA failed (layout mismatch)."
        return f"{msg} Check: {base}" if base else msg
    if f == "docx_qa_error":
        base = _system_path(review_dir, "docx_qa")
        msg = "DOCX QA error (vision QA crashed)."
        return f"{msg} Check: {base}" if base else msg
    if f == "docx_layout_ugly":
        base = _system_path(review_dir, "docx_qa")
        msg = "DOCX aesthetics warning (layout looks odd)."
        return f"{msg} Check: {base}" if base else msg
    if f == "format_qa_aesthetics_warning":
        base = _system_path(review_dir, "format_qa")
        msg = "XLSX aesthetics warning (visual polish suggested)."
        return f"{msg} Check: {base}" if base else msg
    if f == "non_converged":
        return "Did not converge within max rounds (double_pass not reached)."
    if f == "format_preserve_payload_error":
        return "Format-preserve payload error (XLSX/DOCX extraction)."
    if f == "degraded_single_model":
        return "Gemini review unavailable; degraded to single-model path."
    if f == "hard_fail":
        return "Hard failure occurred (no usable rounds produced)."
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _load_quality_report(*, artifacts: dict[str, Any], review_dir: str) -> dict[str, Any] | None:
    candidates: list[str] = []
    p = artifacts.get("quality_report_json")
    if isinstance(p, str) and p.strip():
        candidates.append(p.strip())
    if str(review_dir or "").strip():
        candidates.append(str(Path(review_dir) / ".system" / "quality_report.json"))

    for raw in candidates:
        path = Path(raw).expanduser()
        if not path.exists():
            continue
        data = _read_json(path)
        if data:
            return data
    return None


def attention_summary(
    *,
    status: str,
    review_dir: str,
    status_flags: list[str],
    errors: list[str],
    artifacts: dict[str, Any],
    max_items: int = 3,
) -> list[str]:
    """Return 0..max_items short lines explaining a needs_attention/failed state."""

    if max_items <= 0:
        return []

    status_norm = str(status or "").strip().lower()
    review_dir_norm = str(review_dir or "").strip()

    flags = _dedupe(_norm_items(status_flags or []))
    errs = _dedupe(_norm_items(errors or []))

    reasons: list[str] = []

    # 1) Human-ish mappings for the most common flags.
    mapped: list[str] = []
    for fl in flags:
        msg = _flag_message(fl, review_dir=review_dir_norm)
        if msg:
            mapped.append(msg)
    reasons.extend(_dedupe(mapped))

    # 2) Use last-round "hard_findings" / "unresolved" if available.
    report = _load_quality_report(artifacts=artifacts or {}, review_dir=review_dir_norm) or {}
    rounds = report.get("rounds")
    last_round: dict[str, Any] = {}
    if isinstance(rounds, list) and rounds:
        tail = rounds[-1]
        if isinstance(tail, dict):
            last_round = tail
    for key in ("hard_findings", "unresolved"):
        if len(reasons) >= max_items:
            break
        items = last_round.get(key) or []
        if not isinstance(items, list) or not items:
            continue
        for item in _dedupe(_norm_items(items)):
            if len(reasons) >= max_items:
                break
            if item not in reasons:
                reasons.append(item)
        if reasons:
            break

    # 3) Fallback to errors (avoid the generic noise token when other signals exist).
    if reasons:
        errs = [e for e in errs if e not in _NOISE_ERRORS]
    if not reasons and errs:
        for e in errs:
            if len(reasons) >= max_items:
                break
            if e == "double_pass_not_reached" and status_norm == "needs_attention":
                reasons.append("Did not converge within max rounds (double_pass not reached).")
            else:
                reasons.append(e)

    # 4) If still empty, expose raw flags as a last resort.
    if not reasons and flags:
        for fl in flags[:max_items]:
            reasons.append(f"status_flag:{fl}")

    # Remove duplicates again after mixed sources.
    return _dedupe(reasons)[: max(1, int(max_items))]

#!/usr/bin/env python3
"""Quality gate utilities for OpenClaw translation orchestration."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QualityThresholds:
    judge_margin: float = 0.08
    term_hit: float = 0.92
    critical_changes: int = 5
    terminology_min: float = 0.92
    structure_min: float = 0.94
    purity_min: float = 0.96
    numbering_min: float = 0.94
    timeout_buffer_ratio: float = 1.3
    timeout_hard_cap_minutes: int = 45
    max_rounds: int = 3
    format_fidelity_min: float = 0.85
    format_qa_max_retries: int = 2
    preservation_fidelity_min: float = 1.0  # Require exact preservation


def _safe_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_text_for_comparison(text: str) -> str:
    """Normalize text for comparison by removing extra whitespace."""
    import re
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def check_preservation_fidelity(
    draft: dict[str, Any],
    preserved_text_map: dict[str, str],
) -> tuple[bool, float, list[dict[str, Any]]]:
    """Verify that preserved sections match exactly.

    This is critical for REVISION_UPDATE tasks where unchanged sections
    must be copied verbatim from the English V1 baseline.

    Args:
        draft: The draft output containing docx_translation_map
        preserved_text_map: Map of unit_id -> expected text to preserve

    Returns:
        Tuple of (pass, fidelity_score, errors)
        - pass: True if all preserved sections match exactly
        - fidelity_score: Ratio of correctly preserved sections (0.0 to 1.0)
        - errors: List of errors with details for mismatched sections
    """
    if not preserved_text_map:
        return True, 1.0, []

    # Extract output map from draft
    output_map: dict[str, str] = {}
    entries = draft.get("docx_translation_map") or []
    if isinstance(entries, dict):
        for k, v in entries.items():
            output_map[str(k)] = str(v or "")
    elif isinstance(entries, list):
        for item in entries:
            if not isinstance(item, dict):
                continue
            unit_id = str(item.get("id") or item.get("unit_id") or "")
            text = str(item.get("text") or "")
            if unit_id:
                output_map[unit_id] = text

    errors: list[dict[str, Any]] = []
    preserved_count = 0

    for unit_id, expected_text in preserved_text_map.items():
        actual_text = output_map.get(unit_id, "")

        # Normalize for comparison
        expected_norm = _normalize_text_for_comparison(expected_text)
        actual_norm = _normalize_text_for_comparison(actual_text)

        if expected_norm == actual_norm:
            preserved_count += 1
        else:
            errors.append({
                "unit_id": unit_id,
                "expected_snippet": expected_text[:200] if len(expected_text) > 200 else expected_text,
                "actual_snippet": actual_text[:200] if len(actual_text) > 200 else actual_text,
                "error_type": "preservation_violation",
                "expected_length": len(expected_text),
                "actual_length": len(actual_text),
            })

    total = len(preserved_text_map)
    fidelity_score = preserved_count / total if total > 0 else 1.0
    passed = preserved_count == total

    return passed, round(fidelity_score, 4), errors


def _critical_section_changed(delta_pack: dict[str, Any], thresholds: QualityThresholds) -> bool:
    added = len(delta_pack.get("added", []))
    modified = len(delta_pack.get("modified", []))
    return added >= thresholds.critical_changes or modified >= thresholds.critical_changes


def compute_runtime_timeout(
    estimated_minutes: float,
    thresholds: QualityThresholds | None = None,
) -> tuple[int, list[str]]:
    t = thresholds or QualityThresholds()
    bounded_estimate = max(1.0, float(estimated_minutes))
    buffered = bounded_estimate * t.timeout_buffer_ratio
    runtime_timeout = int(round(min(buffered, float(t.timeout_hard_cap_minutes))))
    flags: list[str] = []
    if buffered > t.timeout_hard_cap_minutes:
        flags.append("long_task_capped")
    return runtime_timeout, flags


def evaluate_round(
    *,
    round_index: int,
    previous_unresolved: list[str],
    metrics: dict[str, Any],
    gemini_enabled: bool,
    thresholds: QualityThresholds | None = None,
    draft: dict[str, Any] | None = None,
    preserved_text_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    t = thresholds or QualityThresholds()

    terminology_rate = _safe_float(metrics.get("terminology_rate"), 0.0)
    structure_rate = _safe_float(metrics.get("structure_complete_rate"), 0.0)
    purity_rate = _safe_float(metrics.get("target_language_purity"), 0.0)
    numbering_rate = _safe_float(metrics.get("numbering_consistency"), 0.0)

    hard_fail_items = list(metrics.get("hard_fail_items") or [])
    findings: list[str] = []
    if terminology_rate < t.terminology_min:
        findings.append("terminology_rate_below_threshold")
    if structure_rate < t.structure_min:
        findings.append("structure_complete_rate_below_threshold")
    if purity_rate < t.purity_min:
        findings.append("target_language_purity_below_threshold")
    if numbering_rate < t.numbering_min:
        findings.append("numbering_consistency_below_threshold")
    findings.extend(f"hard_fail:{x}" for x in hard_fail_items)

    # Check preservation fidelity for REVISION_UPDATE tasks
    preservation_fidelity = 1.0
    preservation_errors: list[dict[str, Any]] = []
    if draft and preserved_text_map:
        pres_pass, preservation_fidelity, preservation_errors = check_preservation_fidelity(
            draft, preserved_text_map
        )
        if not pres_pass:
            findings.append("preservation_fidelity_below_threshold")
            hard_fail_items.append(f"preservation_violations:{len(preservation_errors)}")

    unresolved = sorted(set(findings))
    prev_set = set(previous_unresolved)
    unresolved_set = set(unresolved)
    resolved = sorted(prev_set - unresolved_set)

    codex_pass = len(hard_fail_items) == 0 and not (
        terminology_rate < t.terminology_min
        or structure_rate < t.structure_min
        or numbering_rate < t.numbering_min
    )
    gemini_pass = codex_pass and purity_rate >= t.purity_min if gemini_enabled else codex_pass
    double_pass = codex_pass and (gemini_pass if gemini_enabled else True)

    result: dict[str, Any] = {
        "round": round_index,
        "metrics": {
            "terminology_rate": round(terminology_rate, 4),
            "structure_complete_rate": round(structure_rate, 4),
            "target_language_purity": round(purity_rate, 4),
            "numbering_consistency": round(numbering_rate, 4),
            "hard_fail_items": hard_fail_items,
            "preservation_fidelity": preservation_fidelity,
        },
        "findings": findings,
        "resolved": resolved,
        "unresolved": unresolved,
        "codex_pass": codex_pass,
        "gemini_pass": gemini_pass,
        "pass": double_pass,
    }
    if preservation_errors:
        result["preservation_errors"] = preservation_errors[:10]  # Limit to first 10 errors
    return result


def summarize_quality_report(rounds: list[dict[str, Any]], timeout_hit: bool) -> dict[str, Any]:
    if not rounds:
        return {
            "rounds": [],
            "convergence_reached": False,
            "stop_reason": "hard_fail",
        }

    last = rounds[-1]
    convergence = bool(last.get("pass"))
    if convergence:
        stop_reason = "double_pass"
    elif timeout_hit:
        stop_reason = "timeout"
    else:
        stop_reason = "max_rounds"

    return {
        "rounds": rounds,
        "convergence_reached": convergence,
        "stop_reason": stop_reason,
    }


def evaluate_quality(
    model_scores: dict[str, Any],
    delta_pack: dict[str, Any],
    thresholds: QualityThresholds | None = None,
    format_qa_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    t = thresholds or QualityThresholds()

    judge_margin = _safe_float(model_scores.get("judge_margin"), 0.05)
    term_hit = _safe_float(model_scores.get("term_hit"), 0.90)
    critical_changed = _critical_section_changed(delta_pack, t)

    expansion_used = (
        judge_margin < t.judge_margin or term_hit < t.term_hit or critical_changed
    )

    # Check format fidelity from QA results
    if format_qa_results:
        for _fname, qa in format_qa_results.items():
            score = _safe_float(qa.get("format_fidelity_score"), 1.0)
            if score < t.format_fidelity_min:
                expansion_used = True
                break

    result: dict[str, Any] = {
        "judge_margin": round(judge_margin, 4),
        "term_hit": round(term_hit, 4),
        "critical_section_changed": critical_changed,
        "expansion_used": expansion_used,
    }
    if format_qa_results:
        result["format_qa_results"] = format_qa_results
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-scores-json", required=True)
    parser.add_argument("--delta-pack-json", required=True)
    parser.add_argument("--estimated-minutes", type=float)
    args = parser.parse_args()

    model_scores = json.loads(args.model_scores_json)
    delta_pack = json.loads(args.delta_pack_json)

    result = evaluate_quality(model_scores=model_scores, delta_pack=delta_pack)
    if args.estimated_minutes is not None:
        runtime_timeout, flags = compute_runtime_timeout(args.estimated_minutes)
        result["runtime_timeout_minutes"] = runtime_timeout
        result["status_flags"] = flags

    print(json.dumps({"ok": True, "data": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

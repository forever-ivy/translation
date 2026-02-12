#!/usr/bin/env python3
"""Write V5.2 artifact bundle into the verify folder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from docx import Document

from scripts.compose_docx_from_draft import build_doc

SYSTEM_DIR_NAME = ".system"


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _text_to_lines(text: str) -> list[str]:
    return [line.rstrip() for line in (text or "").splitlines()]


def _write_docx(path: Path, title: str, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_heading(title, level=1)
    for line in lines:
        stripped = line.strip()
        if not stripped:
            doc.add_paragraph("")
            continue
        if stripped.startswith("## "):
            doc.add_heading(stripped[3:], level=2)
            continue
        if stripped.startswith("- "):
            doc.add_paragraph(stripped[2:], style="List Bullet")
            continue
        doc.add_paragraph(stripped)
    doc.save(str(path))


def build_review_brief_lines(
    *,
    task_type: str,
    quality_report: dict[str, Any],
    status_flags: list[str],
    review_questions: list[str],
) -> list[str]:
    rounds = quality_report.get("rounds", [])
    convergence = bool(quality_report.get("convergence_reached"))
    stop_reason = quality_report.get("stop_reason", "unknown")
    lines = [
        f"Task type: {task_type}",
        f"Convergence reached: {convergence}",
        f"Stop reason: {stop_reason}",
        f"Status flags: {', '.join(status_flags) if status_flags else 'none'}",
        "",
        "## Round Results",
    ]
    if not rounds:
        lines.append("- No rounds produced.")
    else:
        for item in rounds:
            lines.append(
                f"- Round {item.get('round')}: pass={item.get('pass')} codex={item.get('codex_pass')} gemini={item.get('gemini_pass')}"
            )
            unresolved = item.get("unresolved") or []
            if unresolved:
                lines.append(f"-   unresolved: {', '.join(unresolved)}")

    lines.extend(["", "## Questions / Notes"])
    if review_questions:
        for q in review_questions:
            lines.append(f"- {q}")
    else:
        lines.append("- No extra notes.")
    lines.extend(
        [
            "",
            "## Manual Policy",
            "- Output is in _VERIFY only.",
            "- System will not auto-move files to final folder.",
            "- After manual validation, use command: ok (status only).",
        ]
    )
    return lines


def _ensure_change_log_text(change_log_points: list[str], task_type: str) -> str:
    if change_log_points:
        body = "\n".join(f"- {x}" for x in change_log_points if str(x).strip())
    else:
        body = "- No explicit change log points were returned by model."
    return "\n".join(
        [
            f"# Change Log ({task_type})",
            "",
            body,
            "",
            "## Delivery Note",
            "- This artifact is generated in _VERIFY only.",
            "- Manual move is required for final delivery.",
        ]
    )


def write_artifacts(
    *,
    review_dir: str,
    draft_a_template_path: str | None,
    delta_pack: dict[str, Any],
    model_scores: dict[str, Any],
    quality: dict[str, Any],
    quality_report: dict[str, Any],
    job_id: str,
    task_type: str,
    confidence: float,
    estimated_minutes: int,
    runtime_timeout_minutes: int,
    iteration_count: int,
    double_pass: bool,
    status_flags: list[str],
    candidate_files: list[dict[str, Any]],
    review_questions: list[str],
    draft_payload: dict[str, Any] | None = None,
    plan_payload: dict[str, Any] | None = None,
) -> dict[str, str]:
    review = Path(review_dir)
    system = review / SYSTEM_DIR_NAME
    review.mkdir(parents=True, exist_ok=True)
    system.mkdir(parents=True, exist_ok=True)

    draft_payload = draft_payload or {}
    plan_payload = plan_payload or {}

    draft_a_text = str(draft_payload.get("draft_a_text") or draft_payload.get("final_text") or "")
    draft_b_text = str(draft_payload.get("draft_b_text") or draft_payload.get("final_reflow_text") or "")
    final_text = str(draft_payload.get("final_text") or draft_a_text)
    final_reflow_text = str(draft_payload.get("final_reflow_text") or draft_b_text or final_text)
    review_points = [str(x) for x in (draft_payload.get("review_brief_points") or [])]
    change_log_points = [str(x) for x in (draft_payload.get("change_log_points") or [])]

    draft_a_docx = review / "Draft A (Preserve).docx"
    draft_b_docx = review / "Draft B (Reflow).docx"
    final_docx = review / "Final.docx"
    final_reflow_docx = review / "Final-Reflow.docx"
    review_brief_docx = review / "Review Brief.docx"
    change_log_md = review / "Change Log.md"

    execution_plan_json = system / "execution_plan.json"
    quality_report_json = system / "quality_report.json"
    delta_summary_json = system / "Delta Summary.json"
    model_scores_json = system / "Model Scores.json"

    template = Path(draft_a_template_path) if draft_a_template_path else None
    if template and template.exists():
        build_doc(template, draft_a_docx, draft_a_text)
        build_doc(template, final_docx, final_text)
    else:
        _write_docx(
            draft_a_docx,
            "Draft A (Preserve)",
            _text_to_lines(draft_a_text or "No template was found. Use Draft B for manual formatting."),
        )
        _write_docx(final_docx, "Final", _text_to_lines(final_text))

    _write_docx(draft_b_docx, "Draft B (Reflow)", _text_to_lines(draft_b_text))
    _write_docx(final_reflow_docx, "Final-Reflow", _text_to_lines(final_reflow_text))

    review_lines = build_review_brief_lines(
        task_type=task_type,
        quality_report=quality_report,
        status_flags=status_flags,
        review_questions=(review_points + review_questions),
    )
    _write_docx(review_brief_docx, "Review Brief", review_lines)

    _write_text(change_log_md, _ensure_change_log_text(change_log_points, task_type))

    plan_write = {
        "job_id": job_id,
        "task_type": task_type,
        "confidence": confidence,
        "estimated_minutes": estimated_minutes,
        "runtime_timeout_minutes": runtime_timeout_minutes,
        "iteration_count": iteration_count,
        "double_pass": double_pass,
        "status_flags": status_flags,
        "candidate_files": candidate_files,
        "plan_payload": plan_payload,
    }
    _write_json(execution_plan_json, plan_write)
    _write_json(quality_report_json, quality_report)
    _write_json(delta_summary_json, delta_pack)
    _write_json(model_scores_json, model_scores)

    return {
        "final_docx": str(final_docx.resolve()),
        "final_reflow_docx": str(final_reflow_docx.resolve()),
        "draft_a_docx": str(draft_a_docx.resolve()),
        "draft_b_docx": str(draft_b_docx.resolve()),
        "review_brief_docx": str(review_brief_docx.resolve()),
        "change_log_md": str(change_log_md.resolve()),
        "execution_plan_json": str(execution_plan_json.resolve()),
        "quality_report_json": str(quality_report_json.resolve()),
        "delta_summary_json": str(delta_summary_json.resolve()),
        "model_scores_json": str(model_scores_json.resolve()),
    }

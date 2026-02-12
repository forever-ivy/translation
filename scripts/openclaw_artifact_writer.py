#!/usr/bin/env python3
"""Write OpenClaw artifacts into the review folder."""

from __future__ import annotations

import json
import shutil
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


def _write_docx(path: Path, title: str, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_heading(title, level=1)
    for line in lines:
        if not line:
            doc.add_paragraph("")
            continue
        if line.startswith("## "):
            doc.add_heading(line[3:], level=2)
            continue
        if line.startswith("- "):
            doc.add_paragraph(line[2:], style="List Bullet")
            continue
        doc.add_paragraph(line)
    doc.save(str(path))


def build_task_brief(
    *,
    job_id: str,
    task_type: str,
    confidence: float,
    estimated_minutes: int,
    runtime_timeout_minutes: int,
    iteration_count: int,
    double_pass: bool,
    status_flags: list[str],
    delta_pack: dict[str, Any],
    quality: dict[str, Any],
) -> str:
    added = len(delta_pack.get("added", []))
    removed = len(delta_pack.get("removed", []))
    modified = len(delta_pack.get("modified", []))

    lines = [
        "# Task Brief",
        "",
        f"- Job: {job_id}",
        f"- Task Type: {task_type}",
        f"- Classifier Confidence: {round(confidence, 4)}",
        f"- Estimated Minutes: {estimated_minutes}",
        f"- Runtime Timeout Minutes: {runtime_timeout_minutes}",
        f"- Iterations Used: {iteration_count}",
        f"- Double Pass: {double_pass}",
        f"- Status Flags: {', '.join(status_flags) if status_flags else 'none'}",
        "",
        "## Delta Overview",
        f"- Added blocks: {added}",
        f"- Removed blocks: {removed}",
        f"- Modified blocks: {modified}",
        f"- Judge margin: {quality.get('judge_margin', 'n/a')}",
        f"- Terminology hit: {quality.get('term_hit', 'n/a')}",
        f"- Expansion used: {quality.get('expansion_used', False)}",
        "",
        "## Manual SOP",
        "1. Open Draft A (Preserve).docx to keep original formatting.",
        "2. Compare with Draft B (Reflow).docx and Review Brief.docx to apply required edits.",
        "3. Save your final manual file as *_manual*.docx or *_edited*.docx in this review folder.",
        "4. Trigger approve_manual callback to deliver.",
    ]
    return "\n".join(lines)


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
        "## Self-check rounds",
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

    lines.extend(["", "## Questions To Confirm"])
    if review_questions:
        for q in review_questions:
            lines.append(f"- {q}")
    else:
        lines.append("- No additional confirmation needed.")
    return lines


def build_reflow_lines(
    *,
    task_type: str,
    candidate_files: list[dict[str, Any]],
    delta_pack: dict[str, Any],
) -> list[str]:
    lines = [
        f"Task Type: {task_type}",
        "This draft is a reflow helper. Keep final formatting edits in Draft A (Preserve).docx.",
        "",
        "## Candidate Files",
    ]
    for item in candidate_files[:15]:
        lines.append(
            f"- {item.get('name')} | {item.get('language')} | {item.get('version')} | {item.get('role')}"
        )

    lines.extend(["", "## Key Delta (Preview)"])
    for entry in (delta_pack.get("summary_by_section") or [])[:20]:
        section = entry.get("section", "General")
        lines.append(f"- [{section}]")
        for ch in entry.get("changes", []):
            lines.append(f"-   {ch}")
    if not (delta_pack.get("summary_by_section") or []):
        lines.append("- No structured delta was detected.")
    return lines


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
) -> dict[str, str]:
    review = Path(review_dir)
    system = review / SYSTEM_DIR_NAME
    review.mkdir(parents=True, exist_ok=True)
    system.mkdir(parents=True, exist_ok=True)

    draft_a_docx = review / "Draft A (Preserve).docx"
    draft_b_docx = review / "Draft B (Reflow).docx"
    review_brief_docx = review / "Review Brief.docx"
    legacy_draft_docx = review / "English V2 Draft.docx"

    task_brief_md = system / "Task Brief.md"
    delta_summary_json = system / "Delta Summary.json"
    model_scores_json = system / "Model Scores.json"
    quality_report_json = system / "quality_report.json"

    task_brief = build_task_brief(
        job_id=job_id,
        task_type=task_type,
        confidence=confidence,
        estimated_minutes=estimated_minutes,
        runtime_timeout_minutes=runtime_timeout_minutes,
        iteration_count=iteration_count,
        double_pass=double_pass,
        status_flags=status_flags,
        delta_pack=delta_pack,
        quality=quality,
    )
    review_brief_lines = build_review_brief_lines(
        task_type=task_type,
        quality_report=quality_report,
        status_flags=status_flags,
        review_questions=review_questions,
    )
    reflow_lines = build_reflow_lines(
        task_type=task_type,
        candidate_files=candidate_files,
        delta_pack=delta_pack,
    )

    if draft_a_template_path and Path(draft_a_template_path).exists():
        build_doc(Path(draft_a_template_path), draft_a_docx, "")
    else:
        _write_docx(
            draft_a_docx,
            "Draft A (Preserve)",
            [
                "No English template file was found.",
                "Use Draft B (Reflow).docx as working base, then format manually.",
            ],
        )

    _write_docx(draft_b_docx, "Draft B (Reflow)", reflow_lines)
    _write_docx(review_brief_docx, "Review Brief", review_brief_lines)
    _write_text(task_brief_md, task_brief)
    _write_json(delta_summary_json, delta_pack)
    _write_json(model_scores_json, model_scores)
    _write_json(quality_report_json, quality_report)

    # Backward compatibility with earlier manual flow references.
    try:
        shutil.copy2(draft_a_docx, legacy_draft_docx)
    except FileNotFoundError:
        pass

    return {
        "draft_a_docx": str(draft_a_docx.resolve()),
        "draft_b_docx": str(draft_b_docx.resolve()),
        "review_brief_docx": str(review_brief_docx.resolve()),
        "legacy_draft_docx": str(legacy_draft_docx.resolve()),
        "task_brief_md": str(task_brief_md.resolve()),
        "delta_summary_json": str(delta_summary_json.resolve()),
        "model_scores_json": str(model_scores_json.resolve()),
        "quality_report_json": str(quality_report_json.resolve()),
    }


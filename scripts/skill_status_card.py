#!/usr/bin/env python3
"""Build user-friendly status cards for WhatsApp responses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.attention_summary import attention_summary


def _read_intent_lang(review_dir: str) -> tuple[str, str]:
    if not review_dir:
        return "unknown", "unknown"
    plan_path = Path(review_dir) / ".system" / "execution_plan.json"
    if not plan_path.exists():
        return "unknown", "unknown"
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception:
        return "unknown", "unknown"

    intent = payload.get("plan_payload", {}).get("intent") or payload.get("intent") or {}
    src = str(intent.get("source_language") or "unknown").strip() or "unknown"
    tgt = str(intent.get("target_language") or "unknown").strip() or "unknown"
    return src, tgt


def _read_pipeline_version(review_dir: str) -> str:
    if not review_dir:
        return ""
    plan_path = Path(review_dir) / ".system" / "execution_plan.json"
    if not plan_path.exists():
        return ""
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    version = str(payload.get("pipeline_version") or "").strip()
    if version:
        return version
    plan_payload = payload.get("plan_payload") or {}
    if isinstance(plan_payload, dict):
        version = str((plan_payload.get("plan") or {}).get("pipeline_version") or "").strip()
        if version:
            return version
        version = str((plan_payload.get("meta") or {}).get("pipeline_version") or "").strip()
    return version


def _extract_missing(errors: list[str]) -> list[str]:
    out: list[str] = []
    for err in errors:
        token = str(err or "").strip()
        if token.startswith("missing:"):
            out.append(token.split(":", 1)[1].strip())
    return out


_STATUS_LABEL: dict[str, str] = {
    "collecting": "Collecting",
    "queued": "Queued",
    "received": "Received",
    "running": "Running...",
    "canceled": "\u26d4\ufe0f Canceled",
    "review_ready": "\u2705 Review ready",
    "needs_attention": "\u26a0\ufe0f Needs attention",
    "needs_revision": "\U0001f527 Needs revision",
    "missing_inputs": "\U0001f4ed Missing inputs",
    "verified": "\u2705 Verified",
    "failed": "\u274c Failed",
    "incomplete_input": "\U0001f4ed Incomplete input",
}


def _status_label(status: str) -> str:
    return _STATUS_LABEL.get(status, status)


def next_action_for_status(status: str, *, require_new: bool = True) -> str:
    status_norm = (status or "").strip().lower()
    if status_norm in {"collecting", "received", "missing_inputs", "needs_revision"}:
        return "run"
    if status_norm in {"queued"}:
        return "status"
    if status_norm in {"running"}:
        return "status"
    if status_norm in {"canceled"}:
        return "rerun | new" if require_new else "rerun"
    if status_norm in {"review_ready", "needs_attention", "failed", "incomplete_input"}:
        return "ok | no {reason} | rerun"
    if status_norm in {"verified"}:
        return "new" if require_new else "done"
    return "new" if require_new else "run"


def build_status_card(
    *,
    job: dict[str, Any],
    files_count: int,
    docx_count: int,
    multiple_hint: int = 0,
    require_new: bool = True,
    task_label: str = "",
    pending_action: str = "",
    pending_expires_at: str = "",
    final_uploads_count: int = 0,
    archived: bool = False,
    last_milestone: str = "",
    last_milestone_at: str = "",
    queue_state: str = "",
    queue_attempt: int = 0,
    queue_worker_id: str = "",
    queue_heartbeat_at: str = "",
    queue_last_error: str = "",
    queue_cancel_requested_at: str = "",
    queue_cancel_reason: str = "",
    queue_cancel_mode: str = "",
) -> str:
    job_id = str(job.get("job_id") or "unknown")
    status = str(job.get("status") or "unknown")
    pipeline_version = _read_pipeline_version(str(job.get("review_dir") or ""))

    errors_raw = job.get("errors_json") if isinstance(job.get("errors_json"), list) else []
    status_flags = job.get("status_flags_json") if isinstance(job.get("status_flags_json"), list) else []
    artifacts = job.get("artifacts_json") if isinstance(job.get("artifacts_json"), dict) else {}
    missing_inputs = _extract_missing(errors_raw)
    if missing_inputs:
        files_line = f"\U0001f4ce Missing: {', '.join(missing_inputs)}"
    else:
        files_line = f"\U0001f4ce Files: {files_count} (docx: {docx_count})"

    rounds = int(job.get("iteration_count") or 0)
    hint = f" (+{multiple_hint} pending)" if multiple_hint > 0 else ""

    lines = [
        "\U0001f4cb Task Status",
        "",
    ]
    if task_label:
        lines.append(f"\U0001f4cb {task_label}{hint}")
    elif status in {"collecting", "received"}:
        lines.append(f"\U0001f4cb New task{hint}")
    else:
        lines.append(f"\U0001f194 {job_id}{hint}")

    detail_lines: list[str] = [f"\U0001f4cc Stage: {_status_label(status)}"]

    why_lines: list[str] = []
    if status.strip().lower() in {"needs_attention", "failed"}:
        why_lines = attention_summary(
            status=status,
            review_dir=str(job.get("review_dir") or ""),
            status_flags=[str(x) for x in status_flags],
            errors=[str(x) for x in errors_raw],
            artifacts={k: v for k, v in artifacts.items()} if isinstance(artifacts, dict) else {},
            max_items=3,
        )
    if why_lines:
        detail_lines.append("Why:")
        detail_lines.extend([f"- {x}" for x in why_lines[:3]])

    queue_state_norm = str(queue_state or "").strip().lower()
    queue_worker_norm = str(queue_worker_id or "").strip()
    queue_hb_norm = str(queue_heartbeat_at or "").strip()
    queue_err_norm = str(queue_last_error or "").strip()
    cancel_at_norm = str(queue_cancel_requested_at or "").strip()
    cancel_reason_norm = str(queue_cancel_reason or "").strip()
    cancel_mode_norm = str(queue_cancel_mode or "").strip().lower()
    if cancel_at_norm:
        line = "\u26d4\ufe0f Cancel: requested"
        if cancel_mode_norm:
            line += f" ({cancel_mode_norm})"
        detail_lines.append(line)
        if cancel_reason_norm:
            detail_lines.append(f"Reason: {cancel_reason_norm}")
    if queue_state_norm:
        qline = f"\U0001f4e5 Queue: {queue_state_norm}"
        if int(queue_attempt or 0) > 0:
            qline += f" \u00b7 attempt {int(queue_attempt)}"
        if queue_state_norm == "running" and queue_worker_norm:
            qline += f" \u00b7 {queue_worker_norm}"
        detail_lines.append(qline)
        if queue_hb_norm:
            detail_lines.append(f"\u23f1\ufe0f Heartbeat: {queue_hb_norm}")
        if queue_err_norm and queue_state_norm in {"failed", "canceled"}:
            detail_lines.append(f"\u26a0\ufe0f Queue error: {queue_err_norm}")

    last_ms = str(last_milestone or "").strip()
    last_at = str(last_milestone_at or "").strip()
    if last_ms:
        line = f"\U0001f553 Last: {last_ms}"
        if last_at:
            line += f" \u00b7 {last_at}"
        detail_lines.append(line)

    detail_lines.extend(
        [
            (f"\U0001f3e2 Company: {job.get('kb_company')}" if str(job.get("kb_company") or "").strip() else ""),
            (f"\u23f3 Pending: {pending_action}" if str(pending_action or "").strip() else ""),
            (f"\u23f1\ufe0f Expires: {pending_expires_at}" if str(pending_expires_at or "").strip() else ""),
            files_line,
            (f"\U0001f4e5 Final uploads: {int(final_uploads_count)}" if int(final_uploads_count or 0) > 0 else ""),
            (f"\U0001f4c1 Archived: {'yes' if archived else 'no'}"),
            f"\U0001f504 Rounds: {rounds}",
            (f"\U0001f3f7\ufe0f Version: {pipeline_version}" if pipeline_version else ""),
            f"\u23ed\ufe0f Next: {next_action_for_status(status, require_new=require_new)}",
        ]
    )

    lines.extend(detail_lines)
    return "\n".join([ln for ln in lines if str(ln).strip()])


def no_active_job_hint(*, require_new: bool = True) -> str:
    if require_new:
        return "\U0001f4ed No active task. Send: new"
    return "\U0001f4ed No active task. Send files first, then run."

#!/usr/bin/env python3
"""OpenClaw skill: contextual command handling (V5.2)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.v4_pipeline import run_job_pipeline
from scripts.v4_runtime import (
    DEFAULT_KB_ROOT,
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_WORK_ROOT,
    db_connect,
    ensure_runtime_paths,
    get_job,
    get_sender_active_job,
    latest_actionable_job,
    list_actionable_jobs_for_sender,
    record_event,
    send_whatsapp_message,
    set_sender_active_job,
    update_job_status,
)


def _parse_command(text: str) -> tuple[str, str | None, str]:
    parts = [p for p in text.strip().split(" ") if p]
    if not parts:
        return "", None, ""
    raw_action = parts[0].lower()

    # Backward compatibility layer.
    if raw_action == "approve":
        explicit_job = parts[1] if len(parts) >= 2 else None
        return "ok", explicit_job, ""
    if raw_action == "reject":
        explicit_job = parts[1] if len(parts) >= 2 else None
        reason = " ".join(parts[2:]).strip() if len(parts) > 2 else "manual_rejected"
        return "no", explicit_job, reason

    action = raw_action
    if action not in {"run", "status", "ok", "no", "rerun"}:
        return "", None, ""

    explicit_job: str | None = None
    reason = ""
    if action in {"run", "status", "ok", "rerun"}:
        if len(parts) >= 2 and parts[1].startswith("job_"):
            explicit_job = parts[1]
    if action == "no":
        if len(parts) >= 2 and parts[1].startswith("job_"):
            explicit_job = parts[1]
            reason = " ".join(parts[2:]).strip()
        else:
            reason = " ".join(parts[1:]).strip()
    return action, explicit_job, reason


def _send_and_record(
    conn,
    *,
    job_id: str,
    milestone: str,
    target: str,
    message: str,
    dry_run: bool,
) -> dict[str, Any]:
    send_result = send_whatsapp_message(target=target, message=message, dry_run=dry_run)
    record_event(
        conn,
        job_id=job_id,
        milestone=milestone,
        payload={"target": target, "message": message, "send_result": send_result},
    )
    return send_result


def _resolve_job(
    conn,
    *,
    sender: str,
    explicit_job_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    sender_norm = (sender or "").strip()
    if explicit_job_id:
        job = get_job(conn, explicit_job_id)
        return job, {"source": "explicit", "multiple": 0}

    if sender_norm:
        active_job_id = get_sender_active_job(conn, sender=sender_norm)
        if active_job_id:
            job = get_job(conn, active_job_id)
            if job and job.get("status") not in {"verified", "failed"}:
                return job, {"source": "active_map", "multiple": 0}

        sender_jobs = list_actionable_jobs_for_sender(conn, sender=sender_norm, limit=20)
        if sender_jobs:
            selected = sender_jobs[0]
            return selected, {"source": "sender_latest", "multiple": max(0, len(sender_jobs) - 1)}

    latest = latest_actionable_job(conn)
    if latest:
        return latest, {"source": "global_latest", "multiple": 0}
    return None, {"source": "none", "multiple": 0}


def _status_text(job: dict[str, Any], *, multiple_hint: int = 0) -> str:
    errors = job.get("errors_json") if isinstance(job.get("errors_json"), list) else []
    errors_text = ", ".join(errors[:3]) if errors else "none"
    suffix = ""
    if multiple_hint > 0:
        suffix = f" | Note: {multiple_hint} more pending job(s)."
    return (
        f"[{job['job_id']}] status={job.get('status')} task_type={job.get('task_type') or 'n/a'} "
        f"iterations={job.get('iteration_count', 0)} verify_dir={job.get('review_dir')} errors={errors_text}{suffix}"
    )


def handle_command(
    *,
    command_text: str,
    work_root: Path,
    kb_root: Path,
    target: str,
    sender: str = "",
    dry_run_notify: bool = False,
) -> dict[str, Any]:
    paths = ensure_runtime_paths(work_root)
    conn = db_connect(paths)
    action, explicit_job_id, reason = _parse_command(command_text)
    if not action:
        conn.close()
        return {"ok": False, "error": "unsupported_command"}

    job, resolve_meta = _resolve_job(conn, sender=sender, explicit_job_id=explicit_job_id)
    if action == "status" and not job:
        send_result = send_whatsapp_message(
            target=target,
            message="No active job found. Send files first, then send: run",
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "status": "no_active_job", "send_result": send_result}
    if not job:
        send_result = send_whatsapp_message(
            target=target,
            message="No active job found. Please send files first.",
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": False, "error": "job_not_found", "send_result": send_result}

    job_id = str(job["job_id"])
    if sender.strip():
        set_sender_active_job(conn, sender=sender.strip(), job_id=job_id)

    if action == "status":
        msg = _status_text(job, multiple_hint=resolve_meta.get("multiple", 0))
        _send_and_record(
            conn,
            job_id=job_id,
            milestone="status",
            target=target,
            message=msg,
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "job_id": job_id, "status": str(job.get("status")), "resolve": resolve_meta}

    if action == "ok":
        update_job_status(conn, job_id=job_id, status="verified", errors=[])
        _send_and_record(
            conn,
            job_id=job_id,
            milestone="verified",
            target=target,
            message=(
                f"[{job_id}] verified. Auto-delivery is disabled by policy. "
                "Please manually move the final file to your destination folder."
            ),
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "job_id": job_id, "status": "verified"}

    if action == "no":
        reason_norm = reason.strip() or "needs_manual_revision"
        update_job_status(conn, job_id=job_id, status="needs_revision", errors=[reason_norm])
        _send_and_record(
            conn,
            job_id=job_id,
            milestone="needs_attention",
            target=target,
            message=f"[{job_id}] marked needs_revision. Reason: {reason_norm}",
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "job_id": job_id, "status": "needs_revision", "reason": reason_norm}

    if action in {"run", "rerun"}:
        update_job_status(conn, job_id=job_id, status="received", errors=[])
        _send_and_record(
            conn,
            job_id=job_id,
            milestone="run_accepted",
            target=target,
            message=f"[{job_id}] run_accepted. Starting execution now.",
            dry_run=dry_run_notify,
        )
        conn.close()
        result = run_job_pipeline(
            job_id=job_id,
            work_root=work_root,
            kb_root=kb_root,
            notify_target=target,
            dry_run_notify=dry_run_notify,
        )
        return {"ok": bool(result.get("ok")), "job_id": job_id, "result": result}

    conn.close()
    return {"ok": False, "error": "unreachable"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", required=True, help="run|status|ok|no {reason}|rerun")
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    parser.add_argument("--kb-root", default=str(DEFAULT_KB_ROOT))
    parser.add_argument("--target", default=DEFAULT_NOTIFY_TARGET)
    parser.add_argument("--sender", default="")
    parser.add_argument("--dry-run-notify", action="store_true")
    args = parser.parse_args()

    result = handle_command(
        command_text=args.command,
        work_root=Path(args.work_root),
        kb_root=Path(args.kb_root),
        target=args.target,
        sender=args.sender,
        dry_run_notify=args.dry_run_notify,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

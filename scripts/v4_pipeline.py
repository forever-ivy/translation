#!/usr/bin/env python3
"""End-to-end V5.2 pipeline helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.openclaw_translation_orchestrator import run as run_translation
from scripts.task_bundle_builder import infer_language, infer_role, infer_version
from scripts.v4_kb import retrieve_kb, sync_kb
from scripts.v4_runtime import (
    DEFAULT_NOTIFY_TARGET,
    RuntimePaths,
    add_job_file,
    append_log,
    db_connect,
    ensure_runtime_paths,
    get_job,
    json_dumps,
    list_job_files,
    record_event,
    set_sender_active_job,
    send_whatsapp_message,
    update_job_plan,
    update_job_result,
    update_job_status,
    write_job,
)


def notify_milestone(
    *,
    paths: RuntimePaths,
    conn,
    job_id: str,
    milestone: str,
    message: str,
    target: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    tgt = target or DEFAULT_NOTIFY_TARGET
    result = send_whatsapp_message(target=tgt, message=message, dry_run=dry_run)
    payload = {"target": tgt, "message": message, "send_result": result}
    record_event(conn, job_id=job_id, milestone=milestone, payload=payload)
    append_log(paths, "events.log", f"{milestone}\t{job_id}\t{message}")
    return result


def create_job(
    *,
    source: str,
    sender: str,
    subject: str,
    message_text: str,
    inbox_dir: Path,
    job_id: str,
    work_root: Path,
    active_sender: str | None = None,
) -> dict[str, Any]:
    paths = ensure_runtime_paths(work_root)
    conn = db_connect(paths)
    review_dir = paths.review_root / job_id
    review_dir.mkdir(parents=True, exist_ok=True)
    write_job(
        conn,
        job_id=job_id,
        source=source,
        sender=sender,
        subject=subject,
        message_text=message_text,
        status="received",
        inbox_dir=inbox_dir,
        review_dir=review_dir,
    )
    set_sender_active_job(conn, sender=sender, job_id=job_id)
    if active_sender and active_sender.strip():
        set_sender_active_job(conn, sender=active_sender.strip(), job_id=job_id)
    record_event(conn, job_id=job_id, milestone="received", payload={"source": source, "sender": sender, "subject": subject})
    conn.close()
    return {
        "job_id": job_id,
        "source": source,
        "from": sender,
        "subject": subject,
        "message_text": message_text,
        "inbox_dir": str(inbox_dir.resolve()),
        "review_dir": str(review_dir.resolve()),
        "status": "received",
    }


def attach_file_to_job(*, work_root: Path, job_id: str, path: Path, mime_type: str = "") -> None:
    paths = ensure_runtime_paths(work_root)
    conn = db_connect(paths)
    add_job_file(conn, job_id=job_id, path=path, mime_type=mime_type)
    conn.close()


def _build_candidates(job_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in job_files:
        p = Path(item["path"])
        if p.suffix.lower() != ".docx":
            continue
        candidates.append(
            {
                "path": str(p.resolve()),
                "name": p.name,
                "language": infer_language(p),
                "version": infer_version(p),
                "role": infer_role(p),
            }
        )
    return candidates


def run_job_pipeline(
    *,
    job_id: str,
    work_root: Path,
    kb_root: Path,
    notify_target: str | None = None,
    dry_run_notify: bool = False,
) -> dict[str, Any]:
    paths = ensure_runtime_paths(work_root)
    conn = db_connect(paths)
    job = get_job(conn, job_id)
    if not job:
        raise ValueError(f"Job not found: {job_id}")

    update_job_status(conn, job_id=job_id, status="running", errors=[])
    set_sender_active_job(conn, sender=job.get("sender", ""), job_id=job_id)

    notify_milestone(
        paths=paths,
        conn=conn,
        job_id=job_id,
        milestone="kb_sync_started",
        message=f"[{job_id}] kb_sync_started",
        target=notify_target,
        dry_run=dry_run_notify,
    )
    kb_report_path = paths.kb_system_root / "kb_sync_latest.json"
    kb_report = sync_kb(conn=conn, kb_root=kb_root, report_path=kb_report_path)
    notify_milestone(
        paths=paths,
        conn=conn,
        job_id=job_id,
        milestone="kb_sync_done",
        message=f"[{job_id}] kb_sync_done created={kb_report['created']} updated={kb_report['updated']} skipped={kb_report['skipped']}",
        target=notify_target,
        dry_run=dry_run_notify,
    )

    files = list_job_files(conn, job_id)
    candidates = _build_candidates(files)
    review_dir = Path(job["review_dir"]).resolve()
    review_dir.mkdir(parents=True, exist_ok=True)

    if not candidates:
        update_job_status(conn, job_id=job_id, status="incomplete_input", errors=["no_docx_attachments"])
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="failed",
            message=f"[{job_id}] failed: no DOCX attachments found.",
            target=notify_target,
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": False, "job_id": job_id, "status": "incomplete_input", "errors": ["no_docx_attachments"]}

    query = " ".join([job.get("subject", ""), job.get("message_text", "")]).strip()
    kb_hits = retrieve_kb(conn=conn, query=query, task_type="", top_k=8) if query else []
    record_event(
        conn,
        job_id=job_id,
        milestone="kb_retrieve",
        payload={"query": query, "hit_count": len(kb_hits), "hits": kb_hits[:8]},
    )

    meta = {
        "job_id": job_id,
        "root_path": str(paths.work_root.resolve()),
        "review_dir": str(review_dir),
        "source": job.get("source", ""),
        "sender": job.get("sender", ""),
        "subject": job.get("subject", ""),
        "message_text": job.get("message_text", ""),
        "candidate_files": candidates,
        "knowledge_context": kb_hits,
        "max_rounds": 3,
        "codex_available": True,
        "gemini_available": True,
    }

    plan = run_translation(meta, plan_only=True)
    intent = plan.get("intent") or {}
    if plan.get("plan"):
        p = plan["plan"]
        update_job_plan(
            conn,
            job_id=job_id,
            status=plan.get("status", "planned"),
            task_type=p.get("task_type", ""),
            confidence=float(p.get("confidence", 0.0)),
            estimated_minutes=int(p.get("estimated_minutes", 0)),
            runtime_timeout_minutes=int(p.get("time_budget_minutes", 0)),
        )
    plan_file = review_dir / ".system" / "execution_plan.json"
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_text(json_dumps(plan), encoding="utf-8")
    notify_milestone(
        paths=paths,
        conn=conn,
        job_id=job_id,
        milestone="intent_classified",
        message=(
            f"[{job_id}] intent_classified: {plan.get('plan', {}).get('task_type', 'unknown')} "
            f"est={plan.get('estimated_minutes', 0)}m timeout={plan.get('runtime_timeout_minutes', 0)}m "
            f"missing={len(intent.get('missing_inputs', []))}"
        ),
        target=notify_target,
        dry_run=dry_run_notify,
    )
    if plan.get("status") == "missing_inputs":
        missing = intent.get("missing_inputs") or []
        update_job_status(conn, job_id=job_id, status="missing_inputs", errors=[f"missing:{x}" for x in missing])
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="missing_inputs",
            message=f"[{job_id}] missing inputs: {', '.join(missing) if missing else 'unknown'}. Upload files then send 'run'.",
            target=notify_target,
            dry_run=dry_run_notify,
        )
        conn.close()
        return {
            "ok": False,
            "job_id": job_id,
            "status": "missing_inputs",
            "intent": intent,
            "errors": [f"missing:{x}" for x in missing],
        }

    notify_milestone(
        paths=paths,
        conn=conn,
        job_id=job_id,
        milestone="running",
        message=f"[{job_id}] running: Codex+Gemini up to 3 rounds.",
        target=notify_target,
        dry_run=dry_run_notify,
    )
    result = run_translation(meta, plan_only=False)

    update_job_result(
        conn,
        job_id=job_id,
        status=result.get("status", "failed"),
        iteration_count=int(result.get("iteration_count", 0)),
        double_pass=bool(result.get("double_pass")),
        status_flags=list(result.get("status_flags", [])),
        artifacts=dict(result.get("artifacts", {})),
        errors=list(result.get("errors", [])),
    )

    if result.get("status") == "review_ready":
        rounds = (((result.get("quality_report") or {}).get("rounds")) or [])
        for rd in rounds:
            rd_no = rd.get("round")
            if not rd_no:
                continue
            notify_milestone(
                paths=paths,
                conn=conn,
                job_id=job_id,
                milestone=f"round_{rd_no}_done",
                message=f"[{job_id}] round_{rd_no}_done codex_pass={rd.get('codex_pass')} gemini_pass={rd.get('gemini_pass')}",
                target=notify_target,
                dry_run=dry_run_notify,
            )
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="review_ready",
            message=(
                f"[{job_id}] review_ready. Verify files in {result.get('review_dir')}. "
                "After your manual checks, send: ok | no {reason} | rerun"
            ),
            target=notify_target,
            dry_run=dry_run_notify,
        )
    elif result.get("status") in {"needs_attention", "failed"}:
        rounds = (((result.get("quality_report") or {}).get("rounds")) or [])
        for rd in rounds:
            rd_no = rd.get("round")
            if not rd_no:
                continue
            notify_milestone(
                paths=paths,
                conn=conn,
                job_id=job_id,
                milestone=f"round_{rd_no}_done",
                message=f"[{job_id}] round_{rd_no}_done codex_pass={rd.get('codex_pass')} gemini_pass={rd.get('gemini_pass')}",
                target=notify_target,
                dry_run=dry_run_notify,
            )
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="needs_attention",
            message=f"[{job_id}] needs_attention. Send: status | rerun | no {{reason}}",
            target=notify_target,
            dry_run=dry_run_notify,
        )
    else:
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="failed",
            message=f"[{job_id}] failed. Check .system/openclaw_result.json",
            target=notify_target,
            dry_run=dry_run_notify,
        )

    conn.close()
    return result

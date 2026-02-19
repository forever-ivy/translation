#!/usr/bin/env python3
"""OpenClaw skill: execute translation with OpenClaw routing self-check."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.attention_summary import attention_summary
from scripts.openclaw_translation_orchestrator import run as run_translation
from scripts.task_bundle_builder import infer_language, infer_role, infer_version
from scripts.v4_kb import retrieve_kb
from scripts.v4_runtime import (
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_WORK_ROOT,
    db_connect,
    ensure_runtime_paths,
    get_job,
    list_job_files,
    record_event,
    send_message,
    update_job_result,
)


def _build_candidates(job_files: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in job_files:
        p = Path(item["path"])
        if p.suffix.lower() != ".docx":
            continue
        out.append(
            {
                "path": str(p.resolve()),
                "name": p.name,
                "language": infer_language(p),
                "version": infer_version(p),
                "role": infer_role(p),
            }
        )
    return out


def _notify(conn, *, job_id: str, milestone: str, target: str, message: str, dry_run: bool) -> None:
    send_result = send_message(target=target, message=message, dry_run=dry_run)
    record_event(conn, job_id=job_id, milestone=milestone, payload={"target": target, "message": message, "send_result": send_result})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    parser.add_argument("--target", default=DEFAULT_NOTIFY_TARGET)
    parser.add_argument("--dry-run-notify", action="store_true")
    args = parser.parse_args()

    paths = ensure_runtime_paths(Path(args.work_root))
    conn = db_connect(paths)
    job = get_job(conn, args.job_id)
    if not job:
        conn.close()
        print(json.dumps({"ok": False, "error": f"job_not_found:{args.job_id}"}, ensure_ascii=False))
        return 2

    files = list_job_files(conn, args.job_id)
    candidates = _build_candidates(files)
    if not candidates:
        update_job_result(
            conn,
            job_id=args.job_id,
            status="incomplete_input",
            iteration_count=0,
            double_pass=False,
            status_flags=[],
            artifacts={},
            errors=["no_docx_attachments"],
        )
        _notify(
            conn,
            job_id=args.job_id,
            milestone="failed",
            target=args.target,
            message=f"[{args.job_id}] failed: no DOCX attachments found.",
            dry_run=args.dry_run_notify,
        )
        conn.close()
        print(json.dumps({"ok": False, "status": "incomplete_input", "job_id": args.job_id}, ensure_ascii=False))
        return 1

    query = " ".join([job.get("subject", ""), job.get("message_text", "")]).strip()
    kb_hits = retrieve_kb(conn=conn, query=query, task_type=str(job.get("task_type", "")), top_k=8) if query else []

    _notify(
        conn,
        job_id=args.job_id,
        milestone="running",
        target=args.target,
        message=f"[{args.job_id}] running translation execution.",
        dry_run=args.dry_run_notify,
    )

    meta = {
        "job_id": args.job_id,
        "source": job.get("source", ""),
        "sender": job.get("sender", ""),
        "subject": job.get("subject", ""),
        "message_text": job.get("message_text", ""),
        "task_type": job.get("task_type", "") or "",
        "root_path": str(paths.work_root.resolve()),
        "review_dir": str(Path(job["review_dir"]).resolve()),
        "candidate_files": candidates,
        "knowledge_context": kb_hits,
        "max_rounds": 3,
        "codex_available": True,
        "gemini_available": True,
    }
    result = run_translation(meta, plan_only=False)
    update_job_result(
        conn,
        job_id=args.job_id,
        status=result.get("status", "failed"),
        iteration_count=int(result.get("iteration_count", 0)),
        double_pass=bool(result.get("double_pass")),
        status_flags=list(result.get("status_flags", [])),
        artifacts=dict(result.get("artifacts", {})),
        errors=list(result.get("errors", [])),
    )

    if result.get("status") == "review_ready":
        _notify(
            conn,
            job_id=args.job_id,
            milestone="review_ready",
            target=args.target,
            message=(
                f"[{args.job_id}] review_ready. Verify files in {result.get('review_dir')} "
                "then send: ok | no {reason} | rerun"
            ),
            dry_run=args.dry_run_notify,
        )
    elif result.get("status") == "needs_attention":
        why_lines = attention_summary(
            status=str(result.get("status") or ""),
            review_dir=str(result.get("review_dir") or ""),
            status_flags=[str(x) for x in (result.get("status_flags") or [])],
            errors=[str(x) for x in (result.get("errors") or [])],
            artifacts=dict(result.get("artifacts") or {}),
            max_items=3,
        )
        why_block = ""
        if why_lines:
            why_block = "\nWhy:\n" + "\n".join(f"- {x}" for x in why_lines[:3])
        _notify(
            conn,
            job_id=args.job_id,
            milestone="needs_attention",
            target=args.target,
            message=(
                f"[{args.job_id}] needs_attention\n"
                f"Folder: {result.get('review_dir')}"
                + why_block
                + f"\nSend: status {args.job_id} | rerun {args.job_id} | no {{reason}}"
            ),
            dry_run=args.dry_run_notify,
        )
    else:
        why_lines = attention_summary(
            status=str(result.get("status") or "failed"),
            review_dir=str(result.get("review_dir") or ""),
            status_flags=[str(x) for x in (result.get("status_flags") or [])],
            errors=[str(x) for x in (result.get("errors") or [])],
            artifacts=dict(result.get("artifacts") or {}),
            max_items=3,
        )
        why_block = ""
        if why_lines:
            why_block = "\nWhy:\n" + "\n".join(f"- {x}" for x in why_lines[:3])
        _notify(
            conn,
            job_id=args.job_id,
            milestone="failed",
            target=args.target,
            message=(
                f"[{args.job_id}] failed\n"
                f"Folder: {result.get('review_dir')}"
                + why_block
                + f"\nSend: rerun {args.job_id} | status {args.job_id}"
            ),
            dry_run=args.dry_run_notify,
        )

    conn.close()
    print(json.dumps({"ok": result.get("ok", False), "result": result}, ensure_ascii=False))
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())

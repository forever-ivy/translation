#!/usr/bin/env python3
"""OpenClaw skill: contextual command handling (V5.2)."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.v4_pipeline import attach_file_to_job
from scripts.skill_status_card import build_status_card, no_active_job_hint
from scripts.v4_runtime import (
    DEFAULT_KB_ROOT,
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_WORK_ROOT,
    add_job_final_upload,
    clear_job_pending_action,
    compute_sha256,
    cancel_job_run,
    db_connect,
    ensure_runtime_paths,
    enqueue_run_job,
    compute_sha256,
    get_job,
    get_job_interaction,
    get_last_event,
    get_active_queue_item,
    get_sender_active_job,
    get_last_kb_company_for_sender,
    list_actionable_jobs_for_sender,
    list_job_final_uploads,
    list_job_files,
    make_job_id,
    mark_job_archived,
    latest_actionable_job,
    record_event,
    send_message,
    set_sender_active_job,
    set_job_archive_project,
    set_job_kb_company,
    set_job_pending_action,
    slugify_identifier,
    update_job_status,
    utc_now_iso,
    write_job,
)

ACTIVE_JOB_STATUSES = {"collecting", "received", "missing_inputs", "needs_revision", "discarded"}
RUN_ALLOWED_STATUSES = {"collecting", "received", "missing_inputs", "needs_revision"}
RERUN_ALLOWED_STATUSES = {"collecting", "received", "missing_inputs", "needs_revision", "review_ready", "needs_attention", "failed", "incomplete_input", "canceled", "discarded"}
DISCARD_ALLOWED_STATUSES = {"collecting", "received", "missing_inputs", "needs_revision", "review_ready", "needs_attention", "failed", "incomplete_input", "canceled", "verified"}

BATCH_PARENT_FLAG = "batch_parent"
BATCH_PARENT_STATUS = "batch_dispatched"


def _env_truthy(name: str, default: str = "0") -> bool:
    raw = str(os.getenv(name, default)).strip().lower()
    return raw not in {"0", "false", "off", "no", ""}


def _is_spreadsheet_path(path: Path) -> bool:
    return path.suffix.lower() in {".xlsx", ".csv"}


def _safe_copy_dest(dest_dir: Path, src: Path, *, index: int) -> Path:
    """Choose a non-colliding destination path under dest_dir for src."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = src.name
    target = dest_dir / base
    if not target.exists():
        return target
    stem = src.stem
    suffix = src.suffix
    ts = int(datetime.now(UTC).timestamp())
    return dest_dir / f"{stem}_{index}_{ts}{suffix}"


def _artifact_is_batch_parent(job: dict[str, Any]) -> bool:
    status = str(job.get("status") or "").strip().lower()
    if status == BATCH_PARENT_STATUS:
        return True
    flags = job.get("status_flags_json") if isinstance(job.get("status_flags_json"), list) else []
    if any(str(x).strip() == BATCH_PARENT_FLAG for x in flags):
        return True
    artifacts = job.get("artifacts_json") if isinstance(job.get("artifacts_json"), dict) else {}
    batch = artifacts.get("batch") if isinstance(artifacts.get("batch"), dict) else {}
    return bool(batch.get("child_jobs"))


def _batch_child_job_ids(job: dict[str, Any]) -> list[str]:
    artifacts = job.get("artifacts_json") if isinstance(job.get("artifacts_json"), dict) else {}
    batch = artifacts.get("batch") if isinstance(artifacts.get("batch"), dict) else {}
    out: list[str] = []
    for item in (batch.get("child_jobs") or []):
        if not isinstance(item, dict):
            continue
        cid = str(item.get("child_job_id") or "").strip()
        if cid:
            out.append(cid)
    return out


def _split_multi_xlsx_job_and_enqueue(
    conn,
    *,
    paths,
    parent_job: dict[str, Any],
    files: list[dict[str, Any]],
    kb_company: str,
    target: str,
    sender: str,
    dry_run_notify: bool,
) -> dict[str, Any]:
    parent_job_id = str(parent_job.get("job_id") or "").strip()
    sender_norm = (sender or "").strip()
    kb_company_norm = (kb_company or "").strip()

    # Guard: do not split twice.
    parent_artifacts = parent_job.get("artifacts_json") if isinstance(parent_job.get("artifacts_json"), dict) else {}
    existing_batch = parent_artifacts.get("batch") if isinstance(parent_artifacts.get("batch"), dict) else {}
    if existing_batch.get("child_jobs"):
        return {"ok": True, "already_split": True, "parent_job_id": parent_job_id, "batch": existing_batch}

    copy_mode = str(os.getenv("OPENCLAW_BATCH_SPLIT_COPY_MODE", "copy")).strip().lower() or "copy"
    if copy_mode not in {"copy"}:
        copy_mode = "copy"

    child_entries: list[dict[str, Any]] = []
    queued_children: list[dict[str, Any]] = []
    failures: list[str] = []

    # Keep parent as active job.
    if sender_norm:
        set_sender_active_job(conn, sender=sender_norm, job_id=parent_job_id)

    for idx, item in enumerate(files, start=1):
        src_path = Path(str(item.get("path") or "")).expanduser().resolve()
        if not src_path.exists() or not src_path.is_file():
            failures.append(f"missing:{src_path}")
            continue

        child_job_id = make_job_id("telegram")
        child_inbox = paths.inbox_messaging / child_job_id
        child_review = paths.review_root / child_job_id
        child_inbox.mkdir(parents=True, exist_ok=True)
        child_review.mkdir(parents=True, exist_ok=True)

        # Copy input into the child's inbox (safer than move; parent inbox can be cleaned independently).
        dst_path = _safe_copy_dest(child_inbox, src_path, index=idx)
        try:
            shutil.copy2(src_path, dst_path)
        except Exception as exc:
            failures.append(f"copy_failed:{src_path.name}:{exc}")
            continue

        write_job(
            conn,
            job_id=child_job_id,
            source=str(parent_job.get("source") or "telegram"),
            sender=str(parent_job.get("sender") or sender_norm),
            subject=str(parent_job.get("subject") or "Telegram Task"),
            message_text=str(parent_job.get("message_text") or ""),
            status="received",
            inbox_dir=child_inbox,
            review_dir=child_review,
        )
        if kb_company_norm:
            set_job_kb_company(conn, job_id=child_job_id, kb_company=kb_company_norm)

        # Tag child label by filename stem for easier scanning.
        try:
            conn.execute(
                "UPDATE jobs SET task_label=?, updated_at=? WHERE job_id=?",
                (src_path.stem, utc_now_iso(), child_job_id),
            )
            conn.commit()
        except Exception:
            pass

        # Tag child as a batch child so status can redirect to the batch parent when needed.
        try:
            child_artifacts = {"batch": {"parent_job_id": parent_job_id, "source_file": src_path.name}}
            child_flags = ["batch_child"]
            conn.execute(
                "UPDATE jobs SET artifacts_json=?, status_flags_json=?, updated_at=? WHERE job_id=?",
                (json.dumps(child_artifacts, ensure_ascii=False), json.dumps(child_flags, ensure_ascii=False), utc_now_iso(), child_job_id),
            )
            conn.commit()
        except Exception:
            pass

        # Attach file record for child job.
        attach_file_to_job(work_root=paths.work_root, job_id=child_job_id, path=dst_path)

        # Mark child queued & enqueue.
        update_job_status(conn, job_id=child_job_id, status="queued", errors=[])
        q = enqueue_run_job(conn, job_id=child_job_id, notify_target=target, created_by_sender=sender_norm)
        queued_children.append({"job_id": child_job_id, "queue": q})

        child_entries.append(
            {
                "file_name": src_path.name,
                "child_job_id": child_job_id,
                "source_path": str(src_path),
                "copied_path": str(dst_path.resolve()),
                "sha256": compute_sha256(dst_path),
            }
        )

    batch_manifest = {
        "mode": "split_by_file",
        "parent_job_id": parent_job_id,
        "child_jobs": child_entries,
        "created_at": utc_now_iso(),
        "kb_company": kb_company_norm,
    }

    # Update parent job to batch container state.
    parent_flags = parent_job.get("status_flags_json") if isinstance(parent_job.get("status_flags_json"), list) else []
    if BATCH_PARENT_FLAG not in parent_flags:
        parent_flags = list(parent_flags) + [BATCH_PARENT_FLAG]
    parent_artifacts = parent_job.get("artifacts_json") if isinstance(parent_job.get("artifacts_json"), dict) else {}
    parent_artifacts = dict(parent_artifacts)
    parent_artifacts["batch"] = batch_manifest
    conn.execute(
        """
        UPDATE jobs
        SET status=?,
            status_flags_json=?,
            artifacts_json=?,
            updated_at=?
        WHERE job_id=?
        """,
        (
            BATCH_PARENT_STATUS,
            json.dumps(parent_flags, ensure_ascii=False),
            json.dumps(parent_artifacts, ensure_ascii=False),
            utc_now_iso(),
            parent_job_id,
        ),
    )
    conn.commit()

    # Restore parent as active job for the sender (so 'status' shows the batch parent).
    if sender_norm:
        set_sender_active_job(conn, sender=sender_norm, job_id=parent_job_id)

    return {
        "ok": True,
        "parent_job_id": parent_job_id,
        "batch": batch_manifest,
        "queued_children": queued_children,
        "failures": failures,
    }


def _require_new_enabled() -> bool:
    return str(os.getenv("OPENCLAW_REQUIRE_NEW", "1")).strip().lower() not in {"0", "false", "off", "no"}


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

    if raw_action == "new":
        note = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
        return "new", None, note

    if raw_action in {"cancel", "stop", "abort"}:
        explicit_job: str | None = None
        reason = ""
        if len(parts) >= 2 and parts[1].startswith("job_"):
            explicit_job = parts[1]
            reason = " ".join(parts[2:]).strip()
        else:
            reason = " ".join(parts[1:]).strip()
        return "cancel", explicit_job, reason

    if raw_action == "company":
        # Allow forcing company re-selection for the active job.
        return "company", None, ""

    action = raw_action
    if action not in {"run", "status", "ok", "no", "rerun", "new", "cancel", "discard", "help"}:
        return "", None, ""

    if action == "help":
        return "help", None, ""

    explicit_job: str | None = None
    reason = ""
    if action in {"run", "status", "ok", "rerun", "cancel"}:
        if len(parts) >= 2 and parts[1].startswith("job_"):
            explicit_job = parts[1]
    if action in {"no", "discard"}:
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
    send_result = send_message(target=target, message=message, dry_run=dry_run)
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
    allow_fallback: bool,
    require_new: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    sender_norm = (sender or "").strip()
    if explicit_job_id:
        job = get_job(conn, explicit_job_id)
        return job, {"source": "explicit", "multiple": 0}

    if sender_norm:
        active_job_id = get_sender_active_job(conn, sender=sender_norm)
        if active_job_id:
            job = get_job(conn, active_job_id)
            if job and job.get("status") in ACTIVE_JOB_STATUSES.union(
                {"queued", "review_ready", "needs_attention", "failed", "incomplete_input", "running", "canceled", BATCH_PARENT_STATUS}
            ):
                return job, {"source": "active_map", "multiple": 0}

        if allow_fallback:
            sender_jobs = list_actionable_jobs_for_sender(conn, sender=sender_norm, limit=20)
            if sender_jobs:
                selected = sender_jobs[0]
                selected_id = str(selected.get("job_id") or "").strip()
                hydrated = get_job(conn, selected_id) if selected_id else None
                return (hydrated or selected), {"source": "sender_latest", "multiple": max(0, len(sender_jobs) - 1)}

    if allow_fallback:
        latest = latest_actionable_job(conn)
        if latest:
            latest_id = str(latest.get("job_id") or "").strip()
            hydrated = get_job(conn, latest_id) if latest_id else None
            return (hydrated or latest), {"source": "global_latest", "multiple": 0}
    return None, {"source": "none", "multiple": 0}


def _status_text(conn, job: dict[str, Any], *, multiple_hint: int = 0, require_new: bool = True) -> str:
    # Normalize legacy rows (some callers fetch raw sqlite rows without json decoding).
    if isinstance(job.get("status_flags_json"), str):
        try:
            job["status_flags_json"] = json.loads(str(job.get("status_flags_json") or "[]"))
        except Exception:
            job["status_flags_json"] = []
    if isinstance(job.get("artifacts_json"), str):
        try:
            job["artifacts_json"] = json.loads(str(job.get("artifacts_json") or "{}"))
        except Exception:
            job["artifacts_json"] = {}
    if isinstance(job.get("errors_json"), str):
        try:
            job["errors_json"] = json.loads(str(job.get("errors_json") or "[]"))
        except Exception:
            job["errors_json"] = []

    files = list_job_files(conn, str(job["job_id"]))
    files_count = len(files)
    docx_count = sum(1 for item in files if Path(str(item.get("path", ""))).suffix.lower() == ".docx")
    task_label = str(job.get("task_label") or "")
    interaction = get_job_interaction(conn, job_id=str(job["job_id"])) or {}
    pending_action = str(interaction.get("pending_action") or "").strip()
    pending_expires_at = str(interaction.get("expires_at") or "").strip()
    if pending_action and pending_expires_at:
        try:
            if datetime.fromisoformat(pending_expires_at) < datetime.now(UTC):
                clear_job_pending_action(conn, job_id=str(job["job_id"]))
                pending_action = ""
                pending_expires_at = ""
        except ValueError:
            pass
    final_uploads_count = len(list_job_final_uploads(conn, job_id=str(job["job_id"])))
    archived = bool(str(job.get("archived_at") or "").strip())
    last_event = get_last_event(conn, job_id=str(job["job_id"])) or {}
    queue_item = get_active_queue_item(conn, job_id=str(job["job_id"])) or {}

    batch_summary_line = ""
    batch_children_sample = ""
    if _artifact_is_batch_parent(job):
        artifacts = job.get("artifacts_json") if isinstance(job.get("artifacts_json"), dict) else {}
        batch = artifacts.get("batch") if isinstance(artifacts.get("batch"), dict) else {}
        children = batch.get("child_jobs") if isinstance(batch.get("child_jobs"), list) else []
        child_ids = _batch_child_job_ids(job)
        counts: dict[str, int] = {}
        for cid in child_ids:
            child = get_job(conn, cid) or {}
            st = str(child.get("status") or "unknown").strip().lower() or "unknown"
            counts[st] = counts.get(st, 0) + 1
        total = len(child_ids)
        order = ["queued", "running", "review_ready", "needs_attention", "failed", "verified", "canceled"]
        parts = [f"total={total}"]
        for key in order:
            if counts.get(key):
                parts.append(f"{key}={counts[key]}")
        # Include other statuses (if any) at the end for completeness.
        other_keys = sorted([k for k in counts.keys() if k not in set(order)])
        for key in other_keys:
            parts.append(f"{key}={counts[key]}")
        batch_summary_line = "\U0001f9fe Batch: " + " | ".join(parts)

        sample_pairs: list[str] = []
        if isinstance(children, list):
            for entry in children[:5]:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("file_name") or "").strip()
                cid = str(entry.get("child_job_id") or "").strip()
                if cid and name:
                    sample_pairs.append(f"{name} -> {cid}")
                elif cid:
                    sample_pairs.append(cid)
        if sample_pairs:
            batch_children_sample = "\U0001f4ce Children (sample): " + ", ".join(sample_pairs)

    return build_status_card(
        job=job,
        files_count=files_count,
        docx_count=docx_count,
        multiple_hint=multiple_hint,
        require_new=require_new,
        task_label=task_label,
        pending_action=pending_action,
        pending_expires_at=pending_expires_at,
        final_uploads_count=final_uploads_count,
        archived=archived,
        last_milestone=str(last_event.get("milestone") or "").strip(),
        last_milestone_at=str(last_event.get("created_at") or "").strip(),
        queue_state=str(queue_item.get("state") or "").strip(),
        queue_attempt=int(queue_item.get("attempt") or 0),
        queue_worker_id=str(queue_item.get("worker_id") or "").strip(),
        queue_heartbeat_at=str(queue_item.get("heartbeat_at") or "").strip(),
        queue_last_error=str(queue_item.get("last_error") or "").strip(),
        queue_cancel_requested_at=str(queue_item.get("cancel_requested_at") or "").strip(),
        queue_cancel_reason=str(queue_item.get("cancel_reason") or "").strip(),
        queue_cancel_mode=str(queue_item.get("cancel_mode") or "").strip(),
        batch_summary_line=batch_summary_line,
        batch_children_sample=batch_children_sample,
    )


def _create_new_job(conn, *, paths, sender: str, note: str) -> dict[str, Any]:
    sender_norm = (sender or "").strip() or "unknown"

    # Clean up empty review folder from previous abandoned job
    prev_job_id = get_sender_active_job(conn, sender=sender_norm)
    if prev_job_id:
        prev_job = get_job(conn, prev_job_id)
        if prev_job:
            prev_review = Path(str(prev_job.get("review_dir", "")))
            if prev_review.is_dir():
                user_files = [f for f in prev_review.iterdir() if f.name != ".system"]
                if not user_files:
                    shutil.rmtree(prev_review, ignore_errors=True)

    job_id = make_job_id("telegram")
    inbox_dir = paths.inbox_messaging / job_id
    review_dir = paths.review_root / job_id
    inbox_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)

    write_job(
        conn,
        job_id=job_id,
        source="telegram",
        sender=sender_norm,
        subject="Telegram Task",
        message_text=note.strip(),
        status="collecting",
        inbox_dir=inbox_dir,
        review_dir=review_dir,
    )
    if _env_truthy("OPENCLAW_PREFILL_COMPANY_FROM_LAST", "1"):
        last_company = get_last_kb_company_for_sender(conn, sender=sender_norm)
        if last_company:
            set_job_kb_company(conn, job_id=job_id, kb_company=last_company)
    set_sender_active_job(conn, sender=sender_norm, job_id=job_id)
    record_event(
        conn,
        job_id=job_id,
        milestone="new_created",
        payload={"sender": sender_norm, "note": note.strip()},
    )
    return (
        {
            "job_id": job_id,
            "status": "collecting",
            "review_dir": str(review_dir.resolve()),
            "inbox_dir": str(inbox_dir.resolve()),
        }
    )


def _discover_kb_companies(*, kb_root: Path) -> list[str]:
    root = kb_root.expanduser().resolve()
    sections = ["00_Glossary", "10_Style_Guide", "20_Domain_Knowledge", "30_Reference", "40_Templates"]
    companies: set[str] = set()
    for section in sections:
        section_root = root / section
        if not section_root.exists() or not section_root.is_dir():
            continue
        for child in section_root.iterdir():
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            companies.add(child.name)
    return sorted(companies, key=lambda s: s.lower())


def _company_menu(companies: list[str]) -> tuple[str, list[dict[str, Any]]]:
    options = [{"company": name} for name in companies]
    if not companies:
        return "\U0001f4da No companies found under KB/30_Reference.", options
    lines = ["\U0001f4da Select company for this run:", ""]
    for idx, name in enumerate(companies, start=1):
        lines.append(f"{idx}) {name}")
    lines.extend(["", "Reply with a number (e.g., 1)."])
    return "\n".join(lines), options


def _expires_at(minutes: int = 15) -> str:
    return (datetime.now(UTC) + timedelta(minutes=max(1, int(minutes)))).isoformat()


def _slugify(value: str, *, fallback: str = "project") -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    slug = slugify_identifier(raw, max_len=64, default_prefix=fallback).replace("-", "_")
    return slug[:64] if slug else fallback


def _discard_job_files(
    *,
    job: dict[str, Any],
    work_root: Path,
) -> dict[str, Any]:
    """Move review_dir and inbox_dir contents to _TRASH/{job_id}_{timestamp}/.

    Returns a dict with the trash destination path and any errors.
    """
    work_root_path = work_root.expanduser().resolve()
    trash_root = work_root_path / "_TRASH"
    trash_root.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    job_id = str(job.get("job_id") or "")
    safe_name = f"{job_id}_{ts}".replace("/", "_")
    dest = trash_root / safe_name
    dest.mkdir(parents=True, exist_ok=True)

    moved: list[dict[str, Any]] = []
    errors: list[str] = []

    # Move review_dir if it exists
    review_dir = Path(str(job.get("review_dir", ""))).expanduser().resolve()
    if review_dir.is_dir():
        try:
            dest_review = dest / "review"
            shutil.move(str(review_dir), str(dest_review))
            moved.append({"source": str(review_dir), "dest": str(dest_review.resolve())})
        except Exception as e:
            errors.append(f"review_dir: {e}")

    # Move inbox_dir if it exists
    inbox_dir = Path(str(job.get("inbox_dir", ""))).expanduser().resolve()
    if inbox_dir.is_dir():
        try:
            dest_inbox = dest / "inbox"
            shutil.move(str(inbox_dir), str(dest_inbox))
            moved.append({"source": str(inbox_dir), "dest": str(dest_inbox.resolve())})
        except Exception as e:
            errors.append(f"inbox_dir: {e}")

    return {
        "ok": True,
        "trash_dir": str(dest.resolve()),
        "moved": moved,
        "errors": errors,
    }


def _default_archive_project(job: dict[str, Any], final_uploads: list[str]) -> str:
    prefix = datetime.now(UTC).strftime("%Y-%m")
    label = str(job.get("task_label") or "").strip()
    if not label and final_uploads:
        label = Path(final_uploads[0]).stem
    if not label:
        label = str(job.get("job_id") or "task")
    return f"{prefix}_{_slugify(label)}"


def _archive_final_uploads(
    *,
    job: dict[str, Any],
    kb_root: Path,
    company: str,
    project: str,
    final_uploads: list[str],
) -> dict[str, Any]:
    company_norm = (company or "").strip()
    project_norm = (project or "").strip()
    dest_dir = kb_root.expanduser().resolve() / "30_Reference" / company_norm / project_norm / "final"
    dest_dir.mkdir(parents=True, exist_ok=True)

    copied: list[dict[str, Any]] = []
    for src_str in final_uploads:
        src = Path(src_str).expanduser()
        if not src.exists() or not src.is_file():
            continue
        dst = dest_dir / src.name
        if dst.exists():
            dst = dest_dir / f"{dst.stem}_{int(datetime.now(UTC).timestamp())}{dst.suffix}"
        shutil.copy2(src, dst)
        copied.append(
            {
                "name": src.name,
                "src": str(src.resolve()),
                "dest": str(dst.resolve()),
                "sha256": compute_sha256(dst),
            }
        )

    manifest = {
        "job_id": str(job.get("job_id") or ""),
        "archived_at": utc_now_iso(),
        "company": company_norm,
        "project": project_norm,
        "source": str(job.get("source") or ""),
        "sender": str(job.get("sender") or ""),
        "task_label": str(job.get("task_label") or ""),
        "task_type": str(job.get("task_type") or ""),
        "review_dir": str(job.get("review_dir") or ""),
        "final_uploads": copied,
    }
    (dest_dir / "reference_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "dest_dir": str(dest_dir), "copied": copied}


def handle_interaction_reply(
    *,
    reply_text: str,
    work_root: Path,
    kb_root: Path,
    target: str,
    sender: str = "",
    dry_run_notify: bool = False,
) -> dict[str, Any]:
    sender_norm = (sender or "").strip()
    if not sender_norm:
        return {"ok": False, "error": "no_sender"}

    paths = ensure_runtime_paths(work_root)
    conn = db_connect(paths)
    active_job_id = get_sender_active_job(conn, sender=sender_norm)
    if not active_job_id:
        conn.close()
        return {"ok": False, "error": "no_active_job"}

    job = get_job(conn, active_job_id)
    if not job:
        conn.close()
        return {"ok": False, "error": "job_not_found"}

    interaction = get_job_interaction(conn, job_id=active_job_id) or {}
    pending_action = str(interaction.get("pending_action") or "").strip()
    if not pending_action:
        conn.close()
        return {"ok": False, "error": "no_pending_action"}

    expires_at = str(interaction.get("expires_at") or "").strip()
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at) < datetime.now(UTC):
                clear_job_pending_action(conn, job_id=active_job_id)
                conn.close()
                send_message(target=target, message="\u23f1\ufe0f Selection expired. Send: run", dry_run=dry_run_notify)
                return {"ok": False, "error": "expired"}
        except ValueError:
            pass

    try:
        options = json.loads(str(interaction.get("options_json") or "[]"))
    except json.JSONDecodeError:
        options = []
    try:
        idx = int(str(reply_text).strip())
    except ValueError:
        conn.close()
        return {"ok": False, "error": "not_a_number"}

    if idx < 1 or idx > len(options):
        conn.close()
        send_message(target=target, message="\u26a0\ufe0f Invalid selection. Reply with a valid number.", dry_run=dry_run_notify)
        return {"ok": False, "error": "invalid_selection"}

    selected = options[idx - 1]
    if pending_action in {"select_company_for_run", "select_company_for_archive"}:
        company = str(selected.get("company") or "").strip()
        if not company:
            conn.close()
            return {"ok": False, "error": "invalid_option"}

        set_job_kb_company(conn, job_id=active_job_id, kb_company=company)
        clear_job_pending_action(conn, job_id=active_job_id)
        _send_and_record(
            conn,
            job_id=active_job_id,
            milestone="company_selected",
            target=target,
            message=f"\u2705 Company selected: {company}",
            dry_run=dry_run_notify,
        )

    # Continue the waiting action.
    if pending_action == "select_company_for_run":
        clear_job_pending_action(conn, job_id=active_job_id)
        update_job_status(conn, job_id=active_job_id, status="queued", errors=[])
        queued = enqueue_run_job(
            conn,
            job_id=active_job_id,
            notify_target=target,
            created_by_sender=sender_norm,
        )
        qstate = str(queued.get("state") or "queued").strip() or "queued"
        review_dir = str(job.get("review_dir") or "").strip()
        folder_line = f"\U0001f4c1 {review_dir}\n" if review_dir else ""
        _send_and_record(
            conn,
            job_id=active_job_id,
            milestone="run_enqueued" if qstate == "queued" else "run_already_running",
            target=target,
            message=(
                f"\u23f3 Accepted \u00b7 {qstate}\n"
                f"\U0001f194 {active_job_id}\n"
                + folder_line
                + "\nSend: status"
            ),
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "job_id": active_job_id, "status": "queued", "queue": queued}

    if pending_action == "select_company_for_archive":
        company = str(selected.get("company") or "").strip()
        final_uploads = list_job_final_uploads(conn, job_id=active_job_id)
        if not final_uploads:
            conn.close()
            send_message(target=target, message="\U0001f4ce Please upload final file(s) first, then send: ok", dry_run=dry_run_notify)
            return {"ok": False, "error": "final_upload_required"}

        project = str(job.get("archive_project") or "").strip() or _default_archive_project(job, final_uploads)
        set_job_archive_project(conn, job_id=active_job_id, archive_project=project)
        archive_result = _archive_final_uploads(
            job=job,
            kb_root=kb_root,
            company=company,
            project=project,
            final_uploads=final_uploads,
        )
        update_job_status(conn, job_id=active_job_id, status="verified", errors=[])
        mark_job_archived(conn, job_id=active_job_id)
        _send_and_record(
            conn,
            job_id=active_job_id,
            milestone="archived",
            target=target,
            message=f"\u2705 Verified & archived\n\U0001f4c1 {archive_result.get('dest_dir')}",
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "job_id": active_job_id, "status": "verified", "archive": archive_result}

    if pending_action == "select_attachment_destination":
        action = str(selected.get("action") or "").strip().lower()
        staging_dir = Path(str(selected.get("staging_dir") or "")).expanduser()
        files_raw = selected.get("files")
        staged_files: list[Path] = []
        if isinstance(files_raw, list):
            for item in files_raw:
                p = Path(str(item)).expanduser()
                staged_files.append(p)
        if not staged_files and staging_dir.is_dir():
            staged_files = [p for p in sorted(staging_dir.iterdir()) if p.is_file()]
        if not staged_files:
            clear_job_pending_action(conn, job_id=active_job_id)
            conn.close()
            send_message(target=target, message="\u26a0\ufe0f No staged files found.", dry_run=dry_run_notify)
            return {"ok": False, "error": "no_staged_files"}

        if action == "final":
            review_dir = Path(str(job.get("review_dir") or "")).expanduser().resolve()
            dest_dir = review_dir / "FinalUploads"
            dest_dir.mkdir(parents=True, exist_ok=True)
            moved: list[str] = []
            failures: list[str] = []
            for idx2, src in enumerate(staged_files, start=1):
                if not src.exists() or not src.is_file():
                    continue
                dst = dest_dir / src.name
                if dst.exists():
                    dst = dest_dir / f"{dst.stem}_{idx2}_{int(datetime.now(UTC).timestamp())}{dst.suffix}"
                try:
                    shutil.move(str(src), str(dst))
                except Exception:
                    failures.append(src.name)
                    continue
                add_job_final_upload(conn, job_id=active_job_id, sender=sender_norm, path=dst)
                moved.append(str(dst.resolve()))

            clear_job_pending_action(conn, job_id=active_job_id)
            _send_and_record(
                conn,
                job_id=active_job_id,
                milestone="final_uploads_staged",
                target=target,
                message=(
                    f"\U0001f4ce Final file(s) received: {len(moved)}\n"
                    "Send: ok to archive"
                    + (f"\n\u26a0\ufe0f Failed: {', '.join(failures[:3])}" if failures else "")
                ),
                dry_run=dry_run_notify,
            )
            conn.close()
            return {"ok": True, "job_id": active_job_id, "status": str(job.get("status") or ""), "moved": moved}

        if action == "new":
            # Create a new collecting job and move staged files into its inbox.
            created = _create_new_job(conn, paths=paths, sender=sender_norm, note="")
            new_job_id = str(created["job_id"])
            new_inbox = Path(str(created.get("inbox_dir") or "")).expanduser().resolve()
            new_inbox.mkdir(parents=True, exist_ok=True)
            moved: list[str] = []
            failures: list[str] = []
            for idx2, src in enumerate(staged_files, start=1):
                if not src.exists() or not src.is_file():
                    continue
                dst = new_inbox / src.name
                if dst.exists():
                    dst = new_inbox / f"{dst.stem}_{idx2}_{int(datetime.now(UTC).timestamp())}{dst.suffix}"
                try:
                    shutil.move(str(src), str(dst))
                except Exception:
                    failures.append(src.name)
                    continue
                attach_file_to_job(work_root=work_root, job_id=new_job_id, path=dst)
                moved.append(str(dst.resolve()))

            # Clear the pending action on the previous job (active_job_id), even though active sender job has changed.
            clear_job_pending_action(conn, job_id=active_job_id)
            _send_and_record(
                conn,
                job_id=new_job_id,
                milestone="new_from_staging",
                target=target,
                message=(
                    f"\u2705 New task created: {new_job_id}\n"
                    f"\U0001f4ce Files: {len(moved)}\n"
                    "Send: run"
                    + (f"\n\u26a0\ufe0f Failed: {', '.join(failures[:3])}" if failures else "")
                ),
                dry_run=dry_run_notify,
            )
            conn.close()
            return {"ok": True, "job_id": new_job_id, "status": "collecting", "moved": moved}

        if action == "discard":
            work_root_path = work_root.expanduser().resolve()
            trash_root = work_root_path / "_TRASH" / "_STAGING"
            trash_root.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            safe_name = f"{sender_norm}_{ts}".replace("/", "_")
            dest = trash_root / safe_name
            try:
                if staging_dir.is_dir():
                    shutil.move(str(staging_dir), str(dest))
                else:
                    dest.mkdir(parents=True, exist_ok=True)
                    for src in staged_files:
                        if src.exists() and src.is_file():
                            shutil.move(str(src), str(dest / src.name))
            except Exception:
                clear_job_pending_action(conn, job_id=active_job_id)
                conn.close()
                send_message(target=target, message="\u26a0\ufe0f Failed to discard staged files.", dry_run=dry_run_notify)
                return {"ok": False, "error": "discard_failed"}

            clear_job_pending_action(conn, job_id=active_job_id)
            _send_and_record(
                conn,
                job_id=active_job_id,
                milestone="staging_discarded",
                target=target,
                message=f"\u2705 Discarded staged files (moved to trash): {dest}",
                dry_run=dry_run_notify,
            )
            conn.close()
            return {"ok": True, "job_id": active_job_id, "status": str(job.get("status") or ""), "trash": str(dest.resolve())}

        clear_job_pending_action(conn, job_id=active_job_id)
        conn.close()
        send_message(target=target, message="\u26a0\ufe0f Invalid selection.", dry_run=dry_run_notify)
        return {"ok": False, "error": "invalid_option"}

    conn.close()
    return {"ok": False, "error": "unsupported_pending_action", "pending_action": pending_action}


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
    require_new = _require_new_enabled()
    action, explicit_job_id, reason = _parse_command(command_text)
    if not action:
        conn.close()
        return {"ok": False, "error": "unsupported_command"}

    if action == "new":
        created = _create_new_job(conn, paths=paths, sender=sender, note=reason)
        conn.close()
        return {"ok": True, "job_id": created["job_id"], "status": "collecting"}

    allow_fallback = action in {"status", "cancel"}
    job, resolve_meta = _resolve_job(
        conn,
        sender=sender,
        explicit_job_id=explicit_job_id,
        allow_fallback=allow_fallback,
        require_new=require_new,
    )
    if action == "status" and not job:
        send_result = send_message(
            target=target,
            message=no_active_job_hint(require_new=require_new),
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "status": "no_active_job", "send_result": send_result}

    # Help is always available, even without an active job
    if action == "help":
        from scripts.skill_status_card import _COMMANDS_BY_STATUS
        current_status_for_help = str(job.get("status") or "collecting").lower() if job else "collecting"
        available_cmds = _COMMANDS_BY_STATUS.get(current_status_for_help, "status | help")
        help_msg = f"""📚 Available Commands

🔹 Task Management
  new [note]      - Create new task
  company         - Select/override company for this task
  status [job_id] - Check task status
  cancel [job_id] - Cancel running task
  discard [reason]- Delete task and files

🔹 Execution
  run [job_id]    - Start translation
  rerun [job_id]  - Re-run translation

🔹 Review
  ok [job_id]     - Approve & archive
  no {{reason}}     - Reject / needs revision

🔹 Quick Help
  help            - Show this message

📋 Typical Flow:
  new → upload files → run → wait → ok/no

⚡ Available now: {available_cmds}"""
        send_message(target=target, message=help_msg, dry_run=dry_run_notify)
        conn.close()
        return {"ok": True, "action": "help"}

    if action == "company":
        # Force company menu for the active job.
        if not job:
            send_result = send_message(
                target=target,
                message=no_active_job_hint(require_new=require_new),
                dry_run=dry_run_notify,
            )
            conn.close()
            return {"ok": False, "error": "job_not_found", "send_result": send_result}
        job_id = str(job["job_id"])
        companies = _discover_kb_companies(kb_root=kb_root)
        menu, options = _company_menu(companies)
        if not options:
            _send_and_record(
                conn,
                job_id=job_id,
                milestone="run_blocked",
                target=target,
                message="\u26a0\ufe0f No companies configured. Create: KB/{Section}/{Company}/ (e.g., 30_Reference/{Company}/)",
                dry_run=dry_run_notify,
            )
            conn.close()
            return {"ok": False, "job_id": job_id, "error": "no_companies_configured"}
        set_job_pending_action(
            conn,
            job_id=job_id,
            sender=str(job.get("sender") or sender).strip(),
            pending_action="select_company_for_run",
            options=options,
            expires_at=_expires_at(20),
        )
        _send_and_record(
            conn,
            job_id=job_id,
            milestone="awaiting_company_for_run",
            target=target,
            message=menu,
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "job_id": job_id, "status": "awaiting_company_selection"}
    if not job:
        send_result = send_message(
            target=target,
            message=no_active_job_hint(require_new=require_new),
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": False, "error": "job_not_found", "send_result": send_result}

    job_id = str(job["job_id"])
    task_label = str(job.get("task_label") or "")
    _task_name = task_label or "New task"
    if sender.strip():
        set_sender_active_job(conn, sender=sender.strip(), job_id=job_id)

    if action == "status":
        # If the active job is a batch child, redirect status to the batch parent for overview.
        job_for_status = job
        redirected_child_id = ""
        if not explicit_job_id:
            artifacts = job.get("artifacts_json") if isinstance(job.get("artifacts_json"), dict) else {}
            batch = artifacts.get("batch") if isinstance(artifacts.get("batch"), dict) else {}
            parent_job_id = str(batch.get("parent_job_id") or "").strip()
            if parent_job_id:
                parent = get_job(conn, parent_job_id)
                if parent:
                    redirected_child_id = str(job.get("job_id") or "").strip()
                    job_for_status = parent

        msg = _status_text(conn, job_for_status, multiple_hint=resolve_meta.get("multiple", 0), require_new=require_new)
        if redirected_child_id:
            msg = (
                msg
                + "\n\n\u21aa Active file job: "
                + redirected_child_id
                + f"\nSend: status {redirected_child_id} for details"
            )
        _send_and_record(
            conn,
            job_id=str(job_for_status.get("job_id") or job_id),
            milestone="status",
            target=target,
            message=msg,
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "job_id": str(job_for_status.get("job_id") or job_id), "status": str(job_for_status.get("status")), "resolve": resolve_meta}

    current_status = str(job.get("status") or "")
    current_norm = current_status.strip().lower()

    if action == "cancel":
        # Batch parent: cancel all children.
        if _artifact_is_batch_parent(job):
            child_ids = _batch_child_job_ids(job)
            requested = 0
            failed: list[str] = []
            for cid in child_ids:
                res = cancel_job_run(conn, job_id=cid, requested_by=sender, reason=reason, mode="force")
                if res.get("ok"):
                    requested += 1
                else:
                    failed.append(cid)
            # Mark parent canceled (use existing status).
            update_job_status(conn, job_id=job_id, status="canceled", errors=[])
            msg = (
                f"\u26d4\ufe0f Canceled batch: {requested} requested\n"
                f"\U0001f4cb {_task_name}\n"
                f"\U0001f194 {job_id}\n"
                + (f"\u26a0\ufe0f Failed: {', '.join(failed[:3])}\n" if failed else "")
                + "\u23ed\ufe0f Send: status | new"
            )
            _send_and_record(conn, job_id=job_id, milestone="batch_canceled", target=target, message=msg, dry_run=dry_run_notify)
            conn.close()
            return {"ok": True, "job_id": job_id, "status": "canceled", "batch_cancel": {"requested": requested, "failed": failed}}

        queue_item = get_active_queue_item(conn, job_id=job_id) or {}
        if not queue_item:
            msg = (
                "\U0001f4ed Nothing to cancel\n"
                f"\U0001f4cb {_task_name}\n"
                f"\U0001f194 {job_id}\n"
                "\u23ed\ufe0f Send: status"
            )
            _send_and_record(conn, job_id=job_id, milestone="cancel_no_active_run", target=target, message=msg, dry_run=dry_run_notify)
            conn.close()
            return {"ok": True, "job_id": job_id, "status": current_status, "cancel": "noop"}

        cancel_result = cancel_job_run(
            conn,
            job_id=job_id,
            requested_by=sender,
            reason=reason,
            mode="force",
        )
        q = dict(cancel_result.get("queue") or {})
        action2 = str(cancel_result.get("action") or "").strip()

        if not cancel_result.get("ok"):
            msg = (
                "\U0001f4ed Nothing to cancel\n"
                f"\U0001f4cb {_task_name}\n"
                f"\U0001f194 {job_id}\n"
                "\u23ed\ufe0f Send: status"
            )
            _send_and_record(conn, job_id=job_id, milestone="cancel_no_active_run", target=target, message=msg, dry_run=dry_run_notify)
            conn.close()
            return {"ok": True, "job_id": job_id, "status": current_status, "cancel": "noop"}

        if action2 == "canceled":
            msg = (
                "\u26d4\ufe0f Canceled\n"
                f"\U0001f4cb {_task_name}\n"
                f"\U0001f194 {job_id}\n"
                "\u23ed\ufe0f Send: rerun | new"
            )
            _send_and_record(conn, job_id=job_id, milestone="canceled", target=target, message=msg, dry_run=dry_run_notify)
            conn.close()
            return {"ok": True, "job_id": job_id, "status": "canceled", "queue": q}

        # running -> cancel requested (force)
        pgid = int(q.get("pipeline_pgid") or 0)
        pid = int(q.get("pipeline_pid") or 0)
        kill_sent = False
        try:
            if pgid > 0 and hasattr(os, "killpg"):
                os.killpg(pgid, signal.SIGTERM)
                os.killpg(pgid, signal.SIGKILL)
                kill_sent = True
            elif pid > 0:
                os.kill(pid, signal.SIGTERM)
                os.kill(pid, signal.SIGKILL)
                kill_sent = True
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
        except Exception:
            pass

        status_line = "Cancel requested" if action2 != "already_requested" else "Already canceling"
        msg = (
            f"\u26d4\ufe0f {status_line}\n"
            f"\U0001f4cb {_task_name}\n"
            f"\U0001f194 {job_id}\n"
            + ("\U0001f5f2 Sent kill signal\n" if kill_sent else "")
            + "\u23ed\ufe0f Send: status"
        )
        _send_and_record(
            conn,
            job_id=job_id,
            milestone="cancel_requested",
            target=target,
            message=msg,
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "job_id": job_id, "status": current_status, "queue": q, "kill_sent": kill_sent}

    if action in {"run", "rerun"} and _artifact_is_batch_parent(job):
        artifacts = job.get("artifacts_json") if isinstance(job.get("artifacts_json"), dict) else {}
        batch = artifacts.get("batch") if isinstance(artifacts.get("batch"), dict) else {}
        children = (batch.get("child_jobs") or []) if isinstance(batch, dict) else []
        total = len(children) if isinstance(children, list) else 0
        sample = []
        if isinstance(children, list):
            for entry in children[:5]:
                if isinstance(entry, dict):
                    cid = str(entry.get("child_job_id") or "").strip()
                    if cid:
                        sample.append(cid)
        msg = (
            f"\u23f3 Already batch queued ({total} files)\n"
            f"\U0001f4cb {_task_name}\n"
            f"\U0001f194 {job_id}\n"
            + (f"\U0001f4ce Children (sample): {', '.join(sample)}\n" if sample else "")
            + "\u23ed\ufe0f Send: status | cancel | new"
        )
        _send_and_record(conn, job_id=job_id, milestone="batch_already_dispatched", target=target, message=msg, dry_run=dry_run_notify)
        conn.close()
        return {"ok": True, "job_id": job_id, "status": str(job.get("status") or ""), "batch": batch}

    if action in {"run", "rerun"} and current_norm in {"queued", "running"}:
        queue_item = get_active_queue_item(conn, job_id=job_id) or {}
        qstate = str(queue_item.get("state") or current_norm).strip() or current_norm
        msg = (
            f"\u23f3 Already {qstate}\n"
            f"\U0001f4cb {_task_name}\n"
            f"\U0001f194 {job_id}\n"
            f"\u23ed\ufe0f Send: status"
        )
        _send_and_record(conn, job_id=job_id, milestone="run_already_queued", target=target, message=msg, dry_run=dry_run_notify)
        conn.close()
        return {"ok": True, "job_id": job_id, "status": current_status, "queue": queue_item}

    if action == "run" and current_status not in RUN_ALLOWED_STATUSES:
        msg = (
            f"\u26a0\ufe0f Cannot run\n"
            f"\U0001f4cb {_task_name}\n"
            f"Current stage: {current_status}\n"
            f"\U0001f4a1 Try: rerun or new"
        )
        _send_and_record(conn, job_id=job_id, milestone="status", target=target, message=msg, dry_run=dry_run_notify)
        conn.close()
        return {"ok": False, "job_id": job_id, "error": "invalid_run_status", "status": current_status}
    if action == "rerun" and current_status not in RERUN_ALLOWED_STATUSES:
        msg = f"\u26a0\ufe0f Cannot rerun\n\U0001f4cb {_task_name}\nCurrent stage: {current_status}\n\U0001f4a1 Send: status"
        _send_and_record(conn, job_id=job_id, milestone="status", target=target, message=msg, dry_run=dry_run_notify)
        conn.close()
        return {"ok": False, "job_id": job_id, "error": "invalid_rerun_status", "status": current_status}

    if action == "ok":
        clear_job_pending_action(conn, job_id=job_id)
        require_final = str(os.getenv("OPENCLAW_ARCHIVE_REQUIRE_FINAL_UPLOAD", "0")).strip().lower() not in {"0", "false", "off", "no"}
        final_uploads = list_job_final_uploads(conn, job_id=job_id)
        if require_final and not final_uploads:
            _send_and_record(
                conn,
                job_id=job_id,
                milestone="archive_blocked",
                target=target,
                message=(
                    "\U0001f4ce Please upload your FINAL file(s) (Telegram attachment), then send: ok\n"
                    "\U0001f4a1 If you want to discard this result, send: no {reason}"
                ),
                dry_run=dry_run_notify,
            )
            conn.close()
            return {"ok": False, "job_id": job_id, "error": "final_upload_required"}

        if not final_uploads:
            update_job_status(conn, job_id=job_id, status="verified", errors=[])
            _send_and_record(
                conn,
                job_id=job_id,
                milestone="verified",
                target=target,
                message=(
                    "\u2705 Verified\n"
                    f"\U0001f4cb {_task_name}\n"
                    "\n"
                    "\U0001f4a1 Manual delivery: move files from _VERIFY to the final folder (if needed)."
                ),
                dry_run=dry_run_notify,
            )
            conn.close()
            return {"ok": True, "job_id": job_id, "status": "verified"}

        kb_company = str(job.get("kb_company") or "").strip()
        if not kb_company:
            companies = _discover_kb_companies(kb_root=kb_root)
            menu, options = _company_menu(companies)
            if not options:
                _send_and_record(
                    conn,
                    job_id=job_id,
                    milestone="archive_blocked",
                    target=target,
                    message="\u26a0\ufe0f No companies configured. Create: KB/{Section}/{Company}/ (e.g., 30_Reference/{Company}/)",
                    dry_run=dry_run_notify,
                )
                conn.close()
                return {"ok": False, "job_id": job_id, "error": "no_companies_configured"}
            set_job_pending_action(
                conn,
                job_id=job_id,
                sender=str(job.get("sender") or sender).strip(),
                pending_action="select_company_for_archive",
                options=options,
                expires_at=_expires_at(20),
            )
            _send_and_record(
                conn,
                job_id=job_id,
                milestone="awaiting_company_for_archive",
                target=target,
                message=menu,
                dry_run=dry_run_notify,
            )
            conn.close()
            return {"ok": True, "job_id": job_id, "status": "awaiting_company_selection"}

        project = str(job.get("archive_project") or "").strip() or _default_archive_project(job, final_uploads)
        set_job_archive_project(conn, job_id=job_id, archive_project=project)
        archive_result = _archive_final_uploads(
            job=job,
            kb_root=kb_root,
            company=kb_company,
            project=project,
            final_uploads=final_uploads,
        )
        update_job_status(conn, job_id=job_id, status="verified", errors=[])
        mark_job_archived(conn, job_id=job_id)
        _send_and_record(
            conn,
            job_id=job_id,
            milestone="verified",
            target=target,
            message=(
                "\u2705 Verified\n"
                f"\U0001f4cb {_task_name}\n"
                f"\U0001f4c1 Archived reference: {archive_result.get('dest_dir')}\n"
                "\n"
                "\U0001f4a1 Manual delivery: move files from _VERIFY to the final folder (if needed)."
            ),
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "job_id": job_id, "status": "verified", "archive": archive_result}

    if action == "no":
        clear_job_pending_action(conn, job_id=job_id)
        reason_norm = reason.strip() or "needs_manual_revision"
        update_job_status(conn, job_id=job_id, status="needs_revision", errors=[reason_norm])
        _send_and_record(
            conn,
            job_id=job_id,
            milestone="needs_attention",
            target=target,
            message=f"\U0001f527 Marked for revision\n\U0001f4cb {_task_name}\nReason: {reason_norm}",
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "job_id": job_id, "status": "needs_revision", "reason": reason_norm}

    if action == "discard":
        clear_job_pending_action(conn, job_id=job_id)
        reason_norm = reason.strip() or "manual_discard"

        if current_status not in DISCARD_ALLOWED_STATUSES:
            msg = (
                f"\u26a0\ufe0f Cannot discard\n"
                f"\U0001f4cb {_task_name}\n"
                f"Current stage: {current_status}\n"
            )
            _send_and_record(conn, job_id=job_id, milestone="discard_rejected", target=target, message=msg, dry_run=dry_run_notify)
            conn.close()
            return {"ok": False, "job_id": job_id, "error": "invalid_discard_status", "status": current_status}

        discard_result = _discard_job_files(job=job, work_root=work_root)
        update_job_status(conn, job_id=job_id, status="discarded", errors=[reason_norm])
        trash_dir = discard_result.get("trash_dir", "")
        errors = discard_result.get("errors", [])
        error_msg = f"\n\u26a0\ufe0f Errors: {', '.join(errors)}" if errors else ""
        _send_and_record(
            conn,
            job_id=job_id,
            milestone="discarded",
            target=target,
            message=(
                f"\U0001f5d1\ufe0f Discarded\n"
                f"\U0001f4cb {_task_name}\n"
                f"\U0001f4c1 Moved to: {trash_dir}{error_msg}"
            ),
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "job_id": job_id, "status": "discarded", "discard": discard_result, "reason": reason_norm}

    if action in {"run", "rerun"}:
        queue_item = get_active_queue_item(conn, job_id=job_id) or {}
        if queue_item:
            qstate = str(queue_item.get("state") or "queued").strip() or "queued"
            msg = (
                f"\u23f3 Already {qstate}\n"
                f"\U0001f4cb {_task_name}\n"
                f"\U0001f194 {job_id}\n"
                f"\u23ed\ufe0f Send: status"
            )
            _send_and_record(conn, job_id=job_id, milestone="run_already_queued", target=target, message=msg, dry_run=dry_run_notify)
            conn.close()
            return {"ok": True, "job_id": job_id, "status": current_status, "queue": queue_item}

        kb_company = str(job.get("kb_company") or "").strip()
        if not kb_company:
            companies = _discover_kb_companies(kb_root=kb_root)
            menu, options = _company_menu(companies)
            if not options:
                _send_and_record(
                    conn,
                    job_id=job_id,
                    milestone="run_blocked",
                    target=target,
                    message="\u26a0\ufe0f No companies configured. Create: KB/{Section}/{Company}/ (e.g., 30_Reference/{Company}/)",
                    dry_run=dry_run_notify,
                )
                conn.close()
                return {"ok": False, "job_id": job_id, "error": "no_companies_configured"}
            set_job_pending_action(
                conn,
                job_id=job_id,
                sender=str(job.get("sender") or sender).strip(),
                pending_action="select_company_for_run",
                options=options,
                expires_at=_expires_at(20),
            )
            _send_and_record(
                conn,
                job_id=job_id,
                milestone="awaiting_company_for_run",
                target=target,
                message=menu,
                dry_run=dry_run_notify,
            )
            conn.close()
            return {"ok": True, "job_id": job_id, "status": "awaiting_company_selection"}

        # Auto split multi-xlsx/csv jobs into child jobs before enqueueing the parent.
        split_enabled = _env_truthy("OPENCLAW_RUN_SPLIT_MULTI_XLSX", "1")
        if split_enabled:
            files = list_job_files(conn, job_id=job_id)
            file_paths = [Path(str(x.get("path") or "")).expanduser() for x in files]
            spreadsheet_only = len(files) >= 2 and all(p and _is_spreadsheet_path(p) for p in file_paths)
            if spreadsheet_only:
                # If already split, don't do it again.
                artifacts = job.get("artifacts_json") if isinstance(job.get("artifacts_json"), dict) else {}
                batch = artifacts.get("batch") if isinstance(artifacts.get("batch"), dict) else {}
                if batch.get("child_jobs"):
                    msg = (
                        f"\u23f3 Already batch queued\n"
                        f"\U0001f4cb {_task_name}\n"
                        f"\U0001f194 {job_id}\n"
                        "\u23ed\ufe0f Send: status"
                    )
                    _send_and_record(conn, job_id=job_id, milestone="batch_already_dispatched", target=target, message=msg, dry_run=dry_run_notify)
                    conn.close()
                    return {"ok": True, "job_id": job_id, "status": str(job.get("status") or ""), "batch": batch}

                split_res = _split_multi_xlsx_job_and_enqueue(
                    conn,
                    paths=paths,
                    parent_job=job,
                    files=files,
                    kb_company=kb_company,
                    target=target,
                    sender=sender,
                    dry_run_notify=dry_run_notify,
                )
                batch = split_res.get("batch") or {}
                children = (batch.get("child_jobs") or []) if isinstance(batch, dict) else []
                total = len(children)
                sample = []
                for entry in children[:5]:
                    if isinstance(entry, dict):
                        sample.append(f"{entry.get('file_name')} -> {entry.get('child_job_id')}")
                more = max(0, total - len(sample))
                failure_list = split_res.get("failures") if isinstance(split_res.get("failures"), list) else []
                review_dir = str(job.get("review_dir") or "").strip()
                folder_line = f"\U0001f4c1 {review_dir}\n" if review_dir else ""
                msg = (
                    f"\u23f3 Accepted \u00b7 batch queued ({total} files)\n"
                    f"\U0001f4cb {_task_name}\n"
                    f"\U0001f194 {job_id}\n"
                    + folder_line
                    + ("\n".join(sample) + ("\n" if sample else ""))
                    + (f"+{more} more\n" if more else "")
                    + (f"\u26a0\ufe0f Failures: {', '.join([str(x) for x in failure_list[:3]])}\n" if failure_list else "")
                    + "\u23ed\ufe0f Send: status"
                )
                _send_and_record(conn, job_id=job_id, milestone="batch_dispatched", target=target, message=msg, dry_run=dry_run_notify)
                conn.close()
                return {"ok": True, "job_id": job_id, "status": BATCH_PARENT_STATUS, "batch": batch, "failures": failure_list}

        clear_job_pending_action(conn, job_id=job_id)
        update_job_status(conn, job_id=job_id, status="queued", errors=[])
        queued = enqueue_run_job(
            conn,
            job_id=job_id,
            notify_target=target,
            created_by_sender=sender.strip(),
        )
        qstate = str(queued.get("state") or "queued").strip() or "queued"
        review_dir = str(job.get("review_dir") or "").strip()
        folder_line = f"\U0001f4c1 {review_dir}\n" if review_dir else ""
        _send_and_record(
            conn,
            job_id=job_id,
            milestone="run_enqueued" if qstate == "queued" else "run_already_running",
            target=target,
            message=(
                f"\u23f3 Accepted \u00b7 {qstate}\n"
                f"\U0001f4cb {_task_name}\n"
                f"\U0001f194 {job_id}\n"
                + folder_line
                + "\nSend: status"
            ),
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": True, "job_id": job_id, "status": "queued", "queue": queued}

    conn.close()
    return {"ok": False, "error": "unreachable"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", required=True, help="new|run|status|ok|no {reason}|rerun|discard [reason]")
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

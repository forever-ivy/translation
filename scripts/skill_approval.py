#!/usr/bin/env python3
"""OpenClaw skill: contextual command handling (V5.2)."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.v4_pipeline import attach_file_to_job
from scripts.v4_pipeline import run_job_pipeline
from scripts.skill_status_card import build_status_card, no_active_job_hint
from scripts.v4_runtime import (
    DEFAULT_KB_ROOT,
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_WORK_ROOT,
    add_job_final_upload,
    clear_job_pending_action,
    compute_sha256,
    db_connect,
    ensure_runtime_paths,
    get_job,
    get_job_interaction,
    get_sender_active_job,
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
    update_job_status,
    utc_now_iso,
    write_job,
)

ACTIVE_JOB_STATUSES = {"collecting", "received", "missing_inputs", "needs_revision"}
RUN_ALLOWED_STATUSES = {"collecting", "received", "missing_inputs", "needs_revision"}
RERUN_ALLOWED_STATUSES = {"collecting", "received", "missing_inputs", "needs_revision", "review_ready", "needs_attention", "failed", "incomplete_input"}


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

    action = raw_action
    if action not in {"run", "status", "ok", "no", "rerun", "new"}:
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
            if job and job.get("status") in ACTIVE_JOB_STATUSES.union({"review_ready", "needs_attention", "failed", "incomplete_input", "running"}):
                return job, {"source": "active_map", "multiple": 0}

        if allow_fallback:
            sender_jobs = list_actionable_jobs_for_sender(conn, sender=sender_norm, limit=20)
            if sender_jobs:
                selected = sender_jobs[0]
                return selected, {"source": "sender_latest", "multiple": max(0, len(sender_jobs) - 1)}

    if allow_fallback:
        latest = latest_actionable_job(conn)
        if latest:
            return latest, {"source": "global_latest", "multiple": 0}
    return None, {"source": "none", "multiple": 0}


def _status_text(conn, job: dict[str, Any], *, multiple_hint: int = 0, require_new: bool = True) -> str:
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


def _discover_reference_companies(*, kb_root: Path) -> list[str]:
    ref_root = (kb_root.expanduser().resolve() / "30_Reference")
    if not ref_root.exists():
        return []
    companies: list[str] = []
    for child in sorted(ref_root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        companies.append(child.name)
    return companies


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
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", raw).strip("_")
    return cleaned[:64] if cleaned else fallback


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
        update_job_status(conn, job_id=active_job_id, status="received", errors=[])
        _send_and_record(
            conn,
            job_id=active_job_id,
            milestone="run_accepted",
            target=target,
            message="\U0001f680 Starting execution\n\u23f3 Codex+Gemini translating...",
            dry_run=dry_run_notify,
        )
        conn.close()
        result = run_job_pipeline(
            job_id=active_job_id,
            work_root=work_root,
            kb_root=kb_root,
            notify_target=target,
            dry_run_notify=dry_run_notify,
        )
        return {"ok": bool(result.get("ok")), "job_id": active_job_id, "result": result}

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

    allow_fallback = action == "status"
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
        msg = _status_text(conn, job, multiple_hint=resolve_meta.get("multiple", 0), require_new=require_new)
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

    current_status = str(job.get("status") or "")
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
            companies = _discover_reference_companies(kb_root=kb_root)
            menu, options = _company_menu(companies)
            if not options:
                _send_and_record(
                    conn,
                    job_id=job_id,
                    milestone="archive_blocked",
                    target=target,
                    message="\u26a0\ufe0f No companies configured. Create: KB/30_Reference/{Company}/",
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

    if action in {"run", "rerun"}:
        kb_company = str(job.get("kb_company") or "").strip()
        if not kb_company:
            companies = _discover_reference_companies(kb_root=kb_root)
            menu, options = _company_menu(companies)
            if not options:
                _send_and_record(
                    conn,
                    job_id=job_id,
                    milestone="run_blocked",
                    target=target,
                    message="\u26a0\ufe0f No companies configured. Create: KB/30_Reference/{Company}/",
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

        clear_job_pending_action(conn, job_id=job_id)
        update_job_status(conn, job_id=job_id, status="received", errors=[])
        _send_and_record(
            conn,
            job_id=job_id,
            milestone="run_accepted",
            target=target,
            message=f"\U0001f680 Starting execution\n\U0001f4cb {_task_name}\n\u23f3 Codex+Gemini translating...",
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
    parser.add_argument("--command", required=True, help="new|run|status|ok|no {reason}|rerun")
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

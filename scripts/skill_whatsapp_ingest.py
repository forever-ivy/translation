#!/usr/bin/env python3
"""OpenClaw skill: ingest WhatsApp messages/files and create jobs."""

from __future__ import annotations

import argparse
import base64
from datetime import UTC, datetime, timedelta
import json
import os
import shutil
from pathlib import Path
from typing import Any

from scripts.skill_approval import handle_command
from scripts.v4_pipeline import attach_file_to_job, create_job, run_job_pipeline
from scripts.v4_runtime import (
    DEFAULT_KB_ROOT,
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_WORK_ROOT,
    db_connect,
    ensure_runtime_paths,
    list_job_files,
    make_job_id,
    send_whatsapp_message,
    update_job_status,
    utc_now_iso,
)


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload_json:
        return json.loads(args.payload_json)
    if args.payload_file:
        return json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    raw = args.payload_stdin
    if raw:
        return json.loads(raw)
    return {}


def _extract_text(payload: dict[str, Any]) -> str:
    for key in ["text", "message", "body", "content"]:
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    msg = payload.get("message")
    if isinstance(msg, dict):
        for key in ["text", "body", "content"]:
            v = msg.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _extract_sender(payload: dict[str, Any]) -> str:
    for key in ["from", "sender", "from_e164", "author"]:
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "unknown"


def _collect_attachments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    atts: list[dict[str, Any]] = []
    if isinstance(payload.get("attachments"), list):
        for item in payload["attachments"]:
            if isinstance(item, dict):
                atts.append(item)
    message = payload.get("message")
    if isinstance(message, dict) and isinstance(message.get("attachments"), list):
        for item in message["attachments"]:
            if isinstance(item, dict):
                atts.append(item)
    if payload.get("media"):
        media = payload["media"]
        if isinstance(media, list):
            for item in media:
                if isinstance(item, dict):
                    atts.append(item)
        elif isinstance(media, dict):
            atts.append(media)
    return atts


def _is_command(text: str) -> bool:
    lowered = text.lower().strip()
    if not lowered:
        return False
    head = lowered.split(" ", 1)[0]
    return head in {"run", "status", "ok", "no", "rerun", "approve", "reject"}


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        # utc_now_iso() stores timezone-aware ISO-8601.
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _find_recent_collecting_job(*, work_root: Path, sender: str, window_seconds: int) -> str | None:
    paths = ensure_runtime_paths(work_root)
    conn = db_connect(paths)
    try:
        rows = conn.execute(
            """
            SELECT job_id, updated_at
            FROM jobs
            WHERE source='whatsapp'
              AND sender=?
              AND status IN ('collecting', 'received', 'missing_inputs', 'needs_revision')
            ORDER BY updated_at DESC
            LIMIT 20
            """,
            (sender,),
        ).fetchall()
        if not rows:
            return None
        now = datetime.now(UTC)
        for row in rows:
            updated = _parse_iso(str(row["updated_at"]))
            if not updated:
                continue
            if now - updated <= timedelta(seconds=max(30, window_seconds)):
                return str(row["job_id"])
        return None
    finally:
        conn.close()


def _append_job_message(*, work_root: Path, job_id: str, text: str) -> None:
    if not text.strip():
        return
    paths = ensure_runtime_paths(work_root)
    conn = db_connect(paths)
    try:
        row = conn.execute("SELECT message_text FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return
        old = str(row["message_text"] or "").strip()
        merged = text.strip() if not old else f"{old}\n\n{text.strip()}"
        conn.execute(
            "UPDATE jobs SET message_text=?, updated_at=? WHERE job_id=?",
            (merged, utc_now_iso(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _get_job_info(*, work_root: Path, job_id: str) -> dict[str, Any]:
    paths = ensure_runtime_paths(work_root)
    conn = db_connect(paths)
    try:
        job = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        files = list_job_files(conn, job_id)
        docx_count = sum(1 for item in files if Path(item["path"]).suffix.lower() == ".docx")
        return {
            "job_exists": bool(job),
            "inbox_dir": str(job["inbox_dir"]) if job else "",
            "docx_count": docx_count,
            "files_count": len(files),
        }
    finally:
        conn.close()


def _set_collecting_status(*, work_root: Path, job_id: str) -> None:
    paths = ensure_runtime_paths(work_root)
    conn = db_connect(paths)
    try:
        update_job_status(conn, job_id=job_id, status="collecting")
    finally:
        conn.close()


def _resolve_reply_target(sender: str, fallback_target: str) -> str:
    s = (sender or "").strip()
    return s if s and s.lower() != "unknown" else fallback_target


def _notify_target(*, target: str, message: str, dry_run: bool = False) -> dict[str, Any]:
    return send_whatsapp_message(target=target, message=message, dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload-json")
    parser.add_argument("--payload-file")
    parser.add_argument("--payload-stdin", default="")
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    parser.add_argument("--kb-root", default=str(DEFAULT_KB_ROOT))
    parser.add_argument("--notify-target", default=DEFAULT_NOTIFY_TARGET)
    parser.add_argument("--auto-run", action="store_true")
    parser.add_argument("--dry-run-notify", action="store_true")
    parser.add_argument("--bundle-window-seconds", type=int, default=int(os.getenv("V4_WA_BUNDLE_WINDOW_SECONDS", "900")))
    args = parser.parse_args()

    payload = _load_payload(args)
    text = _extract_text(payload)
    sender = _extract_sender(payload)
    work_root = Path(args.work_root)
    kb_root = Path(args.kb_root)
    reply_target = _resolve_reply_target(sender, args.notify_target)

    attachments = _collect_attachments(payload)

    if text and _is_command(text) and not attachments:
        result = handle_command(
            command_text=text,
            work_root=work_root,
            kb_root=kb_root,
            target=reply_target,
            sender=sender,
            dry_run_notify=args.dry_run_notify,
        )
        print(json.dumps({"ok": bool(result.get("ok")), "mode": "command", "result": result}, ensure_ascii=False))
        return 0 if result.get("ok") else 1

    existing_job_id = _find_recent_collecting_job(
        work_root=work_root,
        sender=sender,
        window_seconds=args.bundle_window_seconds,
    )
    if existing_job_id:
        job_id = existing_job_id
        info = _get_job_info(work_root=work_root, job_id=job_id)
        inbox_dir = Path(info.get("inbox_dir") or work_root / "_INBOX" / "whatsapp" / job_id)
        inbox_dir.mkdir(parents=True, exist_ok=True)
        _append_job_message(work_root=work_root, job_id=job_id, text=text)
    else:
        job_id = make_job_id("whatsapp")
        inbox_dir = work_root.expanduser().resolve() / "_INBOX" / "whatsapp" / job_id
        inbox_dir.mkdir(parents=True, exist_ok=True)
        envelope = create_job(
            source="whatsapp",
            sender=sender,
            subject="WhatsApp Task",
            message_text=text,
            inbox_dir=inbox_dir,
            job_id=job_id,
            work_root=work_root,
        )
        _set_collecting_status(work_root=work_root, job_id=job_id)

    (inbox_dir / "message.txt").write_text(text, encoding="utf-8")
    ts_name = datetime.now(UTC).strftime("payload_%Y%m%d_%H%M%S.json")
    (inbox_dir / ts_name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    saved_files: list[str] = []
    for idx, item in enumerate(attachments, start=1):
        file_name = item.get("name") or item.get("fileName") or f"wa_attachment_{idx}"
        target_path = inbox_dir / file_name
        if target_path.exists():
            stem = target_path.stem
            suffix = target_path.suffix
            target_path = inbox_dir / f"{stem}_{idx}_{int(datetime.now(UTC).timestamp())}{suffix}"
        if item.get("path"):
            src = Path(str(item["path"])).expanduser()
            if src.exists():
                shutil.copy2(src, target_path)
            else:
                continue
        elif item.get("local_path"):
            src = Path(str(item["local_path"])).expanduser()
            if src.exists():
                shutil.copy2(src, target_path)
            else:
                continue
        elif item.get("content_base64"):
            target_path.write_bytes(base64.b64decode(item["content_base64"].encode("utf-8")))
        else:
            continue
        attach_file_to_job(work_root=work_root, job_id=job_id, path=target_path)
        saved_files.append(str(target_path.resolve()))

    info = _get_job_info(work_root=work_root, job_id=job_id)
    should_run = args.auto_run and text.lower().strip().startswith("run")
    run_result: dict[str, Any] | None = None
    if should_run:
        _notify_target(
            target=reply_target,
            message=f"[{job_id}] run accepted. Starting pipeline...",
            dry_run=args.dry_run_notify,
        )
        run_result = run_job_pipeline(
            job_id=job_id,
            work_root=work_root,
            kb_root=kb_root,
            notify_target=reply_target,
            dry_run_notify=args.dry_run_notify,
        )

    if not should_run:
        if saved_files:
            intake_msg = (
                f"[{job_id}] collecting update: received {len(saved_files)} file(s). "
                f"Current totals: docx={info.get('docx_count', 0)}, all_files={info.get('files_count', 0)}. "
                "Send 'run' when done."
            )
        elif text.strip():
            intake_msg = f"[{job_id}] task note received. You can continue sending files, then send 'run'."
        else:
            intake_msg = f"[{job_id}] message received. Continue sending files, then send 'run'."
        _notify_target(target=reply_target, message=intake_msg, dry_run=args.dry_run_notify)

    response = {
        "ok": True if (not should_run or (run_result and run_result.get("ok"))) else False,
        "mode": "task_bundle",
        "job_id": job_id,
        "sender": sender,
        "saved_files": saved_files,
        "docx_count": info.get("docx_count", 0),
        "files_count": info.get("files_count", 0),
        "status": "running" if should_run else "collecting",
        "hint": "Files were bundled into one job. Send 'run' to start processing." if not should_run else "Run started.",
    }
    if run_result is not None:
        response["run_result"] = run_result
    print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

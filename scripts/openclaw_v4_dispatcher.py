#!/usr/bin/env python3
"""V5.2 dispatcher for OpenClaw full orchestration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from scripts.skill_approval import handle_command
from scripts.v4_kb import sync_kb
from scripts.v4_pipeline import run_job_pipeline
from scripts.v4_runtime import (
    DEFAULT_KB_ROOT,
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_WORK_ROOT,
    db_connect,
    ensure_runtime_paths,
)


def _parse_whatsapp_payload(payload_file: str) -> dict[str, Any]:
    return json.loads(Path(payload_file).read_text(encoding="utf-8"))


def cmd_email_poll(args: argparse.Namespace) -> int:
    from scripts.skill_email_ingest import main as email_main  # lazy import

    argv = [
        "skill_email_ingest.py",
        "--imap-host",
        args.imap_host,
        "--imap-port",
        str(args.imap_port),
        "--imap-user",
        args.imap_user,
        "--imap-password",
        args.imap_password,
        "--mailbox",
        args.mailbox,
        "--from-filter",
        args.from_filter,
        "--max-messages",
        str(args.max_messages),
        "--work-root",
        str(args.work_root),
        "--kb-root",
        str(args.kb_root),
        "--notify-target",
        args.notify_target,
    ]
    if args.auto_run:
        argv.append("--auto-run")
    if args.mark_seen:
        argv.append("--mark-seen")
    os.sys.argv = argv
    return email_main()


def cmd_whatsapp_event(args: argparse.Namespace) -> int:
    from scripts.skill_whatsapp_ingest import main as wa_main  # lazy import

    argv = [
        "skill_whatsapp_ingest.py",
        "--work-root",
        str(args.work_root),
        "--kb-root",
        str(args.kb_root),
        "--notify-target",
        args.notify_target,
    ]
    if args.payload_json:
        argv.extend(["--payload-json", args.payload_json])
    if args.payload_file:
        argv.extend(["--payload-file", args.payload_file])
    if args.auto_run:
        argv.append("--auto-run")
    os.sys.argv = argv
    return wa_main()


def cmd_run_job(args: argparse.Namespace) -> int:
    result = run_job_pipeline(
        job_id=args.job_id,
        work_root=Path(args.work_root),
        kb_root=Path(args.kb_root),
        notify_target=args.notify_target,
        dry_run_notify=args.dry_run_notify,
    )
    print(json.dumps({"ok": bool(result.get("ok")), "result": result}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_kb_sync(args: argparse.Namespace) -> int:
    paths = ensure_runtime_paths(Path(args.work_root))
    conn = db_connect(paths)
    report_path = paths.kb_system_root / "kb_sync_latest.json"
    report = sync_kb(conn=conn, kb_root=Path(args.kb_root), report_path=report_path)
    conn.close()
    print(json.dumps({"ok": report.get("ok", True), "report": report, "report_path": str(report_path)}, ensure_ascii=False))
    return 0 if report.get("ok", True) else 1


def cmd_pending_reminder(args: argparse.Namespace) -> int:
    from scripts.skill_pending_reminder import main as reminder_main  # lazy import

    argv = [
        "skill_pending_reminder.py",
        "--work-root",
        str(args.work_root),
        "--target",
        args.notify_target,
    ]
    if args.dry_run_notify:
        argv.append("--dry-run")
    os.sys.argv = argv
    return reminder_main()


def cmd_approval(args: argparse.Namespace) -> int:
    result = handle_command(
        command_text=args.command,
        work_root=Path(args.work_root),
        kb_root=Path(args.kb_root),
        target=args.notify_target,
        sender=args.sender,
        dry_run_notify=args.dry_run_notify,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw V5.2 dispatcher")
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    parser.add_argument("--kb-root", default=str(DEFAULT_KB_ROOT))
    parser.add_argument("--notify-target", default=DEFAULT_NOTIFY_TARGET)
    parser.add_argument("--dry-run-notify", action="store_true")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_email = sub.add_parser("email-poll")
    p_email.add_argument("--imap-host", required=True)
    p_email.add_argument("--imap-port", type=int, default=993)
    p_email.add_argument("--imap-user", required=True)
    p_email.add_argument("--imap-password", required=True)
    p_email.add_argument("--mailbox", default="INBOX")
    p_email.add_argument("--from-filter", default="modeh@eventranz.com")
    p_email.add_argument("--max-messages", type=int, default=5)
    p_email.add_argument("--auto-run", action="store_true")
    p_email.add_argument("--mark-seen", action="store_true")

    p_wa = sub.add_parser("whatsapp-event")
    p_wa.add_argument("--payload-json")
    p_wa.add_argument("--payload-file")
    p_wa.add_argument("--auto-run", action="store_true")

    p_job = sub.add_parser("run-job")
    p_job.add_argument("--job-id", required=True)

    sub.add_parser("kb-sync")
    sub.add_parser("pending-reminder")

    p_ap = sub.add_parser("approval")
    p_ap.add_argument("--command", required=True)
    p_ap.add_argument("--sender", default="")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.cmd == "email-poll":
        return cmd_email_poll(args)
    if args.cmd == "whatsapp-event":
        return cmd_whatsapp_event(args)
    if args.cmd == "run-job":
        return cmd_run_job(args)
    if args.cmd == "kb-sync":
        return cmd_kb_sync(args)
    if args.cmd == "pending-reminder":
        return cmd_pending_reminder(args)
    if args.cmd == "approval":
        return cmd_approval(args)

    parser.error(f"unsupported cmd: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

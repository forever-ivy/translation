#!/usr/bin/env python3
"""OpenClaw skill: ingest task emails from IMAP and create jobs."""

from __future__ import annotations

import argparse
import email
import imaplib
import json
import re
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from scripts.v4_pipeline import attach_file_to_job, create_job, run_job_pipeline
from scripts.v4_runtime import (
    DEFAULT_KB_ROOT,
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_WORK_ROOT,
    db_connect,
    ensure_runtime_paths,
    make_job_id,
    set_sender_active_job,
    mark_mailbox_uid_seen,
    mailbox_uid_seen,
    update_job_status,
)


def _decode_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="ignore")
        except Exception:
            return value.decode(errors="ignore")
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _extract_plain_text(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if ctype == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="ignore")
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if ctype == "text/html" and "attachment" not in disp:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                html = payload.decode(charset, errors="ignore")
                return re.sub(r"<[^>]+>", " ", html)
    payload = msg.get_payload(decode=True) or b""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="ignore")


def _iter_attachments(msg: Message) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        decoded_name = _decode_text(filename)
        payload = part.get_payload(decode=True) or b""
        out.append((decoded_name, payload))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--imap-host", required=True)
    parser.add_argument("--imap-port", type=int, default=993)
    parser.add_argument("--imap-user", required=True)
    parser.add_argument("--imap-password", required=True)
    parser.add_argument("--mailbox", default="INBOX")
    parser.add_argument("--from-filter", default="modeh@eventranz.com")
    parser.add_argument("--max-messages", type=int, default=5)
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    parser.add_argument("--kb-root", default=str(DEFAULT_KB_ROOT))
    parser.add_argument("--notify-target", default=DEFAULT_NOTIFY_TARGET)
    parser.add_argument("--auto-run", action="store_true")
    parser.add_argument("--mark-seen", action="store_true")
    args = parser.parse_args()

    paths = ensure_runtime_paths(Path(args.work_root))
    conn = db_connect(paths)

    jobs: list[dict[str, Any]] = []
    with imaplib.IMAP4_SSL(args.imap_host, args.imap_port) as imap:
        imap.login(args.imap_user, args.imap_password)
        sel_status, sel_data = imap.select(args.mailbox)
        if sel_status != "OK":
            conn.close()
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "imap_select_failed",
                        "mailbox": args.mailbox,
                        "detail": [d.decode("utf-8", errors="ignore") if isinstance(d, bytes) else str(d) for d in (sel_data or [])],
                        "hint": "Mailbox select failed. For 163 mail, enable IMAP and use client authorization code.",
                    },
                    ensure_ascii=False,
                )
            )
            return 1
        status, data = imap.uid("search", None, "UNSEEN")
        if status != "OK":
            conn.close()
            print(json.dumps({"ok": False, "error": "imap_search_failed"}, ensure_ascii=False))
            return 1

        uids = [u for u in (data[0].decode().split() if data and data[0] else []) if u]
        uids = list(reversed(uids))[: max(1, args.max_messages)]

        for uid in uids:
            if mailbox_uid_seen(conn, args.mailbox, uid):
                continue
            st, msg_data = imap.uid("fetch", uid, "(RFC822)")
            if st != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            from_addr = parseaddr(_decode_text(msg.get("From")))[1].lower()
            subject = _decode_text(msg.get("Subject"))
            if args.from_filter and args.from_filter.lower() not in from_addr:
                mark_mailbox_uid_seen(conn, args.mailbox, uid)
                continue

            job_id = make_job_id("email")
            inbox_dir = paths.inbox_email / job_id
            inbox_dir.mkdir(parents=True, exist_ok=True)
            (inbox_dir / "raw.eml").write_bytes(raw)
            body = _extract_plain_text(msg).strip()
            (inbox_dir / "message.txt").write_text(body, encoding="utf-8")
            (inbox_dir / "metadata.json").write_text(
                json.dumps(
                    {
                        "uid": uid,
                        "from": from_addr,
                        "subject": subject,
                        "mailbox": args.mailbox,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            envelope = create_job(
                source="email",
                sender=from_addr,
                subject=subject,
                message_text=body,
                inbox_dir=inbox_dir,
                job_id=job_id,
                work_root=Path(args.work_root),
                active_sender=args.notify_target,
            )
            if not args.auto_run:
                update_job_status(conn, job_id=job_id, status="collecting", errors=[])
                set_sender_active_job(conn, sender=args.notify_target, job_id=job_id)

            attachments = _iter_attachments(msg)
            for idx, (name, payload) in enumerate(attachments, start=1):
                safe_name = name or f"attachment_{idx}"
                target = inbox_dir / safe_name
                target.write_bytes(payload)
                attach_file_to_job(work_root=Path(args.work_root), job_id=job_id, path=target)

            if args.auto_run:
                result = run_job_pipeline(
                    job_id=job_id,
                    work_root=Path(args.work_root),
                    kb_root=Path(args.kb_root),
                    notify_target=args.notify_target,
                )
                envelope["run_result"] = result
            else:
                send_msg = (
                    f"[{job_id}] collecting_update from email. "
                    f"Received {len(attachments)} attachment(s). Send 'run' to start."
                )
                from scripts.v4_runtime import send_whatsapp_message  # local import to avoid cycles

                send_whatsapp_message(target=args.notify_target, message=send_msg, dry_run=False)

            jobs.append(envelope)
            mark_mailbox_uid_seen(conn, args.mailbox, uid)
            if args.mark_seen:
                imap.uid("store", uid, "+FLAGS", "(\\Seen)")

    conn.close()
    print(json.dumps({"ok": True, "count": len(jobs), "jobs": jobs}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

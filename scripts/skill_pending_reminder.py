#!/usr/bin/env python3
"""OpenClaw skill: remind pending jobs twice per day."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.v4_runtime import (
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_WORK_ROOT,
    db_connect,
    ensure_runtime_paths,
    list_jobs_by_status,
    record_event,
    send_whatsapp_message,
)


def _slot_key(now: datetime) -> str:
    return "am" if now.hour < 12 else "pm"


def _has_reminded_in_slot(conn, *, job_id: str, date_key: str, slot: str) -> bool:
    milestone = f"pending_reminder:{date_key}:{slot}"
    row = conn.execute("SELECT 1 FROM events WHERE job_id=? AND milestone=? LIMIT 1", (job_id, milestone)).fetchone()
    return row is not None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    parser.add_argument("--target", default=DEFAULT_NOTIFY_TARGET)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now = datetime.now(UTC)
    date_key = now.strftime("%Y-%m-%d")
    slot = _slot_key(now)

    paths = ensure_runtime_paths(Path(args.work_root))
    conn = db_connect(paths)
    pending = list_jobs_by_status(conn, ["review_ready", "needs_attention", "running_timeout", "needs_revision"])

    sent_items: list[dict[str, Any]] = []
    overdue_items: list[str] = []
    for job in pending:
        job_id = job["job_id"]
        if _has_reminded_in_slot(conn, job_id=job_id, date_key=date_key, slot=slot):
            continue

        age_hours = 0.0
        try:
            created = datetime.fromisoformat(job["created_at"].replace("Z", "+00:00"))
            age_hours = max(0.0, (now - created).total_seconds() / 3600.0)
        except Exception:
            age_hours = 0.0

        next_cmd = "status"
        if job["status"] == "review_ready":
            next_cmd = "ok"
        elif job["status"] in {"needs_attention", "running_timeout", "needs_revision"}:
            next_cmd = "rerun"

        summary = (
            f"[{job_id}] pending status={job['status']} age={age_hours:.1f}h. "
            f"Next: {next_cmd}"
        )
        if slot == "pm" and age_hours >= 24:
            summary += " | Over 24h pending: please prioritize."
            overdue_items.append(f"{job_id}({job['status']},{age_hours:.1f}h)")

        send_result = send_whatsapp_message(target=args.target, message=summary, dry_run=args.dry_run)
        milestone = f"pending_reminder:{date_key}:{slot}"
        record_event(
            conn,
            job_id=job_id,
            milestone=milestone,
            payload={"target": args.target, "message": summary, "send_result": send_result},
        )
        sent_items.append({"job_id": job_id, "status": job["status"], "message": summary, "send_result": send_result})

    if slot == "pm" and overdue_items:
        brief = "Pending summary (>24h): " + "; ".join(overdue_items[:12])
        send_result = send_whatsapp_message(target=args.target, message=brief, dry_run=args.dry_run)
        record_event(
            conn,
            job_id="",
            milestone=f"pending_summary:{date_key}:{slot}",
            payload={"target": args.target, "message": brief, "send_result": send_result},
        )

    conn.close()
    print(json.dumps({"ok": True, "date": date_key, "slot": slot, "sent_count": len(sent_items), "items": sent_items}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

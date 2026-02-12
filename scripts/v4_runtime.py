#!/usr/bin/env python3
"""Shared runtime utilities for OpenClaw V4.1 workflow."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_KB_ROOT = Path("/Users/ivy/Library/CloudStorage/OneDrive-Personal/Knowledge Repository")
DEFAULT_WORK_ROOT = Path("/Users/ivy/Library/CloudStorage/OneDrive-Personal/Translation Task")
DEFAULT_NOTIFY_TARGET = os.getenv("OPENCLAW_NOTIFY_TARGET") or os.getenv("WHATSAPP_TO") or "+8615071054627"
DEFAULT_NOTIFY_CHANNEL = os.getenv("OPENCLAW_NOTIFY_CHANNEL") or "whatsapp"
DEFAULT_NOTIFY_ACCOUNT = os.getenv("OPENCLAW_NOTIFY_ACCOUNT") or "default"

KB_SUPPORTED_EXTENSIONS = {".docx", ".pdf", ".md", ".txt", ".xlsx", ".csv"}
TASK_DOC_EXTENSIONS = {".docx"}

SOURCE_GROUP_WEIGHTS = {
    "glossary": 1.7,
    "previously_translated": 1.4,
    "translated_en": 1.2,
    "arabic_source": 1.0,
    "general": 0.8,
}


@dataclass(frozen=True)
class RuntimePaths:
    work_root: Path
    inbox_email: Path
    inbox_whatsapp: Path
    review_root: Path
    translated_root: Path
    system_root: Path
    jobs_root: Path
    kb_root: Path
    kb_system_root: Path
    db_path: Path
    logs_root: Path


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def date_key_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def make_job_id(source: str) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    tail = uuid.uuid4().hex[:8]
    clean_source = source.lower().replace(" ", "_")
    return f"job_{clean_source}_{ts}_{tail}"


def ensure_runtime_paths(work_root: Path | str = DEFAULT_WORK_ROOT) -> RuntimePaths:
    root = Path(work_root).expanduser().resolve()
    system_root = root / ".system"
    jobs_root = system_root / "jobs"
    kb_system_root = system_root / "kb"
    logs_root = system_root / "logs"
    inbox_email = root / "_INBOX" / "email"
    inbox_whatsapp = root / "_INBOX" / "whatsapp"
    review_root = root / "Translated -EN" / "_VERIFY"
    translated_root = root / "Translated -EN"

    for p in [jobs_root, kb_system_root, logs_root, inbox_email, inbox_whatsapp, review_root, translated_root]:
        p.mkdir(parents=True, exist_ok=True)

    return RuntimePaths(
        work_root=root,
        inbox_email=inbox_email,
        inbox_whatsapp=inbox_whatsapp,
        review_root=review_root,
        translated_root=translated_root,
        system_root=system_root,
        jobs_root=jobs_root,
        kb_root=DEFAULT_KB_ROOT,
        kb_system_root=kb_system_root,
        db_path=jobs_root / "state.sqlite",
        logs_root=logs_root,
    )


def db_connect(paths: RuntimePaths) -> sqlite3.Connection:
    conn = sqlite3.connect(str(paths.db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            sender TEXT DEFAULT '',
            subject TEXT DEFAULT '',
            message_text TEXT DEFAULT '',
            status TEXT NOT NULL,
            inbox_dir TEXT NOT NULL,
            review_dir TEXT NOT NULL,
            task_type TEXT DEFAULT '',
            confidence REAL DEFAULT 0,
            estimated_minutes INTEGER DEFAULT 0,
            runtime_timeout_minutes INTEGER DEFAULT 0,
            iteration_count INTEGER DEFAULT 0,
            double_pass INTEGER DEFAULT 0,
            status_flags_json TEXT DEFAULT '[]',
            artifacts_json TEXT DEFAULT '{}',
            errors_json TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_reminded_at TEXT DEFAULT '',
            remind_count_today INTEGER DEFAULT 0,
            remind_day TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS job_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            path TEXT NOT NULL,
            name TEXT NOT NULL,
            mime_type TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_job_files_job_id ON job_files(job_id);

        CREATE TABLE IF NOT EXISTS mail_seen (
            mailbox TEXT NOT NULL,
            uid TEXT NOT NULL,
            seen_at TEXT NOT NULL,
            PRIMARY KEY(mailbox, uid)
        );

        CREATE TABLE IF NOT EXISTS kb_files (
            path TEXT PRIMARY KEY,
            mtime_ns INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            parser TEXT NOT NULL,
            source_group TEXT NOT NULL,
            chunk_count INTEGER NOT NULL,
            indexed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS kb_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            source_group TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_kb_chunks_path ON kb_chunks(path);
        CREATE INDEX IF NOT EXISTS idx_kb_chunks_source ON kb_chunks(source_group);

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT DEFAULT '',
            milestone TEXT NOT NULL,
            payload_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_job_id ON events(job_id);

        CREATE TABLE IF NOT EXISTS sender_active_jobs (
            sender TEXT PRIMARY KEY,
            active_job_id TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(active_job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()


def infer_source_group(path: Path, kb_root: Path | None = None) -> str:
    full = str(path).lower()
    if "glossery" in full or "glossary" in full:
        return "glossary"
    if "previously translated" in full:
        return "previously_translated"
    if "translated -en" in full:
        return "translated_en"
    if "arabic source" in full:
        return "arabic_source"
    if kb_root and str(kb_root).lower() in full:
        return "general"
    return "general"


def compute_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def record_event(conn: sqlite3.Connection, *, job_id: str, milestone: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO events(job_id, milestone, payload_json, created_at) VALUES(?,?,?,?)",
        (job_id, milestone, json.dumps(payload, ensure_ascii=False), utc_now_iso()),
    )
    conn.commit()


def write_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    source: str,
    sender: str,
    subject: str,
    message_text: str,
    status: str,
    inbox_dir: Path,
    review_dir: Path,
) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO jobs(
            job_id, source, sender, subject, message_text, status, inbox_dir, review_dir, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(job_id) DO UPDATE SET
            source=excluded.source,
            sender=excluded.sender,
            subject=excluded.subject,
            message_text=excluded.message_text,
            status=excluded.status,
            inbox_dir=excluded.inbox_dir,
            review_dir=excluded.review_dir,
            updated_at=excluded.updated_at
        """,
        (
            job_id,
            source,
            sender,
            subject,
            message_text,
            status,
            str(inbox_dir.resolve()),
            str(review_dir.resolve()),
            now,
            now,
        ),
    )
    conn.commit()


def add_job_file(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    path: Path,
    mime_type: str = "",
) -> None:
    conn.execute(
        "INSERT INTO job_files(job_id, path, name, mime_type, created_at) VALUES(?,?,?,?,?)",
        (job_id, str(path.resolve()), path.name, mime_type, utc_now_iso()),
    )
    conn.commit()


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    for key, default in [
        ("status_flags_json", []),
        ("artifacts_json", {}),
        ("errors_json", []),
    ]:
        raw = data.get(key, "")
        try:
            data[key] = json.loads(raw) if raw else default
        except json.JSONDecodeError:
            data[key] = default
    return data


def list_job_files(conn: sqlite3.Connection, job_id: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM job_files WHERE job_id=? ORDER BY id ASC", (job_id,)).fetchall()
    return [dict(r) for r in rows]


def update_job_plan(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    status: str,
    task_type: str,
    confidence: float,
    estimated_minutes: int,
    runtime_timeout_minutes: int,
) -> None:
    conn.execute(
        """
        UPDATE jobs
        SET status=?, task_type=?, confidence=?, estimated_minutes=?, runtime_timeout_minutes=?, updated_at=?
        WHERE job_id=?
        """,
        (status, task_type, confidence, estimated_minutes, runtime_timeout_minutes, utc_now_iso(), job_id),
    )
    conn.commit()


def update_job_result(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    status: str,
    iteration_count: int,
    double_pass: bool,
    status_flags: list[str],
    artifacts: dict[str, Any],
    errors: list[str],
) -> None:
    conn.execute(
        """
        UPDATE jobs
        SET status=?, iteration_count=?, double_pass=?, status_flags_json=?, artifacts_json=?, errors_json=?, updated_at=?
        WHERE job_id=?
        """,
        (
            status,
            int(iteration_count),
            1 if double_pass else 0,
            json.dumps(status_flags, ensure_ascii=False),
            json.dumps(artifacts, ensure_ascii=False),
            json.dumps(errors, ensure_ascii=False),
            utc_now_iso(),
            job_id,
        ),
    )
    conn.commit()


def update_job_status(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    status: str,
    status_flags: list[str] | None = None,
    errors: list[str] | None = None,
) -> None:
    record = get_job(conn, job_id)
    if not record:
        return
    sf = status_flags if status_flags is not None else record.get("status_flags_json", [])
    er = errors if errors is not None else record.get("errors_json", [])
    conn.execute(
        """
        UPDATE jobs
        SET status=?, status_flags_json=?, errors_json=?, updated_at=?
        WHERE job_id=?
        """,
        (status, json.dumps(sf, ensure_ascii=False), json.dumps(er, ensure_ascii=False), utc_now_iso(), job_id),
    )
    conn.commit()


def list_jobs_by_status(conn: sqlite3.Connection, statuses: list[str]) -> list[dict[str, Any]]:
    if not statuses:
        return []
    ph = ",".join("?" for _ in statuses)
    rows = conn.execute(f"SELECT * FROM jobs WHERE status IN ({ph}) ORDER BY updated_at ASC", tuple(statuses)).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        job = dict(row)
        for key, default in [("status_flags_json", []), ("artifacts_json", {}), ("errors_json", [])]:
            try:
                job[key] = json.loads(job.get(key) or "")
            except json.JSONDecodeError:
                job[key] = default
        out.append(job)
    return out


def set_sender_active_job(conn: sqlite3.Connection, *, sender: str, job_id: str) -> None:
    sender_norm = (sender or "").strip()
    if not sender_norm:
        return
    conn.execute(
        """
        INSERT INTO sender_active_jobs(sender, active_job_id, updated_at)
        VALUES(?,?,?)
        ON CONFLICT(sender) DO UPDATE SET
            active_job_id=excluded.active_job_id,
            updated_at=excluded.updated_at
        """,
        (sender_norm, job_id, utc_now_iso()),
    )
    conn.commit()


def clear_sender_active_job(conn: sqlite3.Connection, *, sender: str, only_if_job_id: str | None = None) -> None:
    sender_norm = (sender or "").strip()
    if not sender_norm:
        return
    if only_if_job_id:
        conn.execute(
            "DELETE FROM sender_active_jobs WHERE sender=? AND active_job_id=?",
            (sender_norm, only_if_job_id),
        )
    else:
        conn.execute("DELETE FROM sender_active_jobs WHERE sender=?", (sender_norm,))
    conn.commit()


def get_sender_active_job(conn: sqlite3.Connection, *, sender: str) -> str | None:
    sender_norm = (sender or "").strip()
    if not sender_norm:
        return None
    row = conn.execute(
        "SELECT active_job_id FROM sender_active_jobs WHERE sender=?",
        (sender_norm,),
    ).fetchone()
    if not row:
        return None
    return str(row["active_job_id"])


def list_actionable_jobs_for_sender(conn: sqlite3.Connection, *, sender: str, limit: int = 20) -> list[dict[str, Any]]:
    sender_norm = (sender or "").strip()
    if not sender_norm:
        return []
    rows = conn.execute(
        """
        SELECT *
        FROM jobs
        WHERE sender=?
          AND status NOT IN ('verified', 'failed')
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (sender_norm, max(1, int(limit))),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_actionable_job(conn: sqlite3.Connection, *, sender: str | None = None) -> dict[str, Any] | None:
    if sender:
        rows = list_actionable_jobs_for_sender(conn, sender=sender, limit=1)
        return rows[0] if rows else None
    row = conn.execute(
        """
        SELECT *
        FROM jobs
        WHERE status NOT IN ('verified', 'failed')
        ORDER BY updated_at DESC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None


def mailbox_uid_seen(conn: sqlite3.Connection, mailbox: str, uid: str) -> bool:
    row = conn.execute("SELECT 1 FROM mail_seen WHERE mailbox=? AND uid=?", (mailbox, uid)).fetchone()
    return row is not None


def mark_mailbox_uid_seen(conn: sqlite3.Connection, mailbox: str, uid: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO mail_seen(mailbox, uid, seen_at) VALUES(?,?,?)",
        (mailbox, uid, utc_now_iso()),
    )
    conn.commit()


def append_log(paths: RuntimePaths, file_name: str, line: str) -> None:
    log_path = paths.logs_root / file_name
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def send_whatsapp_message(
    *,
    target: str,
    message: str,
    channel: str = DEFAULT_NOTIFY_CHANNEL,
    account: str = DEFAULT_NOTIFY_ACCOUNT,
    dry_run: bool = False,
) -> dict[str, Any]:
    cmd = [
        "openclaw",
        "message",
        "send",
        "--channel",
        channel,
        "--account",
        account,
        "--target",
        target,
        "--message",
        message,
        "--json",
    ]
    if dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    payload: dict[str, Any] = {"ok": proc.returncode == 0, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip()}
    if proc.stdout.strip().startswith("{"):
        try:
            payload["response"] = json.loads(proc.stdout)
        except json.JSONDecodeError:
            pass
    return payload


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)

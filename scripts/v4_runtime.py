#!/usr/bin/env python3
"""Shared runtime utilities for OpenClaw V4.1 workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import shutil
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_KB_ROOT = Path("/Users/ivy/Library/CloudStorage/OneDrive-Personal/Knowledge Repository")
DEFAULT_WORK_ROOT = Path("/Users/ivy/Library/CloudStorage/OneDrive-Personal/Translation Task")
DEFAULT_NOTIFY_TARGET = os.getenv("OPENCLAW_NOTIFY_TARGET") or os.getenv("TELEGRAM_CHAT_ID") or ""
DEFAULT_NOTIFY_CHANNEL = os.getenv("OPENCLAW_NOTIFY_CHANNEL") or "telegram"
DEFAULT_NOTIFY_ACCOUNT = os.getenv("OPENCLAW_NOTIFY_ACCOUNT") or "default"
DEFAULT_LOCAL_STATE_DB = Path("~/.openclaw/runtime/translation/state.sqlite").expanduser()

KB_SUPPORTED_EXTENSIONS = {".docx", ".pdf", ".md", ".txt", ".xlsx", ".csv"}
TASK_DOC_EXTENSIONS = {".docx"}

SOURCE_GROUP_WEIGHTS = {
    "glossary": 1.7,
    "previously_translated": 1.4,
    "translated_output": 1.2,   # Generic translated output
    "translated_en": 1.2,       # Backward compatible
    "source_text": 1.0,         # Generic source text
    "arabic_source": 1.0,       # Backward compatible
    "general": 0.8,
}

_MEMORIES_FTS_AVAILABLE: bool | None = None


@dataclass(frozen=True)
class RuntimePaths:
    work_root: Path
    inbox_email: Path
    inbox_messaging: Path
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


def slugify_identifier(value: str, *, max_len: int = 48, default_prefix: str = "id") -> str:
    """Generate a stable ASCII slug for IDs/collections/paths.

    - Prefer human-readable ascii when possible (NFKD -> ascii).
    - If nothing survives (e.g. Arabic/Chinese), fall back to a short SHA1.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKD", raw)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    if not cleaned:
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        cleaned = f"{default_prefix}-{digest}"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip("-")
    return cleaned


def resolve_rag_collection(
    *,
    base_collection: str,
    company: str,
    mode: str,
    isolation_mode: str,
) -> str:
    """Resolve the ClawRAG collection name.

    Modes:
    - shared: use base_collection as-is
    - per_company: suffix base_collection with company slug
    - auto: per_company when isolation_mode=company_strict and company is set; otherwise shared

    Placeholder:
    - If base_collection contains "{company}", it is replaced by the company slug.
    """
    base = (base_collection or "").strip() or "translation-kb"
    mode_norm = (mode or "").strip().lower() or "auto"
    isolation_norm = (isolation_mode or "").strip().lower()
    company_norm = (company or "").strip()
    company_slug = slugify_identifier(company_norm, default_prefix="company") if company_norm else ""

    if "{company}" in base:
        return base.replace("{company}", company_slug or "unknown")

    if mode_norm == "auto":
        mode_norm = "per_company" if isolation_norm == "company_strict" and company_norm else "shared"

    if mode_norm in {"per_company", "per-company", "company", "company_scoped", "company-scoped"} and company_norm:
        if company_slug and (base.endswith(f"-{company_slug}") or base.endswith(f"_{company_slug}")):
            return base
        return f"{base}-{company_slug}" if company_slug else base

    return base


def ensure_runtime_paths(work_root: Path | str = DEFAULT_WORK_ROOT) -> RuntimePaths:
    root = Path(work_root).expanduser().resolve()
    system_root = root / ".system"
    jobs_root = system_root / "jobs"
    kb_system_root = system_root / "kb"
    logs_root = system_root / "logs"
    inbox_email = root / "_INBOX" / "email"
    inbox_messaging = root / "_INBOX" / "telegram"
    review_root = root / "Translated -EN" / "_VERIFY"
    translated_root = root / "Translated -EN"

    for p in [jobs_root, kb_system_root, logs_root, inbox_email, inbox_messaging, review_root, translated_root]:
        p.mkdir(parents=True, exist_ok=True)

    # SQLite on cloud-sync placeholders (e.g., OneDrive File Provider) is prone to "disk I/O error".
    # Keep runtime DB local by default when work root is cloud-backed, unless explicitly overridden.
    db_override = str(os.getenv("OPENCLAW_STATE_DB_PATH", "")).strip()
    if db_override:
        db_path = Path(db_override).expanduser().resolve()
    else:
        root_text = str(root)
        if "/Library/CloudStorage/" in root_text or "OneDrive" in root_text:
            db_path = DEFAULT_LOCAL_STATE_DB.resolve()
        else:
            db_path = jobs_root / "state.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return RuntimePaths(
        work_root=root,
        inbox_email=inbox_email,
        inbox_messaging=inbox_messaging,
        review_root=review_root,
        translated_root=translated_root,
        system_root=system_root,
        jobs_root=jobs_root,
        kb_root=DEFAULT_KB_ROOT,
        kb_system_root=kb_system_root,
        db_path=db_path,
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
            task_label TEXT DEFAULT '',
            kb_company TEXT DEFAULT '',
            archive_project TEXT DEFAULT '',
            archived_at TEXT DEFAULT '',
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

        CREATE TABLE IF NOT EXISTS job_interactions (
            job_id TEXT PRIMARY KEY,
            sender TEXT DEFAULT '',
            pending_action TEXT DEFAULT '',
            options_json TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            expires_at TEXT DEFAULT '',
            final_uploads_json TEXT DEFAULT '[]',
            FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS job_run_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            state TEXT NOT NULL,
            attempt INTEGER NOT NULL DEFAULT 0,
            notify_target TEXT DEFAULT '',
            created_by_sender TEXT DEFAULT '',
            enqueued_at TEXT NOT NULL,
            available_at TEXT DEFAULT '',
            started_at TEXT DEFAULT '',
            finished_at TEXT DEFAULT '',
            heartbeat_at TEXT DEFAULT '',
            worker_id TEXT DEFAULT '',
            last_error TEXT DEFAULT '',
            cancel_requested_at TEXT DEFAULT '',
            cancel_requested_by TEXT DEFAULT '',
            cancel_reason TEXT DEFAULT '',
            cancel_mode TEXT DEFAULT '',
            pipeline_pid INTEGER NOT NULL DEFAULT 0,
            pipeline_pgid INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_job_run_queue_state_time ON job_run_queue(state, enqueued_at);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_job_run_queue_active ON job_run_queue(job_id) WHERE state IN ('queued', 'running');

        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            job_id TEXT DEFAULT '',
            kind TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memories_company ON memories(company);
        CREATE INDEX IF NOT EXISTS idx_memories_created_at ON memories(created_at);

        CREATE TABLE IF NOT EXISTS kb_rag_collections (
            collection TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            seeded_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    # Migrations for existing databases
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN task_label TEXT DEFAULT ''")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    for ddl in [
        "ALTER TABLE jobs ADD COLUMN kb_company TEXT DEFAULT ''",
        "ALTER TABLE jobs ADD COLUMN archive_project TEXT DEFAULT ''",
        "ALTER TABLE jobs ADD COLUMN archived_at TEXT DEFAULT ''",
    ]:
        try:
            conn.execute(ddl)
            conn.commit()
        except sqlite3.OperationalError:
            pass

    for ddl in [
        "ALTER TABLE job_run_queue ADD COLUMN available_at TEXT DEFAULT ''",
        "ALTER TABLE job_run_queue ADD COLUMN cancel_requested_at TEXT DEFAULT ''",
        "ALTER TABLE job_run_queue ADD COLUMN cancel_requested_by TEXT DEFAULT ''",
        "ALTER TABLE job_run_queue ADD COLUMN cancel_reason TEXT DEFAULT ''",
        "ALTER TABLE job_run_queue ADD COLUMN cancel_mode TEXT DEFAULT ''",
        "ALTER TABLE job_run_queue ADD COLUMN pipeline_pid INTEGER DEFAULT 0",
        "ALTER TABLE job_run_queue ADD COLUMN pipeline_pgid INTEGER DEFAULT 0",
    ]:
        try:
            conn.execute(ddl)
            conn.commit()
        except sqlite3.OperationalError:
            pass


def _ensure_memories_fts(conn: sqlite3.Connection) -> bool:
    """Best-effort: enable FTS5 BM25 retrieval for memories when supported."""
    global _MEMORIES_FTS_AVAILABLE
    if _MEMORIES_FTS_AVAILABLE is False:
        return False

    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    ).fetchone()
    if row:
        _MEMORIES_FTS_AVAILABLE = True
        return True

    try:
        conn.execute(
            "CREATE VIRTUAL TABLE memories_fts USING fts5(text, content='memories', content_rowid='id', tokenize='unicode61')"
        )
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
              INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
              INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.id, old.text);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
              INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.id, old.text);
              INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
            END;
            """
        )
        conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        conn.commit()
        _MEMORIES_FTS_AVAILABLE = True
        return True
    except sqlite3.OperationalError:
        _MEMORIES_FTS_AVAILABLE = False
        return False


def add_memory(
    conn: sqlite3.Connection,
    *,
    company: str,
    kind: str,
    text: str,
    job_id: str = "",
) -> None:
    company_norm = (company or "").strip()
    if not company_norm:
        return
    kind_norm = (kind or "").strip() or "decision"
    content = str(text or "").strip()
    if not content:
        return
    if len(content) > 4000:
        content = content[:4000] + "\n...(truncated)"
    conn.execute(
        "INSERT INTO memories(company, job_id, kind, text, created_at) VALUES(?,?,?,?,?)",
        (company_norm, (job_id or "").strip(), kind_norm, content, utc_now_iso()),
    )
    conn.commit()


def search_memories(
    conn: sqlite3.Connection,
    *,
    company: str,
    query: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    company_norm = (company or "").strip()
    q = (query or "").strip()
    if not company_norm or not q:
        return []

    tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9\u0600-\u06FF_]+", q) if len(t) >= 2]
    if not tokens:
        return []

    if _ensure_memories_fts(conn):
        match_query = " OR ".join(sorted(set(tokens)))
        rows = conn.execute(
            """
            SELECT
              m.id AS id,
              m.company AS company,
              m.job_id AS job_id,
              m.kind AS kind,
              substr(m.text, 1, 700) AS snippet,
              m.created_at AS created_at,
              bm25(memories_fts) AS rank
            FROM memories_fts
            JOIN memories m ON m.id = memories_fts.rowid
            WHERE memories_fts MATCH ?
              AND m.company=?
            LIMIT ?
            """,
            (match_query, company_norm, max(10, int(top_k) * 8)),
        ).fetchall()

        scored: list[dict[str, Any]] = []
        for row in rows:
            raw_rank = float(row["rank"] or 0.0)
            inv = 1.0 / (1.0 + max(0.0, raw_rank))
            scored.append(
                {
                    "id": int(row["id"]),
                    "company": str(row["company"] or ""),
                    "job_id": str(row["job_id"] or ""),
                    "kind": str(row["kind"] or ""),
                    "snippet": str(row["snippet"] or ""),
                    "created_at": str(row["created_at"] or ""),
                    "score": round(inv, 6),
                }
            )
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[: max(1, int(top_k))]

    # Fallback: naive token count over recent memories.
    rows = conn.execute(
        "SELECT id, company, job_id, kind, text, created_at FROM memories WHERE company=? ORDER BY created_at DESC LIMIT 200",
        (company_norm,),
    ).fetchall()
    scored: list[dict[str, Any]] = []
    for row in rows:
        text = str(row["text"] or "").lower()
        if not text:
            continue
        hits = 0
        for tk in tokens:
            hits += text.count(tk)
        if hits <= 0:
            continue
        scored.append(
            {
                "id": int(row["id"]),
                "company": str(row["company"] or ""),
                "job_id": str(row["job_id"] or ""),
                "kind": str(row["kind"] or ""),
                "snippet": str(row["text"] or "")[:700],
                "created_at": str(row["created_at"] or ""),
                "score": float(hits),
            }
        )
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: max(1, int(top_k))]


def infer_source_group(path: Path, kb_root: Path | None = None) -> str:
    full = str(path).lower()
    if "/00_glossary/" in full or "glossery" in full or "glossary" in full:
        return "glossary"
    if "/10_style_guide/" in full:
        return "glossary"
    if "/20_domain_knowledge/" in full:
        return "general"
    if "/30_reference/" in full:
        if "/final/" in full:
            return "previously_translated"
        return "general"
    if "/40_templates/" in full:
        return "general"
    if "previously translated" in full:
        return "previously_translated"
    # Generic translated output folder (e.g., "Translated/")
    if "/translated/" in full and "/translated -" not in full:
        return "translated_output"
    if "translated -en" in full:
        return "translated_en"
    # Generic source folder (e.g., "Source/")
    if "/source/" in full or "arabic source" in full:
        return "source_text"
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
    ensure_job_interaction(conn, job_id=job_id, sender=sender)
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
    task_label: str = "",
) -> None:
    conn.execute(
        """
        UPDATE jobs
        SET status=?, task_type=?, task_label=?, confidence=?, estimated_minutes=?, runtime_timeout_minutes=?, updated_at=?
        WHERE job_id=?
        """,
        (status, task_type, task_label, confidence, estimated_minutes, runtime_timeout_minutes, utc_now_iso(), job_id),
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


def get_last_event(conn: sqlite3.Connection, *, job_id: str) -> dict[str, Any] | None:
    job_id_norm = (job_id or "").strip()
    if not job_id_norm:
        return None
    row = conn.execute(
        "SELECT milestone, created_at FROM events WHERE job_id=? ORDER BY id DESC LIMIT 1",
        (job_id_norm,),
    ).fetchone()
    return dict(row) if row else None


def get_active_queue_item(conn: sqlite3.Connection, *, job_id: str) -> dict[str, Any] | None:
    job_id_norm = (job_id or "").strip()
    if not job_id_norm:
        return None
    row = conn.execute(
        """
        SELECT *
        FROM job_run_queue
        WHERE job_id=?
          AND state IN ('queued', 'running')
        ORDER BY id DESC
        LIMIT 1
        """,
        (job_id_norm,),
    ).fetchone()
    return dict(row) if row else None


def enqueue_run_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    notify_target: str = "",
    created_by_sender: str = "",
) -> dict[str, Any]:
    """Idempotently enqueue a job for background execution.

    If an active queue item already exists (queued/running), returns it without
    creating a duplicate.
    """

    job_id_norm = (job_id or "").strip()
    if not job_id_norm:
        raise ValueError("job_id is required")
    notify_norm = (notify_target or "").strip()
    sender_norm = (created_by_sender or "").strip()

    existing = get_active_queue_item(conn, job_id=job_id_norm)
    if existing:
        if notify_norm and not str(existing.get("notify_target") or "").strip():
            conn.execute("UPDATE job_run_queue SET notify_target=? WHERE id=?", (notify_norm, int(existing["id"])))
            conn.commit()
            existing["notify_target"] = notify_norm
        return existing

    now = utc_now_iso()
    try:
        conn.execute(
            """
            INSERT INTO job_run_queue(
              job_id, state, attempt, notify_target, created_by_sender,
              enqueued_at, available_at, heartbeat_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (job_id_norm, "queued", 0, notify_norm, sender_norm, now, now, now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Another process may have enqueued concurrently due to unique active index.
        existing = get_active_queue_item(conn, job_id=job_id_norm)
        if existing:
            return existing
        raise

    # Keep jobs.status aligned for status cards; do not override a running job.
    conn.execute(
        "UPDATE jobs SET status=?, updated_at=? WHERE job_id=? AND status!='running'",
        ("queued", now, job_id_norm),
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM job_run_queue WHERE job_id=? ORDER BY id DESC LIMIT 1",
        (job_id_norm,),
    ).fetchone()
    return dict(row) if row else {"job_id": job_id_norm, "state": "queued"}


def set_queue_pipeline_process(
    conn: sqlite3.Connection,
    *,
    queue_id: int,
    worker_id: str,
    pid: int,
    pgid: int,
) -> None:
    """Persist child pipeline process identifiers for cancellation."""

    conn.execute(
        """
        UPDATE job_run_queue
        SET pipeline_pid=?,
            pipeline_pgid=?,
            heartbeat_at=?
        WHERE id=?
          AND worker_id=?
          AND state='running'
        """,
        (
            int(pid or 0),
            int(pgid or 0),
            utc_now_iso(),
            int(queue_id),
            (worker_id or "").strip() or "worker",
        ),
    )
    conn.commit()


def cancel_job_run(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    requested_by: str = "",
    reason: str = "",
    mode: str = "force",
) -> dict[str, Any]:
    """Cancel a queued/running job.

    - queued  -> mark queue row canceled immediately.
    - running -> set cancel_requested_* (worker should terminate the subprocess).
    """

    job_id_norm = (job_id or "").strip()
    if not job_id_norm:
        return {"ok": False, "error": "missing_job_id"}

    by_norm = (requested_by or "").strip()
    reason_norm = (reason or "").strip()
    mode_norm = (mode or "").strip().lower() or "force"
    if mode_norm not in {"soft", "force"}:
        mode_norm = "force"
    now = utc_now_iso()

    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT *
            FROM job_run_queue
            WHERE job_id=?
              AND state IN ('queued','running')
            ORDER BY id DESC
            LIMIT 1
            """,
            (job_id_norm,),
        ).fetchone()
        if not row:
            conn.execute("COMMIT")
            return {"ok": False, "error": "no_active_queue_item"}

        q = dict(row)
        qid = int(q["id"])
        state = str(q.get("state") or "").strip().lower()

        if state == "queued":
            updated = conn.execute(
                """
                UPDATE job_run_queue
                SET state='canceled',
                    finished_at=?,
                    heartbeat_at=?,
                    last_error=?,
                    cancel_requested_at=?,
                    cancel_requested_by=?,
                    cancel_reason=?,
                    cancel_mode=?
                WHERE id=?
                  AND state='queued'
                """,
                (
                    now,
                    now,
                    "canceled_by_user",
                    now,
                    by_norm,
                    reason_norm,
                    mode_norm,
                    qid,
                ),
            ).rowcount
            if updated == 1:
                # Keep jobs.status aligned for status cards; do not override an actively running job.
                conn.execute(
                    "UPDATE jobs SET status=?, updated_at=? WHERE job_id=? AND status!='running'",
                    ("canceled", now, job_id_norm),
                )
                conn.execute("COMMIT")
                q["state"] = "canceled"
                q["finished_at"] = now
                q["last_error"] = "canceled_by_user"
                q["cancel_requested_at"] = now
                q["cancel_requested_by"] = by_norm
                q["cancel_reason"] = reason_norm
                q["cancel_mode"] = mode_norm
                return {"ok": True, "action": "canceled", "queue": q}
            # It was claimed concurrently; fall through to running path.
            row = conn.execute("SELECT * FROM job_run_queue WHERE id=?", (qid,)).fetchone()
            q = dict(row) if row else q
            state = str(q.get("state") or "").strip().lower()

        already = bool(str(q.get("cancel_requested_at") or "").strip())
        conn.execute(
            """
            UPDATE job_run_queue
            SET cancel_requested_at=CASE WHEN cancel_requested_at='' THEN ? ELSE cancel_requested_at END,
                cancel_requested_by=CASE WHEN cancel_requested_by='' THEN ? ELSE cancel_requested_by END,
                cancel_reason=CASE WHEN cancel_reason='' THEN ? ELSE cancel_reason END,
                cancel_mode=CASE WHEN cancel_mode='' THEN ? ELSE cancel_mode END
            WHERE id=?
              AND state='running'
            """,
            (now, by_norm, reason_norm, mode_norm, qid),
        )
        conn.execute("COMMIT")

        updated = conn.execute("SELECT * FROM job_run_queue WHERE id=?", (qid,)).fetchone()
        q2 = dict(updated) if updated else q
        return {"ok": True, "action": ("already_requested" if already else "cancel_requested"), "queue": q2}
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


def claim_next_queued(conn: sqlite3.Connection, *, worker_id: str) -> dict[str, Any] | None:
    """Atomically claim the next queued job for a worker."""

    worker_norm = (worker_id or "").strip() or "worker"
    now = utc_now_iso()

    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, job_id
            FROM job_run_queue
            WHERE state='queued'
              AND (available_at='' OR available_at<=?)
            ORDER BY enqueued_at ASC, id ASC
            LIMIT 1
            """
        ,
            (now,),
        ).fetchone()
        if not row:
            conn.execute("COMMIT")
            return None
        qid = int(row["id"])
        updated = conn.execute(
            """
            UPDATE job_run_queue
            SET state='running',
                attempt=attempt+1,
                worker_id=?,
                started_at=?,
                heartbeat_at=?
            WHERE id=?
              AND state='queued'
            """,
            (worker_norm, now, now, qid),
        ).rowcount
        if updated != 1:
            conn.execute("ROLLBACK")
            return None

        # Keep top-level job status in sync with queue execution state so UI/status
        # does not show "planned" while queue is already running.
        conn.execute(
            """
            UPDATE jobs
            SET status='preflight',
                updated_at=?
            WHERE job_id=?
              AND status IN ('queued', 'planned')
            """,
            (now, str(row["job_id"] or "").strip()),
        )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    claimed = conn.execute("SELECT * FROM job_run_queue WHERE id=?", (qid,)).fetchone()
    return dict(claimed) if claimed else None


def heartbeat_queue_item(conn: sqlite3.Connection, *, queue_id: int, worker_id: str) -> None:
    now = utc_now_iso()
    conn.execute(
        "UPDATE job_run_queue SET heartbeat_at=? WHERE id=? AND worker_id=? AND state='running'",
        (now, int(queue_id), (worker_id or "").strip() or "worker"),
    )
    conn.commit()


def finish_queue_item(
    conn: sqlite3.Connection,
    *,
    queue_id: int,
    worker_id: str,
    state: str,
    last_error: str = "",
) -> None:
    now = utc_now_iso()
    state_norm = (state or "").strip().lower()
    if state_norm not in {"succeeded", "failed", "canceled"}:
        state_norm = "failed"
    conn.execute(
        """
        UPDATE job_run_queue
        SET state=?,
            finished_at=?,
            heartbeat_at=?,
            last_error=?
        WHERE id=?
          AND worker_id=?
        """,
        (
            state_norm,
            now,
            now,
            (last_error or "").strip(),
            int(queue_id),
            (worker_id or "").strip() or "worker",
        ),
    )
    conn.commit()

    # If the worker failed before the pipeline could write a final job status,
    # surface it on the job so `status` and `rerun` behave sanely.
    if state_norm in {"failed", "canceled"}:
        row = conn.execute("SELECT job_id FROM job_run_queue WHERE id=?", (int(queue_id),)).fetchone()
        job_id = str(row["job_id"] or "").strip() if row else ""
        if job_id:
            job = get_job(conn, job_id)
            if job and str(job.get("status") or "").strip().lower() in {"planned", "queued", "preflight", "running"}:
                token = (last_error or "").strip() or ("queue_failed" if state_norm == "failed" else "canceled")
                errors = list(job.get("errors_json") or [])
                tag = f"queue_{state_norm}:{token}"
                if tag not in errors:
                    errors.append(tag)
                    update_job_status(conn, job_id=job_id, status=("canceled" if state_norm == "canceled" else "failed"), errors=errors)


def defer_queue_item(
    conn: sqlite3.Connection,
    *,
    queue_id: int,
    worker_id: str,
    delay_seconds: int,
    reason: str = "cooldown",
) -> None:
    """Return a running queue item back to queued with a future available_at."""
    now_dt = datetime.now(UTC)
    now_iso = now_dt.isoformat()
    delay_s = max(30, int(delay_seconds))
    available_at_iso = (now_dt.timestamp() + delay_s)
    available_at = datetime.fromtimestamp(available_at_iso, UTC).isoformat()
    err = f"deferred:{(reason or 'cooldown').strip()}:retry_in={delay_s}s"
    conn.execute(
        """
        UPDATE job_run_queue
        SET state='queued',
            worker_id='',
            started_at='',
            heartbeat_at=?,
            available_at=?,
            last_error=?,
            pipeline_pid=0,
            pipeline_pgid=0
        WHERE id=?
          AND worker_id=?
          AND state='running'
        """,
        (
            now_iso,
            available_at,
            err,
            int(queue_id),
            (worker_id or "").strip() or "worker",
        ),
    )
    conn.commit()

    row = conn.execute("SELECT job_id FROM job_run_queue WHERE id=?", (int(queue_id),)).fetchone()
    job_id = str(row["job_id"] or "").strip() if row else ""
    if job_id:
        job = get_job(conn, job_id)
        if job and str(job.get("status") or "").strip().lower() in {"planned", "queued", "running", "failed"}:
            errors = list(job.get("errors_json") or [])
            token = f"queue_deferred:{reason}:retry_in={delay_s}s"
            if token not in errors:
                errors.append(token)
            update_job_status(conn, job_id=job_id, status="queued", errors=errors)


def _kill_pipeline_best_effort(*, pgid: int, pid: int) -> None:
    pg = int(pgid or 0)
    p = int(pid or 0)
    try:
        if pg > 0 and hasattr(os, "killpg"):
            os.killpg(pg, signal.SIGTERM)
            os.killpg(pg, signal.SIGKILL)
            return
    except ProcessLookupError:
        return
    except PermissionError:
        return
    except Exception:
        pass
    if p <= 0:
        return
    try:
        os.kill(p, signal.SIGTERM)
        os.kill(p, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        return


def requeue_stuck_running(
    conn: sqlite3.Connection,
    *,
    stuck_seconds: int,
    max_attempts: int,
) -> int:
    """Requeue or fail running tasks whose heartbeat is too old.

    Returns number of items updated.
    """

    stuck_s = max(60, int(stuck_seconds))
    max_a = max(1, int(max_attempts))
    now = datetime.now(UTC)
    changed = 0

    rows = conn.execute(
        """
        SELECT id, job_id, attempt, started_at, heartbeat_at, worker_id,
               cancel_requested_at, pipeline_pid, pipeline_pgid
        FROM job_run_queue
        WHERE state='running'
        ORDER BY id ASC
        """
    ).fetchall()
    for row in rows:
        qid = int(row["id"])
        job_id = str(row["job_id"] or "").strip()
        attempt = int(row["attempt"] or 0)
        hb = str(row["heartbeat_at"] or "").strip()
        st = str(row["started_at"] or "").strip()
        mark = hb or st
        if not mark:
            continue
        try:
            mark_dt = datetime.fromisoformat(mark)
            if mark_dt.tzinfo is None:
                mark_dt = mark_dt.replace(tzinfo=UTC)
        except ValueError:
            continue
        age = (now - mark_dt).total_seconds()
        if age < stuck_s:
            continue

        cancel_requested_at = str(row["cancel_requested_at"] or "").strip()
        pipeline_pid = int(row["pipeline_pid"] or 0)
        pipeline_pgid = int(row["pipeline_pgid"] or 0)
        if pipeline_pid or pipeline_pgid:
            _kill_pipeline_best_effort(pgid=pipeline_pgid, pid=pipeline_pid)

        # Respect explicit cancellation: do not requeue.
        if cancel_requested_at:
            conn.execute(
                """
                UPDATE job_run_queue
                SET state='canceled',
                    finished_at=?,
                    heartbeat_at=?,
                    last_error=?
                WHERE id=?
                  AND state='running'
                """,
                (utc_now_iso(), utc_now_iso(), f"stuck_canceled:{age:.0f}s", qid),
            )
            changed += 1
            if job_id:
                job = get_job(conn, job_id)
                if job and str(job.get("status") or "").strip().lower() in {"planned", "queued", "running"}:
                    errors = list(job.get("errors_json") or [])
                    tag = f"queue_canceled:stuck_canceled:{age:.0f}s"
                    if tag not in errors:
                        errors.append(tag)
                    update_job_status(conn, job_id=job_id, status="canceled", errors=errors)
            continue

        if attempt >= max_a:
            conn.execute(
                """
                UPDATE job_run_queue
                SET state='failed',
                    finished_at=?,
                    last_error=?
                WHERE id=?
                  AND state='running'
                """,
                (utc_now_iso(), f"stuck_timeout:{age:.0f}s", qid),
            )
            changed += 1
            if job_id:
                job = get_job(conn, job_id)
                if job and str(job.get("status") or "").strip().lower() in {"planned", "queued", "running"}:
                    errors = list(job.get("errors_json") or [])
                    tag = f"queue_failed:stuck_timeout:{age:.0f}s"
                    if tag not in errors:
                        errors.append(tag)
                    update_job_status(conn, job_id=job_id, status="failed", errors=errors)
            continue

        conn.execute(
            """
            UPDATE job_run_queue
            SET state='queued',
                worker_id='',
                started_at='',
                heartbeat_at=?,
                last_error=?
            WHERE id=?
              AND state='running'
            """,
            (utc_now_iso(), f"stuck_requeued:{age:.0f}s", qid),
        )
        changed += 1
        if job_id:
            job = get_job(conn, job_id)
            if job and str(job.get("status") or "").strip().lower() == "running":
                update_job_status(conn, job_id=job_id, status="queued", errors=list(job.get("errors_json") or []))

    if changed:
        conn.commit()
    return changed


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


def ensure_job_interaction(conn: sqlite3.Connection, *, job_id: str, sender: str = "") -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO job_interactions(job_id, sender, created_at)
        VALUES(?,?,?)
        ON CONFLICT(job_id) DO UPDATE SET
            sender=excluded.sender
        """,
        (job_id, (sender or "").strip(), now),
    )


def get_job_interaction(conn: sqlite3.Connection, *, job_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM job_interactions WHERE job_id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def set_job_pending_action(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    sender: str,
    pending_action: str,
    options: list[dict[str, Any]],
    expires_at: str,
) -> None:
    ensure_job_interaction(conn, job_id=job_id, sender=sender)
    conn.execute(
        """
        UPDATE job_interactions
        SET pending_action=?, options_json=?, expires_at=?
        WHERE job_id=?
        """,
        (pending_action, json.dumps(options, ensure_ascii=False), expires_at, job_id),
    )
    conn.commit()


def clear_job_pending_action(conn: sqlite3.Connection, *, job_id: str) -> None:
    conn.execute(
        "UPDATE job_interactions SET pending_action='', options_json='[]', expires_at='' WHERE job_id=?",
        (job_id,),
    )
    conn.commit()


def add_job_final_upload(conn: sqlite3.Connection, *, job_id: str, sender: str, path: Path) -> None:
    ensure_job_interaction(conn, job_id=job_id, sender=sender)
    row = conn.execute("SELECT final_uploads_json FROM job_interactions WHERE job_id=?", (job_id,)).fetchone()
    existing: list[str] = []
    if row:
        try:
            existing = json.loads(str(row["final_uploads_json"] or "[]"))
        except json.JSONDecodeError:
            existing = []
    existing.append(str(path.resolve()))
    conn.execute(
        "UPDATE job_interactions SET final_uploads_json=? WHERE job_id=?",
        (json.dumps(existing, ensure_ascii=False), job_id),
    )
    conn.commit()


def list_job_final_uploads(conn: sqlite3.Connection, *, job_id: str) -> list[str]:
    row = conn.execute("SELECT final_uploads_json FROM job_interactions WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        return []
    try:
        value = json.loads(str(row["final_uploads_json"] or "[]"))
        return [str(x) for x in value if str(x).strip()]
    except json.JSONDecodeError:
        return []


def set_job_kb_company(conn: sqlite3.Connection, *, job_id: str, kb_company: str) -> None:
    conn.execute(
        "UPDATE jobs SET kb_company=?, updated_at=? WHERE job_id=?",
        ((kb_company or "").strip(), utc_now_iso(), job_id),
    )
    conn.commit()


def set_job_archive_project(conn: sqlite3.Connection, *, job_id: str, archive_project: str) -> None:
    conn.execute(
        "UPDATE jobs SET archive_project=?, updated_at=? WHERE job_id=?",
        ((archive_project or "").strip(), utc_now_iso(), job_id),
    )
    conn.commit()


def mark_job_archived(conn: sqlite3.Connection, *, job_id: str) -> None:
    now = utc_now_iso()
    conn.execute(
        "UPDATE jobs SET archived_at=?, updated_at=? WHERE job_id=?",
        (now, now, job_id),
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


def resolve_operation_notify_target(
    conn: sqlite3.Connection,
    *,
    job_id: str = "",
    sender: str = "",
) -> str:
    """Resolve Telegram notify target for an operation event.

    Priority:
    1) sender from job(job_id)
    2) sender from sender_active_jobs (explicit sender first, then latest active sender)
    3) latest actionable job sender
    4) OPENCLAW_NOTIFY_TARGET fallback
    """
    job_id_norm = (job_id or "").strip()
    sender_norm = (sender or "").strip()

    if job_id_norm:
        job = get_job(conn, job_id_norm)
        if job:
            job_sender = str(job.get("sender") or "").strip()
            if job_sender:
                return job_sender
        if not sender_norm:
            interaction = get_job_interaction(conn, job_id=job_id_norm)
            if interaction:
                interaction_sender = str(interaction.get("sender") or "").strip()
                if interaction_sender:
                    sender_norm = interaction_sender

    if sender_norm:
        active_job_id = get_sender_active_job(conn, sender=sender_norm)
        if active_job_id:
            active_job = get_job(conn, active_job_id)
            if active_job:
                active_sender = str(active_job.get("sender") or "").strip()
                if active_sender:
                    return active_sender
        return sender_norm

    row = conn.execute(
        """
        SELECT sender
        FROM sender_active_jobs
        WHERE sender != ''
        ORDER BY updated_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row:
        active_sender = str(row["sender"] or "").strip()
        if active_sender:
            return active_sender

    latest = latest_actionable_job(conn)
    if latest:
        latest_sender = str(latest.get("sender") or "").strip()
        if latest_sender:
            return latest_sender

    return str(DEFAULT_NOTIFY_TARGET or "").strip()


def audit_operation_event(
    conn: sqlite3.Connection,
    *,
    operation_payload: dict[str, Any],
    milestone: str = "ops_audit",
    dry_run: bool = False,
) -> dict[str, Any]:
    payload = dict(operation_payload or {})
    operation_id = str(payload.get("operation_id") or "").strip() or f"op_{uuid.uuid4().hex[:12]}"
    source = str(payload.get("source") or "").strip() or "unknown"
    action = str(payload.get("action") or "").strip() or "unknown_action"
    job_id = str(payload.get("job_id") or "").strip()
    sender = str(payload.get("sender") or "").strip()
    status = str(payload.get("status") or "").strip() or "unknown"
    summary = str(payload.get("summary") or "").strip()
    detail = payload.get("detail")
    ts = str(payload.get("ts") or "").strip() or utc_now_iso()
    target = resolve_operation_notify_target(conn, job_id=job_id, sender=sender)

    normalized = {
        "operation_id": operation_id,
        "source": source,
        "action": action,
        "job_id": job_id,
        "sender": sender,
        "status": status,
        "summary": summary,
        "detail": detail,
        "ts": ts,
    }
    msg = f"[OPS][{operation_id}] {action} | job={job_id or '-'} | status={status}"
    if summary:
        msg = f"{msg}\n{summary}"

    send_result: dict[str, Any]
    try:
        send_result = send_message(target=target, message=msg, dry_run=dry_run)
    except Exception as exc:  # pragma: no cover - defensive
        send_result = {"ok": False, "error": f"audit_send_failed:{exc}"}

    record_ok = True
    record_error = ""
    try:
        record_event(
            conn,
            job_id=job_id,
            milestone=milestone,
            payload={
                "operation": normalized,
                "target": target,
                "message": msg,
                "send_result": send_result,
            },
        )
    except Exception as exc:  # pragma: no cover - defensive
        record_ok = False
        record_error = f"audit_record_failed:{exc}"

    return {
        "ok": record_ok,
        "operation": normalized,
        "target": target,
        "message": msg,
        "send_result": send_result,
        "record_error": record_error,
    }


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


def send_telegram_direct(*, chat_id: str, message: str, bot_token: str) -> dict[str, Any]:
    """Send a message directly via Telegram Bot API (no OpenClaw)."""
    import urllib.error
    import urllib.request

    if not bot_token:
        return {"ok": False, "error": "no_bot_token"}
    text = message
    if len(text) > 4000:
        text = text[:4000] + "\n...(truncated)"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return {"ok": data.get("ok", False), "response": data}
    except urllib.error.HTTPError as exc:
        err = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {"ok": False, "error_code": exc.code, "description": err}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "description": str(exc)}


def send_message(
    *,
    target: str,
    message: str,
    channel: str = DEFAULT_NOTIFY_CHANNEL,
    account: str = DEFAULT_NOTIFY_ACCOUNT,
    dry_run: bool = False,
) -> dict[str, Any]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    direct_mode = os.getenv("TELEGRAM_DIRECT_MODE") == "1"

    # Direct Telegram bypass when enabled
    if direct_mode and channel == "telegram" and not dry_run and token:
        return send_telegram_direct(chat_id=target, message=message, bot_token=token)

    openclaw_bin = shutil.which("openclaw")
    if not openclaw_bin:
        # Fallback: if OpenClaw CLI is unavailable, still deliver Telegram notifications directly.
        if channel == "telegram" and not dry_run and token:
            return send_telegram_direct(chat_id=target, message=message, bot_token=token)
        return {"ok": False, "stderr": "openclaw command not found", "stdout": ""}

    cmd = [
        openclaw_bin,
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

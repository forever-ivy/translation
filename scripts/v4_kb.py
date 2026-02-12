#!/usr/bin/env python3
"""Knowledge-base indexing and retrieval for OpenClaw V4.1."""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from docx import Document

from scripts.v4_runtime import (
    KB_SUPPORTED_EXTENSIONS,
    SOURCE_GROUP_WEIGHTS,
    compute_sha256,
    infer_source_group,
    json_dumps,
    utc_now_iso,
)

try:  # Optional dependency
    from pypdf import PdfReader
except Exception:  # pragma: no cover - optional import
    PdfReader = None

try:  # Optional dependency
    from openpyxl import load_workbook
except Exception:  # pragma: no cover - optional import
    load_workbook = None


def _normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _chunk_text(text: str, max_chars: int = 1200, overlap: int = 120) -> list[str]:
    norm = _normalize_text(text)
    if not norm:
        return []

    units = [u.strip() for u in re.split(r"(?:\n{2,}|(?<=[.!?])\s+)", norm) if u.strip()]
    chunks: list[str] = []
    cur = ""
    for unit in units:
        if not cur:
            cur = unit
            continue
        if len(cur) + 1 + len(unit) <= max_chars:
            cur = f"{cur} {unit}"
        else:
            chunks.append(cur)
            tail = cur[-overlap:] if overlap > 0 and len(cur) > overlap else cur
            cur = f"{tail} {unit}" if tail else unit
            if len(cur) > max_chars:
                chunks.append(cur[:max_chars])
                cur = cur[max_chars - overlap :] if overlap > 0 else ""
    if cur:
        chunks.append(cur)
    return [_normalize_text(c) for c in chunks if _normalize_text(c)]


def _extract_docx(path: Path) -> str:
    doc = Document(str(path))
    lines: list[str] = []
    for p in doc.paragraphs:
        t = _normalize_text(p.text)
        if t:
            lines.append(t)
    for t_idx, table in enumerate(doc.tables, start=1):
        lines.append(f"[Table {t_idx}]")
        for row in table.rows:
            cells = [_normalize_text(c.text).replace("\n", " / ") for c in row.cells]
            row_text = " | ".join([c for c in cells if c])
            if row_text:
                lines.append(row_text)
    return "\n".join(lines)


def _extract_pdf(path: Path) -> str:
    if PdfReader is None:
        raise RuntimeError("pypdf not installed; cannot parse .pdf")
    reader = PdfReader(str(path))
    lines: list[str] = []
    for page in reader.pages:
        txt = page.extract_text() or ""
        txt = _normalize_text(txt)
        if txt:
            lines.append(txt)
    return "\n".join(lines)


def _extract_text_plain(path: Path) -> str:
    return _normalize_text(path.read_text(encoding="utf-8", errors="ignore"))


def _extract_csv(path: Path) -> str:
    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            cleaned = [_normalize_text(c) for c in row if _normalize_text(c)]
            if cleaned:
                lines.append(" | ".join(cleaned))
    return "\n".join(lines)


def _extract_xlsx(path: Path) -> str:
    if load_workbook is None:
        raise RuntimeError("openpyxl not installed; cannot parse .xlsx")
    wb = load_workbook(str(path), read_only=True, data_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        lines.append(f"[Sheet] {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells: list[str] = []
            for value in row:
                if value is None:
                    continue
                text = _normalize_text(str(value))
                if text:
                    cells.append(text)
            if cells:
                lines.append(" | ".join(cells))
    wb.close()
    return "\n".join(lines)


def extract_text(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower()
    if ext == ".docx":
        return "docx", _extract_docx(path)
    if ext == ".pdf":
        return "pdf", _extract_pdf(path)
    if ext in {".md", ".txt"}:
        return ext[1:], _extract_text_plain(path)
    if ext == ".csv":
        return "csv", _extract_csv(path)
    if ext == ".xlsx":
        return "xlsx", _extract_xlsx(path)
    return "unknown", ""


def discover_kb_files(kb_root: Path) -> list[Path]:
    files: list[Path] = []
    for p in kb_root.rglob("*"):
        if not p.is_file():
            continue
        if p.name.startswith("~$") or p.name.startswith("."):
            continue
        if p.suffix.lower() not in KB_SUPPORTED_EXTENSIONS:
            continue
        files.append(p)
    files.sort(key=lambda x: str(x).lower())
    return files


def sync_kb(
    *,
    conn: sqlite3.Connection,
    kb_root: Path,
    report_path: Path | None = None,
) -> dict[str, Any]:
    kb_root = kb_root.expanduser().resolve()
    files = discover_kb_files(kb_root)

    existing_rows = conn.execute("SELECT * FROM kb_files").fetchall()
    existing = {row["path"]: dict(row) for row in existing_rows}
    seen_paths = set()

    report: dict[str, Any] = {
        "ok": True,
        "kb_root": str(kb_root),
        "scanned_count": len(files),
        "created": 0,
        "updated": 0,
        "metadata_only": 0,
        "removed": 0,
        "skipped": 0,
        "errors": [],
        "files": [],
        "indexed_at": utc_now_iso(),
    }

    for path in files:
        ap = str(path.resolve())
        seen_paths.add(ap)
        stat = path.stat()
        rec = existing.get(ap)
        try:
            if rec and int(rec["mtime_ns"]) == stat.st_mtime_ns and int(rec["size_bytes"]) == stat.st_size:
                report["skipped"] += 1
                continue

            sha = compute_sha256(path)
            if rec and rec.get("sha256") == sha:
                conn.execute(
                    """
                    UPDATE kb_files
                    SET mtime_ns=?, size_bytes=?, indexed_at=?
                    WHERE path=?
                    """,
                    (stat.st_mtime_ns, stat.st_size, utc_now_iso(), ap),
                )
                report["metadata_only"] += 1
                continue

            parser, text = extract_text(path)
            chunks = _chunk_text(text)
            source_group = infer_source_group(path, kb_root)

            conn.execute("DELETE FROM kb_chunks WHERE path=?", (ap,))
            for idx, chunk in enumerate(chunks):
                conn.execute(
                    "INSERT INTO kb_chunks(path, source_group, chunk_index, text) VALUES(?,?,?,?)",
                    (ap, source_group, idx, chunk),
                )

            conn.execute(
                """
                INSERT INTO kb_files(path, mtime_ns, size_bytes, sha256, parser, source_group, chunk_count, indexed_at)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET
                    mtime_ns=excluded.mtime_ns,
                    size_bytes=excluded.size_bytes,
                    sha256=excluded.sha256,
                    parser=excluded.parser,
                    source_group=excluded.source_group,
                    chunk_count=excluded.chunk_count,
                    indexed_at=excluded.indexed_at
                """,
                (ap, stat.st_mtime_ns, stat.st_size, sha, parser, source_group, len(chunks), utc_now_iso()),
            )

            report["files"].append(
                {
                    "path": ap,
                    "parser": parser,
                    "source_group": source_group,
                    "chunk_count": len(chunks),
                }
            )
            if rec:
                report["updated"] += 1
            else:
                report["created"] += 1
        except Exception as exc:  # pragma: no cover - keeps sync resilient
            report["errors"].append({"path": ap, "error": str(exc)})

    for old_path in existing:
        if old_path in seen_paths:
            continue
        conn.execute("DELETE FROM kb_chunks WHERE path=?", (old_path,))
        conn.execute("DELETE FROM kb_files WHERE path=?", (old_path,))
        report["removed"] += 1

    conn.commit()
    report["ok"] = len(report["errors"]) == 0

    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json_dumps(report), encoding="utf-8")

    return report


def retrieve_kb(
    *,
    conn: sqlite3.Connection,
    query: str,
    task_type: str = "",
    top_k: int = 8,
) -> list[dict[str, Any]]:
    tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9\u0600-\u06FF_]+", query) if len(t) >= 2]
    if not tokens:
        return []

    task = task_type.upper().strip()
    task_boosts = {
        "REVISION_UPDATE": {"glossary": 1.25, "previously_translated": 1.2},
        "NEW_TRANSLATION": {"glossary": 1.2, "arabic_source": 1.1},
        "BILINGUAL_REVIEW": {"glossary": 1.2, "translated_en": 1.15},
        "EN_ONLY_EDIT": {"translated_en": 1.2, "previously_translated": 1.1},
        "MULTI_FILE_BATCH": {"glossary": 1.15, "previously_translated": 1.1},
        "TERMINOLOGY_ENFORCEMENT": {"glossary": 1.4, "translated_en": 1.15},
        "FORMAT_CRITICAL_TASK": {"glossary": 1.2, "previously_translated": 1.15},
        "LOW_CONTEXT_TASK": {"glossary": 1.1},
    }.get(task, {})

    rows = conn.execute("SELECT path, source_group, chunk_index, text FROM kb_chunks").fetchall()
    scored: list[dict[str, Any]] = []
    for row in rows:
        text = (row["text"] or "").lower()
        if not text:
            continue
        match_hits = 0
        for tk in tokens:
            match_hits += text.count(tk)
        if match_hits <= 0:
            continue
        source_group = row["source_group"] or "general"
        base = SOURCE_GROUP_WEIGHTS.get(source_group, SOURCE_GROUP_WEIGHTS["general"])
        boost = task_boosts.get(source_group, 1.0)
        score = round(float(match_hits) * base * boost, 4)
        scored.append(
            {
                "path": row["path"],
                "source_group": source_group,
                "chunk_index": int(row["chunk_index"]),
                "snippet": row["text"][:700],
                "score": score,
            }
        )

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[: max(1, int(top_k))]

#!/usr/bin/env python3
"""End-to-end V5.2 pipeline helpers."""

from __future__ import annotations

import json
import logging
import re
import os
from pathlib import Path
from typing import Any

from scripts.attention_summary import attention_summary
from scripts.openclaw_translation_orchestrator import run as run_translation
from scripts.pdf_translator import (
    is_pdf2zh_available,
    translate_pdf,
    translate_pdf_fallback_text,
    translate_pdf_with_ocr_fallback,
    translate_pdf_vision,
    check_pdf2zh_installation,
)

log = logging.getLogger(__name__)
from scripts.detail_validator import validate_job_artifacts, ValidationReportGenerator
from scripts.task_bundle_builder import infer_language, infer_role, infer_version
from scripts.v4_kb import retrieve_kb_with_fallback, sync_kb_with_rag
from scripts.v4_runtime import (
    DEFAULT_NOTIFY_TARGET,
    RuntimePaths,
    add_memory,
    add_job_file,
    append_log,
    db_connect,
    ensure_runtime_paths,
    get_active_queue_item,
    get_job,
    json_dumps,
    list_job_files,
    record_event,
    resolve_rag_collection,
    search_memories,
    set_sender_active_job,
    send_media,
    send_message,
    update_job_plan,
    update_job_result,
    update_job_status,
    write_job,
)


def notify_milestone(
    *,
    paths: RuntimePaths,
    conn,
    job_id: str,
    milestone: str,
    message: str,
    target: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    tgt = target or DEFAULT_NOTIFY_TARGET
    result = send_message(target=tgt, message=message, dry_run=dry_run)
    payload = {"target": tgt, "message": message, "send_result": result}
    record_event(conn, job_id=job_id, milestone=milestone, payload=payload)
    append_log(paths, "events.log", f"{milestone}\t{job_id}\t{message}")
    return result


def create_job(
    *,
    source: str,
    sender: str,
    subject: str,
    message_text: str,
    inbox_dir: Path,
    job_id: str,
    work_root: Path,
    active_sender: str | None = None,
) -> dict[str, Any]:
    paths = ensure_runtime_paths(work_root)
    conn = db_connect(paths)
    review_dir = paths.review_root / job_id
    review_dir.mkdir(parents=True, exist_ok=True)
    write_job(
        conn,
        job_id=job_id,
        source=source,
        sender=sender,
        subject=subject,
        message_text=message_text,
        status="received",
        inbox_dir=inbox_dir,
        review_dir=review_dir,
    )
    set_sender_active_job(conn, sender=sender, job_id=job_id)
    if active_sender and active_sender.strip():
        set_sender_active_job(conn, sender=active_sender.strip(), job_id=job_id)
    record_event(conn, job_id=job_id, milestone="received", payload={"source": source, "sender": sender, "subject": subject})
    conn.close()
    return {
        "job_id": job_id,
        "source": source,
        "from": sender,
        "subject": subject,
        "message_text": message_text,
        "inbox_dir": str(inbox_dir.resolve()),
        "review_dir": str(review_dir.resolve()),
        "status": "received",
    }


def attach_file_to_job(*, work_root: Path, job_id: str, path: Path, mime_type: str = "") -> None:
    paths = ensure_runtime_paths(work_root)
    conn = db_connect(paths)
    add_job_file(conn, job_id=job_id, path=path, mime_type=mime_type)
    conn.close()


def _build_candidates(job_files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in job_files:
        p = Path(item["path"])
        if p.suffix.lower() not in {".docx", ".xlsx", ".csv", ".pdf"}:
            continue
        candidates.append(
            {
                "path": str(p.resolve()),
                "name": p.name,
                "language": infer_language(p),
                "version": infer_version(p),
                "role": infer_role(p),
            }
        )
    return candidates


def _process_pdf_files(
    *,
    candidates: list[dict[str, Any]],
    review_dir: Path,
    source_lang: str = "ar",
    target_lang: str = "en",
    pdf2zh_timeout_seconds: int = 600,
) -> dict[str, Any]:
    """
    Process PDF files from candidates.

    If pdf2zh is available, translates PDFs preserving layout.
    Otherwise, extracts text as fallback.

    Returns:
        dict with:
        - pdf_files: list of PDF candidate dicts
        - non_pdf_candidates: list of non-PDF candidates
        - translated_pdfs: list of paths to translated PDF files
        - extracted_texts: list of paths to extracted text files
        - warnings: list of warning messages
        - errors: list of error messages
    """
    pdf_files = [c for c in candidates if Path(c["path"]).suffix.lower() == ".pdf"]
    non_pdf_candidates = [c for c in candidates if Path(c["path"]).suffix.lower() != ".pdf"]

    result: dict[str, Any] = {
        "pdf_files": pdf_files,
        "non_pdf_candidates": non_pdf_candidates,
        "translated_pdfs": [],
        "extracted_texts": [],
        "warnings": [],
        "errors": [],
    }

    if not pdf_files:
        return result

    pdf_output_dir = review_dir / "PDF_Output"
    pdf_output_dir.mkdir(parents=True, exist_ok=True)

    if is_pdf2zh_available():
        log.info("pdf2zh available, translating %d PDF file(s)", len(pdf_files))
        for pdf_candidate in pdf_files:
            pdf_path = Path(pdf_candidate["path"])
            # Use OCR fallback for scanned PDFs
            translate_result = translate_pdf_with_ocr_fallback(
                pdf_path,
                pdf_output_dir,
                service="google",
                source_lang=source_lang,
                target_lang=target_lang,
                timeout_seconds=pdf2zh_timeout_seconds,
            )
            if translate_result.get("ok"):
                if translate_result.get("docx_path"):
                    # Vision translation produced a DOCX
                    result["translated_pdfs"].append(translate_result["docx_path"])
                    result["warnings"].append(
                        f"Vision-LLM translation used for {pdf_path.name} (DOCX output)"
                    )
                else:
                    if translate_result.get("mono_path"):
                        result["translated_pdfs"].append(translate_result["mono_path"])
                    if translate_result.get("dual_path"):
                        result["translated_pdfs"].append(translate_result["dual_path"])
                if translate_result.get("ocr_used"):
                    result["warnings"].append(
                        f"OCR preprocessing used for scanned PDF: {pdf_path.name}"
                    )
            else:
                error_msg = translate_result.get("error", "unknown error")
                result["errors"].append(f"PDF translation failed for {pdf_path.name}: {error_msg}")
                # Fallback to text extraction
                fallback_result = translate_pdf_fallback_text(pdf_path, pdf_output_dir, source_lang=source_lang)
                if fallback_result.get("ok"):
                    result["extracted_texts"].append(fallback_result["text_path"])
                    result["warnings"].append(
                        f"PDF layout not preserved for {pdf_path.name}, extracted text only"
                    )
                else:
                    result["errors"].append(
                        f"PDF fallback extraction also failed for {pdf_path.name}: {fallback_result.get('error')}"
                    )
    else:
        install_info = check_pdf2zh_installation()
        result["warnings"].append(
            f"pdf2zh not installed. PDF translation will use text extraction only.\n{install_info['message']}"
        )
        log.warning("pdf2zh not available, falling back to text extraction for %d PDF(s)", len(pdf_files))
        for pdf_candidate in pdf_files:
            pdf_path = Path(pdf_candidate["path"])
            extract_result = translate_pdf_fallback_text(pdf_path, pdf_output_dir, source_lang=source_lang)
            if extract_result.get("ok"):
                result["extracted_texts"].append(extract_result["text_path"])
            else:
                result["errors"].append(
                    f"PDF extraction failed for {pdf_path.name}: {extract_result.get('error')}"
                )

    return result


def _dedupe_hits(hits: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for hit in hits:
        path = str(hit.get("path") or "")
        chunk = int(hit.get("chunk_index") or 0)
        key = (path, chunk)
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _extract_message_id(payload: dict[str, Any], fallback_text: str = "") -> str:
    for key in ("message_id", "messageId", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    message = payload.get("message")
    if isinstance(message, dict):
        for key in ("message_id", "messageId", "id"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if fallback_text:
        matched = re.search(r"\[message_id:\s*([^\]]+)\]", fallback_text, flags=re.IGNORECASE)
        if matched:
            return matched.group(1).strip()
    return ""


def _latest_message_meta(inbox_dir: Path) -> dict[str, Any]:
    payload_files = sorted(inbox_dir.glob("payload_*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    if not payload_files:
        return {"message_id": "", "raw_message_ref": "", "token_guard_applied": False}
    payload_path = payload_files[0]
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except Exception:
        return {"message_id": "", "raw_message_ref": str(payload_path.resolve()), "token_guard_applied": False}
    text_value = ""
    for key in ("text", "message", "body", "content"):
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            text_value = candidate
            break
    if not text_value and isinstance(payload.get("message"), dict):
        msg = payload["message"]
        for key in ("text", "body", "content"):
            candidate = msg.get(key)
            if isinstance(candidate, str) and candidate.strip():
                text_value = candidate
                break
    token_guard_applied = bool(payload.get("token_guard_applied", False))
    return {
        "message_id": _extract_message_id(payload, fallback_text=text_value),
        "raw_message_ref": str(payload_path.resolve()),
        "token_guard_applied": token_guard_applied,
    }


def _canonicalize_kb_company(*, kb_root: Path, kb_company: str) -> str:
    company_norm = (kb_company or "").strip()
    if not company_norm:
        return ""
    root = kb_root.expanduser().resolve()
    sections = ["00_Glossary", "10_Style_Guide", "20_Domain_Knowledge", "30_Reference", "40_Templates"]
    target = company_norm.casefold()
    for section in sections:
        section_root = root / section
        if not section_root.exists() or not section_root.is_dir():
            continue
        for child in section_root.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name.casefold() == target:
                return child.name
    return company_norm


def _recall_cross_job_context(conn, *, company: str, query: str) -> list[dict[str, Any]]:
    """Recall relevant cross-job memories (company-scoped) from local SQLite."""
    try:
        return search_memories(conn, company=company, query=query, top_k=5)
    except Exception:
        return []


def _read_change_log_points(*, review_dir: Path, limit: int = 12) -> list[str]:
    candidates = [
        Path(review_dir) / ".system" / "change_log.md",
        Path(review_dir) / "Change Log.md",
    ]
    lines: list[str] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            break
        except OSError:
            continue
    if not lines:
        return []
    points = []
    for line in lines:
        item = line.strip()
        if not item.startswith("- "):
            continue
        cleaned = item[2:].strip()
        if cleaned:
            points.append(cleaned)
        if len(points) >= max(1, int(limit)):
            break
    return points


def _collect_delivery_files(*, artifacts: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    def _add(kind: str, path_value: str, *, source_path: str = "", name: str = "") -> None:
        raw = str(path_value or "").strip()
        if not raw:
            return
        path = Path(raw).expanduser().resolve()
        if not path.exists() or not path.is_file():
            return
        out.append(
            {
                "kind": str(kind or "").strip() or "file",
                "path": str(path),
                "name": str(name or path.name),
                "source_path": str(source_path or ""),
            }
        )

    for item in (artifacts.get("delivery_files") or []):
        if not isinstance(item, dict):
            continue
        _add(
            str(item.get("kind") or "file"),
            str(item.get("path") or ""),
            source_path=str(item.get("source_path") or ""),
            name=str(item.get("name") or ""),
        )

    for entry in (artifacts.get("docx_files") or []):
        if not isinstance(entry, dict):
            continue
        source_path = str(entry.get("source_path") or "")
        _add("final_docx", str(entry.get("path") or ""), source_path=source_path, name=str(entry.get("name") or ""))
        _add("bilingual_docx", str(entry.get("bilingual_path") or ""), source_path=source_path)

    for entry in (artifacts.get("xlsx_files") or []):
        if not isinstance(entry, dict):
            continue
        source_path = str(entry.get("source_path") or "")
        _add("final_xlsx", str(entry.get("path") or ""), source_path=source_path, name=str(entry.get("name") or ""))
        _add("bilingual_xlsx", str(entry.get("bilingual_path") or ""), source_path=source_path)

    _add("final_docx", str(artifacts.get("final_docx") or ""))
    _add("bilingual_docx", str(artifacts.get("bilingual_docx") or ""))
    _add("final_xlsx", str(artifacts.get("final_xlsx") or ""))
    _add("bilingual_xlsx", str(artifacts.get("bilingual_xlsx") or ""))

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in out:
        p = str(item.get("path") or "").strip()
        if not p or p in seen:
            continue
        seen.add(p)
        deduped.append(item)
    return deduped


def _send_delivery_files(
    *,
    paths: RuntimePaths,
    conn,
    job_id: str,
    task_name: str,
    target: str,
    dry_run: bool,
    delivery_files: list[dict[str, str]],
) -> dict[str, Any]:
    attempted = 0
    sent: list[str] = []
    failed: list[dict[str, Any]] = []
    for item in delivery_files:
        file_path = str(item.get("path") or "").strip()
        if not file_path:
            continue
        attempted += 1
        send_result = send_media(
            target=target,
            media_path=file_path,
            message="",
            dry_run=dry_run,
        )
        if bool(send_result.get("ok")):
            sent.append(file_path)
        else:
            failed.append(
                {
                    "path": file_path,
                    "name": str(item.get("name") or Path(file_path).name),
                    "kind": str(item.get("kind") or ""),
                    "send_result": send_result,
                }
            )

    summary = {
        "attempted": attempted,
        "sent": len(sent),
        "failed": len(failed),
        "sent_paths": sent,
        "failed_items": failed,
    }
    if attempted <= 0:
        return summary

    milestone = "delivery_sent" if not failed else "delivery_partial_failed"
    record_event(
        conn,
        job_id=job_id,
        milestone=milestone,
        payload={"target": target, "summary": summary},
    )
    append_log(paths, "events.log", f"{milestone}\t{job_id}\t{summary}")

    if failed:
        failed_names = ", ".join(item["name"] for item in failed[:3])
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="delivery_partial_failed",
            message=(
                "⚠️ File delivery partially failed\n"
                f"📋 {task_name}\n"
                f"Failed: {failed_names}\n"
                "Send: status · rerun"
            ),
            target=target,
            dry_run=dry_run,
        )
    return summary


def _summarize_kb_hits(kb_hits: list[dict[str, Any]], *, limit: int = 6) -> list[str]:
    out: list[str] = []
    seen: set[tuple[str, str]] = set()
    for hit in kb_hits:
        if not isinstance(hit, dict):
            continue
        source_group = str(hit.get("source_group") or "general").strip() or "general"
        name = str(Path(str(hit.get("path") or "")).name or "").strip()
        if not name:
            continue
        key = (source_group, name)
        if key in seen:
            continue
        seen.add(key)
        out.append(f"{source_group}:{name}")
        if len(out) >= max(1, int(limit)):
            break
    return out


def _store_job_memory(
    *,
    conn,
    job_id: str,
    task_type: str,
    rounds_count: int,
    review_dir: Path,
    kb_company: str,
    task_label: str,
    kb_hits: list[dict[str, Any]],
) -> None:
    """Store durable translation decision and constraints in openclaw-mem."""
    company_norm = (kb_company or "").strip()
    task_label_norm = (task_label or "").strip()

    lines = []
    if company_norm:
        lines.append(f"Company: {company_norm}")
    if task_label_norm:
        lines.append(f"Task: {task_label_norm}")
    lines.append(f"Job: {job_id}")
    lines.append(f"Type: {task_type}")

    change_points = _read_change_log_points(review_dir=review_dir)
    if change_points:
        lines.append("Decisions:")
        lines.extend([f"- {x}" for x in change_points[:12]])

    kb_summary = _summarize_kb_hits(kb_hits)
    if kb_summary:
        lines.append("KB sources:")
        lines.extend([f"- {x}" for x in kb_summary])

    lines.append(f"Convergence: {rounds_count} round(s)")
    text = "\n".join(lines).strip()
    if len(text) > 1800:
        text = text[:1800] + "\n...(truncated)"
    try:
        add_memory(conn, company=company_norm, kind="decision", text=text, job_id=job_id)
    except Exception:
        pass


def run_job_pipeline(
    *,
    job_id: str,
    work_root: Path,
    kb_root: Path,
    notify_target: str | None = None,
    dry_run_notify: bool = False,
) -> dict[str, Any]:
    paths = ensure_runtime_paths(work_root)
    conn = db_connect(paths)
    job = get_job(conn, job_id)
    if not job:
        raise ValueError(f"Job not found: {job_id}")

    if str(job.get("status", "")) in {"running", "preflight"}:
        # Duplicate guard:
        # When invoked from run-worker, job.status may already be "running"
        # (set during queue claim). In that case, allow the invocation that owns
        # the same queue_id to continue.
        current_queue_id = int(str(os.getenv("OPENCLAW_QUEUE_ID", "0") or "0").strip() or 0)
        active_queue = get_active_queue_item(conn, job_id=job_id) or {}
        active_queue_id = int(active_queue.get("id") or 0)
        active_state = str(active_queue.get("state") or "").strip().lower()
        same_claimed_run = (
            current_queue_id > 0
            and active_queue_id == current_queue_id
            and active_state == "running"
        )
        if not same_claimed_run:
            log.warning("Job %s is already running — skipping duplicate", job_id)
            conn.close()
            return {"ok": False, "job_id": job_id, "status": "already_running", "skipped": True}

    update_job_status(conn, job_id=job_id, status="preflight", errors=[])

    # Web Gateway preflight (web session readiness).
    gateway_enabled = str(os.getenv("OPENCLAW_WEB_GATEWAY_ENABLED", "0")).strip().lower() not in {"0", "false", "off", "no", ""}
    gateway_preflight_enabled = str(os.getenv("OPENCLAW_WEB_GATEWAY_PREFLIGHT", "1")).strip().lower() not in {"0", "false", "off", "no", ""}
    if gateway_enabled and gateway_preflight_enabled:
        import urllib.error
        import urllib.request

        base_url = str(os.getenv("OPENCLAW_WEB_GATEWAY_BASE_URL", "http://127.0.0.1:8765")).strip().rstrip("/")

        global_primary = str(os.getenv("OPENCLAW_WEB_LLM_PRIMARY", "deepseek_web")).strip() or "deepseek_web"
        global_fallback = str(os.getenv("OPENCLAW_WEB_LLM_FALLBACK", "chatgpt_web")).strip() or "chatgpt_web"
        gen_primary = str(os.getenv("OPENCLAW_WEB_LLM_GENERATE_PRIMARY", "")).strip()
        gen_fallback = str(os.getenv("OPENCLAW_WEB_LLM_GENERATE_FALLBACK", "")).strip()
        rev_primary = str(os.getenv("OPENCLAW_WEB_LLM_REVIEW_PRIMARY", "")).strip()
        rev_fallback = str(os.getenv("OPENCLAW_WEB_LLM_REVIEW_FALLBACK", "")).strip()

        def _chain(primary_override: str, fallback_override: str) -> list[str]:
            primary = primary_override or global_primary
            fallback = fallback_override or global_fallback
            out: list[str] = []
            for raw in (primary, fallback):
                p = str(raw or "").strip()
                if not p:
                    continue
                if p.lower() in {"none", "disabled", "off"}:
                    continue
                if p in out:
                    continue
                out.append(p)
            return out

        providers_to_check: list[str] = []
        for p in _chain(gen_primary, gen_fallback) + _chain(rev_primary, rev_fallback):
            if p not in providers_to_check:
                providers_to_check.append(p)

        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="preflight_started",
            message="\U0001f9ea Preflight: checking web gateway session\u2026",
            target=notify_target,
            dry_run=dry_run_notify,
        )

        failures: list[dict[str, Any]] = []
        for provider in (providers_to_check or [global_primary]):
            payload = json.dumps(
                {"provider": provider, "interactive": False, "timeout_seconds": 15},
                ensure_ascii=False,
            ).encode("utf-8")
            req = urllib.request.Request(
                f"{base_url}/session/login",
                data=payload,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            login_resp: dict[str, Any] = {}
            login_ok = False
            preflight_error = ""
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                login_resp = json.loads(raw) if raw.strip().startswith("{") else {"raw": raw[:2000]}
                login_ok = bool(login_resp.get("ok", False))
            except urllib.error.HTTPError as exc:
                raw_err = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
                preflight_error = f"http_{exc.code}:{raw_err[:800]}"
            except urllib.error.URLError as exc:
                preflight_error = f"url_error:{exc}"
            except TimeoutError:
                preflight_error = "timeout"
            except Exception as exc:
                preflight_error = str(exc)

            if not login_ok:
                failures.append(
                    {
                        "provider": provider,
                        "error": preflight_error or "login_required",
                        "response": login_resp,
                    }
                )

        if failures:
            tokens: list[str] = []
            status_flags: list[str] = []
            for item in failures:
                provider = str(item.get("provider") or "").strip()
                err = str(item.get("error") or "").strip()
                token = f"gateway_login_required:{provider}"
                if err and err != "login_required":
                    token = f"gateway_unavailable:{provider}"
                tokens.append(token)
                status_flags.append(token.split(":", 1)[0])

            update_job_status(conn, job_id=job_id, status="needs_attention", errors=tokens)
            notify_milestone(
                paths=paths,
                conn=conn,
                job_id=job_id,
                milestone="preflight_failed",
                message=(
                    "\u26d4 Web gateway not ready.\n"
                    f"Providers: {', '.join(sorted(set([str(x.get('provider') or '').strip() for x in failures if str(x.get('provider') or '').strip()]))) or global_primary}\n"
                    "Open Tauri -> Runtime -> Provider Login (or run: scripts/start.sh --gateway-login)."
                ),
                target=notify_target,
                dry_run=dry_run_notify,
            )
            record_event(
                conn,
                job_id=job_id,
                milestone="preflight_detail",
                payload={
                    "ok": False,
                    "providers": providers_to_check,
                    "failures": failures,
                },
            )
            conn.close()
            return {
                "ok": False,
                "job_id": job_id,
                "status": "needs_attention",
                "errors": tokens,
                "status_flags": status_flags,
            }

        record_event(
            conn,
            job_id=job_id,
            milestone="preflight_done",
            payload={"ok": True, "providers": providers_to_check},
        )
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="preflight_done",
            message=f"\u2705 Preflight OK ({', '.join(providers_to_check or [global_primary])})",
            target=notify_target,
            dry_run=dry_run_notify,
        )

    update_job_status(conn, job_id=job_id, status="running", errors=[])
    set_sender_active_job(conn, sender=str(job.get("sender", "")).strip(), job_id=job_id)

    kb_company_focus = _canonicalize_kb_company(kb_root=kb_root, kb_company=str(job.get("kb_company") or "").strip())
    isolation_mode = str(os.getenv("OPENCLAW_KB_ISOLATION_MODE", "company_strict")).strip().lower() or "company_strict"
    rag_collection_base = str(os.getenv("OPENCLAW_RAG_COLLECTION", "translation-kb")).strip() or "translation-kb"
    rag_collection_mode = str(os.getenv("OPENCLAW_RAG_COLLECTION_MODE", "auto")).strip().lower() or "auto"

    notify_milestone(
        paths=paths,
        conn=conn,
        job_id=job_id,
        milestone="kb_sync_started",
        message="\U0001f4da Syncing knowledge base\u2026",
        target=notify_target,
        dry_run=dry_run_notify,
    )
    kb_report_path = paths.kb_system_root / "kb_sync_latest.json"
    kb_sync_result = sync_kb_with_rag(
        conn=conn,
        kb_root=kb_root,
        report_path=kb_report_path,
        rag_backend=str(os.getenv("OPENCLAW_RAG_BACKEND", "clawrag")).strip().lower(),
        rag_base_url=str(os.getenv("OPENCLAW_RAG_BASE_URL", "http://127.0.0.1:8080")).strip(),
        rag_collection=rag_collection_base,
        rag_collection_mode=rag_collection_mode,
        isolation_mode=isolation_mode,
        focus_company=kb_company_focus,
    )
    kb_report = dict(kb_sync_result.get("local_report") or {})
    rag_sync_report = dict(kb_sync_result.get("rag_report") or {})
    notify_milestone(
        paths=paths,
        conn=conn,
        job_id=job_id,
        milestone="kb_sync_done",
        message=f"\U0001f4da KB ready \u00b7 {kb_report['created']} new \u00b7 {kb_report['updated']} updated",
        target=notify_target,
        dry_run=dry_run_notify,
    )
    files = list_job_files(conn, job_id)
    candidates = _build_candidates(files)
    review_dir = Path(job["review_dir"]).resolve()
    review_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir = Path(str(job.get("inbox_dir") or "")).expanduser().resolve()
    source = str(job.get("source", ""))
    message_meta = _latest_message_meta(inbox_dir) if source == "telegram" else {}
    strict_router_enabled = str(
        os.getenv("OPENCLAW_STRICT_ROUTER", "1")
    ).strip().lower() not in {"0", "false", "off", "no"}
    router_mode = "strict" if strict_router_enabled else "hybrid"

    # Process PDF files - translate with pdf2zh or extract text as fallback
    pdf_result = _process_pdf_files(
        candidates=candidates,
        review_dir=review_dir,
        source_lang="ar",
        target_lang="en",
    )
    pdf_files = pdf_result["pdf_files"]
    candidates = pdf_result["non_pdf_candidates"]  # Continue with non-PDF candidates
    pdf_translated = pdf_result["translated_pdfs"]
    pdf_extracted = pdf_result["extracted_texts"]
    pdf_warnings = pdf_result["warnings"]
    pdf_errors = pdf_result["errors"]

    # If we extracted text from PDFs (fallback mode), feed those .txt files into the main
    # translation pipeline so the user still gets an English output (layout won't be preserved).
    extracted_candidates: list[dict[str, Any]] = []
    for extracted_path in pdf_extracted:
        try:
            p = Path(str(extracted_path)).expanduser().resolve()
        except Exception:
            continue
        if not p.exists():
            continue
        extracted_candidates.append(
            {
                "path": str(p),
                "name": p.name,
                "language": infer_language(p),
                "version": infer_version(p),
                "role": "source",
            }
        )
    if extracted_candidates:
        candidates.extend(extracted_candidates)

    # Record PDF processing results
    if pdf_files:
        record_event(
            conn,
            job_id=job_id,
            milestone="pdf_processed",
            payload={
                "pdf_count": len(pdf_files),
                "translated_count": len(pdf_translated),
                "extracted_count": len(pdf_extracted),
                "warnings": pdf_warnings,
                "errors": pdf_errors,
            },
        )
        if pdf_translated:
            notify_milestone(
                paths=paths,
                conn=conn,
                job_id=job_id,
                milestone="pdf_translated",
                message=f"\U0001f4c4 PDF translated \u00b7 {len(pdf_translated)} file(s)",
                target=notify_target,
                dry_run=dry_run_notify,
            )
        elif pdf_extracted:
            notify_milestone(
                paths=paths,
                conn=conn,
                job_id=job_id,
                milestone="pdf_extracted",
                message=f"\U0001f4c4 PDF text extracted \u00b7 {len(pdf_extracted)} file(s)\n\u26a0\ufe0f Layout not preserved (pdf2zh not available)",
                target=notify_target,
                dry_run=dry_run_notify,
            )
        for warn in pdf_warnings[:2]:
            log.warning("PDF processing warning: %s", warn)

    # Handle PDF-only jobs: if all files were PDFs and they were processed, consider job complete
    if not candidates:
        if pdf_translated or pdf_extracted:
            # PDF-only job with successful processing
            artifacts = {
                "pdf_translated": [str(p) for p in pdf_translated],
                "pdf_extracted": [str(p) for p in pdf_extracted],
            }
            update_job_result(
                conn,
                job_id=job_id,
                status="review_ready",
                iteration_count=1,
                double_pass=False,
                status_flags=pdf_warnings,
                artifacts=artifacts,
                errors=pdf_errors,
            )
            notify_milestone(
                paths=paths,
                conn=conn,
                job_id=job_id,
                milestone="review_ready",
                message=(
                    f"\u2705 PDF translation complete\n"
                    f"\U0001f4c1 {review_dir}\n"
                    + ("\n\u26a0\ufe0f Layout not preserved" if pdf_extracted else "")
                    + "\n\nSend: ok \u00b7 no {reason}"
                ),
                target=notify_target,
                dry_run=dry_run_notify,
            )
            conn.close()
            return {
                "ok": True,
                "job_id": job_id,
                "status": "review_ready",
                "review_dir": str(review_dir),
                "artifacts": artifacts,
                "status_flags": pdf_warnings,
                "errors": pdf_errors,
            }
        else:
            if pdf_files:
                pdf_names = []
                for item in pdf_files:
                    raw_path = str(item.get("path") or "")
                    raw_name = str(item.get("name") or "")
                    pdf_names.append(Path(raw_path).name if raw_path else raw_name)
                pdf_names = [n for n in pdf_names if n]
                file_line = f"\U0001f4c4 {pdf_names[0]}" if len(pdf_names) == 1 else f"\U0001f4c4 PDF(s): {', '.join(pdf_names[:3])}"
                reason = ""
                if pdf_errors:
                    reason_raw = str(pdf_errors[0])
                    reason = reason_raw.split(": ", 1)[1].strip() if ": " in reason_raw else reason_raw
                msg_lines = [
                    "\u26a0\ufe0f PDF received, but it has no extractable text",
                    file_line,
                ]
                if reason:
                    msg_lines.append(f"Reason: {reason}")
                msg_lines.extend(
                    [
                        "",
                        "This usually means the PDF is scanned/image-based. Run OCR (make it searchable) and upload again, or export as DOCX.",
                        "Optional: install pdf2zh for layout-preserving PDF translation: pip install pdf2zh",
                        "",
                        "Send: rerun",
                    ]
                )
                errors_out = [str(e) for e in (pdf_errors or []) if str(e)]
                if not errors_out:
                    errors_out = ["pdf_processing_failed"]
                update_job_status(conn, job_id=job_id, status="needs_revision", status_flags=pdf_warnings, errors=errors_out)
                notify_milestone(
                    paths=paths,
                    conn=conn,
                    job_id=job_id,
                    milestone="needs_revision",
                    message="\n".join(msg_lines),
                    target=notify_target,
                    dry_run=dry_run_notify,
                )
                conn.close()
                return {
                    "ok": False,
                    "job_id": job_id,
                    "status": "needs_revision",
                    "review_dir": str(review_dir),
                    "errors": errors_out,
                    "status_flags": pdf_warnings,
                }

            update_job_status(conn, job_id=job_id, status="incomplete_input", errors=["no_supported_attachments"])
            notify_milestone(
                paths=paths,
                conn=conn,
                job_id=job_id,
                milestone="failed",
                message=f"\U0001f4ed No supported files\nSupported: .docx .xlsx .csv .pdf",
                target=notify_target,
                dry_run=dry_run_notify,
            )
            conn.close()
            return {"ok": False, "job_id": job_id, "status": "incomplete_input", "errors": ["no_supported_attachments"]}

    kb_company = str(job.get("kb_company") or "").strip()
    kb_company = _canonicalize_kb_company(kb_root=kb_root, kb_company=kb_company) if kb_company else ""
    query_parts = [str(job.get("subject", "") or "").strip(), str(job.get("message_text", "") or "").strip()]
    if kb_company:
        query_parts.append(f"Company: {kb_company}")
    file_names = [str(c.get("name") or "").strip() for c in candidates[:8] if str(c.get("name") or "").strip()]
    if file_names:
        query_parts.append("Files: " + " ".join(file_names))
    query = " ".join([p for p in query_parts if p]).strip()
    rag_collection = resolve_rag_collection(
        base_collection=rag_collection_base,
        company=kb_company,
        mode=rag_collection_mode,
        isolation_mode=isolation_mode,
    )
    kb_hits: list[dict[str, Any]] = []
    knowledge_backend = "local"
    pre_status_flags: list[str] = []
    if query:
        rag_fetch = retrieve_kb_with_fallback(
            conn=conn,
            query=query,
            task_type=str(job.get("task_type") or ""),
            kb_root=kb_root,
            kb_company=kb_company,
            isolation_mode=isolation_mode,
            rag_backend=str(os.getenv("OPENCLAW_RAG_BACKEND", "clawrag")).strip().lower(),
            rag_base_url=str(os.getenv("OPENCLAW_RAG_BASE_URL", "http://127.0.0.1:8080")).strip(),
            rag_collection=rag_collection,
            top_k_clawrag=20,
            top_k_local=12,
        )
        kb_hits = _dedupe_hits(list(rag_fetch.get("hits") or []), limit=12)
        knowledge_backend = str(rag_fetch.get("backend") or "local")
        pre_status_flags.extend([str(x) for x in (rag_fetch.get("status_flags") or []) if str(x)])

    cross_job_memories = _recall_cross_job_context(conn, company=kb_company, query=query) if query else []

    record_event(
        conn,
        job_id=job_id,
        milestone="kb_retrieve_done",
        payload={
            "query": query,
            "hit_count": len(kb_hits),
            "backend": knowledge_backend,
            "hits": kb_hits[:12],
            "rerank_report": (rag_fetch.get("rag_result") or {}).get("rerank_report") if query else {},
            "rag_sync_report": rag_sync_report,
        },
    )
    notify_milestone(
        paths=paths,
        conn=conn,
        job_id=job_id,
        milestone="kb_retrieve_done",
        message=f"\U0001f50d KB retrieval \u00b7 {len(kb_hits)} hits",
        target=notify_target,
        dry_run=dry_run_notify,
    )

    meta = {
        "job_id": job_id,
        "root_path": str(paths.work_root.resolve()),
        "review_dir": str(review_dir),
        "source": job.get("source", ""),
        "sender": job.get("sender", ""),
        "message_id": message_meta.get("message_id", ""),
        "raw_message_ref": message_meta.get("raw_message_ref", ""),
        "subject": job.get("subject", ""),
        "message_text": job.get("message_text", ""),
        "candidate_files": candidates,
        "knowledge_context": kb_hits,
        "knowledge_backend": knowledge_backend,
        "kb_root": str(Path(kb_root).expanduser().resolve()),
        "kb_company": kb_company,
        "max_rounds": 3,
        "codex_available": True,
        "gemini_available": True,
        "router_mode": router_mode,
        "token_guard_applied": bool(message_meta.get("token_guard_applied", False)),
        "status_flags_seed": pre_status_flags,
        "cross_job_memories": cross_job_memories,
    }

    plan = run_translation(meta, plan_only=True)
    intent = plan.get("intent") or {}
    task_label = str(intent.get("task_label") or "")
    if plan.get("plan"):
        p = plan["plan"]
        update_job_plan(
            conn,
            job_id=job_id,
            # Keep job status as running while the pipeline is executing.
            # The plan-only classifier returns status="planned" on success, which
            # would otherwise make `status` look stuck before round 1.
            status="running",
            task_type=p.get("task_type", ""),
            confidence=float(p.get("confidence", 0.0)),
            estimated_minutes=int(p.get("estimated_minutes", 0)),
            runtime_timeout_minutes=int(p.get("time_budget_minutes", 0)),
            task_label=task_label,
        )
    plan_file = review_dir / ".system" / "execution_plan.json"
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_text(json_dumps(plan), encoding="utf-8")
    _task_name = task_label or "Translation task"
    _task_type = str(intent.get("task_type") or plan.get("plan", {}).get("task_type") or "").replace("_", " ").title() or "Unknown"
    _src_lang = str(intent.get("source_language") or "").strip()
    _tgt_lang = str(intent.get("target_language") or "").strip()
    _lang_line = f"{_src_lang} \u2192 {_tgt_lang}" if _src_lang and _tgt_lang and _src_lang != "unknown" else ""
    if plan.get("status") == "failed":
        errors = plan.get("errors") or ["intent_classification_failed"]
        update_job_status(conn, job_id=job_id, status="failed", errors=errors)
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="failed",
            message=f"\u274c Classification failed\n\U0001f4cb {_task_name}\nSend: rerun to retry",
            target=notify_target,
            dry_run=dry_run_notify,
        )
        conn.close()
        return {"ok": False, "job_id": job_id, "status": "failed", "errors": errors}

    if plan.get("status") == "missing_inputs":
        missing = intent.get("missing_inputs") or []
        update_job_status(conn, job_id=job_id, status="missing_inputs", errors=[f"missing:{x}" for x in missing])
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="missing_inputs",
            message=f"\U0001f4ed Missing inputs\n\U0001f4cb {_task_name}\n\U0001f4ce {', '.join(missing) if missing else 'unknown'}\nUpload files, then: run",
            target=notify_target,
            dry_run=dry_run_notify,
        )
        conn.close()
        return {
            "ok": False,
            "job_id": job_id,
            "status": "missing_inputs",
            "intent": intent,
            "errors": [f"missing:{x}" for x in missing],
        }

    notify_milestone(
        paths=paths,
        conn=conn,
        job_id=job_id,
        milestone="intent_classified",
        message=(
            f"\U0001f9e0 Intent classified\n"
            f"\U0001f4cb {_task_name}\n"
            f"{_task_type}"
            + (f" \u00b7 {_lang_line}" if _lang_line else "")
            + f" \u00b7 ~{plan.get('estimated_minutes', 0)}m"
        ),
        target=notify_target,
        dry_run=dry_run_notify,
    )

    sent_rounds: set[int] = set()

    def _format_round_message(rd: dict[str, Any]) -> str:
        rd_no = rd.get("round") or "?"
        gen_model, rev_model = _extract_models(rd)
        gen_ok = "\u2705" if rd.get("codex_pass") else "\u274c"
        rev_ok = "\u2705" if rd.get("gemini_pass") else "\u274c"
        return f"\U0001f504 Round {rd_no} done\nGen({gen_model}): {gen_ok} \u00b7 Review({rev_model}): {rev_ok}"

    def _extract_models(rd: dict[str, Any]) -> tuple[str, str]:
        generator = (rd.get("generator") or {}) if isinstance(rd.get("generator"), dict) else {}
        reviewer = (rd.get("reviewer") or {}) if isinstance(rd.get("reviewer"), dict) else {}
        gen_model = (
            str(rd.get("generator_model") or "")
            or str(generator.get("model") or "")
        ).strip() or "unknown"
        rev_model = (
            str(rd.get("review_model") or "")
            or str(reviewer.get("model") or "")
        ).strip() or "unknown"

        gen_provider = str(generator.get("provider") or "").strip()
        rev_provider = str(reviewer.get("provider") or "").strip()
        gen_agent = str(generator.get("agent_id") or "").strip()
        rev_agent = str(reviewer.get("agent_id") or "").strip()
        if gen_agent == "web_gateway" and gen_provider:
            gen_model = gen_provider.replace("_", "-")
        if rev_agent == "web_gateway" and rev_provider:
            rev_model = rev_provider.replace("_", "-")
        return gen_model, rev_model

    def _format_models_summary(rd: dict[str, Any]) -> str:
        gen_model, rev_model = _extract_models(rd)
        return f"\U0001f9e0 Models: Gen({gen_model}) \u00b7 Review({rev_model})"

    def _on_round_complete(rd: dict[str, Any]) -> None:
        try:
            rd_no_int = int(rd.get("round") or 0)
        except (TypeError, ValueError):
            return
        if rd_no_int <= 0 or rd_no_int in sent_rounds:
            return
        sent_rounds.add(rd_no_int)
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone=f"round_{rd_no_int}_done",
            message=_format_round_message(rd),
            target=notify_target,
            dry_run=dry_run_notify,
        )

    notify_milestone(
        paths=paths,
        conn=conn,
        job_id=job_id,
        milestone="running",
        message=f"\U0001f680 Translating\n\U0001f4cb {_task_name}\n\u23f3 OpenClaw routing \u00b7 up to 3 rounds",
        target=notify_target,
        dry_run=dry_run_notify,
    )
    # Round 1 can take a while for spreadsheet jobs because generation runs in
    # multiple batches before any round artifacts are written. Emit an explicit
    # "round started" milestone so status does not look stuck.
    notify_milestone(
        paths=paths,
        conn=conn,
        job_id=job_id,
        milestone="round_1_started",
        message=f"\U0001f504 Round 1 started\n\U0001f4cb {_task_name}",
        target=notify_target,
        dry_run=dry_run_notify,
    )
    result = run_translation(meta, plan_only=False, on_round_complete=_on_round_complete)
    cooldown_friendly = str(os.getenv("OPENCLAW_COOLDOWN_FRIENDLY_MODE", "1")).strip().lower() not in {"0", "false", "off", "no"}
    if cooldown_friendly and bool(result.get("queue_retry_recommended")):
        retry_after = max(30, int(result.get("queue_retry_after_seconds") or 300))
        retry_reason = str(result.get("queue_retry_reason") or "all_providers_cooldown").strip() or "all_providers_cooldown"
        errs = [str(x) for x in (result.get("errors") or []) if str(x).strip()]
        defer_token = f"queue_defer_cooldown:{retry_after}"
        if defer_token not in errs:
            errs.insert(0, defer_token)
        reason_token = f"cooldown_reason:{retry_reason}"
        if reason_token not in errs:
            errs.append(reason_token)
        flags = [str(x) for x in (result.get("status_flags") or []) if str(x).strip()]
        if "cooldown_deferred" not in flags:
            flags.append("cooldown_deferred")
        result["errors"] = errs
        result["status_flags"] = flags
        result["status"] = "queued"
        result["ok"] = False

    # Run detail validation if enabled (after translation, before quality gate)
    detail_validation_enabled = str(
        os.getenv("OPENCLAW_DETAIL_VALIDATION", "1")
    ).strip().lower() not in {"0", "false", "off", "no"}
    if detail_validation_enabled and result.get("status") in {"review_ready", "needs_attention"}:
        try:
            from scripts.detail_validator import ValidationReportGenerator, validate_file_pair

            # Orchestrator returns a manifest with source/translated paths; prefer that over
            # guessing based on filenames (Final.xlsx/Final.docx don't match source names).
            artifacts = dict(result.get("artifacts", {}))
            pairs: list[tuple[Path, Path]] = []
            for entry in (artifacts.get("docx_files") or []):
                if not isinstance(entry, dict):
                    continue
                src = str(entry.get("source_path") or "").strip()
                out = str(entry.get("path") or "").strip()
                if src and out:
                    pairs.append((Path(src), Path(out)))
            for entry in (artifacts.get("xlsx_files") or []):
                if not isinstance(entry, dict):
                    continue
                src = str(entry.get("source_path") or "").strip()
                out = str(entry.get("path") or "").strip()
                if src and out:
                    pairs.append((Path(src), Path(out)))

            # Back-compat: manifest can omit *_files arrays when only a single Final.*
            # is produced. Pair it with the first matching source candidate.
            if not any(p[0].suffix.lower() == ".xlsx" for p in pairs):
                final_xlsx = str(artifacts.get("final_xlsx") or "").strip()
                if final_xlsx:
                    src_xlsx = next((c.get("path") for c in candidates if str(c.get("path") or "").lower().endswith(".xlsx")), "")
                    if src_xlsx:
                        pairs.append((Path(str(src_xlsx)), Path(final_xlsx)))
            if not any(p[0].suffix.lower() == ".docx" for p in pairs):
                final_docx = str(artifacts.get("final_docx") or "").strip()
                if final_docx:
                    src_docx = next((c.get("path") for c in candidates if str(c.get("path") or "").lower().endswith(".docx")), "")
                    if src_docx:
                        pairs.append((Path(str(src_docx)), Path(final_docx)))

            # Only validate if we have both original and translated files
            validated = {}
            for orig, trans in pairs:
                try:
                    orig_path = Path(orig).expanduser().resolve()
                    trans_path = Path(trans).expanduser().resolve()
                    if not orig_path.exists() or not trans_path.exists():
                        continue
                    res = validate_file_pair(orig_path, trans_path)
                    validated[trans_path.name] = res
                except Exception as ve:
                    log.warning("Detail validation failed for %s -> %s: %s", orig, trans, ve)

            if validated:
                # Generate markdown report
                generator = ValidationReportGenerator()
                report = generator.generate_markdown(list(validated.values()))

                # Write to .system directory
                system_dir = review_dir / ".system"
                system_dir.mkdir(parents=True, exist_ok=True)
                report_path = system_dir / "detail_validation_report.md"
                report_path.write_text(report, encoding="utf-8")

                # Generate summary for result
                validation_summary = generator.generate_summary(list(validated.values()))
                result["detail_validation"] = validation_summary

                # Record event
                record_event(
                    conn,
                    job_id=job_id,
                    milestone="detail_validation_done",
                    payload={
                        "files_validated": len(validated),
                        "score": validation_summary.get("score", 0.0),
                        "issues_found": validation_summary.get("failed", 0),
                        "warnings": validation_summary.get("warnings", 0),
                        "report_path": str(report_path.resolve()),
                    },
                )

                log.info(
                    "Detail validation: %d files, score=%.2f, %d critical, %d warnings",
                    validation_summary.get("total_files", 0),
                    validation_summary.get("score", 0.0),
                    validation_summary.get("failed", 0),
                    validation_summary.get("warnings", 0),
                )
        except Exception as e:
            # Don't fail the job if detail validation has issues
            log.warning("Detail validation failed for job %s: %s", job_id, e)
            record_event(
                conn,
                job_id=job_id,
                milestone="detail_validation_failed",
                payload={"error": str(e)},
            )

    # Merge PDF artifacts into final result
    final_artifacts = dict(result.get("artifacts", {}))
    if pdf_translated:
        final_artifacts["pdf_translated"] = [str(p) for p in pdf_translated]
    if pdf_extracted:
        final_artifacts["pdf_extracted"] = [str(p) for p in pdf_extracted]
    result["artifacts"] = final_artifacts

    # Add PDF warnings/errors to status flags
    final_status_flags = list(result.get("status_flags", [])) + pdf_warnings

    if source == "telegram" and result.get("status") in {"review_ready", "needs_attention"}:
        delivery_files = _collect_delivery_files(artifacts=final_artifacts)
        final_artifacts["delivery_files"] = delivery_files
        delivery_target = notify_target or DEFAULT_NOTIFY_TARGET
        if delivery_files:
            delivery_summary = _send_delivery_files(
                paths=paths,
                conn=conn,
                job_id=job_id,
                task_name=_task_name,
                target=delivery_target,
                dry_run=dry_run_notify,
                delivery_files=delivery_files,
            )
            final_artifacts["delivery_send"] = delivery_summary

    update_job_result(
        conn,
        job_id=job_id,
        status=result.get("status", "failed"),
        iteration_count=int(result.get("iteration_count", 0)),
        double_pass=bool(result.get("double_pass")),
        status_flags=final_status_flags,
        artifacts=final_artifacts,
        errors=list(result.get("errors", [])) + pdf_errors,
    )

    gateway_error_tokens = {
        "gateway_unavailable",
        "gateway_login_required",
        "gateway_timeout",
        "gateway_bad_payload",
    }
    gateway_error = ""
    for token in [str(x) for x in (result.get("errors") or [])] + [str(x) for x in (result.get("status_flags") or [])]:
        t = token.strip()
        if t in gateway_error_tokens:
            gateway_error = t
            break
        for prefix in gateway_error_tokens:
            if t.startswith(prefix):
                gateway_error = prefix
                break
        if gateway_error:
            break
    if gateway_error:
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="gateway_failed",
            message=(
                f"🚨 Gateway failed ({gateway_error})\n"
                f"📋 {_task_name}\n"
                "Fix: gateway-status · gateway-login · rerun"
            ),
            target=notify_target,
            dry_run=dry_run_notify,
        )

    format_contract_failed = any(
        "format_contract_failed" in str(token or "")
        for token in [str(x) for x in (result.get("errors") or [])] + [str(x) for x in (result.get("status_flags") or [])]
    )
    if format_contract_failed:
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="format_contract_failed",
            message=(
                "🚨 Output format contract failed\n"
                f"📋 {_task_name}\n"
                "Fix: gateway-status · gateway-login · rerun"
            ),
            target=notify_target,
            dry_run=dry_run_notify,
        )

    if result.get("status") == "queued":
        retry_after = 300
        for item in (result.get("errors") or []):
            token = str(item or "").strip()
            if token.startswith("queue_defer_cooldown:"):
                try:
                    retry_after = max(30, int(token.split(":", 1)[1].strip()))
                except Exception:
                    retry_after = 300
                break
        retry_min = max(1, int(round(retry_after / 60)))
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="cooldown_wait",
            message=(
                f"⏸️ Provider cooldown\n"
                f"📋 {_task_name}\n"
                f"Will retry automatically in ~{retry_min} min\n"
                f"Send: status"
            ),
            target=notify_target,
            dry_run=dry_run_notify,
        )
    elif result.get("status") == "review_ready":
        rounds = (((result.get("quality_report") or {}).get("rounds")) or [])
        for rd in rounds:
            rd_no = rd.get("round")
            if not rd_no:
                continue
            try:
                rd_no_int = int(rd_no)
            except (TypeError, ValueError):
                rd_no_int = 0
            if rd_no_int in sent_rounds:
                continue
            notify_milestone(
                paths=paths,
                conn=conn,
                job_id=job_id,
                milestone=f"round_{rd_no}_done",
                message=_format_round_message(rd),
                target=notify_target,
                dry_run=dry_run_notify,
            )
        models_line = f"{_format_models_summary(rounds[-1])}\n" if rounds else ""
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="review_ready",
            message=(
                f"\u2705 Translation complete\n"
                f"\U0001f4cb {_task_name}\n"
                f"\U0001f4c1 {result.get('review_dir')}\n\n"
                + models_line
                + f"Send: ok \u00b7 no {{reason}} \u00b7 rerun"
            ),
            target=notify_target,
            dry_run=dry_run_notify,
        )
        task_type = (result.get("intent") or {}).get("task_type", "unknown")
        _store_job_memory(
            conn=conn,
            job_id=job_id,
            task_type=str(task_type),
            rounds_count=int(result.get("iteration_count", 0)),
            review_dir=review_dir,
            kb_company=kb_company,
            task_label=str(job.get("task_label") or ""),
            kb_hits=kb_hits,
        )
    elif result.get("status") == "needs_attention":
        rounds = (((result.get("quality_report") or {}).get("rounds")) or [])
        for rd in rounds:
            rd_no = rd.get("round")
            if not rd_no:
                continue
            try:
                rd_no_int = int(rd_no)
            except (TypeError, ValueError):
                rd_no_int = 0
            if rd_no_int in sent_rounds:
                continue
            notify_milestone(
                paths=paths,
                conn=conn,
                job_id=job_id,
                milestone=f"round_{rd_no}_done",
                message=_format_round_message(rd),
                target=notify_target,
                dry_run=dry_run_notify,
            )
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
            why_block = "\n\nWhy:\n" + "\n".join(f"- {x}" for x in why_lines[:3])
        folder = str(result.get("review_dir") or "").strip()
        folder_line = f"\U0001f4c1 {folder}\n" if folder else ""
        models_line = f"{_format_models_summary(rounds[-1])}\n" if rounds else ""
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="needs_attention",
            message=(
                f"\u26a0\ufe0f Needs attention\n"
                f"\U0001f4cb {_task_name}\n"
                + folder_line
                + models_line
                + why_block
                + "\n\nSend: status \u00b7 rerun \u00b7 no {reason}"
            ),
            target=notify_target,
            dry_run=dry_run_notify,
        )
    elif result.get("status") == "failed":
        rounds = (((result.get("quality_report") or {}).get("rounds")) or [])
        for rd in rounds:
            rd_no = rd.get("round")
            if not rd_no:
                continue
            try:
                rd_no_int = int(rd_no)
            except (TypeError, ValueError):
                rd_no_int = 0
            if rd_no_int in sent_rounds:
                continue
            notify_milestone(
                paths=paths,
                conn=conn,
                job_id=job_id,
                milestone=f"round_{rd_no}_done",
                message=_format_round_message(rd),
                target=notify_target,
                dry_run=dry_run_notify,
            )
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
            why_block = "\n\nWhy:\n" + "\n".join(f"- {x}" for x in why_lines[:3])
        folder = str(result.get("review_dir") or "").strip()
        folder_line = f"\U0001f4c1 {folder}\n" if folder else ""
        models_line = f"{_format_models_summary(rounds[-1])}\n" if rounds else ""
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="failed",
            message=(
                f"\u274c Failed\n"
                f"\U0001f4cb {_task_name}\n"
                + folder_line
                + models_line
                + why_block
                + "\n\nSend: status \u00b7 rerun \u00b7 no {reason}"
            ),
            target=notify_target,
            dry_run=dry_run_notify,
        )
    else:
        notify_milestone(
            paths=paths,
            conn=conn,
            job_id=job_id,
            milestone="failed",
            message=f"\u274c Failed\n\U0001f4cb {_task_name}\nSend: rerun to retry",
            target=notify_target,
            dry_run=dry_run_notify,
        )

    conn.close()
    return result

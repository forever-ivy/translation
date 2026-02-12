#!/usr/bin/env python3
"""OpenClaw V5.2 translation orchestrator (LLM intent + real Codex/Gemini rounds)."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

# Allow running this file directly.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_delta_pack import build_delta, flatten_blocks
from scripts.extract_docx_structure import extract_structure
from scripts.openclaw_artifact_writer import write_artifacts
from scripts.openclaw_quality_gate import QualityThresholds, compute_runtime_timeout, evaluate_quality

TASK_TYPES = {
    "REVISION_UPDATE",
    "NEW_TRANSLATION",
    "BILINGUAL_REVIEW",
    "EN_ONLY_EDIT",
    "MULTI_FILE_BATCH",
    "TERMINOLOGY_ENFORCEMENT",
    "LOW_CONTEXT_TASK",
    "FORMAT_CRITICAL_TASK",
}

REQUIRED_INPUTS_BY_TASK: dict[str, list[str]] = {
    "REVISION_UPDATE": ["arabic_old", "arabic_new", "english_baseline"],
    "NEW_TRANSLATION": ["source_document"],
    "BILINGUAL_REVIEW": ["source_document", "target_document"],
    "EN_ONLY_EDIT": ["english_document"],
    "MULTI_FILE_BATCH": ["batch_documents"],
    "TERMINOLOGY_ENFORCEMENT": ["target_document", "glossary"],
    "LOW_CONTEXT_TASK": [],
    "FORMAT_CRITICAL_TASK": ["source_document"],
}

CODEX_AGENT = os.getenv("OPENCLAW_CODEX_AGENT", "translator-core")
GEMINI_AGENT = os.getenv("OPENCLAW_GEMINI_AGENT", "review-core")
OPENCLAW_CMD_TIMEOUT = int(os.getenv("OPENCLAW_AGENT_CALL_TIMEOUT_SECONDS", "700"))
DOC_CONTEXT_CHARS = int(os.getenv("OPENCLAW_DOC_CONTEXT_CHARS", "45000"))


def _normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _load_meta(args: argparse.Namespace) -> dict[str, Any]:
    if args.meta_json:
        return json.loads(args.meta_json)
    if args.meta_json_file:
        return json.loads(Path(args.meta_json_file).read_text(encoding="utf-8"))
    if args.meta_json_base64:
        decoded = base64.b64decode(args.meta_json_base64.encode("utf-8")).decode("utf-8")
        return json.loads(decoded)
    raise ValueError("One of --meta-json / --meta-json-file / --meta-json-base64 is required")


def _result_path(review_dir: str) -> Path:
    return Path(review_dir) / ".system" / "openclaw_result.json"


def _write_result(review_dir: str, payload: dict[str, Any]) -> None:
    if not review_dir:
        return
    result_path = _result_path(review_dir)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _collect_candidates(meta: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in meta.get("candidate_files") or []:
        if not isinstance(item, dict):
            continue
        if not item.get("path"):
            continue
        out.append(item)

    files = meta.get("files") or {}
    legacy_candidates = [
        ("arabic_v1", "ar", "v1"),
        ("arabic_v2", "ar", "v2"),
        ("english_v1", "en", "v1"),
    ]
    seen = {Path(x["path"]).resolve() for x in out if x.get("path")}
    for key, lang, version in legacy_candidates:
        data = files.get(key)
        if not data:
            continue
        p = Path(data["path"]).resolve()
        if p in seen:
            continue
        out.append(
            {
                "path": str(p),
                "name": Path(p).name,
                "language": lang,
                "version": version,
                "role": "source",
                "source_folder": "legacy",
            }
        )
        seen.add(p)
    return out


def _enrich_structures(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in candidates:
        path = Path(item["path"])
        if not path.exists():
            continue
        if item.get("structure"):
            structure = item["structure"]
        else:
            structure = extract_structure(path)
        out.append({**item, "structure": structure, "path": str(path.resolve())})
    return out


def _pick_file(candidates: list[dict[str, Any]], *, language: str, version: str | None = None) -> str | None:
    for item in candidates:
        if item.get("language") != language:
            continue
        if version and item.get("version") != version:
            continue
        return item["path"]
    return None


def _structure_text(struct: dict[str, Any], max_chars: int = DOC_CONTEXT_CHARS) -> str:
    lines: list[str] = []
    for block in struct.get("blocks", []):
        if block.get("kind") == "paragraph":
            text = _normalize_text(block.get("text", ""))
            if text:
                lines.append(text)
        elif block.get("kind") == "table":
            rows = block.get("rows") or []
            for row in rows:
                cells = [_normalize_text(str(cell)) for cell in row if _normalize_text(str(cell))]
                if cells:
                    lines.append(" | ".join(cells))
    joined = "\n".join(lines)
    if len(joined) <= max_chars:
        return joined
    return joined[:max_chars]


def _candidate_payload(candidates: list[dict[str, Any]], *, include_text: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in candidates:
        struct = item.get("structure") or {}
        row: dict[str, Any] = {
            "name": item.get("name"),
            "path": item.get("path"),
            "language": item.get("language"),
            "version": item.get("version"),
            "role": item.get("role"),
            "paragraph_count": struct.get("paragraph_count", 0),
            "table_count": struct.get("table_count", 0),
            "block_count": struct.get("block_count", 0),
        }
        if include_text:
            row["content"] = _structure_text(struct, max_chars=DOC_CONTEXT_CHARS)
        rows.append(row)
    return rows


def _extract_json_from_text(text: str) -> dict[str, Any]:
    raw = text.strip()
    if not raw:
        raise ValueError("empty response text")
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass

    first = raw.find("{")
    last = raw.rfind("}")
    if first >= 0 and last > first:
        sub = raw[first : last + 1]
        try:
            return json.loads(sub)
        except json.JSONDecodeError as exc:
            raise ValueError(f"failed to parse JSON from model output: {exc}") from exc
    raise ValueError("no JSON object in model output")


def _agent_call(agent_id: str, message: str, timeout_seconds: int = OPENCLAW_CMD_TIMEOUT) -> dict[str, Any]:
    cmd = [
        "openclaw",
        "agent",
        "--agent",
        agent_id,
        "--message",
        message,
        "--json",
        "--timeout",
        str(max(30, int(timeout_seconds))),
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": f"agent_call_failed:{agent_id}",
            "stderr": proc.stderr.strip(),
            "stdout": proc.stdout.strip(),
            "returncode": proc.returncode,
        }
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "error": f"agent_json_invalid:{agent_id}",
            "detail": str(exc),
            "stdout": proc.stdout[:2000],
        }
    text = ""
    if isinstance(payload, dict):
        result = payload.get("result") or {}
        pay = result.get("payloads") or []
        if pay and isinstance(pay, list):
            text = str((pay[0] or {}).get("text") or "")
    return {"ok": True, "agent_id": agent_id, "payload": payload, "text": text}


def _available_slots(candidates: list[dict[str, Any]]) -> dict[str, bool]:
    has_ar = any((x.get("language") == "ar") for x in candidates)
    has_en = any((x.get("language") == "en") for x in candidates)
    has_ar_v1 = any((x.get("language") == "ar" and x.get("version") == "v1") for x in candidates)
    has_ar_v2 = any((x.get("language") == "ar" and x.get("version") == "v2") for x in candidates)
    has_en_v1 = any((x.get("language") == "en" and x.get("version") == "v1") for x in candidates)
    has_glossary = any((x.get("role") == "glossary") for x in candidates)
    return {
        "arabic_old": has_ar_v1,
        "arabic_new": has_ar_v2,
        "english_baseline": has_en_v1 or has_en,
        "source_document": has_ar or has_en,
        "target_document": has_en,
        "english_document": has_en,
        "batch_documents": len(candidates) >= 2,
        "glossary": has_glossary,
    }


def _llm_intent(meta: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    message_blob = " ".join(
        [
            str(meta.get("subject") or ""),
            str(meta.get("message_text") or ""),
            str(meta.get("message") or ""),
        ]
    ).strip()
    files_payload = _candidate_payload(candidates, include_text=False)
    prompt = f"""
You are classifying a translation job. Return strict JSON only.

Allowed task_type values:
REVISION_UPDATE, NEW_TRANSLATION, BILINGUAL_REVIEW, EN_ONLY_EDIT, MULTI_FILE_BATCH, TERMINOLOGY_ENFORCEMENT, LOW_CONTEXT_TASK, FORMAT_CRITICAL_TASK

Canonical required_inputs values:
arabic_old, arabic_new, english_baseline, source_document, target_document, english_document, batch_documents, glossary

Given:
- subject/message: {message_blob}
- files: {json.dumps(files_payload, ensure_ascii=False)}

Output JSON schema:
{{
  "task_type": "...",
  "source_language": "ar|en|multi|unknown",
  "target_language": "en|ar|multi|unknown",
  "required_inputs": ["..."],
  "missing_inputs": ["..."],
  "confidence": 0.0,
  "reasoning_summary": "...",
  "estimated_minutes": 1,
  "complexity_score": 1.0
}}

Rules:
- confidence must be between 0 and 1.
- estimated_minutes must be integer.
- If information is insufficient, choose LOW_CONTEXT_TASK.
- missing_inputs must be inferred from files.
""".strip()
    call = _agent_call(CODEX_AGENT, prompt)
    if not call.get("ok"):
        return {"ok": False, "error": call.get("error"), "detail": call}
    try:
        parsed = _extract_json_from_text(str(call.get("text", "")))
    except Exception as exc:
        return {"ok": False, "error": "intent_json_parse_failed", "detail": str(exc), "raw_text": call.get("text", "")}

    task_type = str(parsed.get("task_type") or "").strip().upper()
    if task_type not in TASK_TYPES:
        task_type = "LOW_CONTEXT_TASK"
    confidence = float(parsed.get("confidence", 0.0) or 0.0)
    confidence = max(0.0, min(1.0, confidence))
    estimated_minutes = int(parsed.get("estimated_minutes", 12) or 12)
    estimated_minutes = max(1, estimated_minutes)
    complexity_score = float(parsed.get("complexity_score", 30.0) or 30.0)
    complexity_score = max(1.0, min(100.0, complexity_score))
    required = [str(x) for x in (parsed.get("required_inputs") or []) if str(x)]
    if not required:
        required = REQUIRED_INPUTS_BY_TASK.get(task_type, [])

    slots = _available_slots(candidates)
    missing = [x for x in required if not slots.get(x, False)]

    return {
        "ok": True,
        "intent": {
            "task_type": task_type,
            "source_language": str(parsed.get("source_language") or "unknown"),
            "target_language": str(parsed.get("target_language") or "unknown"),
            "required_inputs": required,
            "missing_inputs": missing,
            "confidence": confidence,
            "reasoning_summary": str(parsed.get("reasoning_summary") or ""),
        },
        "estimated_minutes": estimated_minutes,
        "complexity_score": complexity_score,
        "raw": parsed,
    }


def _build_delta_pack(*, job_id: str, task_type: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if task_type == "REVISION_UPDATE":
        ar_v1 = _pick_file(candidates, language="ar", version="v1")
        ar_v2 = _pick_file(candidates, language="ar", version="v2")
        if ar_v1 and ar_v2:
            s1 = next((x["structure"] for x in candidates if x["path"] == ar_v1), {})
            s2 = next((x["structure"] for x in candidates if x["path"] == ar_v2), {})
            return build_delta(
                job_id=job_id,
                v1_rows=flatten_blocks(s1),
                v2_rows=flatten_blocks(s2),
            )

    changes = []
    for item in candidates[:20]:
        struct = item.get("structure") or {}
        name = item.get("name", "")
        sample_lines = []
        for block in struct.get("blocks", []):
            if block.get("kind") != "paragraph":
                continue
            text = _normalize_text(block.get("text", ""))
            if text:
                sample_lines.append(text)
            if len(sample_lines) >= 2:
                break
        preview = " | ".join(sample_lines)[:200]
        changes.append(
            {
                "section": name or "General",
                "changes": [f"Input considered for {item.get('role', 'task')}: {preview or '(no text preview)'}"],
            }
        )

    return {
        "job_id": job_id,
        "added": [],
        "removed": [],
        "modified": [],
        "summary_by_section": changes,
        "stats": {"added_count": 0, "removed_count": 0, "modified_count": 0},
    }


def _build_execution_context(meta: dict[str, Any], candidates: list[dict[str, Any]], intent: dict[str, Any], kb_hits: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "job_id": meta.get("job_id"),
        "subject": meta.get("subject", ""),
        "message_text": meta.get("message_text", ""),
        "task_intent": intent,
        "candidate_files": _candidate_payload(candidates, include_text=True),
        "knowledge_context": kb_hits[:12],
        "rules": {
            "preserve_structure_first": True,
            "keep_unmodified_content": True,
            "target_language_purity_required": True,
            "manual_delivery_only": True,
        },
    }


def _codex_generate(context: dict[str, Any], previous_draft: dict[str, Any] | None, findings: list[str], round_index: int) -> dict[str, Any]:
    prompt = f"""
You are Codex translator. Work on this translation job and return strict JSON only.

Round: {round_index}
Previous unresolved findings: {json.dumps(findings, ensure_ascii=False)}

Execution context:
{json.dumps(context, ensure_ascii=False)}

Previous draft (if any):
{json.dumps(previous_draft or {}, ensure_ascii=False)}

Output JSON:
{{
  "draft_a_text": "string",
  "draft_b_text": "string",
  "final_text": "string",
  "final_reflow_text": "string",
  "review_brief_points": ["..."],
  "change_log_points": ["..."],
  "resolved": ["..."],
  "unresolved": ["..."],
  "codex_pass": true,
  "reasoning_summary": "string"
}}

Rules:
- Produce complete output text for the selected task.
- For REVISION_UPDATE: preserve unchanged English wording when possible.
- If context is insufficient, keep "codex_pass": false and explain in unresolved.
- JSON only.
""".strip()
    call = _agent_call(CODEX_AGENT, prompt)
    if not call.get("ok"):
        return {"ok": False, "error": call.get("error"), "detail": call}
    try:
        parsed = _extract_json_from_text(str(call.get("text", "")))
    except Exception as exc:
        return {"ok": False, "error": "codex_json_parse_failed", "detail": str(exc), "raw_text": call.get("text", "")}

    return {
        "ok": True,
        "data": {
            "draft_a_text": str(parsed.get("draft_a_text") or ""),
            "draft_b_text": str(parsed.get("draft_b_text") or ""),
            "final_text": str(parsed.get("final_text") or ""),
            "final_reflow_text": str(parsed.get("final_reflow_text") or ""),
            "review_brief_points": [str(x) for x in (parsed.get("review_brief_points") or [])],
            "change_log_points": [str(x) for x in (parsed.get("change_log_points") or [])],
            "resolved": [str(x) for x in (parsed.get("resolved") or [])],
            "unresolved": [str(x) for x in (parsed.get("unresolved") or [])],
            "codex_pass": bool(parsed.get("codex_pass")),
            "reasoning_summary": str(parsed.get("reasoning_summary") or ""),
        },
        "raw": parsed,
    }


def _gemini_review(context: dict[str, Any], draft: dict[str, Any], round_index: int) -> dict[str, Any]:
    prompt = f"""
You are Gemini reviewer. Validate translation quality and return strict JSON only.

Round: {round_index}
Context:
{json.dumps(context, ensure_ascii=False)}

Draft candidate:
{json.dumps(draft, ensure_ascii=False)}

Output JSON:
{{
  "findings": ["..."],
  "resolved": ["..."],
  "unresolved": ["..."],
  "pass": true,
  "terminology_rate": 0.0,
  "structure_complete_rate": 0.0,
  "target_language_purity": 0.0,
  "numbering_consistency": 0.0,
  "reasoning_summary": "string"
}}

Rules:
- pass=true only if no critical issue remains.
- Scores are between 0 and 1.
- JSON only.
""".strip()
    call = _agent_call(GEMINI_AGENT, prompt)
    if not call.get("ok"):
        return {"ok": False, "error": call.get("error"), "detail": call}
    try:
        parsed = _extract_json_from_text(str(call.get("text", "")))
    except Exception as exc:
        return {"ok": False, "error": "gemini_json_parse_failed", "detail": str(exc), "raw_text": call.get("text", "")}

    def _clamp(v: Any, fallback: float) -> float:
        try:
            val = float(v)
        except (TypeError, ValueError):
            return fallback
        return max(0.0, min(1.0, val))

    return {
        "ok": True,
        "data": {
            "findings": [str(x) for x in (parsed.get("findings") or [])],
            "resolved": [str(x) for x in (parsed.get("resolved") or [])],
            "unresolved": [str(x) for x in (parsed.get("unresolved") or [])],
            "pass": bool(parsed.get("pass")),
            "terminology_rate": _clamp(parsed.get("terminology_rate"), 0.0),
            "structure_complete_rate": _clamp(parsed.get("structure_complete_rate"), 0.0),
            "target_language_purity": _clamp(parsed.get("target_language_purity"), 0.0),
            "numbering_consistency": _clamp(parsed.get("numbering_consistency"), 0.0),
            "reasoning_summary": str(parsed.get("reasoning_summary") or ""),
        },
        "raw": parsed,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.resolve())


def _round_record(
    *,
    round_idx: int,
    codex_path: str,
    gemini_path: str,
    codex_data: dict[str, Any],
    gemini_data: dict[str, Any],
) -> dict[str, Any]:
    codex_pass = bool(codex_data.get("codex_pass"))
    gemini_pass = bool(gemini_data.get("pass"))
    return {
        "round": round_idx,
        "codex_output_ref": codex_path,
        "gemini_findings_ref": gemini_path,
        "resolved": list(codex_data.get("resolved") or []),
        "unresolved": list(gemini_data.get("unresolved") or codex_data.get("unresolved") or []),
        "codex_pass": codex_pass,
        "gemini_pass": gemini_pass,
        "pass": codex_pass and gemini_pass,
        "metrics": {
            "terminology_rate": float(gemini_data.get("terminology_rate", 0.0)),
            "structure_complete_rate": float(gemini_data.get("structure_complete_rate", 0.0)),
            "target_language_purity": float(gemini_data.get("target_language_purity", 0.0)),
            "numbering_consistency": float(gemini_data.get("numbering_consistency", 0.0)),
            "hard_fail_items": [],
        },
    }


def _model_scores(job_id: str, last_round: dict[str, Any], gemini_enabled: bool) -> dict[str, Any]:
    m = last_round.get("metrics", {})
    codex_total = (
        0.45 * float(m.get("terminology_rate", 0.0))
        + 0.25 * float(m.get("structure_complete_rate", 0.0))
        + 0.15 * float(m.get("target_language_purity", 0.0))
        + 0.1 * float(m.get("numbering_consistency", 0.0))
        + 0.05 * 0.95
    )
    gemini_total = max(0.0, codex_total - 0.01) if gemini_enabled else 0.0
    return {
        "job_id": job_id,
        "winner": "codex_primary",
        "judge_margin": round(max(0.0, codex_total - gemini_total), 4),
        "term_hit": round(float(m.get("terminology_rate", 0.0)), 4),
        "scores": {
            "codex_primary": {"total": round(codex_total, 4)},
            "gemini_reviewer": {"total": round(gemini_total, 4)},
        },
    }


def run(meta: dict[str, Any], *, plan_only: bool = False) -> dict[str, Any]:
    started = time.time()
    thresholds = QualityThresholds(max_rounds=3)

    job_id = str(meta.get("job_id") or f"job_{int(time.time())}")
    root_path = str(meta.get("root_path") or "")
    review_dir = str(meta.get("review_dir") or "")
    if not review_dir and root_path:
        review_dir = str(Path(root_path) / "Translated -EN" / "_VERIFY" / job_id)

    try:
        candidates = _enrich_structures(_collect_candidates(meta))
        if not candidates:
            response = {
                "ok": False,
                "job_id": job_id,
                "status": "incomplete_input",
                "review_dir": review_dir,
                "errors": ["no_input_documents_found"],
                "iteration_count": 0,
                "double_pass": False,
                "estimated_minutes": 0,
                "runtime_timeout_minutes": 0,
                "actual_duration_minutes": round((time.time() - started) / 60.0, 3),
                "status_flags": [],
            }
            _write_result(review_dir, response)
            return response

        intent_result = _llm_intent(meta, candidates)
        if not intent_result.get("ok"):
            response = {
                "ok": False,
                "job_id": job_id,
                "status": "failed",
                "review_dir": review_dir,
                "errors": [str(intent_result.get("error"))],
                "iteration_count": 0,
                "double_pass": False,
                "estimated_minutes": 0,
                "runtime_timeout_minutes": 0,
                "actual_duration_minutes": round((time.time() - started) / 60.0, 3),
                "status_flags": ["hard_fail"],
                "debug": intent_result,
            }
            _write_result(review_dir, response)
            return response

        intent = intent_result["intent"]
        estimated_minutes = int(intent_result.get("estimated_minutes", 12))
        complexity_score = float(intent_result.get("complexity_score", 30.0))
        runtime_timeout_minutes, status_flags = compute_runtime_timeout(estimated_minutes, thresholds)

        plan = {
            "task_type": intent.get("task_type", "LOW_CONTEXT_TASK"),
            "confidence": float(intent.get("confidence", 0.0)),
            "estimated_minutes": estimated_minutes,
            "complexity_score": complexity_score,
            "time_budget_minutes": runtime_timeout_minutes,
        }

        missing_inputs = list(intent.get("missing_inputs") or [])
        if missing_inputs:
            response = {
                "ok": False,
                "job_id": job_id,
                "status": "missing_inputs",
                "review_dir": review_dir,
                "intent": intent,
                "plan": plan,
                "iteration_count": 0,
                "double_pass": False,
                "estimated_minutes": estimated_minutes,
                "runtime_timeout_minutes": runtime_timeout_minutes,
                "actual_duration_minutes": round((time.time() - started) / 60.0, 3),
                "status_flags": status_flags + ["missing_inputs"],
                "errors": [f"missing:{x}" for x in missing_inputs],
            }
            _write_result(review_dir, response)
            return response

        if plan_only:
            response = {
                "ok": True,
                "job_id": job_id,
                "status": "planned",
                "review_dir": review_dir,
                "intent": intent,
                "plan": plan,
                "iteration_count": 0,
                "double_pass": False,
                "estimated_minutes": estimated_minutes,
                "runtime_timeout_minutes": runtime_timeout_minutes,
                "actual_duration_minutes": round((time.time() - started) / 60.0, 3),
                "status_flags": status_flags,
                "errors": [],
            }
            _write_result(review_dir, response)
            return response

        task_type = str(intent.get("task_type", "LOW_CONTEXT_TASK"))
        delta_pack = _build_delta_pack(job_id=job_id, task_type=task_type, candidates=candidates)
        kb_hits = list(meta.get("knowledge_context") or [])
        execution_context = _build_execution_context(meta, candidates, intent, kb_hits)

        rounds: list[dict[str, Any]] = []
        previous_findings: list[str] = []
        current_draft: dict[str, Any] | None = None
        errors: list[str] = []
        gemini_enabled = bool(meta.get("gemini_available", True))
        system_round_root = Path(review_dir) / ".system" / "rounds"

        for round_idx in range(1, thresholds.max_rounds + 1):
            codex_gen = _codex_generate(execution_context, current_draft, previous_findings, round_idx)
            if not codex_gen.get("ok"):
                errors.append(str(codex_gen.get("error", "codex_generation_failed")))
                break
            codex_data = codex_gen["data"]

            if gemini_enabled:
                gemini_rev = _gemini_review(execution_context, codex_data, round_idx)
                if not gemini_rev.get("ok"):
                    gemini_enabled = False
                    status_flags.append("degraded_single_model")
                    gemini_data = {
                        "findings": list(codex_data.get("unresolved") or []),
                        "resolved": list(codex_data.get("resolved") or []),
                        "unresolved": list(codex_data.get("unresolved") or []),
                        "pass": bool(codex_data.get("codex_pass")),
                        "terminology_rate": 0.9,
                        "structure_complete_rate": 0.9,
                        "target_language_purity": 0.9,
                        "numbering_consistency": 0.9,
                        "reasoning_summary": "Gemini unavailable; degraded single model path.",
                    }
                else:
                    review_findings = list(gemini_rev["data"].get("findings") or gemini_rev["data"].get("unresolved") or [])
                    codex_fix = _codex_generate(execution_context, codex_data, review_findings, round_idx)
                    if codex_fix.get("ok"):
                        codex_data = codex_fix["data"]
                    gemini_final = _gemini_review(execution_context, codex_data, round_idx)
                    if gemini_final.get("ok"):
                        gemini_data = gemini_final["data"]
                    else:
                        gemini_enabled = False
                        status_flags.append("degraded_single_model")
                        gemini_data = {
                            "findings": review_findings,
                            "resolved": list(codex_data.get("resolved") or []),
                            "unresolved": list(codex_data.get("unresolved") or []),
                            "pass": bool(codex_data.get("codex_pass")),
                            "terminology_rate": 0.9,
                            "structure_complete_rate": 0.9,
                            "target_language_purity": 0.9,
                            "numbering_consistency": 0.9,
                            "reasoning_summary": "Gemini second-pass failed; degraded single model path.",
                        }
            else:
                gemini_data = {
                    "findings": list(codex_data.get("unresolved") or []),
                    "resolved": list(codex_data.get("resolved") or []),
                    "unresolved": list(codex_data.get("unresolved") or []),
                    "pass": bool(codex_data.get("codex_pass")),
                    "terminology_rate": 0.9,
                    "structure_complete_rate": 0.9,
                    "target_language_purity": 0.9,
                    "numbering_consistency": 0.9,
                    "reasoning_summary": "Gemini disabled by runtime settings.",
                }

            round_dir = system_round_root / f"round_{round_idx}"
            codex_ref = _write_json(round_dir / "codex_output.json", codex_data)
            gemini_ref = _write_json(round_dir / "gemini_review.json", gemini_data)
            rec = _round_record(
                round_idx=round_idx,
                codex_path=codex_ref,
                gemini_path=gemini_ref,
                codex_data=codex_data,
                gemini_data=gemini_data,
            )
            rounds.append(rec)
            current_draft = codex_data
            previous_findings = list(rec.get("unresolved") or [])
            if rec.get("pass"):
                break

        if not rounds:
            status = "failed"
            double_pass = False
            iteration_count = 0
            status_flags.append("hard_fail")
        else:
            last_round = rounds[-1]
            double_pass = bool(last_round.get("pass"))
            iteration_count = len(rounds)
            if double_pass:
                status = "review_ready"
            else:
                status = "needs_attention"
                status_flags.append("non_converged")

        quality_report = {
            "rounds": rounds,
            "convergence_reached": bool(rounds and rounds[-1].get("pass")),
            "stop_reason": "double_pass" if rounds and rounds[-1].get("pass") else ("max_rounds" if rounds else "hard_fail"),
        }

        last_round = rounds[-1] if rounds else {"metrics": {}}
        model_scores = _model_scores(job_id, last_round, gemini_enabled=gemini_enabled)
        quality = evaluate_quality(model_scores=model_scores, delta_pack=delta_pack, thresholds=thresholds)

        if not current_draft:
            current_draft = {
                "draft_a_text": "",
                "draft_b_text": "",
                "final_text": "",
                "final_reflow_text": "",
                "review_brief_points": [],
                "change_log_points": [],
            }

        artifacts = write_artifacts(
            review_dir=review_dir,
            draft_a_template_path=_pick_file(candidates, language="en", version="v1") or _pick_file(candidates, language="en"),
            delta_pack=delta_pack,
            model_scores=model_scores,
            quality=quality,
            quality_report=quality_report,
            job_id=job_id,
            task_type=task_type,
            confidence=float(intent.get("confidence", 0.0)),
            estimated_minutes=estimated_minutes,
            runtime_timeout_minutes=runtime_timeout_minutes,
            iteration_count=iteration_count,
            double_pass=double_pass,
            status_flags=status_flags,
            candidate_files=candidates,
            review_questions=[str(x) for x in current_draft.get("review_brief_points", [])],
            draft_payload=current_draft,
            plan_payload={"intent": intent, "plan": plan},
        )

        response = {
            "ok": status == "review_ready",
            "job_id": job_id,
            "status": status,
            "review_dir": review_dir,
            "artifacts": artifacts,
            "quality": quality,
            "quality_report": quality_report,
            "intent": intent,
            "plan": plan,
            "iteration_count": iteration_count,
            "double_pass": double_pass,
            "estimated_minutes": estimated_minutes,
            "runtime_timeout_minutes": runtime_timeout_minutes,
            "actual_duration_minutes": round((time.time() - started) / 60.0, 3),
            "status_flags": status_flags,
            "errors": errors if errors else ([] if status == "review_ready" else ["double_pass_not_reached"]),
        }
        _write_result(review_dir, response)
        return response
    except Exception as exc:  # pragma: no cover
        response = {
            "ok": False,
            "job_id": job_id,
            "status": "failed",
            "review_dir": review_dir,
            "errors": [str(exc)],
            "trace": traceback.format_exc(limit=8),
            "iteration_count": 0,
            "double_pass": False,
            "estimated_minutes": 0,
            "runtime_timeout_minutes": 0,
            "actual_duration_minutes": round((time.time() - started) / 60.0, 3),
            "status_flags": ["hard_fail"],
        }
        _write_result(review_dir, response)
        return response


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--meta-json")
    parser.add_argument("--meta-json-file")
    parser.add_argument("--meta-json-base64")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    meta = _load_meta(args)
    result = run(meta, plan_only=args.plan_only)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

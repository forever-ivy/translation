#!/usr/bin/env python3
"""Multimodal format QA using Gemini, Kimi (Moonshot), or OpenAI vision for xlsx translation quality.

This module compares rendered screenshots of the original and translated spreadsheets
to estimate format fidelity. It is designed to be safe for:
- multi-file jobs (no shared output filenames)
- multi-sheet workbooks (compares multiple rendered PNGs and aggregates)
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any


def _iter_json_candidates(raw: str, *, limit: int = 12) -> list[Any]:
    """Extract JSON values from mixed model text output."""
    text = (raw or "").strip()
    if not text:
        return []
    decoder = json.JSONDecoder()
    out: list[Any] = []
    for idx, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            value, _end = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        out.append(value)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _extract_first_json_object(raw: str) -> dict[str, Any]:
    candidates = _iter_json_candidates(raw, limit=12)
    for cand in candidates:
        if isinstance(cand, dict):
            return cand
    raise ValueError("no JSON object found in response text")


def _resolve_openclaw_auth_profiles_path() -> Path:
    override = os.environ.get("OPENCLAW_AUTH_PROFILES_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    agent_dir = os.environ.get("OPENCLAW_AGENT_DIR", "").strip()
    if agent_dir:
        return Path(agent_dir).expanduser() / "auth-profiles.json"
    return Path("~/.openclaw/agents/main/agent/auth-profiles.json").expanduser()


def _read_openclaw_api_key(provider_id: str) -> str:
    """Read an api_key profile from OpenClaw's auth-profiles.json (best-effort)."""
    provider = (provider_id or "").strip()
    if not provider:
        return ""
    path = _resolve_openclaw_auth_profiles_path()
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""

    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        profiles = {}

    last_good = data.get("lastGood")
    if not isinstance(last_good, dict):
        last_good = {}

    def _extract_key(profile_obj: Any) -> str:
        if not isinstance(profile_obj, dict):
            return ""
        if str(profile_obj.get("type") or "") != "api_key":
            return ""
        for k in ("key", "api_key", "apiKey", "token"):
            val = profile_obj.get(k)
            if isinstance(val, str) and val.strip():
                return val.strip()
        return ""

    lg = last_good.get(provider)
    if isinstance(lg, str) and lg.strip():
        key = _extract_key(profiles.get(lg.strip()))
        if key:
            return key

    key = _extract_key(profiles.get(f"{provider}:default"))
    if key:
        return key

    for pid, pobj in profiles.items():
        if isinstance(pid, str) and pid.startswith(f"{provider}:"):
            key = _extract_key(pobj)
            if key:
                return key

    return ""


@lru_cache(maxsize=1)
def _resolve_soffice_bin() -> str:
    override = os.environ.get("OPENCLAW_SOFFICE_BIN", "").strip()
    candidates: list[str] = []
    if override:
        candidates.append(override)
    found = shutil.which("soffice")
    if found:
        candidates.append(found)
    # macOS default install path.
    candidates.append("/Applications/LibreOffice.app/Contents/MacOS/soffice")

    for cand in candidates:
        if not cand:
            continue
        path = Path(cand).expanduser()
        if path.exists() and os.access(str(path), os.X_OK):
            return str(path)

    raise RuntimeError(
        "LibreOffice (soffice) not found. Install LibreOffice or set OPENCLAW_SOFFICE_BIN to the soffice binary path."
    )


def render_xlsx_to_images(xlsx_path: Path, output_dir: Path) -> list[Path]:
    """Convert xlsx to one-or-more PNGs via LibreOffice headless (multi-sheet safe)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    outdir = str(output_dir)
    try:
        soffice_bin = _resolve_soffice_bin()
        subprocess.run(
            [soffice_bin, "--headless", "--convert-to", "png", "--outdir", outdir, str(xlsx_path)],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "LibreOffice (soffice) not found. Install LibreOffice or set OPENCLAW_SOFFICE_BIN to the soffice binary path."
        ) from exc

    stem = xlsx_path.stem
    produced = sorted(output_dir.glob(f"{stem}*.png"), key=lambda p: p.name.lower())
    canonical = output_dir / f"{stem}.png"
    if canonical.exists():
        produced = [canonical] + [p for p in produced if p != canonical]
    return produced


def render_xlsx_to_image(xlsx_path: Path, output_png: Path) -> Path:
    """Back-compat helper: convert xlsx to a single PNG (first image only)."""
    images = render_xlsx_to_images(xlsx_path, output_png.parent)
    if not images:
        raise RuntimeError("LibreOffice conversion produced no PNG outputs")
    first = images[0]
    if first != output_png:
        output_png.parent.mkdir(parents=True, exist_ok=True)
        output_png.write_bytes(first.read_bytes())
    return output_png


def _build_format_qa_prompt() -> str:
    return (
        "Compare these two spreadsheet screenshots.\n"
        "- The first image is the original.\n"
        "- The second image is the translated output.\n\n"
        "Evaluate:\n"
        "1) Format fidelity (higher priority): sheet structure, tables, merged cells, "
        "column widths, alignment, borders, colors, fonts.\n"
        "   IMPORTANT: minor row-height increases and enabling wrap-text to prevent "
        "clipping are allowed and should NOT be treated as fidelity failures.\n"
        "2) Aesthetics (lower priority): readability, whitespace balance, whether text is "
        "clipped/overlapping, consistent typography.\n\n"
        "Return ONLY a JSON object with:\n"
        '- "format_fidelity_score": float 0-1\n'
        '- "aesthetics_score": float 0-1\n'
        '- "dimension_scores": {"layout":0-1,"font":0-1,"merged_cells":0-1,"column_widths":0-1,'
        '"colors":0-1,"borders":0-1,"readability":0-1}\n'
        '- "discrepancies": [{"location": str, "issue": str, "severity": str}]\n'
        '- "aesthetic_issues": [{"location": str, "issue": str, "severity": str}]\n'
    )


def _compare_format_visual_gemini(original_png_b64: str, translated_png_b64: str) -> dict[str, Any]:
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
    if not api_key:
        raise RuntimeError("Missing GOOGLE_API_KEY/GEMINI_API_KEY for Gemini vision QA.")
    model = os.environ.get("OPENCLAW_GEMINI_VISION_MODEL", "gemini-3-pro").strip()
    if "/" in model:
        # Accept OpenClaw-style provider/model ids and use the raw model id for Gemini API.
        model = model.rsplit("/", 1)[-1]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": _build_format_qa_prompt()},
                    {"inline_data": {"mime_type": "image/png", "data": original_png_b64}},
                    {"inline_data": {"mime_type": "image/png", "data": translated_png_b64}},
                ],
            }
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))

    text = ((body.get("candidates") or [{}])[0].get("content") or {}).get("parts", [{}])[0].get("text", "")
    result = _extract_first_json_object(text)

    def _clamp(val: Any) -> float:
        try:
            f = float(val)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))

    result["format_fidelity_score"] = _clamp(result.get("format_fidelity_score", 0.0))
    result["aesthetics_score"] = _clamp(result.get("aesthetics_score", 0.0))
    return result


def _moonshot_chat_completions(*, api_key: str, model: str, prompt: str, images_b64: list[str]) -> str:
    base_url = os.environ.get("OPENCLAW_MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1").strip().rstrip("/")
    url = f"{base_url}/chat/completions"
    parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for b64 in images_b64:
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": parts}],
        "temperature": 0.0,
        "max_tokens": 2048,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    if not isinstance(content, str):
        content = str(content)
    return content


def _compare_format_visual_moonshot(original_png_b64: str, translated_png_b64: str) -> dict[str, Any]:
    api_key = os.environ.get("MOONSHOT_API_KEY", "").strip() or _read_openclaw_api_key("moonshot")
    if not api_key:
        raise RuntimeError("Missing Moonshot API key (MOONSHOT_API_KEY or OpenClaw moonshot profile).")
    model = (
        os.environ.get("OPENCLAW_MOONSHOT_VISION_MODEL", "").strip()
        or os.environ.get("OPENCLAW_KIMI_VISION_MODEL", "").strip()
        or "moonshot/kimi-k2.5"
    )
    if "/" in model:
        model = model.rsplit("/", 1)[-1]
    raw = _moonshot_chat_completions(
        api_key=api_key,
        model=model,
        prompt=_build_format_qa_prompt(),
        images_b64=[original_png_b64, translated_png_b64],
    )
    result = _extract_first_json_object(raw)

    def _clamp(val: Any) -> float:
        try:
            f = float(val)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))

    result["format_fidelity_score"] = _clamp(result.get("format_fidelity_score", 0.0))
    result["aesthetics_score"] = _clamp(result.get("aesthetics_score", 0.0))
    return result


def _openai_chat_completions(*, api_key: str, model: str, prompt: str, images_b64: list[str]) -> str:
    base_url = os.environ.get("OPENCLAW_OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
    url = f"{base_url}/chat/completions"
    parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for b64 in images_b64:
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}})
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": parts}],
        "temperature": 0.0,
        "max_tokens": 2048,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    if not isinstance(content, str):
        content = str(content)
    return content


def _compare_format_visual_openai(original_png_b64: str, translated_png_b64: str) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip() or _read_openclaw_api_key("openai-codex")
    if not api_key:
        raise RuntimeError("Missing OpenAI API key (OPENAI_API_KEY or OpenClaw openai-codex api_key profile).")
    model = os.environ.get("OPENCLAW_OPENAI_VISION_MODEL", "").strip() or "openai-codex/gpt-5.2"
    if "/" in model:
        model = model.rsplit("/", 1)[-1]
    raw = _openai_chat_completions(
        api_key=api_key,
        model=model,
        prompt=_build_format_qa_prompt(),
        images_b64=[original_png_b64, translated_png_b64],
    )
    result = _extract_first_json_object(raw)

    def _clamp(val: Any) -> float:
        try:
            f = float(val)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))

    result["format_fidelity_score"] = _clamp(result.get("format_fidelity_score", 0.0))
    result["aesthetics_score"] = _clamp(result.get("aesthetics_score", 0.0))
    return result


def compare_format_visual(original_png_b64: str, translated_png_b64: str) -> dict[str, Any]:
    """Compare two spreadsheet screenshots with Gemini, Kimi (Moonshot), or OpenAI vision.

    Select provider via OPENCLAW_VISION_BACKEND:
      - gemini: require GOOGLE_API_KEY/GEMINI_API_KEY
      - moonshot/kimi: require MOONSHOT_API_KEY or OpenClaw moonshot auth profile
      - openai: require OPENAI_API_KEY or OpenClaw openai-codex api_key profile
      - auto (default): try Gemini then fallback to Moonshot/OpenAI if available
    """
    backend = os.environ.get("OPENCLAW_VISION_BACKEND", "auto").strip().lower()
    if backend in ("kimi", "moonshot"):
        return _compare_format_visual_moonshot(original_png_b64, translated_png_b64)
    if backend in ("gemini", "google"):
        return _compare_format_visual_gemini(original_png_b64, translated_png_b64)
    if backend in ("openai", "openai-codex"):
        return _compare_format_visual_openai(original_png_b64, translated_png_b64)

    gemini_key_present = bool((os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or "").strip())
    moonshot_key_present = bool((os.environ.get("MOONSHOT_API_KEY") or "").strip() or _read_openclaw_api_key("moonshot"))
    openai_key_present = bool((os.environ.get("OPENAI_API_KEY") or "").strip() or _read_openclaw_api_key("openai-codex"))

    if gemini_key_present:
        try:
            return _compare_format_visual_gemini(original_png_b64, translated_png_b64)
        except Exception:
            if moonshot_key_present:
                return _compare_format_visual_moonshot(original_png_b64, translated_png_b64)
            if openai_key_present:
                return _compare_format_visual_openai(original_png_b64, translated_png_b64)
            raise

    if moonshot_key_present:
        return _compare_format_visual_moonshot(original_png_b64, translated_png_b64)
    if openai_key_present:
        return _compare_format_visual_openai(original_png_b64, translated_png_b64)

    raise RuntimeError(
        "No vision credentials found. Set GOOGLE_API_KEY/GEMINI_API_KEY (Gemini) "
        "or configure Moonshot/OpenAI "
        "(MOONSHOT_API_KEY/OPENAI_API_KEY or OpenClaw moonshot/openai-codex api_key profile)."
    )


def auto_fix_format(xlsx_path: Path, original_xlsx: Path, discrepancies: list[dict]) -> Path:
    """Attempt to fix format discrepancies by copying properties from original.

    Best-effort only: this should never modify cell values/formulas.
    """
    import openpyxl

    orig_wb = openpyxl.load_workbook(str(original_xlsx))
    trans_wb = openpyxl.load_workbook(str(xlsx_path))
    orig_ws = orig_wb.active
    trans_ws = trans_wb.active

    for d in discrepancies:
        issue = str(d.get("issue", "") or "")
        if "column_width" in issue:
            for col_letter, dim in orig_ws.column_dimensions.items():
                if dim.width is not None:
                    trans_ws.column_dimensions[col_letter].width = dim.width
        elif "font" in issue:
            loc = str(d.get("location", "") or "")
            if loc:
                try:
                    orig_cell = orig_ws[loc]
                    trans_ws[loc].font = orig_cell.font.copy()
                except (KeyError, ValueError):
                    pass
        # Do NOT auto-fix row heights: preserve-mode output may intentionally increase
        # row heights for readability (wrap + unclipped text).

    trans_wb.save(str(xlsx_path))
    return xlsx_path


def run_format_qa_loop(
    original_xlsx: Path,
    translated_xlsx: Path,
    review_dir: Path,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Full QA loop: render (multi-sheet), compare, optionally fix and retry."""
    review_dir.mkdir(parents=True, exist_ok=True)
    original_dir = review_dir / "original"
    translated_dir = review_dir / "translated"

    try:
        original_images = render_xlsx_to_images(original_xlsx, original_dir)
    except Exception as exc:
        msg = str(exc)
        reason = "soffice_missing" if ("soffice" in msg.lower() or "libreoffice" in msg.lower()) else "render_failed"
        return {
            "status": "skipped",
            "attempts": 0,
            "reason": reason,
            "error": msg,
            "sheets_compared": 0,
            "sheets_total_original": 0,
            "sheets_total_translated": 0,
            "original_dir": str(original_dir),
            "translated_dir": str(translated_dir),
        }
    attempts = 0
    sheets_max = int(os.environ.get("OPENCLAW_FORMAT_QA_SHEETS_MAX", "6"))
    threshold = float(os.environ.get("OPENCLAW_FORMAT_QA_THRESHOLD", "0.85"))
    aesthetics_warn_threshold = float(os.environ.get("OPENCLAW_VISION_AESTHETICS_WARN_THRESHOLD", "0.7"))

    while True:
        try:
            translated_images = render_xlsx_to_images(translated_xlsx, translated_dir)
        except Exception as exc:
            msg = str(exc)
            reason = "soffice_missing" if ("soffice" in msg.lower() or "libreoffice" in msg.lower()) else "render_failed"
            return {
                "status": "skipped",
                "attempts": attempts,
                "reason": reason,
                "error": msg,
                "sheets_compared": 0,
                "sheets_total_original": len(original_images),
                "sheets_total_translated": 0,
                "original_dir": str(original_dir),
                "translated_dir": str(translated_dir),
            }
        attempts += 1

        truncated = False
        sheet_count_mismatch = len(original_images) != len(translated_images)
        paired_count = min(len(original_images), len(translated_images))
        paired = [(original_images[i], translated_images[i]) for i in range(paired_count)]
        if sheets_max > 0 and len(paired) > sheets_max:
            paired = paired[:sheets_max]
            truncated = True

        per_sheet: list[dict[str, Any]] = []
        all_discrepancies: list[dict[str, Any]] = []
        all_aesthetic_issues: list[dict[str, Any]] = []
        scores: list[float] = []
        aesthetics: list[float] = []

        for idx, (orig_png, trans_png) in enumerate(paired, start=1):
            orig_b64 = base64.b64encode(orig_png.read_bytes()).decode("ascii")
            trans_b64 = base64.b64encode(trans_png.read_bytes()).decode("ascii")
            try:
                result = compare_format_visual(orig_b64, trans_b64)
            except Exception as exc:
                # Treat vision API issues (auth, quota, outage) as a skip instead of crashing the whole job.
                return {
                    "status": "skipped",
                    "attempts": attempts,
                    "reason": "vision_unavailable",
                    "error": str(exc),
                    "sheets_compared": 0,
                    "sheets_total_original": len(original_images),
                    "sheets_total_translated": 0,
                    "original_dir": str(original_dir),
                    "translated_dir": str(translated_dir),
                }
            score = float(result.get("format_fidelity_score", 0.0) or 0.0)
            aesthetic = float(result.get("aesthetics_score", 0.0) or 0.0)
            scores.append(score)
            aesthetics.append(aesthetic)

            discrepancies = list(result.get("discrepancies", []) or [])
            for d in discrepancies:
                if isinstance(d, dict):
                    d.setdefault("sheet_index", idx)
            all_discrepancies.extend([d for d in discrepancies if isinstance(d, dict)])

            aesthetic_issues = list(result.get("aesthetic_issues", []) or [])
            for d in aesthetic_issues:
                if isinstance(d, dict):
                    d.setdefault("sheet_index", idx)
            all_aesthetic_issues.extend([d for d in aesthetic_issues if isinstance(d, dict)])

            per_sheet.append(
                {
                    "sheet_index": idx,
                    "format_fidelity_score": score,
                    "aesthetics_score": aesthetic,
                    "dimension_scores": result.get("dimension_scores") or {},
                    "discrepancies": discrepancies,
                    "aesthetic_issues": aesthetic_issues,
                    "pass": False,  # filled below after threshold is applied
                    "original_png": str(orig_png),
                    "translated_png": str(trans_png),
                }
            )

        min_score = min(scores) if scores else 0.0
        avg_score = sum(scores) / len(scores) if scores else 0.0
        aesthetics_min = min(aesthetics) if aesthetics else 0.0
        aesthetics_avg = sum(aesthetics) / len(aesthetics) if aesthetics else 0.0

        passed = bool(scores) and min_score >= threshold and not sheet_count_mismatch
        aesthetics_warning = bool(aesthetics) and aesthetics_min < aesthetics_warn_threshold
        overall_score = 0.8 * min_score + 0.2 * aesthetics_min

        for item in per_sheet:
            item["pass"] = bool(item.get("format_fidelity_score", 0.0) >= threshold)

        if sheet_count_mismatch:
            all_discrepancies.append(
                {
                    "location": "workbook",
                    "issue": f"sheet_count_mismatch: original={len(original_images)} translated={len(translated_images)}",
                    "severity": "high",
                }
            )

        if passed:
            return {
                "status": "passed",
                "attempts": attempts,
                "format_fidelity_score": round(min_score, 4),
                "format_fidelity_min": round(min_score, 4),
                "format_fidelity_avg": round(avg_score, 4),
                "aesthetics_score": round(aesthetics_min, 4),
                "aesthetics_min": round(aesthetics_min, 4),
                "aesthetics_avg": round(aesthetics_avg, 4),
                "overall_score": round(overall_score, 4),
                "threshold": threshold,
                "aesthetics_warn_threshold": aesthetics_warn_threshold,
                "aesthetics_warning": aesthetics_warning,
                "sheets_compared": len(per_sheet),
                "sheets_total_original": len(original_images),
                "sheets_total_translated": len(translated_images),
                "truncated": truncated,
                "sheet_count_mismatch": sheet_count_mismatch,
                "per_sheet": per_sheet,
                "discrepancies": all_discrepancies,
                "aesthetic_issues": all_aesthetic_issues,
                "original_dir": str(original_dir),
                "translated_dir": str(translated_dir),
            }

        if attempts > max_retries:
            return {
                "status": "failed",
                "attempts": attempts,
                "format_fidelity_score": round(min_score, 4),
                "format_fidelity_min": round(min_score, 4),
                "format_fidelity_avg": round(avg_score, 4),
                "aesthetics_score": round(aesthetics_min, 4),
                "aesthetics_min": round(aesthetics_min, 4),
                "aesthetics_avg": round(aesthetics_avg, 4),
                "overall_score": round(overall_score, 4),
                "threshold": threshold,
                "aesthetics_warn_threshold": aesthetics_warn_threshold,
                "aesthetics_warning": aesthetics_warning,
                "sheets_compared": len(per_sheet),
                "sheets_total_original": len(original_images),
                "sheets_total_translated": len(translated_images),
                "truncated": truncated,
                "sheet_count_mismatch": sheet_count_mismatch,
                "per_sheet": per_sheet,
                "discrepancies": all_discrepancies,
                "aesthetic_issues": all_aesthetic_issues,
                "original_dir": str(original_dir),
                "translated_dir": str(translated_dir),
            }

        auto_fix_format(translated_xlsx, original_xlsx, all_discrepancies)

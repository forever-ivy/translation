#!/usr/bin/env python3
"""Gemini, Kimi (Moonshot), or OpenAI vision QA for DOCX layout: format fidelity + aesthetics.

This compares rendered page screenshots of the original/template DOCX and the translated DOCX.

Important: For translation, exact line breaks/page counts may change. The intent is to catch
obvious format regressions (lost tables, broken lists, inconsistent heading styles, etc.)
and also report aesthetic issues. Format fidelity is higher priority than aesthetics.
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
    for cand in _iter_json_candidates(raw, limit=12):
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


def render_docx_to_images(docx_path: Path, output_dir: Path) -> list[Path]:
    """Convert docx to one-or-more PNGs via LibreOffice headless."""
    output_dir.mkdir(parents=True, exist_ok=True)
    outdir = str(output_dir)
    try:
        soffice_bin = _resolve_soffice_bin()
        subprocess.run(
            [soffice_bin, "--headless", "--convert-to", "png", "--outdir", outdir, str(docx_path)],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "LibreOffice (soffice) not found. Install LibreOffice or set OPENCLAW_SOFFICE_BIN to the soffice binary path."
        ) from exc

    stem = docx_path.stem
    produced = sorted(output_dir.glob(f"{stem}*.png"), key=lambda p: p.name.lower())
    canonical = output_dir / f"{stem}.png"
    if canonical.exists():
        produced = [canonical] + [p for p in produced if p != canonical]
    return produced


def _build_docx_qa_prompt() -> str:
    return (
        "Compare these two document page screenshots. The first is the original/template, "
        "the second is the translated output. Evaluate:\n"
        "1) Format fidelity (higher priority): headings, lists, tables, spacing, alignment, margins.\n"
        "2) Aesthetics (lower priority): readability, whitespace balance, consistent typography.\n\n"
        "Return ONLY a JSON object with:\n"
        '- "format_fidelity_score": float 0-1\n'
        '- "aesthetics_score": float 0-1\n'
        '- "dimension_scores": {"headings":0-1,"lists":0-1,"tables":0-1,"spacing":0-1,"alignment":0-1,"fonts":0-1}\n'
        '- "discrepancies": [{"location": str, "issue": str, "severity": str}]\n'
        '- "aesthetic_issues": [{"location": str, "issue": str, "severity": str}]\n'
    )


def _compare_doc_visual_gemini(original_png_b64: str, translated_png_b64: str) -> dict[str, Any]:
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
                    {"text": _build_docx_qa_prompt()},
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


def _compare_doc_visual_moonshot(original_png_b64: str, translated_png_b64: str) -> dict[str, Any]:
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
        prompt=_build_docx_qa_prompt(),
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


def _compare_doc_visual_openai(original_png_b64: str, translated_png_b64: str) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip() or _read_openclaw_api_key("openai-codex")
    if not api_key:
        raise RuntimeError("Missing OpenAI API key (OPENAI_API_KEY or OpenClaw openai-codex api_key profile).")
    model = os.environ.get("OPENCLAW_OPENAI_VISION_MODEL", "").strip() or "openai-codex/gpt-5.2"
    if "/" in model:
        model = model.rsplit("/", 1)[-1]
    raw = _openai_chat_completions(
        api_key=api_key,
        model=model,
        prompt=_build_docx_qa_prompt(),
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


def compare_doc_visual(original_png_b64: str, translated_png_b64: str) -> dict[str, Any]:
    """Compare two document screenshots with Gemini, Kimi (Moonshot), or OpenAI vision.

    Select provider via OPENCLAW_VISION_BACKEND:
      - gemini: require GOOGLE_API_KEY/GEMINI_API_KEY
      - moonshot/kimi: require MOONSHOT_API_KEY or OpenClaw moonshot auth profile
      - openai: require OPENAI_API_KEY or OpenClaw openai-codex api_key profile
      - auto (default): try Gemini then fallback to Moonshot/OpenAI if available
    """
    backend = os.environ.get("OPENCLAW_VISION_BACKEND", "auto").strip().lower()
    if backend in ("kimi", "moonshot"):
        return _compare_doc_visual_moonshot(original_png_b64, translated_png_b64)
    if backend in ("gemini", "google"):
        return _compare_doc_visual_gemini(original_png_b64, translated_png_b64)
    if backend in ("openai", "openai-codex"):
        return _compare_doc_visual_openai(original_png_b64, translated_png_b64)

    gemini_key_present = bool((os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or "").strip())
    moonshot_key_present = bool((os.environ.get("MOONSHOT_API_KEY") or "").strip() or _read_openclaw_api_key("moonshot"))
    openai_key_present = bool((os.environ.get("OPENAI_API_KEY") or "").strip() or _read_openclaw_api_key("openai-codex"))

    if gemini_key_present:
        try:
            return _compare_doc_visual_gemini(original_png_b64, translated_png_b64)
        except Exception:
            if moonshot_key_present:
                return _compare_doc_visual_moonshot(original_png_b64, translated_png_b64)
            if openai_key_present:
                return _compare_doc_visual_openai(original_png_b64, translated_png_b64)
            raise

    if moonshot_key_present:
        return _compare_doc_visual_moonshot(original_png_b64, translated_png_b64)
    if openai_key_present:
        return _compare_doc_visual_openai(original_png_b64, translated_png_b64)

    raise RuntimeError(
        "No vision credentials found. Set GOOGLE_API_KEY/GEMINI_API_KEY (Gemini) "
        "or configure Moonshot/OpenAI "
        "(MOONSHOT_API_KEY/OPENAI_API_KEY or OpenClaw moonshot/openai-codex api_key profile)."
    )


def run_docx_qa(
    *,
    original_docx: Path,
    translated_docx: Path,
    review_dir: Path,
    max_pages: int = 6,
    fidelity_threshold: float = 0.85,
    aesthetics_warn_threshold: float = 0.7,
) -> dict[str, Any]:
    review_dir.mkdir(parents=True, exist_ok=True)
    orig_dir = review_dir / "original"
    trans_dir = review_dir / "translated"
    try:
        orig_images = render_docx_to_images(original_docx, orig_dir)
        trans_images = render_docx_to_images(translated_docx, trans_dir)
    except Exception as exc:
        msg = str(exc)
        reason = "soffice_missing" if ("soffice" in msg.lower() or "libreoffice" in msg.lower()) else "render_failed"
        return {
            "status": "skipped",
            "reason": reason,
            "error": msg,
            "pages_compared": 0,
            "pages_total_original": 0,
            "pages_total_translated": 0,
            "original_dir": str(orig_dir),
            "translated_dir": str(trans_dir),
        }

    page_count_mismatch = len(orig_images) != len(trans_images)
    paired_count = min(len(orig_images), len(trans_images))
    paired = [(orig_images[i], trans_images[i]) for i in range(paired_count)]
    truncated = False
    if max_pages > 0 and len(paired) > max_pages:
        paired = paired[:max_pages]
        truncated = True

    per_page: list[dict[str, Any]] = []
    discrepancies: list[dict[str, Any]] = []
    aesthetic_issues: list[dict[str, Any]] = []
    fidelities: list[float] = []
    aesthetics: list[float] = []

    for idx, (orig_png, trans_png) in enumerate(paired, start=1):
        orig_b64 = base64.b64encode(orig_png.read_bytes()).decode("ascii")
        trans_b64 = base64.b64encode(trans_png.read_bytes()).decode("ascii")
        try:
            result = compare_doc_visual(orig_b64, trans_b64)
        except Exception as exc:
            return {
                "status": "skipped",
                "reason": "vision_unavailable",
                "error": str(exc),
                "pages_compared": max(0, idx - 1),
                "pages_total_original": len(orig_images),
                "pages_total_translated": len(trans_images),
                "truncated": truncated,
                "page_count_mismatch": page_count_mismatch,
                "original_dir": str(orig_dir),
                "translated_dir": str(trans_dir),
            }
        f = float(result.get("format_fidelity_score", 0.0) or 0.0)
        a = float(result.get("aesthetics_score", 0.0) or 0.0)
        fidelities.append(f)
        aesthetics.append(a)

        page_discrepancies = list(result.get("discrepancies") or [])
        for d in page_discrepancies:
            if isinstance(d, dict):
                d.setdefault("page_index", idx)
        discrepancies.extend([d for d in page_discrepancies if isinstance(d, dict)])

        page_aesthetic = list(result.get("aesthetic_issues") or [])
        for d in page_aesthetic:
            if isinstance(d, dict):
                d.setdefault("page_index", idx)
        aesthetic_issues.extend([d for d in page_aesthetic if isinstance(d, dict)])

        per_page.append(
            {
                "page_index": idx,
                "format_fidelity_score": f,
                "aesthetics_score": a,
                "dimension_scores": result.get("dimension_scores") or {},
                "discrepancies": page_discrepancies,
                "aesthetic_issues": page_aesthetic,
                "original_png": str(orig_png),
                "translated_png": str(trans_png),
            }
        )

    fidelity_min = min(fidelities) if fidelities else 0.0
    fidelity_avg = sum(fidelities) / len(fidelities) if fidelities else 0.0
    aesthetics_min = min(aesthetics) if aesthetics else 0.0
    aesthetics_avg = sum(aesthetics) / len(aesthetics) if aesthetics else 0.0

    passed = bool(fidelities) and fidelity_min >= fidelity_threshold
    aesthetics_warning = bool(aesthetics) and aesthetics_min < aesthetics_warn_threshold
    overall_score = 0.8 * fidelity_min + 0.2 * aesthetics_min

    if page_count_mismatch:
        discrepancies.append(
            {
                "location": "document",
                "issue": f"page_count_mismatch: original={len(orig_images)} translated={len(trans_images)}",
                "severity": "low",
            }
        )

    return {
        "status": "passed" if passed else "failed",
        "format_fidelity_score": round(fidelity_min, 4),
        "format_fidelity_min": round(fidelity_min, 4),
        "format_fidelity_avg": round(fidelity_avg, 4),
        "aesthetics_score": round(aesthetics_min, 4),
        "aesthetics_min": round(aesthetics_min, 4),
        "aesthetics_avg": round(aesthetics_avg, 4),
        "overall_score": round(overall_score, 4),
        "fidelity_threshold": fidelity_threshold,
        "aesthetics_warn_threshold": aesthetics_warn_threshold,
        "aesthetics_warning": aesthetics_warning,
        "pages_compared": len(per_page),
        "pages_total_original": len(orig_images),
        "pages_total_translated": len(trans_images),
        "truncated": truncated,
        "page_count_mismatch": page_count_mismatch,
        "per_page": per_page,
        "discrepancies": discrepancies,
        "aesthetic_issues": aesthetic_issues,
        "original_dir": str(orig_dir),
        "translated_dir": str(trans_dir),
    }

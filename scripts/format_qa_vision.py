#!/usr/bin/env python3
"""Multimodal format QA using Gemini Vision for xlsx translation quality.

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


def compare_format_visual(original_png_b64: str, translated_png_b64: str) -> dict[str, Any]:
    """Call Gemini Vision to compare two spreadsheet screenshots."""
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY (or GEMINI_API_KEY) environment variable is required for format QA vision.")
    model = os.environ.get("OPENCLAW_GEMINI_VISION_MODEL", "gemini-3-pro").strip()
    if "/" in model:
        # Accept OpenClaw-style provider/model ids and use the raw model id for Gemini API.
        model = model.rsplit("/", 1)[-1]
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
        f":generateContent?key={api_key}"
    )
    prompt_text = (
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
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt_text},
                {"inline_data": {"mime_type": "image/png", "data": original_png_b64}},
                {"inline_data": {"mime_type": "image/png", "data": translated_png_b64}},
            ],
        }],
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

    original_images = render_xlsx_to_images(original_xlsx, original_dir)
    attempts = 0
    sheets_max = int(os.environ.get("OPENCLAW_FORMAT_QA_SHEETS_MAX", "6"))
    threshold = float(os.environ.get("OPENCLAW_FORMAT_QA_THRESHOLD", "0.85"))
    aesthetics_warn_threshold = float(os.environ.get("OPENCLAW_VISION_AESTHETICS_WARN_THRESHOLD", "0.7"))

    while True:
        translated_images = render_xlsx_to_images(translated_xlsx, translated_dir)
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
            result = compare_format_visual(orig_b64, trans_b64)
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

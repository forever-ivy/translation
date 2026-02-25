#!/usr/bin/env python3
"""Format contract helpers for web gateway responses."""

from __future__ import annotations

import json
import re
from typing import Any


def build_section_format_contract(prompt_text: str, *, section_prefix: str = "§") -> dict[str, Any] | None:
    """Derive a section-based output contract from prompt text.

    Returns None when no stable section sequence can be inferred.
    """
    text = str(prompt_text or "")
    if not text.strip():
        return None
    marker_re = re.compile(rf"{re.escape(section_prefix)}(\d+){re.escape(section_prefix)}")
    nums = [int(m.group(1)) for m in marker_re.finditer(text)]
    if not nums:
        return None

    # Prefer the latest contiguous run that starts with 1.
    latest_run = 0
    run = 0
    expected = 1
    for n in nums:
        if n == 1:
            run = 1
            expected = 2
            latest_run = 1
            continue
        if run > 0 and n == expected:
            run += 1
            expected += 1
            latest_run = run
            continue
        run = 0
        expected = 1

    if latest_run < 2:
        return None
    return {
        "mode": "sectioned_text_ar_en_v1",
        "expected_sections": latest_run,
        "section_prefix": section_prefix,
        "forbid_extra_text": True,
        "forbid_markdown_fence": True,
    }


def _strip_outer_fence(text: str) -> str:
    raw = str(text or "").strip()
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        body = "\n".join(lines[1:-1]).strip()
        return body
    return raw


def _decode_json_candidates(raw_text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    def _append(source: str, value: Any) -> None:
        if isinstance(value, str):
            txt = value.strip()
            if txt:
                out.append((source, txt))

    text = str(raw_text or "").strip()
    candidates = [text]
    unfenced = _strip_outer_fence(text)
    if unfenced and unfenced != text:
        candidates.append(unfenced)

    decoder = json.JSONDecoder()
    parsed_values: list[Any] = []
    for candidate in candidates:
        parsed = None
        try:
            parsed = json.loads(candidate)
        except Exception:
            parsed = None
        if parsed is not None:
            parsed_values.append(parsed)
            continue
        # Fallback: decode first embedded JSON object/array.
        for idx, ch in enumerate(candidate):
            if ch not in "{[":
                continue
            try:
                value, _end = decoder.raw_decode(candidate[idx:])
            except Exception:
                continue
            parsed_values.append(value)
            break

    for value in parsed_values:
        if isinstance(value, dict):
            _append("json.final_text", value.get("final_text"))
            _append("json.final_reflow_text", value.get("final_reflow_text"))
            _append("json.text", value.get("text"))
            _append("json.translated_text", value.get("translated_text"))
            choices = value.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0] if isinstance(choices[0], dict) else {}
                msg = first.get("message") if isinstance(first, dict) else {}
                if isinstance(msg, dict):
                    _append("json.choices[0].message.content", msg.get("content"))
    return out


def _validate_sectioned_text(candidate_text: str, contract: dict[str, Any]) -> tuple[bool, str]:
    text = str(candidate_text or "").strip()
    if not text:
        return False, "empty_text"

    prefix = str(contract.get("section_prefix") or "§")
    expected_sections = int(contract.get("expected_sections") or 0)
    forbid_extra_text = bool(contract.get("forbid_extra_text", True))
    forbid_markdown_fence = bool(contract.get("forbid_markdown_fence", True))

    if forbid_markdown_fence and "```" in text:
        return False, "markdown_fence_detected"

    marker_re = re.compile(rf"{re.escape(prefix)}(\d+){re.escape(prefix)}")
    matches = list(marker_re.finditer(text))
    if not matches:
        return False, "section_marker_missing"
    if forbid_extra_text and text[: matches[0].start()].strip():
        return False, "extra_prefix_text"

    numbers: list[int] = []
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        section_no = int(match.group(1))
        numbers.append(section_no)
        if not content:
            return False, f"section_empty:{section_no}"

    contiguous = list(range(1, len(numbers) + 1))
    if numbers != contiguous:
        return False, f"section_numbering_invalid:{numbers}"

    if expected_sections > 0 and len(numbers) != expected_sections:
        return False, f"expected_sections:{expected_sections},got:{len(numbers)}"
    return True, ""


def apply_format_contract(raw_text: str, contract: dict[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize raw model text by contract."""
    text = str(raw_text or "").strip()
    if not contract:
        return {"ok": True, "text": text, "meta": {"source": "raw", "contract_applied": False}}

    mode = str(contract.get("mode") or "").strip()
    if mode != "sectioned_text_ar_en_v1":
        return {
            "ok": False,
            "error": "format_contract_failed",
            "detail": f"unsupported_contract_mode:{mode or 'empty'}",
            "meta": {"source": "raw"},
        }

    attempted: list[str] = []
    raw_candidates: list[tuple[str, str]] = [("raw", text)]
    stripped = _strip_outer_fence(text)
    if stripped and stripped != text:
        raw_candidates.append(("raw.unfenced", stripped))
    raw_candidates.extend(_decode_json_candidates(text))

    seen: set[str] = set()
    for source, candidate in raw_candidates:
        norm = str(candidate or "").strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        attempted.append(source)
        ok, err = _validate_sectioned_text(norm, contract)
        if ok:
            return {"ok": True, "text": norm, "meta": {"source": source, "contract_applied": True}}
        last_error = err

    return {
        "ok": False,
        "error": "format_contract_failed",
        "detail": last_error if "last_error" in locals() else "no_valid_candidate",
        "meta": {"attempted_sources": attempted},
    }


def build_format_repair_prompt(raw_text: str, contract: dict[str, Any], *, reason: str = "") -> str:
    """Prompt that requests format-only rewrite without semantic edits."""
    expected = int(contract.get("expected_sections") or 0)
    prefix = str(contract.get("section_prefix") or "§")
    reason_text = f"Reason: {reason}\n" if reason else ""
    return (
        "Reformat the following content only.\n"
        "Do not add explanations, do not translate again, and do not change meaning.\n"
        f"Output must be exactly {expected} sections in order using {prefix}n{prefix} markers.\n"
        f"{reason_text}"
        "Output only the final formatted content.\n\n"
        "Content to reformat:\n"
        f"{raw_text}"
    )

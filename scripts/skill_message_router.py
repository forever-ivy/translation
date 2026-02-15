#!/usr/bin/env python3
"""Strict Telegram router for V6.0.

Parses raw OpenClaw Telegram message text, extracts attachment paths + command,
and forwards to dispatcher without refeeding large inline file payloads.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

from scripts.v4_runtime import DEFAULT_KB_ROOT, DEFAULT_NOTIFY_TARGET, DEFAULT_WORK_ROOT, send_message

COMMAND_HEADS = {"new", "run", "status", "ok", "no", "rerun", "cancel", "stop", "abort", "approve", "reject"}
ATTACHED_RE = re.compile(r"\[media attached:\s*(.+?)\s*\(([^)]*)\)\]", re.IGNORECASE)
# OpenClaw unified prefix: [Telegram <chat_id> <date> <tz>] [openclaw] <text>
TELEGRAM_PREFIX_RE = re.compile(
    r"^\[Telegram\s+(\d+)\s+[^\]]*\]\s*(?:\[openclaw\]\s*)?(.*)$",
    re.IGNORECASE,
)
MESSAGE_ID_RE = re.compile(r"\[message_id:\s*([^\]]+)\]", re.IGNORECASE)
FILE_BLOCK_RE = re.compile(r"<file\b[^>]*>.*?</file>", re.IGNORECASE | re.DOTALL)
FILE_BLOCK_CAPTURE_RE = re.compile(r"<file\b([^>]*)>(.*?)</file>", re.IGNORECASE | re.DOTALL)
FILE_ATTR_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_-]*)="([^"]*)"')


def _is_http_url(value: str) -> bool:
    v = (value or "").strip().lower()
    return v.startswith("http://") or v.startswith("https://")


def _safe_basename(name: str) -> str:
    base = Path(str(name or "attachment")).name
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return base or "attachment"


def _extract_file_block_attachments(raw_text: str, *, temp_dir: Path) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    max_mb = int(os.getenv("OPENCLAW_ROUTER_FILE_BLOCK_MAX_MB", "35"))
    max_bytes = max(1, max_mb) * 1024 * 1024

    for idx, matched in enumerate(FILE_BLOCK_CAPTURE_RE.finditer(raw_text), start=1):
        attr_text = matched.group(1) or ""
        body = (matched.group(2) or "").strip()
        attrs = {k: v for (k, v) in FILE_ATTR_RE.findall(attr_text)}
        name = _safe_basename(attrs.get("name") or attrs.get("filename") or attrs.get("fileName") or f"file_block_{idx}")
        mime_type = str(attrs.get("mime") or attrs.get("mime_type") or attrs.get("type") or "").strip()
        url = str(attrs.get("mediaUrl") or attrs.get("media_url") or attrs.get("url") or "").strip()
        content_base64 = str(attrs.get("content_base64") or attrs.get("base64") or "").strip()

        if url and not content_base64 and not body:
            attachments.append({"mediaUrl": url, "name": name, "mime_type": mime_type})
            continue

        if not content_base64:
            # Try reading as base64 from body when it looks like it.
            if body and re.fullmatch(r"[A-Za-z0-9+/=\s]+", body or ""):
                content_base64 = re.sub(r"\s+", "", body)

        if not content_base64:
            continue

        approx = int(len(content_base64) * 3 / 4)
        if approx > max_bytes:
            continue

        try:
            import base64

            data = base64.b64decode(content_base64.encode("utf-8"), validate=True)
        except Exception:
            continue
        if len(data) > max_bytes:
            continue

        temp_dir.mkdir(parents=True, exist_ok=True)
        out_path = temp_dir / name
        if out_path.exists():
            out_path = temp_dir / f"{out_path.stem}_{idx}{out_path.suffix}"
        out_path.write_bytes(data)
        attachments.append({"path": str(out_path.resolve()), "name": out_path.name, "mime_type": mime_type})

    return attachments


def _load_raw_text(args: argparse.Namespace) -> tuple[str, str]:
    if args.raw_text:
        return args.raw_text, ""
    if args.raw_file:
        p = Path(args.raw_file).expanduser().resolve()
        return p.read_text(encoding="utf-8"), str(p)
    data = sys.stdin.read()
    return data, ""


def _extract_sender(raw_text: str, fallback_sender: str) -> str:
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        matched = TELEGRAM_PREFIX_RE.match(line)
        if matched and matched.group(1):
            return matched.group(1).strip()
    return fallback_sender.strip() or "unknown"


def _extract_message_id(raw_text: str) -> str:
    matched = MESSAGE_ID_RE.search(raw_text)
    if not matched:
        return ""
    return matched.group(1).strip()


def _strip_file_blocks(raw_text: str) -> tuple[str, bool]:
    cleaned, count = FILE_BLOCK_RE.subn("", raw_text)
    return cleaned, bool(count)


def _extract_attachment_paths(raw_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for matched in ATTACHED_RE.finditer(raw_text):
        path_text = matched.group(1).strip()
        if not path_text:
            continue
        mime_type = matched.group(2).strip()
        if _is_http_url(path_text):
            url = path_text
            if url in seen:
                continue
            parsed = urllib.parse.urlparse(url)
            name = _safe_basename(Path(parsed.path).name or f"attachment_{len(rows)+1}")
            rows.append({"mediaUrl": url, "name": name, "mime_type": mime_type})
            seen.add(url)
            continue

        p = Path(path_text).expanduser()
        if not p.exists():
            continue
        resolved = str(p.resolve())
        if resolved in seen:
            continue
        rows.append({"path": resolved, "name": p.name, "mime_type": mime_type})
        seen.add(resolved)
    return rows


def _extract_text_content(raw_text: str) -> str:
    keep: list[str] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = MESSAGE_ID_RE.sub("", line).strip()
        if not line:
            continue
        if line.startswith("<file ") or line == "</file>":
            continue
        if line.lower().startswith("to send an image back"):
            continue
        if ATTACHED_RE.search(line):
            continue
        matched = TELEGRAM_PREFIX_RE.match(line)
        if matched:
            body = (matched.group(2) or "").strip()
            if body:
                keep.append(body)
            continue
        if line.startswith("System:"):
            continue
        keep.append(line)
    return "\n".join(keep).strip()


def _is_command(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    head = lowered.split(" ", 1)[0]
    return head in COMMAND_HEADS


def _build_payload(*, sender: str, text: str, attachments: list[dict[str, Any]], message_id: str, raw_ref: str, token_guard_applied: bool) -> dict[str, Any]:
    return {
        "from": sender,
        "sender": sender,
        "text": text,
        "message_id": message_id,
        "attachments": attachments,
        "meta": {
            "sender": sender,
            "message_id": message_id,
            "raw_message_ref": raw_ref,
            "token_guard_applied": token_guard_applied,
        },
        "token_guard_applied": token_guard_applied,
        "raw_message_ref": raw_ref,
    }


def _run_dispatcher(dispatcher_args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(dispatcher_args, check=False, text=True, capture_output=True)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _notify_hint(target: str, dry_run: bool = False) -> dict[str, Any]:
    message = (
        "[router] Task mode is strict.\n"
        "Start with: new\n"
        "Then send files/text, and run\n"
        "Other commands: status | cancel | ok | no {reason} | rerun"
    )
    return send_message(target=target, message=message, dry_run=dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description="V6.0 strict Telegram router")
    parser.add_argument("--raw-text")
    parser.add_argument("--raw-file")
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    parser.add_argument("--kb-root", default=str(DEFAULT_KB_ROOT))
    parser.add_argument("--notify-target", default=DEFAULT_NOTIFY_TARGET)
    parser.add_argument("--sender", default="")
    parser.add_argument("--auto-run", action="store_true", default=True)
    parser.add_argument("--dry-run-notify", action="store_true")
    args = parser.parse_args()

    strict_router_enabled = str(
        os.getenv("OPENCLAW_STRICT_ROUTER", "1")
    ).strip().lower() not in {"0", "false", "off", "no"}
    raw_text, raw_ref = _load_raw_text(args)
    router_tmp_dir = Path(tempfile.mkdtemp(prefix="openclaw_router_"))
    try:
        extracted_attachments = _extract_file_block_attachments(raw_text, temp_dir=router_tmp_dir)
        cleaned_raw, token_guard_applied = _strip_file_blocks(raw_text)
        sender = _extract_sender(cleaned_raw, args.sender)
        reply_target = sender if sender and sender.lower() != "unknown" else args.notify_target
        message_id = _extract_message_id(cleaned_raw)
        attachments = _extract_attachment_paths(cleaned_raw)
        if extracted_attachments:
            # Prefer explicit [media attached] paths, but keep file-block derived attachments too.
            attachments.extend(extracted_attachments)
        text = _extract_text_content(cleaned_raw)

        if strict_router_enabled and not attachments and not _is_command(text) and not (text or "").strip().isdigit():
            notify_result = _notify_hint(reply_target, dry_run=args.dry_run_notify)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "mode": "strict_hint",
                        "sender": sender,
                        "message_id": message_id,
                        "notify_result": notify_result,
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        payload = _build_payload(
            sender=sender,
            text=text,
            attachments=attachments,
            message_id=message_id,
            raw_ref=raw_ref,
            token_guard_applied=token_guard_applied,
        )

        python_bin = os.getenv("V4_PYTHON_BIN") or sys.executable
        base_cmd = [
            python_bin,
            "-m",
            "scripts.openclaw_v4_dispatcher",
            "--work-root",
            str(Path(args.work_root).expanduser().resolve()),
            "--kb-root",
            str(Path(args.kb_root).expanduser().resolve()),
            "--notify-target",
            args.notify_target,
        ]
        if args.dry_run_notify:
            base_cmd.append("--dry-run-notify")

        if _is_command(text) and not attachments:
            cmd = base_cmd + ["approval", "--sender", sender, "--command", text]
            returncode, stdout, stderr = _run_dispatcher(cmd)
            print(
                json.dumps(
                    {
                        "ok": returncode == 0,
                        "mode": "command",
                        "sender": sender,
                        "message_id": message_id,
                        "token_guard_applied": token_guard_applied,
                        "dispatcher_stdout": stdout,
                        "dispatcher_stderr": stderr,
                    },
                    ensure_ascii=False,
                )
            )
            return 0 if returncode == 0 else 1

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp:
            tmp.write(json.dumps(payload, ensure_ascii=False))
            tmp_path = Path(tmp.name)
        try:
            cmd = base_cmd + ["message-event", "--payload-file", str(tmp_path)]
            if args.auto_run:
                cmd.append("--auto-run")
            returncode, stdout, stderr = _run_dispatcher(cmd)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

        print(
            json.dumps(
                {
                    "ok": returncode == 0,
                    "mode": "task_route",
                    "sender": sender,
                    "message_id": message_id,
                    "text": text,
                    "attachments_count": len(attachments),
                    "token_guard_applied": token_guard_applied,
                    "dispatcher_stdout": stdout,
                    "dispatcher_stderr": stderr,
                },
                ensure_ascii=False,
            )
        )
        return 0 if returncode == 0 else 1
    finally:
        shutil.rmtree(router_tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

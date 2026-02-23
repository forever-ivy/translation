#!/usr/bin/env python3
"""Telegram Bot polling daemon — bypasses OpenClaw's broken DM routing.

Long-polls Telegram getUpdates API directly, dispatches commands to
skill_approval.handle_command() and file/text messages to the dispatcher's
message-event subcommand.
"""

from __future__ import annotations

import json
import re
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:  # POSIX-only
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [tg-bot] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("telegram_bot")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_IDS: set[str] = set(
    s.strip() for s in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "7720887962").split(",") if s.strip()
)
POLL_TIMEOUT = int(os.getenv("TELEGRAM_POLL_TIMEOUT", "30"))
OFFSET_FILE = Path(os.getenv(
    "TELEGRAM_OFFSET_FILE",
    str(Path("~/.openclaw/runtime/translation/tg_bot_offset").expanduser()),
))

WORK_ROOT = os.getenv("V4_WORK_ROOT", "/Users/ivy/Library/CloudStorage/OneDrive-Personal/Translation Task")
KB_ROOT = os.getenv("V4_KB_ROOT", "/Users/ivy/Library/CloudStorage/OneDrive-Personal/Knowledge Repository")
PYTHON_BIN = os.getenv("V4_PYTHON_BIN", sys.executable)

COMMAND_HEADS = {"new", "run", "status", "ok", "no", "rerun", "cancel", "stop", "abort", "approve", "reject", "discard", "help", "company"}

_running = True


def _signal_handler(signum: int, _frame: Any) -> None:
    global _running
    log.info("Received signal %s, shutting down...", signum)
    _running = False


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------


def tg_api(method: str, bot_token: str = "", **params: Any) -> dict[str, Any]:
    """Call a Telegram Bot API method. Returns the parsed JSON response."""
    token = bot_token or BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    timeout = max(POLL_TIMEOUT + 10, 60) if method == "getUpdates" else 30
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        log.error("Telegram API %s HTTP %s: %s", method, exc.code, error_body)
        return {"ok": False, "error_code": exc.code, "description": error_body}
    except (urllib.error.URLError, OSError) as exc:
        log.error("Telegram API %s network error: %s", method, exc)
        return {"ok": False, "description": str(exc)}


def tg_send(chat_id: str, text: str, bot_token: str = "") -> dict[str, Any]:
    """Send a text message to a Telegram chat."""
    # Telegram sendMessage limit is 4096 chars; truncate if needed.
    if len(text) > 4000:
        text = text[:4000] + "\n...(truncated)"
    return tg_api("sendMessage", bot_token=bot_token, chat_id=chat_id, text=text)


def tg_download_file(file_id: str, dest_dir: Path, bot_token: str = "") -> Path | None:
    """Download a file from Telegram by file_id. Returns local path or None."""
    token = bot_token or BOT_TOKEN
    info = tg_api("getFile", bot_token=token, file_id=file_id)
    if not info.get("ok"):
        log.error("getFile failed for %s: %s", file_id, info)
        return None
    file_path = info.get("result", {}).get("file_path", "")
    if not file_path:
        log.error("getFile returned empty file_path for %s: %s", file_id, info)
        return None
    encoded_path = urllib.parse.quote(file_path, safe="/")
    download_url = f"https://api.telegram.org/file/bot{token}/{encoded_path}"
    file_name = Path(file_path).name
    local_path = dest_dir / file_name
    for attempt in range(2):
        try:
            urllib.request.urlretrieve(download_url, str(local_path))
            return local_path
        except (urllib.error.URLError, OSError) as exc:
            log.error("Download attempt %d failed for %s: %s", attempt + 1, file_path, exc)
            if attempt == 0:
                time.sleep(1)
    return None


# ---------------------------------------------------------------------------
# Offset persistence
# ---------------------------------------------------------------------------


def _load_offset() -> int:
    try:
        return int(OFFSET_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _save_offset(offset: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(offset))


# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------


def _is_command(text: str) -> bool:
    head = (text or "").strip().lower().split(" ", 1)[0]
    return head in COMMAND_HEADS


def _handle_command(chat_id: str, text: str) -> None:
    """Dispatch a command directly to skill_approval.handle_command()."""
    from scripts.skill_approval import handle_command

    log.info("Command from %s: %s", chat_id, text)
    try:
        result = handle_command(
            command_text=text,
            work_root=Path(WORK_ROOT),
            kb_root=Path(KB_ROOT),
            target=chat_id,
            sender=chat_id,
        )
        log.info("Command result: %s", json.dumps(result, ensure_ascii=False)[:500])
        # handle_command already sends notifications via send_message,
        # but for 'new' we send an explicit ack since it doesn't notify.
        if text.strip().lower().startswith("new") and result.get("ok"):
            job_id = result.get("job_id", "")
            tg_send(chat_id, f"\u2705 New task created\n\U0001f194 {job_id}\n\U0001f4ce Send files or text, then: run")
    except Exception as exc:
        log.exception("Command handler error")
        tg_send(chat_id, f"\u274c Command error: {exc}")


def _handle_document(chat_id: str, message: dict[str, Any]) -> None:
    """Download document and dispatch via message-event subprocess."""
    doc = message.get("document", {})
    file_id = doc.get("file_id", "")
    file_name = doc.get("file_name", "attachment")
    caption = (message.get("caption") or "").strip()

    with tempfile.TemporaryDirectory(prefix="tg_dl_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        local_file = tg_download_file(file_id, tmp_path)
        if not local_file:
            tg_send(chat_id, f"\u274c Download failed: {file_name}")
            return

        # Rename to original file name
        final_path = tmp_path / file_name
        if local_file != final_path:
            local_file.rename(final_path)
            local_file = final_path

        payload = {
            "sender": chat_id,
            "from": chat_id,
            "text": caption,
            "attachments": [
                {
                    "path": str(local_file),
                    "name": file_name,
                    "mime_type": doc.get("mime_type", ""),
                }
            ],
        }

        payload_file = tmp_path / "payload.json"
        payload_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        cmd = [
            PYTHON_BIN, "-m", "scripts.openclaw_v4_dispatcher",
            "--work-root", WORK_ROOT,
            "--kb-root", KB_ROOT,
            "--notify-target", chat_id,
            "message-event",
            "--payload-file", str(payload_file),
        ]
        log.info("Dispatching document %s for %s", file_name, chat_id)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode == 0:
            log.info("Document dispatch OK: %s", proc.stdout[:300])
        else:
            log.error("Document dispatch failed: %s", proc.stderr[:500])
            tg_send(chat_id, f"\u274c File ingest error: {file_name}")


def _handle_text(chat_id: str, text: str) -> None:
    """Handle non-command text in strict mode."""
    stripped = (text or "").strip()

    # Interaction selections (e.g., company menu) are often numeric replies like "1" or "1) Eventranz".
    # These must not be blocked by the short-text guard.
    selection_match = re.fullmatch(r"(\d{1,3})", stripped) or re.match(r"^(\d{1,3})\s*[)\.]\s*\S+", stripped)
    if selection_match:
        from scripts.skill_approval import handle_interaction_reply

        try:
            result = handle_interaction_reply(
                reply_text=selection_match.group(1),
                work_root=Path(WORK_ROOT),
                kb_root=Path(KB_ROOT),
                target=chat_id,
                sender=chat_id,
            )
            # When a selection is pending, handle_interaction_reply will send the relevant message(s).
            if result.get("ok") or result.get("error") in {"invalid_selection", "expired"}:
                return
        except Exception as exc:
            log.exception("Interaction reply handler error")
            tg_send(chat_id, f"\u274c Selection error: {exc}")
            return

    # Reject trivially short or punctuation-only text
    if len(re.sub(r"\W+", "", stripped)) < 2:
        tg_send(chat_id, "⚠️ Text too short — send a document or longer message")
        return
    require_new = str(os.getenv("OPENCLAW_REQUIRE_NEW", "1")).strip().lower() not in {"0", "false", "off", "no"}
    if require_new:
        # Check if there's an active collecting job — if so, append text
        from scripts.v4_runtime import db_connect, ensure_runtime_paths, get_sender_active_job, get_job

        paths = ensure_runtime_paths(Path(WORK_ROOT))
        conn = db_connect(paths)
        try:
            active_job_id = get_sender_active_job(conn, sender=chat_id)
            if active_job_id:
                job = get_job(conn, active_job_id)
                if job and str(job.get("status", "")) in {"collecting", "received", "missing_inputs", "needs_revision"}:
                    # Append text to the active job via message-event
                    payload = {"sender": chat_id, "from": chat_id, "text": text, "attachments": []}
                    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False)
                        payload_path = f.name
                    try:
                        cmd = [
                            PYTHON_BIN, "-m", "scripts.openclaw_v4_dispatcher",
                            "--work-root", WORK_ROOT,
                            "--kb-root", KB_ROOT,
                            "--notify-target", chat_id,
                            "message-event",
                            "--payload-file", payload_path,
                        ]
                        subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                    finally:
                        Path(payload_path).unlink(missing_ok=True)
                    tg_send(chat_id, f"\U0001f4dd Text added\n\U0001f194 {active_job_id}\nSend more files/text, or: run")
                    return
        finally:
            conn.close()

        tg_send(
            chat_id,
            "\U0001f4cb Strict mode\n\n"
            "1\ufe0f\u20e3 new \u2014 create task\n"
            "2\ufe0f\u20e3 send files/text\n"
            "3\ufe0f\u20e3 run \u2014 start translation\n\n"
            "Other: status \u00b7 ok \u00b7 no \u00b7 rerun",
        )
    else:
        # Non-strict: dispatch as message-event
        payload = {"sender": chat_id, "from": chat_id, "text": text, "attachments": []}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            payload_path = f.name
        try:
            cmd = [
                PYTHON_BIN, "-m", "scripts.openclaw_v4_dispatcher",
                "--work-root", WORK_ROOT,
                "--kb-root", KB_ROOT,
                "--notify-target", chat_id,
                "message-event",
                "--payload-file", payload_path,
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        finally:
            Path(payload_path).unlink(missing_ok=True)


def handle_update(update: dict[str, Any]) -> None:
    """Process a single Telegram update."""
    message = update.get("message")
    if not message:
        return

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    if not chat_id or chat_id not in ALLOWED_CHAT_IDS:
        log.debug("Ignoring message from chat_id=%s (not in allowlist)", chat_id)
        return

    text = (message.get("text") or "").strip()
    has_document = bool(message.get("document"))

    if has_document:
        _handle_document(chat_id, message)
    elif text and _is_command(text):
        _handle_command(chat_id, text)
    elif text:
        _handle_text(chat_id, text)
    # else: ignore (stickers, photos without document, etc.)


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------


PID_FILE = Path(os.getenv(
    "TELEGRAM_PID_FILE",
    str(Path("~/.openclaw/runtime/translation/tg_bot.pid").expanduser()),
))

_pid_lock_handle: Any | None = None


def _acquire_pid_lock() -> bool:
    """Acquire a singleton lock. Returns True if lock acquired.

    Prefer an OS-level file lock (flock) so we can reliably prevent multiple bot
    instances even if the PID file is missing/stale or running under different
    supervisors. Fallback to the older PID check if flock isn't available.
    """
    global _pid_lock_handle
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is not None:
        handle = PID_FILE.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            log.error("Another instance is running (lock busy). Exiting.")
            return False
        # Keep the handle open for the lifetime of the process (lock is tied to FD).
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        _pid_lock_handle = handle
        return True

    # Fallback: PID file check (less reliable).
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            os.kill(old_pid, 0)  # Check if process is alive
            log.error("Another instance is running (PID %d). Exiting.", old_pid)
            return False
        except (ValueError, ProcessLookupError, PermissionError):
            log.info("Removing stale PID file (old PID gone)")
    PID_FILE.write_text(str(os.getpid()))
    return True


def _release_pid_lock() -> None:
    """Release the singleton lock on clean shutdown."""
    global _pid_lock_handle
    if _pid_lock_handle is not None:
        try:
            _pid_lock_handle.close()
        except Exception:
            pass
        _pid_lock_handle = None


def poll_loop() -> None:
    """Long-poll Telegram getUpdates in a loop."""
    if not _acquire_pid_lock():
        return

    offset = _load_offset()
    log.info("Starting Telegram bot poll loop (offset=%d, allowed=%s)", offset, ALLOWED_CHAT_IDS)

    try:
        while _running:
            params: dict[str, Any] = {"timeout": POLL_TIMEOUT, "allowed_updates": ["message"]}
            if offset:
                params["offset"] = offset
            resp = tg_api("getUpdates", **params)
            if not resp.get("ok"):
                if int(resp.get("error_code") or 0) == 409:
                    # Telegram enforces single long-polling consumer. If we see a conflict,
                    # another instance is alive; exit so we don't spam errors forever.
                    log.error("getUpdates conflict (409) — another bot instance is running. Exiting.")
                    return
                log.warning("getUpdates failed: %s", resp.get("description", "unknown"))
                time.sleep(5)
                continue

            updates = resp.get("result", [])
            for update in updates:
                update_id = update.get("update_id", 0)
                try:
                    handle_update(update)
                except Exception:
                    log.exception("Error handling update %s", update_id)
                offset = update_id + 1
                _save_offset(offset)
    finally:
        _release_pid_lock()


def main() -> int:
    if not BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN is not set. Exiting.")
        return 1

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    log.info("Telegram direct bot starting (token=...%s)", BOT_TOKEN[-6:])
    try:
        poll_loop()
    except KeyboardInterrupt:
        pass
    log.info("Telegram bot stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

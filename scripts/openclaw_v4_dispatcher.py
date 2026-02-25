#!/usr/bin/env python3
"""V6.0 dispatcher for OpenClaw full orchestration."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from scripts.skill_approval import handle_command
from scripts.v4_kb import sync_kb
from scripts.v4_pipeline import run_job_pipeline
from scripts.v4_runtime import (
    DEFAULT_KB_ROOT,
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_WORK_ROOT,
    audit_operation_event,
    db_connect,
    ensure_runtime_paths,
)

DEFAULT_GATEWAY_BASE_URL = os.getenv("OPENCLAW_WEB_GATEWAY_BASE_URL", "http://127.0.0.1:8765").strip()


def _parse_message_payload(payload_file: str) -> dict[str, Any]:
    return json.loads(Path(payload_file).read_text(encoding="utf-8"))


def _gateway_runtime_dir() -> Path:
    root = Path("~/.openclaw/runtime/translation").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _gateway_pid_path() -> Path:
    return _gateway_runtime_dir() / "web_gateway.pid"


def _gateway_log_path() -> Path:
    return _gateway_runtime_dir() / "web_gateway.log"


def _gateway_health_file(args: argparse.Namespace) -> Path:
    paths = ensure_runtime_paths(Path(args.work_root))
    return paths.system_root / "web_gateway_health.json"


def _gateway_url(path: str, base_url: str = "") -> str:
    base = (base_url or DEFAULT_GATEWAY_BASE_URL).strip().rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{base}{suffix}"


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_gateway_pid() -> int | None:
    pid_file = _gateway_pid_path()
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        pid_file.unlink(missing_ok=True)
        return None
    if not _process_alive(pid):
        pid_file.unlink(missing_ok=True)
        return None
    return pid


def _http_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
    body: bytes | None = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid_gateway_json", "raw": raw[:2000]}


def _gateway_status(args: argparse.Namespace) -> dict[str, Any]:
    pid = _read_gateway_pid()
    base_url = (args.base_url or DEFAULT_GATEWAY_BASE_URL).strip()
    health: dict[str, Any] = {}
    healthy = False
    providers: dict[str, Any] = {}
    primary_provider = str(os.getenv("OPENCLAW_WEB_LLM_PRIMARY", "gemini_web")).strip() or "gemini_web"
    primary_last_error = ""
    primary_logged_in = False
    if pid:
        try:
            health = _http_json(_gateway_url("/health", base_url=base_url), timeout=3.0)
            healthy = bool(health.get("healthy", health.get("ok", False)))
        except Exception:
            health_file = _gateway_health_file(args)
            if health_file.exists():
                try:
                    health = json.loads(health_file.read_text(encoding="utf-8"))
                except Exception:
                    health = {}
            healthy = bool(health.get("healthy", False))
    providers = health.get("providers") if isinstance(health.get("providers"), dict) else {}
    if isinstance(providers.get(primary_provider), dict):
        primary_last_error = str(providers[primary_provider].get("last_error") or "")
        primary_logged_in = bool(providers[primary_provider].get("logged_in", False))
    return {
        "running": bool(pid),
        "healthy": healthy,
        "pid": pid or 0,
        "base_url": base_url,
        "model": str(os.getenv("OPENCLAW_WEB_GATEWAY_MODEL", "web-llm")),
        "logged_in": bool(health.get("logged_in", primary_logged_in)),
        "last_error": str(health.get("last_error") or primary_last_error),
        "updated_at": str(health.get("updated_at") or ""),
        "version": str(health.get("version") or ""),
        "primary_provider": str(health.get("primary_provider") or primary_provider),
        "providers": providers,
        "health": health,
    }


def _gateway_start(args: argparse.Namespace) -> dict[str, Any]:
    already = _gateway_status(args)
    if already.get("running"):
        return {"ok": True, "action": "start", "status": already, "message": "already_running"}

    project_root = Path(__file__).resolve().parents[1]
    python_bin = str(os.getenv("V4_PYTHON_BIN") or "").strip() or str(project_root / ".venv" / "bin" / "python")
    if not Path(python_bin).exists():
        python_bin = sys.executable

    log_file = _gateway_log_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    health_file = _gateway_health_file(args)
    profiles_dir = Path(os.getenv("OPENCLAW_WEB_GATEWAY_PROFILES_DIR", "~/.openclaw/runtime/translation/web-profiles")).expanduser()
    profiles_dir.mkdir(parents=True, exist_ok=True)
    base_url = (args.base_url or DEFAULT_GATEWAY_BASE_URL).strip()
    host = base_url.replace("http://", "").replace("https://", "").split(":", 1)[0] or "127.0.0.1"
    port = int(base_url.rsplit(":", 1)[-1]) if ":" in base_url else int(os.getenv("OPENCLAW_WEB_GATEWAY_PORT", "8765"))

    cmd = [
        python_bin,
        "-m",
        "scripts.openclaw_web_gateway",
        "--host",
        host,
        "--port",
        str(port),
        "--health-file",
        str(health_file),
        "--profiles-dir",
        str(profiles_dir),
    ]
    model = str(os.getenv("OPENCLAW_WEB_GATEWAY_MODEL", "chatgpt-web")).strip()
    if model:
        cmd.extend(["--model", model])

    with log_file.open("a", encoding="utf-8") as f:
        proc = subprocess.Popen(
            cmd,
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(project_root),
        )
    _gateway_pid_path().write_text(str(proc.pid), encoding="utf-8")

    started = False
    for _ in range(30):
        time.sleep(0.3)
        status = _gateway_status(args)
        if status.get("running") and status.get("healthy"):
            started = True
            break
    status = _gateway_status(args)
    return {
        "ok": bool(started or status.get("running")),
        "action": "start",
        "status": status,
        "log_file": str(log_file),
        "health_file": str(health_file),
    }


def _gateway_stop(args: argparse.Namespace) -> dict[str, Any]:
    pid = _read_gateway_pid()
    if not pid:
        return {"ok": True, "action": "stop", "status": _gateway_status(args), "message": "already_stopped"}

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass

    for _ in range(20):
        time.sleep(0.2)
        if not _process_alive(pid):
            break
    if _process_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    _gateway_pid_path().unlink(missing_ok=True)
    return {"ok": True, "action": "stop", "status": _gateway_status(args)}


def _gateway_login(args: argparse.Namespace) -> dict[str, Any]:
    status = _gateway_status(args)
    if not status.get("running"):
        start_result = _gateway_start(args)
        if not start_result.get("ok"):
            return {"ok": False, "action": "login", "error": "gateway_start_failed", "start_result": start_result}
    base_url = (args.base_url or DEFAULT_GATEWAY_BASE_URL).strip()
    provider = str(getattr(args, "provider", "") or "").strip()
    timeout_seconds = int(getattr(args, "timeout_seconds", 15) or 15)
    try:
        login = _http_json(
            _gateway_url("/session/login", base_url=base_url),
            method="POST",
            payload={
                "provider": provider,
                "interactive": bool(args.interactive_login),
                "timeout_seconds": timeout_seconds,
            },
            timeout=30.0,
        )
    except urllib.error.URLError as exc:
        return {"ok": False, "action": "login", "error": f"gateway_login_request_failed:{exc}"}
    return {"ok": bool(login.get("ok", False)), "action": "login", "result": login, "status": _gateway_status(args)}


def _gateway_diagnose(args: argparse.Namespace) -> dict[str, Any]:
    status = _gateway_status(args)
    base_url = (args.base_url or DEFAULT_GATEWAY_BASE_URL).strip()
    if not status.get("running"):
        return {
            "ok": False,
            "action": "diagnose",
            "status": status,
            "diagnose": {"ok": False, "error": "gateway_not_running"},
        }
    provider = str(getattr(args, "provider", "") or "").strip()
    suffix = "/session/diagnose"
    if provider:
        from urllib.parse import quote

        suffix = f"/session/diagnose?provider={quote(provider)}"
    try:
        diagnose = _http_json(_gateway_url(suffix, base_url=base_url), timeout=8.0)
    except Exception as exc:
        diagnose = {"ok": False, "error": f"gateway_diagnose_request_failed:{exc}"}
    return {"ok": bool(diagnose.get("ok", False)), "action": "diagnose", "status": status, "diagnose": diagnose}


def cmd_email_poll(args: argparse.Namespace) -> int:
    from scripts.skill_email_ingest import main as email_main  # lazy import

    argv = [
        "skill_email_ingest.py",
        "--imap-host",
        args.imap_host,
        "--imap-port",
        str(args.imap_port),
        "--imap-user",
        args.imap_user,
        "--imap-password",
        args.imap_password,
        "--mailbox",
        args.mailbox,
        "--from-filter",
        args.from_filter,
        "--max-messages",
        str(args.max_messages),
        "--work-root",
        str(args.work_root),
        "--kb-root",
        str(args.kb_root),
        "--notify-target",
        args.notify_target,
    ]
    if args.auto_run:
        argv.append("--auto-run")
    if args.mark_seen:
        argv.append("--mark-seen")
    os.sys.argv = argv
    return email_main()


def cmd_message_event(args: argparse.Namespace) -> int:
    from scripts.skill_message_ingest import main as msg_main  # lazy import

    argv = [
        "skill_message_ingest.py",
        "--work-root",
        str(args.work_root),
        "--kb-root",
        str(args.kb_root),
        "--notify-target",
        args.notify_target,
    ]
    if args.payload_json:
        argv.extend(["--payload-json", args.payload_json])
    if args.payload_file:
        argv.extend(["--payload-file", args.payload_file])
    if args.auto_run:
        argv.append("--auto-run")
    os.sys.argv = argv
    return msg_main()


def cmd_run_job(args: argparse.Namespace) -> int:
    result = run_job_pipeline(
        job_id=args.job_id,
        work_root=Path(args.work_root),
        kb_root=Path(args.kb_root),
        notify_target=args.notify_target,
        dry_run_notify=args.dry_run_notify,
    )
    print(json.dumps({"ok": bool(result.get("ok")), "result": result}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_kb_sync(args: argparse.Namespace) -> int:
    paths = ensure_runtime_paths(Path(args.work_root))
    conn = db_connect(paths)
    report_path = paths.kb_system_root / "kb_sync_latest.json"
    report = sync_kb(conn=conn, kb_root=Path(args.kb_root), report_path=report_path)
    conn.close()
    print(json.dumps({"ok": report.get("ok", True), "report": report, "report_path": str(report_path)}, ensure_ascii=False))
    return 0 if report.get("ok", True) else 1


def cmd_pending_reminder(args: argparse.Namespace) -> int:
    from scripts.skill_pending_reminder import main as reminder_main  # lazy import

    argv = [
        "skill_pending_reminder.py",
        "--work-root",
        str(args.work_root),
        "--target",
        args.notify_target,
    ]
    if args.dry_run_notify:
        argv.append("--dry-run")
    os.sys.argv = argv
    return reminder_main()


def cmd_approval(args: argparse.Namespace) -> int:
    result = handle_command(
        command_text=args.command,
        work_root=Path(args.work_root),
        kb_root=Path(args.kb_root),
        target=args.notify_target,
        sender=args.sender,
        dry_run_notify=args.dry_run_notify,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_ops_audit(args: argparse.Namespace) -> int:
    paths = ensure_runtime_paths(Path(args.work_root))
    conn = db_connect(paths)
    detail: Any = {}
    if args.detail_json:
        try:
            detail = json.loads(args.detail_json)
        except json.JSONDecodeError:
            detail = {"raw": args.detail_json}
    payload = {
        "operation_id": args.operation_id or f"op_{uuid.uuid4().hex[:12]}",
        "source": args.source or "dispatcher",
        "action": args.action,
        "job_id": args.job_id or "",
        "sender": args.sender or "",
        "status": args.status,
        "summary": args.summary or "",
        "detail": detail,
        "ts": args.ts or "",
    }
    result = audit_operation_event(
        conn,
        operation_payload=payload,
        milestone=args.milestone,
        dry_run=args.dry_run_notify,
    )
    conn.close()
    print(json.dumps({"ok": bool(result.get("ok")), "result": result}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def _gateway_audit(args: argparse.Namespace, *, action: str, status: str, summary: str, detail: dict[str, Any]) -> dict[str, Any]:
    paths = ensure_runtime_paths(Path(args.work_root))
    conn = db_connect(paths)
    payload = {
        "operation_id": f"op_{uuid.uuid4().hex[:12]}",
        "source": "dispatcher",
        "action": action,
        "job_id": str(args.job_id or "").strip(),
        "sender": str(args.sender or "").strip(),
        "status": status,
        "summary": summary,
        "detail": detail,
        "ts": "",
    }
    audit = audit_operation_event(
        conn,
        operation_payload=payload,
        milestone="ops_audit",
        dry_run=args.dry_run_notify,
    )
    conn.close()
    return audit


def cmd_gateway_start(args: argparse.Namespace) -> int:
    result = _gateway_start(args)
    op_status = "success" if result.get("ok") else "failed"
    _gateway_audit(
        args,
        action="gateway_start",
        status=op_status,
        summary=f"Gateway start {op_status}",
        detail=result,
    )
    print(json.dumps({"ok": bool(result.get("ok")), "result": result}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_gateway_stop(args: argparse.Namespace) -> int:
    result = _gateway_stop(args)
    op_status = "success" if result.get("ok") else "failed"
    _gateway_audit(
        args,
        action="gateway_stop",
        status=op_status,
        summary=f"Gateway stop {op_status}",
        detail=result,
    )
    print(json.dumps({"ok": bool(result.get("ok")), "result": result}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_gateway_status(args: argparse.Namespace) -> int:
    status = _gateway_status(args)
    op_status = "success" if status.get("running") else "failed"
    _gateway_audit(
        args,
        action="gateway_status",
        status=op_status,
        summary=f"Gateway status running={bool(status.get('running'))} healthy={bool(status.get('healthy'))}",
        detail=status,
    )
    print(json.dumps({"ok": True, "result": status}, ensure_ascii=False))
    return 0


def cmd_gateway_login(args: argparse.Namespace) -> int:
    result = _gateway_login(args)
    op_status = "success" if result.get("ok") else "failed"
    _gateway_audit(
        args,
        action="gateway_login",
        status=op_status,
        summary=f"Gateway login {op_status}",
        detail=result,
    )
    print(json.dumps({"ok": bool(result.get("ok")), "result": result}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def cmd_gateway_diagnose(args: argparse.Namespace) -> int:
    result = _gateway_diagnose(args)
    op_status = "success" if result.get("ok") else "failed"
    _gateway_audit(
        args,
        action="gateway_diagnose",
        status=op_status,
        summary=f"Gateway diagnose {op_status}",
        detail=result,
    )
    print(json.dumps({"ok": bool(result.get("ok")), "result": result}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw V6.0 dispatcher")
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    parser.add_argument("--kb-root", default=str(DEFAULT_KB_ROOT))
    parser.add_argument("--notify-target", default=DEFAULT_NOTIFY_TARGET)
    parser.add_argument("--dry-run-notify", action="store_true")

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_email = sub.add_parser("email-poll")
    p_email.add_argument("--imap-host", required=True)
    p_email.add_argument("--imap-port", type=int, default=993)
    p_email.add_argument("--imap-user", required=True)
    p_email.add_argument("--imap-password", required=True)
    p_email.add_argument("--mailbox", default="INBOX")
    p_email.add_argument("--from-filter", default="modeh@eventranz.com")
    p_email.add_argument("--max-messages", type=int, default=5)
    p_email.add_argument("--auto-run", action="store_true")
    p_email.add_argument("--mark-seen", action="store_true")

    p_msg = sub.add_parser("message-event")
    p_msg.add_argument("--payload-json")
    p_msg.add_argument("--payload-file")
    p_msg.add_argument("--auto-run", action="store_true")

    p_job = sub.add_parser("run-job")
    p_job.add_argument("--job-id", required=True)

    sub.add_parser("kb-sync")
    sub.add_parser("pending-reminder")

    p_ap = sub.add_parser("approval")
    p_ap.add_argument("--command", required=True)
    p_ap.add_argument("--sender", default="")

    p_ops = sub.add_parser("ops-audit")
    p_ops.add_argument("--source", default="dispatcher")
    p_ops.add_argument("--action", required=True)
    p_ops.add_argument("--status", default="success")
    p_ops.add_argument("--summary", default="")
    p_ops.add_argument("--detail-json", default="")
    p_ops.add_argument("--job-id", default="")
    p_ops.add_argument("--sender", default="")
    p_ops.add_argument("--operation-id", default="")
    p_ops.add_argument("--milestone", default="ops_audit")
    p_ops.add_argument("--ts", default="")

    for cmd in ("gateway-start", "gateway-stop", "gateway-status", "gateway-login", "gateway-diagnose"):
        p_gw = sub.add_parser(cmd)
        p_gw.add_argument("--base-url", default=DEFAULT_GATEWAY_BASE_URL)
        p_gw.add_argument("--job-id", default="")
        p_gw.add_argument("--sender", default="")
        p_gw.add_argument("--operation-id", default="")
        p_gw.add_argument("--interactive-login", action="store_true")
        p_gw.add_argument("--provider", default="")
        p_gw.add_argument("--timeout-seconds", type=int, default=15)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.cmd == "email-poll":
        return cmd_email_poll(args)
    if args.cmd == "message-event":
        return cmd_message_event(args)
    if args.cmd == "run-job":
        return cmd_run_job(args)
    if args.cmd == "kb-sync":
        return cmd_kb_sync(args)
    if args.cmd == "pending-reminder":
        return cmd_pending_reminder(args)
    if args.cmd == "approval":
        return cmd_approval(args)
    if args.cmd == "ops-audit":
        return cmd_ops_audit(args)
    if args.cmd == "gateway-start":
        return cmd_gateway_start(args)
    if args.cmd == "gateway-stop":
        return cmd_gateway_stop(args)
    if args.cmd == "gateway-status":
        return cmd_gateway_status(args)
    if args.cmd == "gateway-login":
        return cmd_gateway_login(args)
    if args.cmd == "gateway-diagnose":
        return cmd_gateway_diagnose(args)

    parser.error(f"unsupported cmd: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""OpenClaw skill: background worker that executes queued jobs.

This worker pulls from the persistent SQLite queue (job_run_queue) and runs
the full pipeline out-of-band so the bot can respond immediately to `status`.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
import signal

try:  # POSIX-only
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None

from scripts.v4_runtime import (
    DEFAULT_KB_ROOT,
    DEFAULT_NOTIFY_TARGET,
    DEFAULT_WORK_ROOT,
    claim_next_queued,
    db_connect,
    ensure_runtime_paths,
    set_queue_pipeline_process,
    finish_queue_item,
    get_job,
    heartbeat_queue_item,
    requeue_stuck_running,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [run-worker] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("run_worker")

_DEFAULT_PID_FILE = Path("~/.openclaw/runtime/translation/run_worker.pid").expanduser()
_pid_lock_handle: Any | None = None


def _pid_file() -> Path:
    raw = str(os.getenv("OPENCLAW_RUN_WORKER_PID_FILE", "")).strip()
    return (Path(raw).expanduser() if raw else _DEFAULT_PID_FILE)


def _acquire_pid_lock() -> bool:
    """Acquire a singleton lock. Returns True if lock acquired."""
    global _pid_lock_handle
    pid_file = _pid_file()
    pid_file.parent.mkdir(parents=True, exist_ok=True)

    if fcntl is not None:
        handle = pid_file.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            log.info("Another run worker instance is already running (lock busy). Exiting.")
            return False
        # Keep the handle open for the lifetime of the process (lock is tied to FD).
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        _pid_lock_handle = handle
        return True

    # Fallback: PID file check (less reliable).
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            os.kill(old_pid, 0)  # Check if process is alive
            log.info("Another run worker instance is already running (PID %d). Exiting.", old_pid)
            return False
        except (ValueError, ProcessLookupError, PermissionError):
            log.info("Removing stale PID file (old PID gone)")
    pid_file.write_text(str(os.getpid()))
    return True


def _release_pid_lock() -> None:
    global _pid_lock_handle
    if _pid_lock_handle is not None:
        try:
            _pid_lock_handle.close()
        except Exception:
            pass
        _pid_lock_handle = None


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError:
        return int(default)


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def _worker_id() -> str:
    host = socket.gethostname().strip() or "host"
    return f"{host}:{os.getpid()}"


def _python_bin() -> str:
    return str(os.getenv("V4_PYTHON_BIN") or sys.executable).strip() or sys.executable


def _run_job_cmd(*, job_id: str, work_root: Path, kb_root: Path, notify_target: str, dry_run: bool) -> list[str]:
    cmd = [
        _python_bin(),
        "-m",
        "scripts.openclaw_v4_dispatcher",
        "--work-root",
        str(work_root),
        "--kb-root",
        str(kb_root),
        "--notify-target",
        (notify_target or "").strip(),
    ]
    if dry_run:
        cmd.append("--dry-run-notify")
    cmd.extend(["run-job", "--job-id", (job_id or "").strip()])
    return cmd


def _read_cancel_request(paths, *, queue_id: int) -> dict[str, str]:
    try:
        conn = db_connect(paths)
        row = conn.execute(
            "SELECT cancel_requested_at, cancel_reason, cancel_mode FROM job_run_queue WHERE id=?",
            (int(queue_id),),
        ).fetchone()
        conn.close()
    except Exception:
        return {"cancel_requested_at": "", "cancel_reason": "", "cancel_mode": ""}
    if not row:
        return {"cancel_requested_at": "", "cancel_reason": "", "cancel_mode": ""}
    return {
        "cancel_requested_at": str(row["cancel_requested_at"] or "").strip(),
        "cancel_reason": str(row["cancel_reason"] or "").strip(),
        "cancel_mode": str(row["cancel_mode"] or "").strip().lower(),
    }


def _signal_process_group(pgid: int, sig: int) -> None:
    if pgid <= 0:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(int(pgid), sig)
        else:  # pragma: no cover
            os.kill(int(pgid), sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        pass


def _heartbeat_loop(
    *,
    stop: threading.Event,
    paths,
    queue_id: int,
    worker_id: str,
    interval_seconds: int,
) -> None:
    interval = max(5, int(interval_seconds))
    while not stop.wait(interval):
        try:
            conn = db_connect(paths)
            heartbeat_queue_item(conn, queue_id=queue_id, worker_id=worker_id)
            conn.close()
        except Exception:
            # Heartbeats are best-effort; the main execution path will set
            # finished state even if heartbeats fail.
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", default=os.getenv("V4_WORK_ROOT", str(DEFAULT_WORK_ROOT)))
    parser.add_argument("--kb-root", default=os.getenv("V4_KB_ROOT", str(DEFAULT_KB_ROOT)))
    parser.add_argument("--once", action="store_true", help="Claim at most one job then exit (cron-friendly).")
    parser.add_argument("--dry-run-notify", action="store_true")
    args = parser.parse_args()

    if not _acquire_pid_lock():
        return 0

    worker_id = _worker_id()
    work_root = Path(args.work_root)
    kb_root = Path(args.kb_root)
    paths = ensure_runtime_paths(work_root)

    poll_seconds = max(1, _env_int("OPENCLAW_RUN_WORKER_POLL_SECONDS", 2))
    stuck_seconds = max(60, _env_int("OPENCLAW_RUN_WORKER_STUCK_SECONDS", 6 * 60 * 60))
    max_attempts = max(1, _env_int("OPENCLAW_RUN_WORKER_MAX_ATTEMPTS", 3))
    hb_interval = max(10, _env_int("OPENCLAW_RUN_WORKER_HEARTBEAT_SECONDS", 30))
    cancel_poll_seconds = max(0.2, _env_float("OPENCLAW_RUN_WORKER_CANCEL_POLL_SECONDS", 0.5))
    cancel_grace_seconds = max(0, _env_int("OPENCLAW_RUN_WORKER_CANCEL_GRACE_SECONDS", 5))

    log.info(
        "Run worker started (worker_id=%s, poll=%ss, stuck=%ss, max_attempts=%s, hb=%ss, cancel_poll=%ss, cancel_grace=%ss, pid_file=%s)",
        worker_id,
        poll_seconds,
        stuck_seconds,
        max_attempts,
        hb_interval,
        cancel_poll_seconds,
        cancel_grace_seconds,
        str(_pid_file()),
    )

    try:
        last_requeue_check = 0.0
        while True:
            # Periodic stuck-task recovery.
            now = time.time()
            if now - last_requeue_check >= 60:
                try:
                    conn = db_connect(paths)
                    requeue_stuck_running(conn, stuck_seconds=stuck_seconds, max_attempts=max_attempts)
                    conn.close()
                except Exception:
                    pass
                last_requeue_check = now

            conn = db_connect(paths)
            item = claim_next_queued(conn, worker_id=worker_id)
            conn.close()

            if not item:
                if args.once:
                    return 0
                time.sleep(poll_seconds)
                continue

            queue_id = int(item["id"])
            job_id = str(item.get("job_id") or "").strip()
            notify_target = str(item.get("notify_target") or DEFAULT_NOTIFY_TARGET).strip()
            if not job_id:
                conn = db_connect(paths)
                finish_queue_item(
                    conn, queue_id=queue_id, worker_id=worker_id, state="failed", last_error="missing_job_id"
                )
                conn.close()
                if args.once:
                    return 1
                continue

            conn = db_connect(paths)
            job = get_job(conn, job_id)
            conn.close()
            if not job:
                conn = db_connect(paths)
                finish_queue_item(conn, queue_id=queue_id, worker_id=worker_id, state="failed", last_error="job_not_found")
                conn.close()
                if args.once:
                    return 1
                continue

            log.info("Claimed job_id=%s (queue_id=%s)", job_id, queue_id)

            stop = threading.Event()
            hb = threading.Thread(
                target=_heartbeat_loop,
                kwargs={
                    "stop": stop,
                    "paths": paths,
                    "queue_id": queue_id,
                    "worker_id": worker_id,
                    "interval_seconds": hb_interval,
                },
                daemon=True,
            )
            hb.start()

            state = "failed"
            last_error = ""
            try:
                cmd = _run_job_cmd(
                    job_id=job_id,
                    work_root=work_root,
                    kb_root=kb_root,
                    notify_target=notify_target,
                    dry_run=bool(args.dry_run_notify),
                )
                log.info("Starting pipeline subprocess: %s", " ".join(cmd))
                proc = subprocess.Popen(cmd, start_new_session=True)
                pid = int(proc.pid or 0)
                try:
                    pgid = int(os.getpgid(pid)) if pid else 0
                except Exception:
                    pgid = pid

                conn = db_connect(paths)
                set_queue_pipeline_process(conn, queue_id=queue_id, worker_id=worker_id, pid=pid, pgid=pgid)
                conn.close()

                cancel_enforced = False
                kill_sent_at = 0.0
                kill_escalated = False
                cancel_reason = ""
                cancel_mode = ""

                while True:
                    rc = proc.poll()
                    if rc is not None:
                        break

                    cancel = _read_cancel_request(paths, queue_id=queue_id)
                    if cancel.get("cancel_requested_at"):
                        cancel_reason = str(cancel.get("cancel_reason") or "").strip()
                        cancel_mode = str(cancel.get("cancel_mode") or "").strip().lower() or "force"
                        if not cancel_enforced:
                            _signal_process_group(pgid, signal.SIGTERM)
                            cancel_enforced = True
                            kill_sent_at = time.time()
                        if cancel_mode == "force" and cancel_grace_seconds == 0 and not kill_escalated:
                            _signal_process_group(pgid, signal.SIGKILL)
                            kill_escalated = True
                        if cancel_enforced and not kill_escalated and (time.time() - kill_sent_at) >= cancel_grace_seconds:
                            _signal_process_group(pgid, signal.SIGKILL)
                            kill_escalated = True

                    time.sleep(cancel_poll_seconds)

                rc = int(proc.returncode or 0)
                if cancel_enforced:
                    state = "canceled"
                    last_error = ("canceled_by_user" + (f":{cancel_reason}" if cancel_reason else "")).strip()
                else:
                    state = "succeeded" if rc == 0 else "failed"
                    if state != "succeeded":
                        try:
                            conn = db_connect(paths)
                            job2 = get_job(conn, job_id) or {}
                            conn.close()
                        except Exception:
                            job2 = {}
                        errs = list(job2.get("errors_json") or [])
                        last_error = str(errs[0] or "").strip() if errs else f"exit_code:{rc}"
            except Exception as exc:  # pragma: no cover
                state = "failed"
                last_error = f"worker_exception:{exc}"
            finally:
                stop.set()
                try:
                    hb.join(timeout=2)
                except Exception:
                    pass
                conn = db_connect(paths)
                finish_queue_item(conn, queue_id=queue_id, worker_id=worker_id, state=state, last_error=last_error)
                conn.close()

            log.info("Finished job_id=%s (queue_id=%s, state=%s, last_error=%s)", job_id, queue_id, state, last_error)

            if args.once:
                return 0 if state == "succeeded" else 1
    finally:
        _release_pid_lock()


if __name__ == "__main__":
    raise SystemExit(main())

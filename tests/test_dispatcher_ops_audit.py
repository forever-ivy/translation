#!/usr/bin/env python3

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import openclaw_v4_dispatcher
from scripts.v4_runtime import (
    audit_operation_event,
    db_connect,
    ensure_runtime_paths,
    resolve_operation_notify_target,
    set_sender_active_job,
    write_job,
)


class DispatcherOpsAuditTest(unittest.TestCase):
    def _seed_job(self, work_root: Path, *, job_id: str, sender: str, status: str = "running") -> None:
        paths = ensure_runtime_paths(work_root)
        conn = db_connect(paths)
        inbox_dir = paths.inbox_messaging / job_id
        review_dir = paths.review_root / job_id
        inbox_dir.mkdir(parents=True, exist_ok=True)
        review_dir.mkdir(parents=True, exist_ok=True)
        write_job(
            conn,
            job_id=job_id,
            source="telegram",
            sender=sender,
            subject="seed",
            message_text="seed",
            status=status,
            inbox_dir=inbox_dir,
            review_dir=review_dir,
        )
        conn.close()

    @patch("scripts.v4_runtime.send_message")
    def test_audit_operation_event_records_event_and_sends_message(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            self._seed_job(work_root, job_id="job_ops_1", sender="+15550001")

            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)
            out = audit_operation_event(
                conn,
                operation_payload={
                    "operation_id": "op_test_1",
                    "source": "tauri",
                    "action": "service_start",
                    "job_id": "job_ops_1",
                    "status": "success",
                    "summary": "Service started",
                },
                dry_run=True,
            )
            self.assertTrue(out["ok"])
            self.assertEqual(out["target"], "+15550001")

            row = conn.execute(
                "SELECT milestone, payload_json FROM events WHERE milestone='ops_audit' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            self.assertIsNotNone(row)
            payload = json.loads(str(row["payload_json"]))
            self.assertEqual(payload["operation"]["action"], "service_start")
            self.assertEqual(payload["operation"]["operation_id"], "op_test_1")
            mocked_send.assert_called_once()

    def test_resolve_operation_notify_target_priority_chain(self):
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)

            # 1) job.sender wins
            self._seed_job(work_root, job_id="job_target_1", sender="+15551001")
            target = resolve_operation_notify_target(conn, job_id="job_target_1", sender="+19999999")
            self.assertEqual(target, "+15551001")

            # 2) sender_active_jobs wins when no job_id sender
            self._seed_job(work_root, job_id="job_target_2", sender="+15551002")
            set_sender_active_job(conn, sender="+15551002", job_id="job_target_2")
            target = resolve_operation_notify_target(conn, sender="")
            self.assertEqual(target, "+15551002")

            # 3) latest actionable sender used after sender_active_jobs cleared
            conn.execute("DELETE FROM sender_active_jobs")
            conn.commit()
            target = resolve_operation_notify_target(conn, sender="")
            self.assertEqual(target, "+15551002")

            # 4) fallback to default notify target
            conn.execute("DELETE FROM jobs")
            conn.commit()
            with patch("scripts.v4_runtime.DEFAULT_NOTIFY_TARGET", "+15550000"):
                target = resolve_operation_notify_target(conn, sender="")
            self.assertEqual(target, "+15550000")
            conn.close()

    @patch("scripts.v4_runtime.send_message")
    def test_dispatcher_ops_audit_command_outputs_json(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            self._seed_job(work_root, job_id="job_dispatcher_ops", sender="+15552001")

            argv = [
                "openclaw_v4_dispatcher.py",
                "--work-root",
                str(work_root),
                "ops-audit",
                "--action",
                "gateway_status",
                "--status",
                "success",
                "--job-id",
                "job_dispatcher_ops",
                "--summary",
                "Gateway is healthy",
            ]

            with patch.object(sys, "argv", argv):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = openclaw_v4_dispatcher.main()
            self.assertEqual(code, 0)
            payload = json.loads(buf.getvalue())
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["result"]["operation"]["action"], "gateway_status")
            self.assertEqual(payload["result"]["target"], "+15552001")


if __name__ == "__main__":
    unittest.main()

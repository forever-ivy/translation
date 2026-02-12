#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.skill_approval import handle_command
from scripts.v4_pipeline import create_job
from scripts.v4_runtime import db_connect, ensure_runtime_paths, get_job


class SkillApprovalTest(unittest.TestCase):
    @patch("scripts.skill_approval.send_whatsapp_message")
    def test_ok_marks_verified_without_delivery_copy(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "Translation Task"
            kb_root = Path(tmp) / "Knowledge Repository"
            kb_root.mkdir(parents=True, exist_ok=True)
            job_id = "job_test_ok"
            inbox = work_root / "_INBOX" / "whatsapp" / job_id
            inbox.mkdir(parents=True, exist_ok=True)
            create_job(
                source="whatsapp",
                sender="+8613",
                subject="Test",
                message_text="status",
                inbox_dir=inbox,
                job_id=job_id,
                work_root=work_root,
            )

            result = handle_command(
                command_text="ok",
                work_root=work_root,
                kb_root=kb_root,
                target="+8613",
                sender="+8613",
                dry_run_notify=True,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "verified")

            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)
            job = get_job(conn, job_id)
            conn.close()
            self.assertEqual(job["status"], "verified")
            delivered = list((work_root / "Translated -EN").glob("*.docx"))
            self.assertEqual(len(delivered), 0)

    @patch("scripts.skill_approval.send_whatsapp_message")
    def test_no_marks_needs_revision(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "Translation Task"
            kb_root = Path(tmp) / "Knowledge Repository"
            kb_root.mkdir(parents=True, exist_ok=True)
            job_id = "job_test_no"
            inbox = work_root / "_INBOX" / "whatsapp" / job_id
            inbox.mkdir(parents=True, exist_ok=True)
            create_job(
                source="whatsapp",
                sender="+8613",
                subject="Test",
                message_text="status",
                inbox_dir=inbox,
                job_id=job_id,
                work_root=work_root,
            )

            result = handle_command(
                command_text="no wrong numbering in table section",
                work_root=work_root,
                kb_root=kb_root,
                target="+8613",
                sender="+8613",
                dry_run_notify=True,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "needs_revision")

            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)
            job = get_job(conn, job_id)
            conn.close()
            self.assertEqual(job["status"], "needs_revision")


if __name__ == "__main__":
    unittest.main()

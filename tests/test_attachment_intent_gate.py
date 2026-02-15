#!/usr/bin/env python3

import json
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.skill_message_ingest import main as ingest_main
from scripts.v4_pipeline import create_job
from scripts.v4_runtime import (
    db_connect,
    ensure_runtime_paths,
    get_job_interaction,
    list_job_final_uploads,
    set_sender_active_job,
    update_job_status,
)


class AttachmentIntentGateTest(unittest.TestCase):
    def test_post_run_attachments_prompt_for_destination_then_finalize(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "Translation Task"
            kb_root = Path(tmp) / "Knowledge Repository"
            kb_root.mkdir(parents=True, exist_ok=True)
            sender = "+8613"

            job_id = "job_test_gate"
            inbox = work_root / "_INBOX" / "telegram" / job_id
            inbox.mkdir(parents=True, exist_ok=True)
            create_job(
                source="telegram",
                sender=sender,
                subject="Test",
                message_text="",
                inbox_dir=inbox,
                job_id=job_id,
                work_root=work_root,
            )

            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)
            update_job_status(conn, job_id=job_id, status="review_ready", errors=[])
            set_sender_active_job(conn, sender=sender, job_id=job_id)
            conn.close()

            src = Path(tmp) / "sample.docx"
            src.write_text("final", encoding="utf-8")

            payload = {
                "from": sender,
                "message_id": "m1",
                "text": "",
                "attachments": [{"path": str(src), "name": "sample.docx"}],
            }
            argv = [
                "skill_message_ingest.py",
                "--payload-json",
                json.dumps(payload, ensure_ascii=False),
                "--work-root",
                str(work_root),
                "--kb-root",
                str(kb_root),
                "--notify-target",
                sender,
                "--dry-run-notify",
            ]
            with patch.object(sys, "argv", argv), patch("scripts.skill_message_ingest.send_message") as mocked_send:
                mocked_send.return_value = {"ok": True}
                with redirect_stdout(io.StringIO()):
                    rc = ingest_main()
            self.assertEqual(rc, 0)
            self.assertTrue(mocked_send.called)
            sent_msg = mocked_send.call_args.kwargs.get("message") or ""
            self.assertIn("Where should these files go?", sent_msg)

            conn = db_connect(paths)
            interaction = get_job_interaction(conn, job_id=job_id) or {}
            self.assertEqual(str(interaction.get("pending_action") or ""), "select_attachment_destination")
            options = json.loads(str(interaction.get("options_json") or "[]"))
            self.assertEqual(options[0]["action"], "final")
            staging_dir = Path(options[0]["staging_dir"])
            self.assertTrue((staging_dir / "sample.docx").exists())
            self.assertEqual(list_job_final_uploads(conn, job_id=job_id), [])
            conn.close()

            # Pick option 1 -> attach as FINAL for current task
            payload2 = {"from": sender, "message_id": "m2", "text": "1"}
            argv2 = [
                "skill_message_ingest.py",
                "--payload-json",
                json.dumps(payload2, ensure_ascii=False),
                "--work-root",
                str(work_root),
                "--kb-root",
                str(kb_root),
                "--notify-target",
                sender,
                "--dry-run-notify",
            ]
            with patch.object(sys, "argv", argv2), patch("scripts.skill_approval.send_message") as mocked_send2:
                mocked_send2.return_value = {"ok": True}
                with redirect_stdout(io.StringIO()):
                    rc2 = ingest_main()
            self.assertEqual(rc2, 0)
            self.assertTrue(mocked_send2.called)

            conn = db_connect(paths)
            interaction2 = get_job_interaction(conn, job_id=job_id) or {}
            self.assertEqual(str(interaction2.get("pending_action") or ""), "")
            final_uploads = list_job_final_uploads(conn, job_id=job_id)
            self.assertEqual(len(final_uploads), 1)
            final_path = Path(final_uploads[0])
            self.assertTrue(final_path.exists())
            self.assertIn("FinalUploads", str(final_path))
            conn.close()


if __name__ == "__main__":
    unittest.main()

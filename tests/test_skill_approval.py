#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import signal

from scripts.skill_approval import handle_command
from scripts.v4_pipeline import create_job
from scripts.v4_runtime import (
    add_job_final_upload,
    db_connect,
    ensure_runtime_paths,
    get_active_queue_item,
    get_job,
    set_job_kb_company,
    update_job_status,
    set_sender_active_job,
)


class SkillApprovalTest(unittest.TestCase):
    @patch("scripts.skill_approval.send_message")
    def test_new_creates_collecting_job(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "Translation Task"
            kb_root = Path(tmp) / "Knowledge Repository"
            kb_root.mkdir(parents=True, exist_ok=True)

            result = handle_command(
                command_text="new teachers survey task",
                work_root=work_root,
                kb_root=kb_root,
                target="+8613",
                sender="+8613",
                dry_run_notify=True,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "collecting")
            self.assertTrue(str(result["job_id"]).startswith("job_"))

            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)
            job = get_job(conn, str(result["job_id"]))
            conn.close()
            self.assertIsNotNone(job)
            self.assertEqual(job["status"], "collecting")

    @patch("scripts.skill_approval.send_message")
    def test_run_rejected_without_active_job_when_require_new(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "Translation Task"
            kb_root = Path(tmp) / "Knowledge Repository"
            kb_root.mkdir(parents=True, exist_ok=True)

            with patch.dict("os.environ", {"OPENCLAW_REQUIRE_NEW": "1"}, clear=False):
                result = handle_command(
                    command_text="run",
                    work_root=work_root,
                    kb_root=kb_root,
                    target="+8613",
                    sender="+8613",
                    dry_run_notify=True,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "job_not_found")

    @patch("scripts.skill_approval.send_message")
    def test_run_enqueues_job_once(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "Translation Task"
            kb_root = Path(tmp) / "Knowledge Repository"
            (kb_root / "30_Reference" / "Eventranz").mkdir(parents=True, exist_ok=True)
            sender = "+8613"

            job_id = "job_test_run_queue"
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
            set_job_kb_company(conn, job_id=job_id, kb_company="Eventranz")
            set_sender_active_job(conn, sender=sender, job_id=job_id)
            conn.close()

            result = handle_command(
                command_text="run",
                work_root=work_root,
                kb_root=kb_root,
                target=sender,
                sender=sender,
                dry_run_notify=True,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result.get("status"), "queued")

            conn = db_connect(paths)
            job = get_job(conn, job_id)
            self.assertEqual(job["status"], "queued")
            q = get_active_queue_item(conn, job_id=job_id)
            self.assertIsNotNone(q)
            conn.close()

            # Idempotent second run should not create a duplicate active queue item.
            result2 = handle_command(
                command_text="run",
                work_root=work_root,
                kb_root=kb_root,
                target=sender,
                sender=sender,
                dry_run_notify=True,
            )
            self.assertTrue(result2["ok"])

            conn = db_connect(paths)
            row = conn.execute(
                "SELECT COUNT(1) AS c FROM job_run_queue WHERE job_id=? AND state IN ('queued','running')",
                (job_id,),
            ).fetchone()
            conn.close()
            self.assertEqual(int(row["c"]), 1)

    @patch("scripts.skill_approval.send_message")
    def test_cancel_cancels_queued_job(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "Translation Task"
            kb_root = Path(tmp) / "Knowledge Repository"
            (kb_root / "30_Reference" / "Eventranz").mkdir(parents=True, exist_ok=True)
            sender = "+8613"

            job_id = "job_test_cancel_queued"
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
            set_job_kb_company(conn, job_id=job_id, kb_company="Eventranz")
            set_sender_active_job(conn, sender=sender, job_id=job_id)
            conn.close()

            result = handle_command(
                command_text="run",
                work_root=work_root,
                kb_root=kb_root,
                target=sender,
                sender=sender,
                dry_run_notify=True,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result.get("status"), "queued")

            cancel = handle_command(
                command_text="cancel",
                work_root=work_root,
                kb_root=kb_root,
                target=sender,
                sender=sender,
                dry_run_notify=True,
            )
            self.assertTrue(cancel["ok"])
            self.assertEqual(cancel.get("status"), "canceled")

            conn = db_connect(paths)
            job = get_job(conn, job_id)
            self.assertEqual(job["status"], "canceled")
            active = get_active_queue_item(conn, job_id=job_id)
            self.assertIsNone(active)
            row = conn.execute("SELECT state FROM job_run_queue WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
            conn.close()
            self.assertEqual(str(row["state"]), "canceled")

            # rerun should be allowed after cancellation
            rerun = handle_command(
                command_text="rerun",
                work_root=work_root,
                kb_root=kb_root,
                target=sender,
                sender=sender,
                dry_run_notify=True,
            )
            self.assertTrue(rerun["ok"])
            self.assertEqual(rerun.get("status"), "queued")

    @patch("scripts.skill_approval.send_message")
    def test_cancel_requests_kill_for_running_job(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "Translation Task"
            kb_root = Path(tmp) / "Knowledge Repository"
            kb_root.mkdir(parents=True, exist_ok=True)
            sender = "+8613"

            job_id = "job_test_cancel_running"
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
            update_job_status(conn, job_id=job_id, status="running", errors=[])
            set_sender_active_job(conn, sender=sender, job_id=job_id)
            now = conn.execute("SELECT datetime('now')").fetchone()[0]  # sqlite current time
            conn.execute(
                """
                INSERT INTO job_run_queue(
                  job_id, state, attempt, notify_target, created_by_sender,
                  enqueued_at, started_at, heartbeat_at, worker_id, pipeline_pid, pipeline_pgid
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (job_id, "running", 1, sender, sender, now, now, now, "w1", 123, 123),
            )
            conn.commit()
            conn.close()

            with patch("scripts.skill_approval.os.killpg") as mocked_killpg:
                result = handle_command(
                    command_text="cancel please",
                    work_root=work_root,
                    kb_root=kb_root,
                    target=sender,
                    sender=sender,
                    dry_run_notify=True,
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result.get("kill_sent"))
            mocked_killpg.assert_any_call(123, signal.SIGTERM)
            mocked_killpg.assert_any_call(123, signal.SIGKILL)

            conn = db_connect(paths)
            row = conn.execute(
                "SELECT cancel_requested_at FROM job_run_queue WHERE job_id=? AND state='running' ORDER BY id DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            conn.close()
            self.assertTrue(str(row["cancel_requested_at"] or "").strip())

    @patch("scripts.skill_approval.send_message")
    def test_ok_marks_verified_without_delivery_copy(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "Translation Task"
            kb_root = Path(tmp) / "Knowledge Repository"
            kb_root.mkdir(parents=True, exist_ok=True)
            job_id = "job_test_ok"
            inbox = work_root / "_INBOX" / "telegram" / job_id
            inbox.mkdir(parents=True, exist_ok=True)
            create_job(
                source="telegram",
                sender="+8613",
                subject="Test",
                message_text="status",
                inbox_dir=inbox,
                job_id=job_id,
                work_root=work_root,
            )
            paths = ensure_runtime_paths(work_root)
            review_dir = paths.review_root / job_id
            final_dir = review_dir / "FinalUploads"
            final_dir.mkdir(parents=True, exist_ok=True)
            final_file = final_dir / "MyFinal.docx"
            final_file.write_text("final", encoding="utf-8")
            conn = db_connect(paths)
            set_job_kb_company(conn, job_id=job_id, kb_company="Eventranz")
            add_job_final_upload(conn, job_id=job_id, sender="+8613", path=final_file)
            conn.close()

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

            conn = db_connect(paths)
            job = get_job(conn, job_id)
            conn.close()
            self.assertEqual(job["status"], "verified")
            delivered = list((work_root / "Translated -EN").glob("*.docx"))
            self.assertEqual(len(delivered), 0)
            archived = list((kb_root / "30_Reference" / "Eventranz").rglob("MyFinal.docx"))
            self.assertEqual(len(archived), 1)

    @patch("scripts.skill_approval.send_message")
    def test_no_marks_needs_revision(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "Translation Task"
            kb_root = Path(tmp) / "Knowledge Repository"
            kb_root.mkdir(parents=True, exist_ok=True)
            job_id = "job_test_no"
            inbox = work_root / "_INBOX" / "telegram" / job_id
            inbox.mkdir(parents=True, exist_ok=True)
            create_job(
                source="telegram",
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

    @patch("scripts.skill_approval.send_message")
    def test_status_finds_review_ready_job_with_require_new(self, mocked_send):
        """status should find a review_ready job even when OPENCLAW_REQUIRE_NEW=1."""
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "Translation Task"
            kb_root = Path(tmp) / "Knowledge Repository"
            kb_root.mkdir(parents=True, exist_ok=True)
            job_id = "job_test_status_rr"
            inbox = work_root / "_INBOX" / "telegram" / job_id
            inbox.mkdir(parents=True, exist_ok=True)
            create_job(
                source="telegram",
                sender="+8613",
                subject="Test",
                message_text="",
                inbox_dir=inbox,
                job_id=job_id,
                work_root=work_root,
            )
            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)
            update_job_status(conn, job_id=job_id, status="review_ready", errors=[])
            set_sender_active_job(conn, sender="+8613", job_id=job_id)
            conn.close()

            with patch.dict("os.environ", {"OPENCLAW_REQUIRE_NEW": "1"}, clear=False):
                result = handle_command(
                    command_text="status",
                    work_root=work_root,
                    kb_root=kb_root,
                    target="+8613",
                    sender="+8613",
                    dry_run_notify=True,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result["job_id"], job_id)
            self.assertEqual(result["status"], "review_ready")

    @patch("scripts.skill_approval.send_message")
    def test_new_cleans_empty_previous_review_folder(self, mocked_send):
        """Sending 'new' should remove the previous job's empty _VERIFY folder."""
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "Translation Task"
            kb_root = Path(tmp) / "Knowledge Repository"
            kb_root.mkdir(parents=True, exist_ok=True)

            # Create first job with an empty review folder
            first_result = handle_command(
                command_text="new first task",
                work_root=work_root,
                kb_root=kb_root,
                target="+8613",
                sender="+8613",
                dry_run_notify=True,
            )
            first_job_id = first_result["job_id"]
            paths = ensure_runtime_paths(work_root)
            review_dir = paths.review_root / first_job_id
            # Ensure review dir exists but has only .system
            (review_dir / ".system").mkdir(parents=True, exist_ok=True)
            self.assertTrue(review_dir.is_dir())

            # Create second job — should clean up the empty first review folder
            handle_command(
                command_text="new second task",
                work_root=work_root,
                kb_root=kb_root,
                target="+8613",
                sender="+8613",
                dry_run_notify=True,
            )
            self.assertFalse(review_dir.exists())


if __name__ == "__main__":
    unittest.main()

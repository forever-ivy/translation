#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.skill_approval import handle_command
from scripts.v4_pipeline import create_job, attach_file_to_job
from scripts.v4_runtime import (
    db_connect,
    ensure_runtime_paths,
    get_active_queue_item,
    get_job,
    list_job_files,
    set_job_kb_company,
    set_sender_active_job,
)


class SkillApprovalBatchSplitTest(unittest.TestCase):
    def _make_job_with_files(self, *, work_root: Path, kb_root: Path, sender: str, job_id: str, paths: list[Path]) -> None:
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
        for p in paths:
            attach_file_to_job(work_root=work_root, job_id=job_id, path=p)

        rt = ensure_runtime_paths(work_root)
        conn = db_connect(rt)
        set_job_kb_company(conn, job_id=job_id, kb_company="Eventranz")
        set_sender_active_job(conn, sender=sender, job_id=job_id)
        conn.close()

    @patch("scripts.skill_approval.send_message")
    def test_run_splits_multi_xlsx_into_child_jobs_and_enqueues(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            kb_root = Path(td) / "Knowledge Repository"
            (kb_root / "30_Reference" / "Eventranz").mkdir(parents=True, exist_ok=True)
            sender = "+8613"

            src1 = work_root / "_INBOX" / "sources" / "A.xlsx"
            src2 = work_root / "_INBOX" / "sources" / "B.xlsx"
            src1.parent.mkdir(parents=True, exist_ok=True)
            src1.write_bytes(b"")
            src2.write_bytes(b"")

            job_id = "job_test_batch_parent"
            self._make_job_with_files(work_root=work_root, kb_root=kb_root, sender=sender, job_id=job_id, paths=[src1, src2])

            with patch.dict("os.environ", {"OPENCLAW_RUN_SPLIT_MULTI_XLSX": "1"}, clear=False):
                result = handle_command(
                    command_text="run",
                    work_root=work_root,
                    kb_root=kb_root,
                    target=sender,
                    sender=sender,
                    dry_run_notify=True,
                )
            self.assertTrue(result["ok"])
            self.assertEqual(result.get("status"), "batch_dispatched")
            batch = result.get("batch") or {}
            children = batch.get("child_jobs") or []
            self.assertEqual(len(children), 2)

            rt = ensure_runtime_paths(work_root)
            conn = db_connect(rt)
            parent = get_job(conn, job_id)
            self.assertIsNotNone(parent)
            self.assertEqual(parent["status"], "batch_dispatched")

            # Each child job exists, has 1 attached file, and has an active queue item.
            for entry in children:
                cid = str(entry.get("child_job_id") or "")
                self.assertTrue(cid.startswith("job_"))
                child = get_job(conn, cid)
                self.assertIsNotNone(child)
                self.assertEqual(str(child.get("status")), "queued")
                files = list_job_files(conn, cid)
                self.assertEqual(len(files), 1)
                q = get_active_queue_item(conn, job_id=cid)
                self.assertIsNotNone(q)
            conn.close()

    @patch("scripts.skill_approval.send_message")
    def test_run_does_not_split_when_mixed_types(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            kb_root = Path(td) / "Knowledge Repository"
            (kb_root / "30_Reference" / "Eventranz").mkdir(parents=True, exist_ok=True)
            sender = "+8613"

            src_xlsx = work_root / "_INBOX" / "sources" / "A.xlsx"
            src_docx = work_root / "_INBOX" / "sources" / "B.docx"
            src_xlsx.parent.mkdir(parents=True, exist_ok=True)
            src_xlsx.write_bytes(b"")
            src_docx.write_bytes(b"fake docx")

            job_id = "job_test_mixed_no_split"
            self._make_job_with_files(work_root=work_root, kb_root=kb_root, sender=sender, job_id=job_id, paths=[src_xlsx, src_docx])

            with patch.dict("os.environ", {"OPENCLAW_RUN_SPLIT_MULTI_XLSX": "1"}, clear=False):
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

            rt = ensure_runtime_paths(work_root)
            conn = db_connect(rt)
            parent = get_job(conn, job_id)
            self.assertEqual(parent["status"], "queued")
            q = get_active_queue_item(conn, job_id=job_id)
            self.assertIsNotNone(q)
            conn.close()

    @patch("scripts.skill_approval.send_message")
    def test_run_does_not_split_single_xlsx(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            kb_root = Path(td) / "Knowledge Repository"
            (kb_root / "30_Reference" / "Eventranz").mkdir(parents=True, exist_ok=True)
            sender = "+8613"

            src1 = work_root / "_INBOX" / "sources" / "A.xlsx"
            src1.parent.mkdir(parents=True, exist_ok=True)
            src1.write_bytes(b"")

            job_id = "job_test_single_no_split"
            self._make_job_with_files(work_root=work_root, kb_root=kb_root, sender=sender, job_id=job_id, paths=[src1])

            with patch.dict("os.environ", {"OPENCLAW_RUN_SPLIT_MULTI_XLSX": "1"}, clear=False):
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

    @patch("scripts.skill_approval.send_message")
    def test_run_again_on_batch_parent_is_idempotent(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            kb_root = Path(td) / "Knowledge Repository"
            (kb_root / "30_Reference" / "Eventranz").mkdir(parents=True, exist_ok=True)
            sender = "+8613"

            src1 = work_root / "_INBOX" / "sources" / "A.xlsx"
            src2 = work_root / "_INBOX" / "sources" / "B.xlsx"
            src1.parent.mkdir(parents=True, exist_ok=True)
            src1.write_bytes(b"")
            src2.write_bytes(b"")

            job_id = "job_test_batch_idempotent"
            self._make_job_with_files(work_root=work_root, kb_root=kb_root, sender=sender, job_id=job_id, paths=[src1, src2])

            with patch.dict("os.environ", {"OPENCLAW_RUN_SPLIT_MULTI_XLSX": "1"}, clear=False):
                first = handle_command(
                    command_text="run",
                    work_root=work_root,
                    kb_root=kb_root,
                    target=sender,
                    sender=sender,
                    dry_run_notify=True,
                )
                self.assertTrue(first["ok"])
                self.assertEqual(first.get("status"), "batch_dispatched")

                second = handle_command(
                    command_text="run",
                    work_root=work_root,
                    kb_root=kb_root,
                    target=sender,
                    sender=sender,
                    dry_run_notify=True,
                )
                self.assertTrue(second["ok"])
                # should not error out with invalid status
                self.assertIn(str(second.get("status") or ""), {"batch_dispatched", "queued", "running"})

    @patch("scripts.skill_approval.send_message")
    def test_cancel_batch_parent_cancels_children(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            kb_root = Path(td) / "Knowledge Repository"
            (kb_root / "30_Reference" / "Eventranz").mkdir(parents=True, exist_ok=True)
            sender = "+8613"

            src1 = work_root / "_INBOX" / "sources" / "A.xlsx"
            src2 = work_root / "_INBOX" / "sources" / "B.xlsx"
            src1.parent.mkdir(parents=True, exist_ok=True)
            src1.write_bytes(b"")
            src2.write_bytes(b"")

            job_id = "job_test_batch_cancel"
            self._make_job_with_files(work_root=work_root, kb_root=kb_root, sender=sender, job_id=job_id, paths=[src1, src2])

            with patch.dict("os.environ", {"OPENCLAW_RUN_SPLIT_MULTI_XLSX": "1"}, clear=False):
                run_res = handle_command(
                    command_text="run",
                    work_root=work_root,
                    kb_root=kb_root,
                    target=sender,
                    sender=sender,
                    dry_run_notify=True,
                )
            self.assertTrue(run_res["ok"])
            self.assertEqual(run_res.get("status"), "batch_dispatched")
            children = (run_res.get("batch") or {}).get("child_jobs") or []
            self.assertEqual(len(children), 2)

            cancel_res = handle_command(
                command_text="cancel",
                work_root=work_root,
                kb_root=kb_root,
                target=sender,
                sender=sender,
                dry_run_notify=True,
            )
            self.assertTrue(cancel_res["ok"])
            self.assertEqual(cancel_res.get("status"), "canceled")

            rt = ensure_runtime_paths(work_root)
            conn = db_connect(rt)
            parent = get_job(conn, job_id)
            self.assertEqual(parent["status"], "canceled")
            for entry in children:
                cid = str(entry.get("child_job_id") or "")
                child = get_job(conn, cid)
                self.assertEqual(child["status"], "canceled")
                q = get_active_queue_item(conn, job_id=cid)
                self.assertIsNone(q)
                row = conn.execute(
                    "SELECT state FROM job_run_queue WHERE job_id=? ORDER BY id DESC LIMIT 1",
                    (cid,),
                ).fetchone()
                self.assertEqual(str(row["state"]), "canceled")
            conn.close()

    @patch("scripts.skill_approval.send_message")
    def test_status_redirects_from_batch_child_to_parent_overview(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            kb_root = Path(td) / "Knowledge Repository"
            (kb_root / "30_Reference" / "Eventranz").mkdir(parents=True, exist_ok=True)
            sender = "+8613"

            src1 = work_root / "_INBOX" / "sources" / "A.xlsx"
            src2 = work_root / "_INBOX" / "sources" / "B.xlsx"
            src1.parent.mkdir(parents=True, exist_ok=True)
            src1.write_bytes(b"")
            src2.write_bytes(b"")

            job_id = "job_test_batch_status_redirect"
            self._make_job_with_files(work_root=work_root, kb_root=kb_root, sender=sender, job_id=job_id, paths=[src1, src2])

            with patch.dict("os.environ", {"OPENCLAW_RUN_SPLIT_MULTI_XLSX": "1"}, clear=False):
                run_res = handle_command(
                    command_text="run",
                    work_root=work_root,
                    kb_root=kb_root,
                    target=sender,
                    sender=sender,
                    dry_run_notify=True,
                )
            children = (run_res.get("batch") or {}).get("child_jobs") or []
            self.assertEqual(len(children), 2)
            child_id = str(children[0].get("child_job_id") or "")

            rt = ensure_runtime_paths(work_root)
            conn = db_connect(rt)
            set_sender_active_job(conn, sender=sender, job_id=child_id)
            conn.close()

            status_res = handle_command(
                command_text="status",
                work_root=work_root,
                kb_root=kb_root,
                target=sender,
                sender=sender,
                dry_run_notify=True,
            )
            self.assertTrue(status_res["ok"])
            self.assertEqual(status_res.get("job_id"), job_id)
            # Ensure status message contains batch overview line.
            sent_msg = mocked_send.call_args.kwargs.get("message", "")
            self.assertIn("Batch:", sent_msg)

    @patch("scripts.skill_approval.send_message")
    def test_new_prefills_company_from_last(self, mocked_send):
        mocked_send.return_value = {"ok": True}
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            kb_root = Path(td) / "Knowledge Repository"
            (kb_root / "30_Reference" / "Eventranz").mkdir(parents=True, exist_ok=True)
            sender = "+8613"

            # Seed a prior job with kb_company.
            job_id = "job_test_company_seed"
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
            rt = ensure_runtime_paths(work_root)
            conn = db_connect(rt)
            set_job_kb_company(conn, job_id=job_id, kb_company="Eventranz")
            conn.close()

            with patch.dict("os.environ", {"OPENCLAW_PREFILL_COMPANY_FROM_LAST": "1"}, clear=False):
                created = handle_command(
                    command_text="new",
                    work_root=work_root,
                    kb_root=kb_root,
                    target=sender,
                    sender=sender,
                    dry_run_notify=True,
                )
            self.assertTrue(created["ok"])
            new_job_id = str(created["job_id"])
            conn = db_connect(rt)
            new_job = get_job(conn, new_job_id)
            conn.close()
            self.assertEqual(str(new_job.get("kb_company") or ""), "Eventranz")


if __name__ == "__main__":
    unittest.main()

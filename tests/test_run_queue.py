#!/usr/bin/env python3

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.v4_runtime import (
    claim_next_queued,
    db_connect,
    enqueue_run_job,
    ensure_runtime_paths,
    finish_queue_item,
    get_active_queue_item,
    requeue_stuck_running,
    write_job,
)


class RunQueueTest(unittest.TestCase):
    def _make_job(self, conn, *, work_root: Path, job_id: str) -> None:
        paths = ensure_runtime_paths(work_root)
        inbox_dir = paths.inbox_messaging / job_id
        review_dir = paths.review_root / job_id
        inbox_dir.mkdir(parents=True, exist_ok=True)
        review_dir.mkdir(parents=True, exist_ok=True)
        write_job(
            conn,
            job_id=job_id,
            source="telegram",
            sender="+1",
            subject="Test",
            message_text="",
            status="received",
            inbox_dir=inbox_dir,
            review_dir=review_dir,
        )

    def test_enqueue_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)
            job_id = "job_queue_idempotent"
            self._make_job(conn, work_root=work_root, job_id=job_id)

            q1 = enqueue_run_job(conn, job_id=job_id, notify_target="+1", created_by_sender="+1")
            q2 = enqueue_run_job(conn, job_id=job_id, notify_target="+1", created_by_sender="+1")
            self.assertEqual(int(q1["id"]), int(q2["id"]))
            self.assertEqual(str(q1["state"]), "queued")
            conn.close()

    def test_claim_is_exclusive(self):
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)
            job_id = "job_queue_claim"
            self._make_job(conn, work_root=work_root, job_id=job_id)
            enqueue_run_job(conn, job_id=job_id, notify_target="+1", created_by_sender="+1")
            conn.close()

            conn1 = db_connect(paths)
            item1 = claim_next_queued(conn1, worker_id="w1")
            conn1.close()
            self.assertIsNotNone(item1)
            self.assertEqual(str(item1.get("state")), "running")

            conn2 = db_connect(paths)
            item2 = claim_next_queued(conn2, worker_id="w2")
            conn2.close()
            self.assertIsNone(item2)

    def test_claim_syncs_job_status_to_running(self):
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)
            job_id = "job_queue_status_sync"
            self._make_job(conn, work_root=work_root, job_id=job_id)
            conn.execute(
                "UPDATE jobs SET status='planned', updated_at=datetime('now') WHERE job_id=?",
                (job_id,),
            )
            enqueue_run_job(conn, job_id=job_id, notify_target="+1", created_by_sender="+1")

            claimed = claim_next_queued(conn, worker_id="w1")
            self.assertIsNotNone(claimed)
            row = conn.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(str(row["status"]), "running")
            conn.close()

    def test_requeue_stuck_running(self):
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)
            job_id = "job_queue_requeue"
            self._make_job(conn, work_root=work_root, job_id=job_id)
            enqueue_run_job(conn, job_id=job_id, notify_target="+1", created_by_sender="+1")
            claimed = claim_next_queued(conn, worker_id="w1")
            self.assertIsNotNone(claimed)
            qid = int(claimed["id"])

            old = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
            conn.execute("UPDATE job_run_queue SET heartbeat_at=? WHERE id=?", (old, qid))
            conn.commit()

            changed = requeue_stuck_running(conn, stuck_seconds=10, max_attempts=3)
            self.assertGreaterEqual(changed, 1)
            active = get_active_queue_item(conn, job_id=job_id) or {}
            self.assertEqual(str(active.get("state") or ""), "queued")
            conn.close()

    def test_finish_queue_item_updates_planned_job_to_failed(self):
        with tempfile.TemporaryDirectory() as td:
            work_root = Path(td) / "Translation Task"
            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)
            job_id = "job_queue_finish_sync"
            self._make_job(conn, work_root=work_root, job_id=job_id)
            conn.execute(
                "UPDATE jobs SET status='planned', updated_at=datetime('now') WHERE job_id=?",
                (job_id,),
            )
            enqueue_run_job(conn, job_id=job_id, notify_target="+1", created_by_sender="+1")
            claimed = claim_next_queued(conn, worker_id="w1")
            self.assertIsNotNone(claimed)

            finish_queue_item(
                conn,
                queue_id=int(claimed["id"]),
                worker_id="w1",
                state="failed",
                last_error="unit_test_failure",
            )
            row = conn.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(str(row["status"]), "failed")
            conn.close()


if __name__ == "__main__":
    unittest.main()

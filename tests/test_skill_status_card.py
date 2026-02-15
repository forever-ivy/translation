#!/usr/bin/env python3

import json
import tempfile
import unittest
from pathlib import Path

from scripts.skill_status_card import build_status_card, no_active_job_hint


class SkillStatusCardTest(unittest.TestCase):
    def test_build_card_contains_expected_lines(self):
        job = {
            "job_id": "job_123",
            "status": "collecting",
            "task_type": "REVISION_UPDATE",
            "review_dir": "",
            "iteration_count": 1,
            "double_pass": False,
            "errors_json": [],
        }
        card = build_status_card(job=job, files_count=3, docx_count=3, multiple_hint=1, require_new=True)
        self.assertIn("New task", card)
        self.assertIn("+1 pending", card)
        self.assertIn("Collecting", card)
        self.assertIn("Files: 3", card)
        self.assertIn("Rounds: 1", card)
        self.assertIn("Next: run", card)

    def test_build_card_shows_job_id_after_classification(self):
        job = {
            "job_id": "job_456",
            "status": "running",
            "task_type": "SPREADSHEET_TRANSLATION",
            "review_dir": "",
            "iteration_count": 0,
            "double_pass": False,
            "errors_json": [],
        }
        card = build_status_card(job=job, files_count=1, docx_count=0, require_new=True)
        self.assertIn("job_456", card)

    def test_build_card_shows_task_label_when_available(self):
        job = {
            "job_id": "job_789",
            "status": "running",
            "review_dir": "",
            "iteration_count": 0,
            "errors_json": [],
        }
        card = build_status_card(job=job, files_count=1, docx_count=0, task_label="Translate Salt Field report")
        self.assertIn("Translate Salt Field report", card)
        self.assertNotIn("job_789", card)

    def test_no_active_job_hint_require_new(self):
        self.assertIn("No active task", no_active_job_hint(require_new=True))
        self.assertIn("new", no_active_job_hint(require_new=True))
        self.assertIn("No active task", no_active_job_hint(require_new=False))
        self.assertIn("run", no_active_job_hint(require_new=False))

    def test_needs_attention_card_includes_why_from_flags(self):
        job = {
            "job_id": "job_why_flags",
            "status": "needs_attention",
            "review_dir": "/tmp/openclaw_job_why_flags",
            "iteration_count": 2,
            "errors_json": [],
            "status_flags_json": ["format_qa_failed"],
            "artifacts_json": {},
        }
        card = build_status_card(job=job, files_count=1, docx_count=0, require_new=True)
        self.assertIn("Why:", card)
        self.assertIn("Format QA failed", card)

    def test_needs_attention_card_falls_back_to_quality_report_unresolved(self):
        with tempfile.TemporaryDirectory() as td:
            review_dir = Path(td)
            system_dir = review_dir / ".system"
            system_dir.mkdir(parents=True, exist_ok=True)
            (system_dir / "quality_report.json").write_text(
                json.dumps(
                    {"rounds": [{"round": 1, "unresolved": ["term_mismatch:foo", "numbering_mismatch:1.2"]}]},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            job = {
                "job_id": "job_why_report",
                "status": "needs_attention",
                "review_dir": str(review_dir),
                "iteration_count": 1,
                "errors_json": [],
                "status_flags_json": [],
                "artifacts_json": {},
            }
            card = build_status_card(job=job, files_count=1, docx_count=0, require_new=True)
            self.assertIn("Why:", card)
            self.assertIn("term_mismatch:foo", card)

    def test_queued_card_shows_queue_and_last_milestone(self):
        job = {
            "job_id": "job_q",
            "status": "queued",
            "review_dir": "",
            "iteration_count": 0,
            "errors_json": [],
        }
        card = build_status_card(
            job=job,
            files_count=0,
            docx_count=0,
            require_new=True,
            queue_state="queued",
            queue_attempt=1,
            last_milestone="run_enqueued",
            last_milestone_at="2026-01-01T00:00:00+00:00",
        )
        self.assertIn("Stage: Queued", card)
        self.assertIn("Queue: queued", card)
        self.assertIn("Last: run_enqueued", card)


if __name__ == "__main__":
    unittest.main()

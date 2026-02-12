#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

from docx import Document

from scripts.openclaw_translation_orchestrator import run


def _make_docx(path: Path, text: str) -> None:
    doc = Document()
    doc.add_paragraph(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


class OpenClawTranslationOrchestratorTest(unittest.TestCase):
    def test_revision_update_reaches_review_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Translation Task"
            review = root / "Translated -EN" / "_REVIEW" / "job_1"
            ar_dir = root / "Arabic Source"
            prev_dir = root / "Previously Translated"
            _make_docx(ar_dir / "v1 استبانة.docx", "نص عربي إصدار 1")
            _make_docx(ar_dir / "v2 استبانة.docx", "نص عربي إصدار 2 مع تعديلات")
            _make_docx(prev_dir / "V1 AI Readiness Survey.docx", "English baseline v1")

            meta = {
                "job_id": "job_1",
                "root_path": str(root),
                "review_dir": str(review),
                "candidate_files": [
                    {
                        "path": str(ar_dir / "v1 استبانة.docx"),
                        "name": "v1 استبانة.docx",
                        "language": "ar",
                        "version": "v1",
                        "role": "source",
                    },
                    {
                        "path": str(ar_dir / "v2 استبانة.docx"),
                        "name": "v2 استبانة.docx",
                        "language": "ar",
                        "version": "v2",
                        "role": "source",
                    },
                    {
                        "path": str(prev_dir / "V1 AI Readiness Survey.docx"),
                        "name": "V1 AI Readiness Survey.docx",
                        "language": "en",
                        "version": "v1",
                        "role": "reference_translation",
                    },
                ],
            }

            out = run(meta)
            self.assertEqual(out["status"], "review_pending")
            self.assertTrue(out["double_pass"])
            self.assertGreaterEqual(out["iteration_count"], 1)
            self.assertLessEqual(out["iteration_count"], 3)
            self.assertIn("draft_a_docx", out["artifacts"])

    def test_incomplete_input_when_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = Path(tmp) / "r"
            out = run({"job_id": "job_empty", "review_dir": str(review), "candidate_files": []})
            self.assertEqual(out["status"], "incomplete_input")
            self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()


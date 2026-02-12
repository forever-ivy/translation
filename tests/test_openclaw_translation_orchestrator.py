#!/usr/bin/env python3

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

from scripts.openclaw_translation_orchestrator import run


def _make_docx(path: Path, text: str) -> None:
    doc = Document()
    doc.add_paragraph(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


def _agent_ok(text: dict) -> dict:
    return {
        "ok": True,
        "agent_id": "mock",
        "payload": {},
        "text": json.dumps(text, ensure_ascii=False),
    }


class OpenClawTranslationOrchestratorTest(unittest.TestCase):
    @patch("scripts.openclaw_translation_orchestrator._agent_call")
    def test_revision_update_reaches_review_ready(self, mocked_call):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Translation Task"
            review = root / "Translated -EN" / "_VERIFY" / "job_1"
            ar_dir = root / "Arabic Source"
            prev_dir = root / "Previously Translated"
            _make_docx(ar_dir / "v1 استبانة.docx", "نص عربي إصدار 1")
            _make_docx(ar_dir / "v2 استبانة.docx", "نص عربي إصدار 2 مع تعديلات")
            _make_docx(prev_dir / "V1 AI Readiness Survey.docx", "English baseline v1")

            mocked_call.side_effect = [
                _agent_ok(
                    {
                        "task_type": "REVISION_UPDATE",
                        "source_language": "ar",
                        "target_language": "en",
                        "required_inputs": ["arabic_old", "arabic_new", "english_baseline"],
                        "missing_inputs": [],
                        "confidence": 0.97,
                        "reasoning_summary": "Detected AR v1+v2 and EN baseline.",
                        "estimated_minutes": 14,
                        "complexity_score": 34,
                    }
                ),
                _agent_ok(
                    {
                        "draft_a_text": "Draft A content",
                        "draft_b_text": "Draft B content",
                        "final_text": "Final content",
                        "final_reflow_text": "Final reflow",
                        "review_brief_points": ["Review numbering."],
                        "change_log_points": ["Applied Arabic V2 changes."],
                        "resolved": [],
                        "unresolved": [],
                        "codex_pass": True,
                        "reasoning_summary": "Initial draft done.",
                    }
                ),
                _agent_ok(
                    {
                        "findings": ["none"],
                        "resolved": [],
                        "unresolved": [],
                        "pass": True,
                        "terminology_rate": 0.96,
                        "structure_complete_rate": 0.96,
                        "target_language_purity": 0.98,
                        "numbering_consistency": 0.97,
                        "reasoning_summary": "Looks good.",
                    }
                ),
                _agent_ok(
                    {
                        "draft_a_text": "Draft A content revised",
                        "draft_b_text": "Draft B content revised",
                        "final_text": "Final content revised",
                        "final_reflow_text": "Final reflow revised",
                        "review_brief_points": ["Review numbering."],
                        "change_log_points": ["Applied Arabic V2 changes."],
                        "resolved": ["none"],
                        "unresolved": [],
                        "codex_pass": True,
                        "reasoning_summary": "Fixed all findings.",
                    }
                ),
                _agent_ok(
                    {
                        "findings": [],
                        "resolved": ["none"],
                        "unresolved": [],
                        "pass": True,
                        "terminology_rate": 0.97,
                        "structure_complete_rate": 0.97,
                        "target_language_purity": 0.98,
                        "numbering_consistency": 0.98,
                        "reasoning_summary": "Pass.",
                    }
                ),
            ]

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
            self.assertEqual(out["status"], "review_ready")
            self.assertTrue(out["double_pass"])
            self.assertGreaterEqual(out["iteration_count"], 1)
            self.assertLessEqual(out["iteration_count"], 3)
            self.assertIn("final_docx", out["artifacts"])
            self.assertTrue(Path(out["artifacts"]["final_docx"]).exists())

    @patch("scripts.openclaw_translation_orchestrator._agent_call")
    def test_missing_inputs_status(self, mocked_call):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Translation Task"
            review = root / "Translated -EN" / "_VERIFY" / "job_missing"
            prev_dir = root / "Previously Translated"
            _make_docx(prev_dir / "V1 AI Readiness Survey.docx", "English baseline v1")

            mocked_call.return_value = _agent_ok(
                {
                    "task_type": "REVISION_UPDATE",
                    "source_language": "ar",
                    "target_language": "en",
                    "required_inputs": ["arabic_old", "arabic_new", "english_baseline"],
                    "missing_inputs": ["arabic_old", "arabic_new"],
                    "confidence": 0.88,
                    "reasoning_summary": "Arabic files missing.",
                    "estimated_minutes": 10,
                    "complexity_score": 18,
                }
            )

            out = run(
                {
                    "job_id": "job_missing",
                    "root_path": str(root),
                    "review_dir": str(review),
                    "candidate_files": [
                        {
                            "path": str(prev_dir / "V1 AI Readiness Survey.docx"),
                            "name": "V1 AI Readiness Survey.docx",
                            "language": "en",
                            "version": "v1",
                            "role": "reference_translation",
                        }
                    ],
                },
                plan_only=False,
            )
            self.assertEqual(out["status"], "missing_inputs")
            self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()

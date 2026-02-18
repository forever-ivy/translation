#!/usr/bin/env python3

import os
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

from scripts.openclaw_translation_orchestrator import _agent_call, _available_slots, run


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
    @patch("scripts.openclaw_translation_orchestrator.subprocess.run")
    def test_agent_call_enforces_thinking_level(self, mocked_run):
        mocked_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"result":{"payloads":[{"text":"{}"}]}}',
            stderr="",
        )
        out = _agent_call("translator-core", "ping", timeout_seconds=90)
        self.assertTrue(out.get("ok"))
        called_cmd = mocked_run.call_args.args[0]
        self.assertIn("--thinking", called_cmd)
        self.assertIn("high", called_cmd)

    @patch("scripts.openclaw_translation_orchestrator.subprocess.run")
    def test_agent_call_parses_embedded_stdout_with_log_prefix(self, mocked_run):
        # Newer OpenClaw runtimes may emit a non-JSON log line prefix and return a
        # top-level payload structure (no "result" wrapper).
        stdout = (
            "[agent/embedded] google tool schema snapshot\n"
            "{\n"
            "  \"payloads\": [\n"
            "    {\n"
            "      \"text\": \"{\\\"hello\\\": \\\"world\\\"}\",\n"
            "      \"mediaUrl\": null\n"
            "    }\n"
            "  ],\n"
            "  \"meta\": {\"durationMs\": 1}\n"
            "}\n"
        )
        mocked_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
        )
        out = _agent_call("translator-core", "ping", timeout_seconds=90)
        self.assertTrue(out.get("ok"))
        self.assertEqual(out.get("text"), '{"hello": "world"}')

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
                        "required_inputs": ["source_old", "source_new", "target_baseline"],
                        "missing_inputs": [],
                        "confidence": 0.97,
                        "reasoning_summary": "Detected AR v1+v2 and EN baseline.",
                        "estimated_minutes": 14,
                        "complexity_score": 34,
                    }
                ),
                # Codex candidate
                _agent_ok(
                    {
                        "final_text": "Final content",
                        "final_reflow_text": "Final reflow",
                        "docx_translation_map": [{"id": "p:1", "text": "Final content"}],
                        "review_brief_points": ["Review numbering."],
                        "change_log_points": ["Applied Arabic V2 changes."],
                        "resolved": [],
                        "unresolved": [],
                        "codex_pass": True,
                        "reasoning_summary": "Initial draft done.",
                    }
                ),
                # GLM candidate
                _agent_ok(
                    {
                        "final_text": "Final content (GLM)",
                        "final_reflow_text": "Final reflow (GLM)",
                        "docx_translation_map": [{"id": "p:1", "text": "Final content (GLM)"}],
                        "review_brief_points": [],
                        "change_log_points": [],
                        "resolved": [],
                        "unresolved": [],
                        "codex_pass": True,
                        "reasoning_summary": "GLM candidate done.",
                    }
                ),
                # Gemini review (Codex)
                _agent_ok(
                    {
                        "findings": [],
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
                # Gemini review (GLM)
                _agent_ok(
                    {
                        "findings": [],
                        "resolved": [],
                        "unresolved": [],
                        "pass": True,
                        "terminology_rate": 0.95,
                        "structure_complete_rate": 0.95,
                        "target_language_purity": 0.98,
                        "numbering_consistency": 0.98,
                        "reasoning_summary": "Pass.",
                    }
                ),
                # GLM advisory review (after rounds)
                _agent_ok(
                    {
                        "findings": [],
                        "pass": True,
                        "terminology_score": 0.95,
                        "completeness_score": 0.95,
                        "naturalness_score": 0.95,
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

            with patch.dict(os.environ, {"OPENCLAW_GLM_ENABLED": "1"}, clear=False):
                out = run(meta)
            self.assertEqual(out["status"], "review_ready")
            self.assertTrue(out["double_pass"])
            self.assertGreaterEqual(out["iteration_count"], 1)
            self.assertLessEqual(out["iteration_count"], 3)
            self.assertEqual(out["thinking_level"], "high")
            self.assertEqual((out.get("quality_report") or {}).get("thinking_level"), "high")
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
                    "required_inputs": ["source_old", "source_new", "target_baseline"],
                    "missing_inputs": ["source_old", "source_new"],
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

    @patch("scripts.openclaw_translation_orchestrator._agent_call")
    def test_bilingual_proofreading_plan_only(self, mocked_call):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Translation Task"
            review = root / "Translated -EN" / "_VERIFY" / "job_proof"
            _make_docx(root / "english_original.docx", "English original text")
            _make_docx(root / "french_translation.docx", "Texte traduit en français")

            mocked_call.return_value = _agent_ok(
                {
                    "task_type": "BILINGUAL_PROOFREADING",
                    "source_language": "en",
                    "target_language": "fr",
                    "required_inputs": ["source_document", "target_document"],
                    "missing_inputs": [],
                    "confidence": 0.92,
                    "reasoning_summary": "Proofreading EN→FR pair.",
                    "estimated_minutes": 8,
                    "complexity_score": 20,
                }
            )

            out = run(
                {
                    "job_id": "job_proof",
                    "root_path": str(root),
                    "review_dir": str(review),
                    "message_text": "could you kindly review the attached paragraphs and proofread the French version",
                    "candidate_files": [
                        {
                            "path": str(root / "english_original.docx"),
                            "name": "english_original.docx",
                            "language": "en",
                            "version": "unknown",
                            "role": "general",
                        },
                        {
                            "path": str(root / "french_translation.docx"),
                            "name": "french_translation.docx",
                            "language": "fr",
                            "version": "unknown",
                            "role": "general",
                        },
                    ],
                },
                plan_only=True,
            )
            self.assertEqual(out["status"], "planned")
            self.assertEqual(out["intent"]["task_type"], "BILINGUAL_PROOFREADING")
            self.assertGreater(out["intent"]["confidence"], 0.0)


class AvailableSlotsTest(unittest.TestCase):
    def test_french_english_pair(self):
        candidates = [
            {"language": "en", "version": "unknown", "role": "general"},
            {"language": "fr", "version": "unknown", "role": "general"},
        ]
        slots = _available_slots(candidates, source_language="en", target_language="fr")
        self.assertTrue(slots["source_document"])
        self.assertTrue(slots["target_document"])

    def test_single_arabic_file(self):
        candidates = [
            {"language": "ar", "version": "v1", "role": "source"},
        ]
        slots = _available_slots(candidates, source_language="ar", target_language="en")
        self.assertTrue(slots["source_document"])
        self.assertFalse(slots["target_document"])

    def test_empty_candidates(self):
        slots = _available_slots([], source_language="en", target_language="fr")
        self.assertFalse(slots["source_document"])
        self.assertFalse(slots["target_document"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

from docx import Document

from scripts.openclaw_artifact_writer import write_artifacts


def _make_docx(path: Path, text: str) -> None:
    doc = Document()
    doc.add_paragraph(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


class OpenClawArtifactWriterTest(unittest.TestCase):
    def test_write_artifacts_outputs_verify_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = Path(tmp) / "_VERIFY" / "job_1"
            template = Path(tmp) / "english_v1.docx"
            _make_docx(template, "Baseline text")

            manifest = write_artifacts(
                review_dir=str(review),
                draft_a_template_path=str(template),
                delta_pack={"added": [], "removed": [], "modified": [], "summary_by_section": [], "stats": {}},
                model_scores={"judge_margin": 0.1, "term_hit": 0.95},
                quality={"judge_margin": 0.1, "term_hit": 0.95, "expansion_used": False},
                quality_report={"rounds": [], "convergence_reached": True, "stop_reason": "double_pass"},
                job_id="job_1",
                task_type="REVISION_UPDATE",
                confidence=0.93,
                estimated_minutes=12,
                runtime_timeout_minutes=16,
                iteration_count=2,
                double_pass=True,
                status_flags=[],
                candidate_files=[],
                review_questions=["Check table numbering."],
                draft_payload={
                    "draft_a_text": "Draft A line 1",
                    "draft_b_text": "Draft B line 1",
                    "final_text": "Final line 1",
                    "final_reflow_text": "Final reflow line 1",
                    "review_brief_points": ["Focus on Arabic V2 additions."],
                    "change_log_points": ["Updated Section 3 wording."],
                },
                plan_payload={"intent": {"task_type": "REVISION_UPDATE"}, "plan": {"estimated_minutes": 12}},
            )

            self.assertTrue(Path(manifest["final_docx"]).exists())
            self.assertTrue(Path(manifest["final_reflow_docx"]).exists())
            self.assertTrue(Path(manifest["draft_a_docx"]).exists())
            self.assertTrue(Path(manifest["draft_b_docx"]).exists())
            self.assertTrue(Path(manifest["review_brief_docx"]).exists())
            self.assertTrue(Path(manifest["change_log_md"]).exists())
            self.assertTrue(Path(manifest["execution_plan_json"]).exists())
            self.assertTrue(Path(manifest["quality_report_json"]).exists())


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.format_qa_vision import run_format_qa_loop


def _write_dummy_pngs(dir_path: Path, *, count: int) -> list[Path]:
    dir_path.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for idx in range(count):
        p = dir_path / f"sheet_{idx + 1}.png"
        p.write_bytes(b"fake_png")
        out.append(p)
    return out


class FormatQaVisionTest(unittest.TestCase):
    def test_aesthetics_warning_does_not_fail_when_fidelity_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            review_dir = Path(tmp) / "qa"
            orig_images = _write_dummy_pngs(review_dir / "original", count=2)
            trans_images = _write_dummy_pngs(review_dir / "translated", count=2)

            with (
                patch("scripts.format_qa_vision.render_xlsx_to_images", side_effect=[orig_images, trans_images]),
                patch(
                    "scripts.format_qa_vision.compare_format_visual",
                    return_value={
                        "format_fidelity_score": 0.9,
                        "aesthetics_score": 0.6,
                        "discrepancies": [],
                        "aesthetic_issues": [{"location": "A1", "issue": "text a bit dense", "severity": "low"}],
                    },
                ),
                patch.dict(
                    os.environ,
                    {
                        "OPENCLAW_FORMAT_QA_THRESHOLD": "0.85",
                        "OPENCLAW_VISION_AESTHETICS_WARN_THRESHOLD": "0.7",
                        "OPENCLAW_FORMAT_QA_SHEETS_MAX": "6",
                    },
                    clear=False,
                ),
            ):
                out = run_format_qa_loop(
                    original_xlsx=Path("orig.xlsx"),
                    translated_xlsx=Path("trans.xlsx"),
                    review_dir=review_dir,
                    max_retries=0,
                )
                self.assertEqual(out["status"], "passed")
                self.assertTrue(out["aesthetics_warning"])
                self.assertGreaterEqual(out["format_fidelity_min"], 0.85)

    def test_sheet_count_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            review_dir = Path(tmp) / "qa"
            orig_images = _write_dummy_pngs(review_dir / "original", count=2)
            trans_images = _write_dummy_pngs(review_dir / "translated", count=1)

            with (
                patch("scripts.format_qa_vision.render_xlsx_to_images", side_effect=[orig_images, trans_images]),
                patch(
                    "scripts.format_qa_vision.compare_format_visual",
                    return_value={"format_fidelity_score": 0.95, "aesthetics_score": 0.95, "discrepancies": [], "aesthetic_issues": []},
                ),
                patch.dict(
                    os.environ,
                    {
                        "OPENCLAW_FORMAT_QA_THRESHOLD": "0.85",
                        "OPENCLAW_VISION_AESTHETICS_WARN_THRESHOLD": "0.7",
                        "OPENCLAW_FORMAT_QA_SHEETS_MAX": "6",
                    },
                    clear=False,
                ),
            ):
                out = run_format_qa_loop(
                    original_xlsx=Path("orig.xlsx"),
                    translated_xlsx=Path("trans.xlsx"),
                    review_dir=review_dir,
                    max_retries=0,
                )
                self.assertEqual(out["status"], "failed")
                self.assertTrue(out["sheet_count_mismatch"])

    def test_soffice_missing_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            review_dir = Path(tmp) / "qa"
            with patch(
                "scripts.format_qa_vision.render_xlsx_to_images",
                side_effect=RuntimeError("LibreOffice (soffice) not found"),
            ):
                out = run_format_qa_loop(
                    original_xlsx=Path("orig.xlsx"),
                    translated_xlsx=Path("trans.xlsx"),
                    review_dir=review_dir,
                    max_retries=0,
                )
                self.assertEqual(out["status"], "skipped")
                self.assertIn(out.get("reason"), {"soffice_missing", "render_failed"})


if __name__ == "__main__":
    unittest.main()

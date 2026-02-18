#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.docx_qa_vision import run_docx_qa


class DocxQaVisionTest(unittest.TestCase):
    def test_soffice_missing_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            review_dir = Path(tmp) / "qa"
            with patch(
                "scripts.docx_qa_vision.render_docx_to_images",
                side_effect=RuntimeError("LibreOffice (soffice) not found"),
            ):
                out = run_docx_qa(
                    original_docx=Path("orig.docx"),
                    translated_docx=Path("trans.docx"),
                    review_dir=review_dir,
                    max_pages=2,
                )
                self.assertEqual(out["status"], "skipped")
                self.assertIn(out.get("reason"), {"soffice_missing", "render_failed"})


if __name__ == "__main__":
    unittest.main()


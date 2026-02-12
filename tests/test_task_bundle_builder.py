#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

from scripts.task_bundle_builder import build_bundle


class TaskBundleBuilderTest(unittest.TestCase):
    def test_valid_with_any_docx(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Arabic Source").mkdir(parents=True, exist_ok=True)
            (root / "Arabic Source" / "input_ar.docx").write_text("x", encoding="utf-8")
            bundle = build_bundle(root, "job_1")
            self.assertTrue(bundle["valid"])
            self.assertGreaterEqual(bundle["stats"]["doc_count"], 1)

    def test_invalid_when_no_docx(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = build_bundle(root, "job_2")
            self.assertFalse(bundle["valid"])
            self.assertIn("no_docx_found", bundle["missing"])


if __name__ == "__main__":
    unittest.main()


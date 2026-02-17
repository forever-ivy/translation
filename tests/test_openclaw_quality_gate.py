#!/usr/bin/env python3

import unittest

from scripts.openclaw_quality_gate import (
    check_preservation_fidelity,
    compute_runtime_timeout,
    evaluate_quality,
    evaluate_round,
)


class OpenClawQualityGateTest(unittest.TestCase):
    def test_expansion_true_on_low_margin(self):
        model_scores = {"judge_margin": 0.02, "term_hit": 0.95}
        delta_pack = {"added": [], "modified": []}
        out = evaluate_quality(model_scores, delta_pack)
        self.assertTrue(out["expansion_used"])

    def test_expansion_false_on_good_scores(self):
        model_scores = {"judge_margin": 0.12, "term_hit": 0.96}
        delta_pack = {"added": [1], "modified": [1]}
        out = evaluate_quality(model_scores, delta_pack)
        self.assertFalse(out["expansion_used"])

    def test_runtime_timeout_with_cap(self):
        timeout, flags = compute_runtime_timeout(estimated_minutes=40)
        self.assertEqual(timeout, 45)
        self.assertIn("long_task_capped", flags)

    def test_round_pass_when_metrics_good(self):
        out = evaluate_round(
            round_index=1,
            previous_unresolved=[],
            metrics={
                "terminology_rate": 0.96,
                "structure_complete_rate": 0.97,
                "target_language_purity": 0.98,
                "numbering_consistency": 0.97,
                "hard_fail_items": [],
            },
            gemini_enabled=True,
        )
        self.assertTrue(out["pass"])


class PreservationFidelityTest(unittest.TestCase):
    def test_passes_when_all_preserved(self):
        draft = {
            "docx_translation_map": [
                {"id": "p:0", "text": "Hello World"},
                {"id": "p:1", "text": "Good morning"},
            ]
        }
        preserved = {
            "p:0": "Hello World",
            "p:1": "Good morning",
        }
        passed, fidelity, errors = check_preservation_fidelity(draft, preserved)
        self.assertTrue(passed)
        self.assertEqual(fidelity, 1.0)
        self.assertEqual(len(errors), 0)

    def test_fails_when_text_modified(self):
        draft = {
            "docx_translation_map": [
                {"id": "p:0", "text": "Hello Universe"},  # Changed
                {"id": "p:1", "text": "Good morning"},
            ]
        }
        preserved = {
            "p:0": "Hello World",
            "p:1": "Good morning",
        }
        passed, fidelity, errors = check_preservation_fidelity(draft, preserved)
        self.assertFalse(passed)
        self.assertLess(fidelity, 1.0)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["unit_id"], "p:0")

    def test_handles_missing_unit(self):
        draft = {
            "docx_translation_map": [
                {"id": "p:0", "text": "Hello World"},
                # p:1 is missing
            ]
        }
        preserved = {
            "p:0": "Hello World",
            "p:1": "Good morning",
        }
        passed, fidelity, errors = check_preservation_fidelity(draft, preserved)
        self.assertFalse(passed)
        self.assertEqual(len(errors), 1)

    def test_empty_preserved_map(self):
        draft = {"docx_translation_map": [{"id": "p:0", "text": "Hello"}]}
        passed, fidelity, errors = check_preservation_fidelity(draft, {})
        self.assertTrue(passed)
        self.assertEqual(fidelity, 1.0)

    def test_normalizes_whitespace(self):
        draft = {
            "docx_translation_map": [
                {"id": "p:0", "text": "Hello   World"},  # Extra spaces
            ]
        }
        preserved = {
            "p:0": "Hello World",
        }
        passed, fidelity, errors = check_preservation_fidelity(draft, preserved)
        self.assertTrue(passed)  # Should pass after normalization

    def test_evaluate_round_with_preservation(self):
        draft = {
            "docx_translation_map": [
                {"id": "p:0", "text": "Hello World"},
            ]
        }
        preserved = {"p:0": "Hello World"}

        out = evaluate_round(
            round_index=1,
            previous_unresolved=[],
            metrics={
                "terminology_rate": 0.96,
                "structure_complete_rate": 0.97,
                "target_language_purity": 0.98,
                "numbering_consistency": 0.97,
                "hard_fail_items": [],
            },
            gemini_enabled=True,
            draft=draft,
            preserved_text_map=preserved,
        )
        self.assertTrue(out["pass"])
        self.assertEqual(out["metrics"]["preservation_fidelity"], 1.0)

    def test_evaluate_round_fails_on_preservation_violation(self):
        draft = {
            "docx_translation_map": [
                {"id": "p:0", "text": "Wrong text"},
            ]
        }
        preserved = {"p:0": "Correct text"}

        out = evaluate_round(
            round_index=1,
            previous_unresolved=[],
            metrics={
                "terminology_rate": 0.96,
                "structure_complete_rate": 0.97,
                "target_language_purity": 0.98,
                "numbering_consistency": 0.97,
                "hard_fail_items": [],
            },
            gemini_enabled=True,
            draft=draft,
            preserved_text_map=preserved,
        )
        self.assertFalse(out["pass"])
        self.assertIn("preservation_fidelity_below_threshold", out["findings"])


if __name__ == "__main__":
    unittest.main()

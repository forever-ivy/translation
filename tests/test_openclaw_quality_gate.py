#!/usr/bin/env python3

import unittest

from scripts.openclaw_quality_gate import compute_runtime_timeout, evaluate_quality, evaluate_round


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


if __name__ == "__main__":
    unittest.main()

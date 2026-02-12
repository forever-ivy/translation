#!/usr/bin/env python3

import unittest

from scripts.openclaw_artifact_writer import build_task_brief


class OpenClawArtifactWriterTest(unittest.TestCase):
    def test_build_task_brief_contains_metrics(self):
        text = build_task_brief(
            job_id="job_1",
            task_type="REVISION_UPDATE",
            confidence=0.95,
            estimated_minutes=20,
            runtime_timeout_minutes=26,
            iteration_count=2,
            double_pass=True,
            status_flags=["long_task_capped"],
            delta_pack={"added": [1, 2], "removed": [1], "modified": [1, 2, 3]},
            quality={"judge_margin": 0.11, "term_hit": 0.95, "expansion_used": True},
        )
        self.assertIn("Job: job_1", text)
        self.assertIn("Task Type: REVISION_UPDATE", text)
        self.assertIn("Added blocks: 2", text)
        self.assertIn("Expansion used: True", text)


if __name__ == "__main__":
    unittest.main()

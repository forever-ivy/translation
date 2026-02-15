#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

from scripts.v4_runtime import add_memory, db_connect, ensure_runtime_paths, search_memories


class MemoriesTest(unittest.TestCase):
    def test_company_scoped_memory_store_and_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_root = Path(tmp) / "Translation Task"
            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)
            add_memory(conn, company="Eventranz", kind="decision", text="DCU = Data Collection Unit", job_id="job1")
            add_memory(conn, company="OtherClient", kind="decision", text="Other secret term", job_id="job2")

            hits = search_memories(conn, company="Eventranz", query="DCU Data Collection", top_k=5)
            self.assertGreaterEqual(len(hits), 1)
            for h in hits:
                self.assertEqual(h.get("company"), "Eventranz")

            hits2 = search_memories(conn, company="OtherClient", query="secret", top_k=5)
            self.assertGreaterEqual(len(hits2), 1)
            for h in hits2:
                self.assertEqual(h.get("company"), "OtherClient")

            # Empty company should not return anything (strict isolation).
            self.assertEqual(search_memories(conn, company="", query="DCU", top_k=5), [])
            conn.close()


if __name__ == "__main__":
    unittest.main()


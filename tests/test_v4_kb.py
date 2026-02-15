#!/usr/bin/env python3

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.v4_kb import _extract_pdf, _extract_xlsx, retrieve_kb, retrieve_kb_with_fallback, sync_kb, sync_kb_with_rag
from scripts.v4_runtime import db_connect, ensure_runtime_paths


class V4KnowledgeBaseTest(unittest.TestCase):
    def test_incremental_sync_and_retrieve(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            work_root = base / "Translation Task"
            kb_root = base / "Knowledge Repository"
            (kb_root / "00_Glossary" / "Eventranz").mkdir(parents=True, exist_ok=True)
            (kb_root / "00_Glossary" / "OtherClient").mkdir(parents=True, exist_ok=True)
            (kb_root / "10_Style_Guide" / "Eventranz").mkdir(parents=True, exist_ok=True)
            (kb_root / "20_Domain_Knowledge" / "Eventranz").mkdir(parents=True, exist_ok=True)
            (kb_root / "40_Templates" / "Eventranz").mkdir(parents=True, exist_ok=True)
            (kb_root / "30_Reference" / "Eventranz" / "2024-02_AI_Readiness" / "final").mkdir(parents=True, exist_ok=True)

            (kb_root / "00_Glossary" / "Eventranz" / "terms.txt").write_text("Siraj platform\nAI readiness\n", encoding="utf-8")
            (kb_root / "00_Glossary" / "OtherClient" / "terms.txt").write_text("Other secret term\n", encoding="utf-8")
            (kb_root / "10_Style_Guide" / "Eventranz" / "translation_rules.md").write_text("Keep headings.\nPreserve numbering.\n", encoding="utf-8")
            (kb_root / "30_Reference" / "Eventranz" / "2024-02_AI_Readiness" / "final" / "ref.csv").write_text(
                "term,translation\nAI readiness,AI readiness\n", encoding="utf-8"
            )
            (kb_root / "20_Domain_Knowledge" / "Eventranz" / "task.md").write_text("This is the source text for translation update", encoding="utf-8")

            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)

            report1 = sync_kb(conn=conn, kb_root=kb_root, report_path=paths.kb_system_root / "kb_sync_latest.json")
            self.assertTrue(report1["ok"])
            self.assertGreaterEqual(report1["created"], 3)

            report2 = sync_kb(conn=conn, kb_root=kb_root, report_path=paths.kb_system_root / "kb_sync_latest.json")
            self.assertTrue(report2["ok"])
            self.assertGreaterEqual(report2["skipped"], 3)

            hits = retrieve_kb(
                conn=conn,
                query="AI readiness Siraj",
                task_type="REVISION_UPDATE",
                top_k=5,
                kb_root=kb_root,
                kb_company="Eventranz",
                isolation_mode="company_strict",
            )
            self.assertGreaterEqual(len(hits), 1)
            self.assertIn("score", hits[0])
            self.assertIn("source_group", hits[0])
            for hit in hits:
                self.assertIn("/Eventranz/", str(hit.get("path")))

            with patch("scripts.v4_kb.clawrag_search") as mocked_clawrag:
                hit_path = str((kb_root / "00_Glossary" / "Eventranz" / "terms.txt").resolve())
                mocked_clawrag.return_value = {
                    "ok": True,
                    "backend": "clawrag",
                    "hits": [{"path": hit_path, "source_group": "glossary", "chunk_index": 0, "snippet": "AI readiness", "score": 0.9}],
                }
                rag = retrieve_kb_with_fallback(
                    conn=conn,
                    query="AI readiness",
                    task_type="REVISION_UPDATE",
                    rag_backend="clawrag",
                    rag_base_url="http://127.0.0.1:8080",
                    rag_collection="translation-kb",
                    kb_root=kb_root,
                    kb_company="Eventranz",
                    isolation_mode="company_strict",
                )
                self.assertEqual(rag["backend"], "merged")
                self.assertGreaterEqual(len(rag["hits"]), 1)
                self.assertTrue(any(str(h.get("path")) == hit_path for h in rag["hits"]))
                self.assertIn("rerank_report", rag.get("rag_result") or {})

            with patch("scripts.v4_kb.clawrag_search") as mocked_clawrag:
                mocked_clawrag.return_value = {"ok": False, "backend": "clawrag", "hits": [], "errors": ["down"]}
                rag = retrieve_kb_with_fallback(
                    conn=conn,
                    query="AI readiness",
                    task_type="REVISION_UPDATE",
                    rag_backend="clawrag",
                    rag_base_url="http://127.0.0.1:8080",
                    rag_collection="translation-kb",
                )
                self.assertEqual(rag["backend"], "local")
                self.assertGreaterEqual(len(rag["hits"]), 1)
                self.assertIn("rag_fallback_local", rag["status_flags"])
            conn.close()

    def test_sync_kb_with_rag_calls_delete_on_removed_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            work_root = base / "Translation Task"
            kb_root = base / "Knowledge Repository"
            (kb_root / "00_Glossary" / "Eventranz").mkdir(parents=True, exist_ok=True)
            kb_file = kb_root / "00_Glossary" / "Eventranz" / "terms.txt"
            kb_file.write_text("AI readiness\n", encoding="utf-8")

            paths = ensure_runtime_paths(work_root)
            conn = db_connect(paths)

            with patch("scripts.v4_kb.clawrag_sync") as mocked_sync, patch("scripts.v4_kb.clawrag_delete") as mocked_delete:
                mocked_sync.return_value = {"ok": True, "uploaded_count": 1}
                mocked_delete.return_value = {"ok": True, "deleted_count": 0}
                report1 = sync_kb_with_rag(conn=conn, kb_root=kb_root, rag_backend="clawrag")
                self.assertTrue(report1["local_report"]["ok"])
                self.assertTrue(mocked_sync.called)
                self.assertTrue(mocked_delete.called)

                kb_file.unlink(missing_ok=True)
                mocked_delete.reset_mock()
                mocked_sync.reset_mock()

                report2 = sync_kb_with_rag(conn=conn, kb_root=kb_root, rag_backend="clawrag")
                self.assertTrue(report2["local_report"]["ok"])
                removed = list(report2["local_report"].get("removed_paths") or [])
                self.assertEqual(len(removed), 1)
                self.assertTrue(mocked_delete.called)
                matched = False
                for _, kwargs in mocked_delete.call_args_list:
                    if "removed_paths" in kwargs and len(kwargs.get("removed_paths") or []) == 1:
                        matched = True
                        break
                self.assertTrue(matched)
            conn.close()

    def test_merge_rerank_enforces_glossary_min_and_terminology_ratio(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            kb_root = base / "Knowledge Repository"
            company = "Eventranz"

            glossary_file = kb_root / "00_Glossary" / company / "terms.txt"
            ref_file = kb_root / "30_Reference" / company / "Proj" / "final" / "ref.txt"
            glossary_file.parent.mkdir(parents=True, exist_ok=True)
            ref_file.parent.mkdir(parents=True, exist_ok=True)
            glossary_file.write_text("t\n", encoding="utf-8")
            ref_file.write_text("r\n", encoding="utf-8")

            def mk_hit(path: Path, *, chunk: int, source_group: str) -> dict:
                return {
                    "path": str(path.resolve()),
                    "source_group": source_group,
                    "chunk_index": chunk,
                    "snippet": f"{source_group}-{chunk}",
                    "score": 1.0,
                }

            rag_hits = [mk_hit(ref_file, chunk=i, source_group="previously_translated") for i in range(20)]
            # 3 glossary candidates (local-only) — put them early so they survive top_k_local=12 truncation.
            local_hits = [mk_hit(glossary_file, chunk=100 + i, source_group="glossary") for i in range(3)]
            local_hits.extend([mk_hit(ref_file, chunk=i, source_group="previously_translated") for i in range(12)])

            with patch("scripts.v4_kb.clawrag_search") as mocked_rag, patch("scripts.v4_kb.retrieve_kb") as mocked_local:
                mocked_rag.return_value = {"ok": True, "backend": "clawrag", "hits": rag_hits}
                mocked_local.return_value = local_hits

                with patch.dict(
                    os.environ,
                    {
                        "OPENCLAW_KB_RERANK_FINAL_K": "12",
                        "OPENCLAW_KB_RERANK_GLOSSARY_MIN": "3",
                        "OPENCLAW_KB_RERANK_TERMINOLOGY_GLOSSARY_RATIO": "0.4",
                    },
                    clear=False,
                ):
                    merged = retrieve_kb_with_fallback(
                        conn=None,  # patched retrieve_kb does not use conn
                        query="test",
                        task_type="NEW_TRANSLATION",
                        rag_backend="clawrag",
                        rag_collection="translation-kb",
                        kb_root=kb_root,
                        kb_company=company,
                        isolation_mode="company_strict",
                    )
                    self.assertEqual(merged["backend"], "merged")
                    report = (merged.get("rag_result") or {}).get("rerank_report") or {}
                    self.assertEqual(int(report.get("forced_glossary") or 0), 3)
                    self.assertGreaterEqual(int((report.get("selected_by_source_group") or {}).get("glossary") or 0), 3)

                    term = retrieve_kb_with_fallback(
                        conn=None,
                        query="test",
                        task_type="TERMINOLOGY_ENFORCEMENT",
                        rag_backend="clawrag",
                        rag_collection="translation-kb",
                        kb_root=kb_root,
                        kb_company=company,
                        isolation_mode="company_strict",
                    )
                    report2 = (term.get("rag_result") or {}).get("rerank_report") or {}
                    # 40% of 12 => 5 glossary forced (capped by availability; we provided 3 local-only)
                    self.assertEqual(int(report2.get("glossary_needed") or 0), 3)

    def test_merge_rerank_targets_rag_local_ratio(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            kb_root = base / "Knowledge Repository"
            company = "Eventranz"
            ref_file = kb_root / "30_Reference" / company / "Proj" / "final" / "ref.txt"
            local_file = kb_root / "20_Domain_Knowledge" / company / "domain.txt"
            ref_file.parent.mkdir(parents=True, exist_ok=True)
            local_file.parent.mkdir(parents=True, exist_ok=True)
            ref_file.write_text("r\n", encoding="utf-8")
            local_file.write_text("d\n", encoding="utf-8")

            def mk_hit(path: Path, *, chunk: int, source_group: str) -> dict:
                return {
                    "path": str(path.resolve()),
                    "source_group": source_group,
                    "chunk_index": chunk,
                    "snippet": f"{source_group}-{chunk}",
                    "score": 1.0,
                }

            rag_hits = [mk_hit(ref_file, chunk=i, source_group="previously_translated") for i in range(30)]
            local_hits = [mk_hit(local_file, chunk=i, source_group="general") for i in range(30)]

            with patch("scripts.v4_kb.clawrag_search") as mocked_rag, patch("scripts.v4_kb.retrieve_kb") as mocked_local:
                mocked_rag.return_value = {"ok": True, "backend": "clawrag", "hits": rag_hits}
                mocked_local.return_value = local_hits

                with patch.dict(os.environ, {"OPENCLAW_KB_RERANK_FINAL_K": "12"}, clear=False):
                    merged = retrieve_kb_with_fallback(
                        conn=None,
                        query="test",
                        task_type="NEW_TRANSLATION",
                        rag_backend="clawrag",
                        rag_collection="translation-kb",
                        kb_root=kb_root,
                        kb_company=company,
                        isolation_mode="company_strict",
                    )
                    report = (merged.get("rag_result") or {}).get("rerank_report") or {}
                    self.assertEqual(int(report.get("rag_target") or 0), 7)
                    self.assertEqual(int(report.get("local_target") or 0), 5)
                    self.assertGreaterEqual(int(report.get("selected_rag") or 0), 6)
                    self.assertGreaterEqual(int(report.get("selected_local") or 0), 4)


class PdfExtractTest(unittest.TestCase):
    def test_pdftotext_preferred_when_available(self):
        fake_pdf = Path(tempfile.mktemp(suffix=".pdf"))
        fake_pdf.write_bytes(b"dummy")
        try:
            with patch("scripts.v4_kb.subprocess.run") as mock_run, \
                 patch("scripts.v4_kb.Path.exists", return_value=True):
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="Extracted layout text from PDF"
                )
                result = _extract_pdf(fake_pdf)
                self.assertIn("Extracted layout text from PDF", result)
                mock_run.assert_called_once()
                args = mock_run.call_args[0][0]
                self.assertEqual(args[0], "/opt/homebrew/bin/pdftotext")
                self.assertIn("-layout", args)
        finally:
            fake_pdf.unlink(missing_ok=True)

    def test_pdftotext_fallback_to_pypdf(self):
        fake_pdf = Path(tempfile.mktemp(suffix=".pdf"))
        fake_pdf.write_bytes(b"dummy")
        try:
            with patch("scripts.v4_kb.subprocess.run") as mock_run, \
                 patch("scripts.v4_kb.Path.exists", return_value=True), \
                 patch("scripts.v4_kb.PdfReader") as mock_reader:
                mock_run.return_value = MagicMock(returncode=1, stdout="")
                mock_page = MagicMock()
                mock_page.extract_text.return_value = "pypdf fallback text"
                mock_reader.return_value.pages = [mock_page]
                result = _extract_pdf(fake_pdf)
                self.assertIn("pypdf fallback text", result)
        finally:
            fake_pdf.unlink(missing_ok=True)


class XlsxExtractTest(unittest.TestCase):
    def test_sheetsmith_preferred_when_available(self):
        fake_xlsx = Path(tempfile.mktemp(suffix=".xlsx"))
        fake_xlsx.write_bytes(b"dummy")
        try:
            with patch("scripts.v4_kb.subprocess.run") as mock_run, \
                 patch("scripts.v4_kb.SHEETSMITH_SCRIPT", new=Path("/tmp/fake_sheetsmith.py")), \
                 patch.object(Path, "exists", return_value=True):
                mock_run.return_value = MagicMock(
                    returncode=0, stdout="Sheet1\ncol1 | col2\nval1 | val2"
                )
                result = _extract_xlsx(fake_xlsx)
                self.assertIn("col1", result)
                mock_run.assert_called_once()
        finally:
            fake_xlsx.unlink(missing_ok=True)

    def test_sheetsmith_fallback_to_openpyxl(self):
        fake_xlsx = Path(tempfile.mktemp(suffix=".xlsx"))
        fake_xlsx.write_bytes(b"dummy")
        try:
            with patch("scripts.v4_kb.SHEETSMITH_SCRIPT", new=Path("/nonexistent/sheetsmith.py")), \
                 patch("scripts.v4_kb.load_workbook") as mock_wb:
                mock_ws = MagicMock()
                mock_ws.title = "Sheet1"
                mock_ws.iter_rows.return_value = [("a", "b"), ("c", None)]
                mock_wb.return_value.worksheets = [mock_ws]
                mock_wb.return_value.close = MagicMock()
                result = _extract_xlsx(fake_xlsx)
                self.assertIn("Sheet1", result)
                self.assertIn("a | b", result)
        finally:
            fake_xlsx.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

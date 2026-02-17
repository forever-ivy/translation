#!/usr/bin/env python3
"""Tests for revision pack module."""

import unittest

from scripts.revision_pack import (
    RevisionPack,
    _alignment_to_dict,
    _extract_modified_ids_from_delta,
    build_revision_pack,
    format_revision_context_for_prompt,
)
from scripts.paragraph_aligner import AlignmentMatch


class TestExtractModifiedIdsFromDelta(unittest.TestCase):
    def test_extracts_from_modified_blocks(self):
        delta = {
            "modified": [
                {"v1_range": [0, 2], "v2_range": [0, 2], "before": ["A", "B"], "after": ["X", "Y"]}
            ],
            "added": [],
            "removed": [],
        }
        ids = _extract_modified_ids_from_delta(delta)
        self.assertIn("p:0", ids)
        self.assertIn("p:1", ids)

    def test_empty_delta(self):
        delta = {"modified": [], "added": [], "removed": []}
        ids = _extract_modified_ids_from_delta(delta)
        self.assertEqual(len(ids), 0)


class TestAlignmentToDict(unittest.TestCase):
    def test_converts_match(self):
        match = AlignmentMatch(
            source_id="p:0",
            target_id="p:0",
            confidence=0.9,
            source_text="Arabic",
            target_text="English",
            source_index=0,
            target_index=0,
        )
        d = _alignment_to_dict(match)
        self.assertEqual(d["source_id"], "p:0")
        self.assertEqual(d["target_id"], "p:0")
        self.assertEqual(d["confidence"], 0.9)
        self.assertEqual(d["source_text"], "Arabic")
        self.assertEqual(d["target_text"], "English")


class TestRevisionPack(unittest.TestCase):
    def test_to_dict(self):
        pack = RevisionPack(
            delta={"job_id": "test"},
            alignment=[],
            english_to_update=[],
            english_to_preserve=[],
            preserved_text_map={"p:0": "Hello"},
            new_sections=[],
            modified_ids={"p:1"},
        )
        d = pack.to_dict()
        self.assertIn("delta", d)
        self.assertIn("preserved_text_map", d)
        self.assertIn("modified_ids", d)
        # Set should be converted to list
        self.assertIsInstance(d["modified_ids"], list)

    def test_to_json(self):
        pack = RevisionPack(
            delta={"job_id": "test"},
            alignment=[],
            english_to_update=[],
            english_to_preserve=[],
            preserved_text_map={"p:0": "Hello"},
            new_sections=[],
            modified_ids=set(),
        )
        json_str = pack.to_json()
        self.assertIn("delta", json_str)
        self.assertIn("preserved_text_map", json_str)


class TestBuildRevisionPack(unittest.TestCase):
    def test_builds_pack_from_structures(self):
        arabic_v1 = {
            "blocks": [
                {"kind": "paragraph", "text": "مرحبا"},
                {"kind": "paragraph", "text": "عالم"},
            ]
        }
        arabic_v2 = {
            "blocks": [
                {"kind": "paragraph", "text": "مرحبا"},  # Unchanged
                {"kind": "paragraph", "text": "العالم"},  # Changed
                {"kind": "paragraph", "text": "جديد"},  # New
            ]
        }
        english_v1 = {
            "blocks": [
                {"kind": "paragraph", "text": "Hello"},
                {"kind": "paragraph", "text": "World"},
            ]
        }

        pack = build_revision_pack(arabic_v1, arabic_v2, english_v1, job_id="test_job")

        self.assertEqual(pack.delta["job_id"], "test_job")
        self.assertGreater(len(pack.alignment), 0)

        # Check delta has changes detected
        self.assertIn("stats", pack.delta)

    def test_handles_empty_structures(self):
        empty = {"blocks": []}
        pack = build_revision_pack(empty, empty, empty, job_id="test")
        self.assertEqual(len(pack.alignment), 0)
        self.assertEqual(len(pack.preserved_text_map), 0)


class TestFormatRevisionContextForPrompt(unittest.TestCase):
    def test_formats_preserved_sections(self):
        pack = RevisionPack(
            delta={},
            alignment=[],
            english_to_update=[],
            english_to_preserve=[
                {"source_id": "p:0", "target_id": "p:0", "target_text": "Hello"}
            ],
            preserved_text_map={"p:0": "Hello"},
            new_sections=[],
            modified_ids=set(),
        )
        prompt = format_revision_context_for_prompt(pack)
        self.assertIn("UNCHANGED SECTIONS", prompt)
        self.assertIn("Hello", prompt)

    def test_formats_sections_to_translate(self):
        pack = RevisionPack(
            delta={},
            alignment=[],
            english_to_update=[
                {"source_id": "p:1", "target_id": "p:1", "target_text": "Old text"}
            ],
            english_to_preserve=[],
            preserved_text_map={},
            new_sections=[],
            modified_ids={"p:1"},
        )
        prompt = format_revision_context_for_prompt(pack)
        self.assertIn("SECTIONS TO TRANSLATE", prompt)

    def test_formats_new_sections(self):
        pack = RevisionPack(
            delta={},
            alignment=[],
            english_to_update=[],
            english_to_preserve=[],
            preserved_text_map={},
            new_sections=[{"index": 5, "text": "New content"}],
            modified_ids=set(),
        )
        prompt = format_revision_context_for_prompt(pack)
        self.assertIn("NEW SECTIONS", prompt)
        self.assertIn("New content", prompt)

    def test_includes_summary(self):
        pack = RevisionPack(
            delta={},
            alignment=[],
            english_to_update=[],
            english_to_preserve=[],
            preserved_text_map={},
            new_sections=[],
            modified_ids=set(),
        )
        prompt = format_revision_context_for_prompt(pack)
        self.assertIn("REVISION SUMMARY", prompt)


if __name__ == "__main__":
    unittest.main()

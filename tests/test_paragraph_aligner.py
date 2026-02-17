#!/usr/bin/env python3
"""Tests for paragraph alignment module."""

import unittest

from scripts.paragraph_aligner import (
    AlignmentMatch,
    align_by_position,
    align_paragraphs,
    build_block_map,
    _generate_block_id,
    _normalize_for_comparison,
    _similarity_score,
)


class TestNormalizeForComparison(unittest.TestCase):
    def test_normalizes_whitespace(self):
        self.assertEqual(_normalize_for_comparison("  hello  world  "), "hello world")

    def test_normalizes_nbsp(self):
        self.assertEqual(_normalize_for_comparison("hello\u00a0world"), "hello world")

    def test_lowercases(self):
        self.assertEqual(_normalize_for_comparison("Hello World"), "hello world")


class TestSimilarityScore(unittest.TestCase):
    def test_identical_texts(self):
        score = _similarity_score("hello world", "hello world")
        self.assertEqual(score, 1.0)

    def test_completely_different(self):
        score = _similarity_score("hello world", "foo bar")
        self.assertLess(score, 0.5)

    def test_empty_texts(self):
        score = _similarity_score("", "hello")
        self.assertEqual(score, 0.0)


class TestGenerateBlockId(unittest.TestCase):
    def test_paragraph_id(self):
        block = {"kind": "paragraph", "text": "test"}
        self.assertEqual(_generate_block_id(block, 5), "p:5")

    def test_table_row_id(self):
        block = {"kind": "table_row", "text": "test", "row": 3}
        self.assertEqual(_generate_block_id(block, 2), "t:2:r:3")


class TestAlignByPosition(unittest.TestCase):
    def test_aligns_same_length_lists(self):
        source = [
            {"kind": "paragraph", "text": "A"},
            {"kind": "paragraph", "text": "B"},
        ]
        target = [
            {"kind": "paragraph", "text": "1"},
            {"kind": "paragraph", "text": "2"},
        ]
        matches = align_by_position(source, target)
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0].source_id, "p:0")
        self.assertEqual(matches[0].target_id, "p:0")

    def test_respects_min_confidence(self):
        source = [{"kind": "paragraph", "text": "A"}]
        target = [{"kind": "paragraph", "text": "1"}]
        # Position-based alignment gives ~0.7 confidence for matching kinds
        matches_high = align_by_position(source, target, min_confidence=0.5)
        self.assertEqual(len(matches_high), 1)  # Should match with lower threshold

        matches_low = align_by_position(source, target, min_confidence=0.9)
        self.assertEqual(len(matches_low), 0)  # Should not match with high threshold

    def test_handles_empty_lists(self):
        matches = align_by_position([], [])
        self.assertEqual(len(matches), 0)


class TestAlignParagraphs(unittest.TestCase):
    def test_aligns_structures(self):
        source = {
            "blocks": [
                {"kind": "paragraph", "text": "First para"},
                {"kind": "paragraph", "text": "Second para"},
            ]
        }
        target = {
            "blocks": [
                {"kind": "paragraph", "text": "First translation"},
                {"kind": "paragraph", "text": "Second translation"},
            ]
        }
        matches = align_paragraphs(source, target)
        self.assertEqual(len(matches), 2)

    def test_handles_empty_structures(self):
        matches = align_paragraphs({}, {})
        self.assertEqual(len(matches), 0)


class TestBuildBlockMap(unittest.TestCase):
    def test_builds_map_from_structure(self):
        structure = {
            "blocks": [
                {"kind": "paragraph", "text": "Para 1"},
                {"kind": "paragraph", "text": "Para 2"},
            ]
        }
        block_map = build_block_map(structure)
        self.assertIn("p:0", block_map)
        self.assertIn("p:1", block_map)
        self.assertEqual(block_map["p:0"]["text"], "Para 1")

    def test_includes_table_rows(self):
        structure = {
            "blocks": [
                {
                    "kind": "table",
                    "rows": [
                        ["Cell 1", "Cell 2"],
                        ["Cell 3", "Cell 4"],
                    ],
                }
            ]
        }
        block_map = build_block_map(structure)
        self.assertEqual(len(block_map), 2)


if __name__ == "__main__":
    unittest.main()

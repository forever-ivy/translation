#!/usr/bin/env python3
"""Tests for questionnaire_detector module."""

import unittest
from scripts.questionnaire_detector import (
    detect_response_scale,
    is_likely_question_text,
    is_domain_header,
    extract_questions_from_table,
    detect_questionnaire_table,
    compute_block_checksum,
    compute_structure_checksum,
    QuestionInfo,
    QuestionnaireInfo,
)


class TestDetectResponseScale(unittest.TestCase):
    """Tests for detect_response_scale function."""

    def test_numeric_likert_scale(self):
        """Detect numeric 1-5 Likert scale."""
        cells = ["Question", "1", "2", "3", "4", "5"]
        result = detect_response_scale(cells)
        self.assertEqual(result, ["1", "2", "3", "4", "5"])

    def test_agreement_scale(self):
        """Detect Strongly disagree to Strongly agree scale."""
        cells = ["Item", "Strongly disagree", "Disagree", "Neutral", "Agree", "Strongly Agree"]
        result = detect_response_scale(cells)
        self.assertEqual(len(result), 5)

    def test_development_scale(self):
        """Detect Not Yet to Leading scale."""
        cells = ["Criteria", "Not Yet", "Emerging", "Developing", "Proficient", "Leading"]
        result = detect_response_scale(cells)
        self.assertEqual(len(result), 5)

    def test_no_scale(self):
        """Return empty list when no scale detected."""
        cells = ["Name", "Age", "City", "Country"]
        result = detect_response_scale(cells)
        self.assertEqual(result, [])

    def test_mixed_cells(self):
        """Handle mixed content in header row."""
        cells = ["Question text", "1", "2", "3", "4", "5", "Notes"]
        result = detect_response_scale(cells)
        self.assertEqual(result, ["1", "2", "3", "4", "5"])

    def test_arabic_scale(self):
        """Detect Arabic response scale."""
        cells = ["السؤال", "لم يبدأ", "ناشئ", "متطور", "متمكن", "رائد"]
        result = detect_response_scale(cells)
        self.assertEqual(len(result), 5)


class TestIsLikelyQuestionText(unittest.TestCase):
    """Tests for is_likely_question_text function."""

    def test_regular_question(self):
        """Identify regular question text."""
        self.assertTrue(is_likely_question_text("How often do you practice coding?"))

    def test_statement(self):
        """Identify statement as question-like."""
        self.assertTrue(is_likely_question_text("Teachers demonstrate proficiency in AI tools."))

    def test_numeric_cell(self):
        """Reject purely numeric cells."""
        self.assertFalse(is_likely_question_text("123"))

    def test_likert_value(self):
        """Reject Likert scale values."""
        self.assertFalse(is_likely_question_text("Strongly Agree"))
        self.assertFalse(is_likely_question_text("Not Yet"))

    def test_empty_text(self):
        """Reject empty text."""
        self.assertFalse(is_likely_question_text(""))
        self.assertFalse(is_likely_question_text("   "))

    def test_short_text(self):
        """Reject very short text."""
        self.assertFalse(is_likely_question_text("AB"))


class TestIsDomainHeader(unittest.TestCase):
    """Tests for is_domain_header function."""

    def test_single_cell_domain(self):
        """Detect domain header with single non-empty cell."""
        cells = ["AI Literacy & Foundations", "", "", "", "", ""]
        result = is_domain_header(cells)
        self.assertEqual(result, "AI Literacy & Foundations")

    def test_multi_cell_row_not_domain(self):
        """Multi-cell rows with questions are not domains."""
        cells = ["Question text", "1", "2", "3", "4", "5"]
        result = is_domain_header(cells)
        self.assertIsNone(result)

    def test_empty_row(self):
        """Empty rows are not domains."""
        cells = ["", "", "", ""]
        result = is_domain_header(cells)
        self.assertIsNone(result)


class TestExtractQuestionsFromTable(unittest.TestCase):
    """Tests for extract_questions_from_table function."""

    def test_simple_questionnaire(self):
        """Extract questions from a simple questionnaire."""
        rows = [
            ["Question", "1", "2", "3", "4", "5"],
            ["How often do you code?", "1", "2", "3", "4", "5"],
            ["Do you enjoy debugging?", "1", "2", "3", "4", "5"],
        ]
        info = extract_questions_from_table(rows)
        self.assertTrue(info.is_questionnaire)
        self.assertEqual(info.total_questions, 2)
        self.assertEqual(info.question_ids, ["q:1", "q:2"])

    def test_questionnaire_with_domains(self):
        """Extract questions with domain categorization."""
        rows = [
            ["Item", "Not Yet", "Emerging", "Developing", "Proficient", "Leading"],
            ["Technical Skills", "", "", "", "", ""],
            ["Can write clean code", "Not Yet", "Emerging", "Developing", "Proficient", "Leading"],
            ["Understands design patterns", "Not Yet", "Emerging", "Developing", "Proficient", "Leading"],
            ["Communication", "", "", "", "", ""],
            ["Explains technical concepts clearly", "Not Yet", "Emerging", "Developing", "Proficient", "Leading"],
        ]
        info = extract_questions_from_table(rows)
        self.assertTrue(info.is_questionnaire)
        self.assertEqual(info.total_questions, 3)
        self.assertIn("Technical Skills", info.domains)
        self.assertIn("Communication", info.domains)
        # Questions should have domain-prefixed IDs
        self.assertTrue(any("technical_skills" in qid for qid in info.question_ids))

    def test_non_questionnaire_table(self):
        """Return False for non-questionnaire tables."""
        rows = [
            ["Name", "Age", "City"],
            ["Alice", "30", "New York"],
            ["Bob", "25", "London"],
        ]
        info = extract_questions_from_table(rows)
        self.assertFalse(info.is_questionnaire)
        self.assertEqual(info.total_questions, 0)

    def test_empty_table(self):
        """Handle empty table."""
        info = extract_questions_from_table([])
        self.assertFalse(info.is_questionnaire)

    def test_single_row_table(self):
        """Handle single row table."""
        rows = [["Header only"]]
        info = extract_questions_from_table(rows)
        self.assertFalse(info.is_questionnaire)


class TestDetectQuestionnaireTable(unittest.TestCase):
    """Tests for detect_questionnaire_table function."""

    def test_returns_boolean(self):
        """Return boolean result."""
        rows = [
            ["Question", "1", "2", "3", "4", "5"],
            ["Test question?", "1", "2", "3", "4", "5"],
        ]
        self.assertTrue(detect_questionnaire_table(rows))

    def test_non_questionnaire_returns_false(self):
        """Return False for data tables."""
        rows = [
            ["Name", "Value"],
            ["Item A", "100"],
        ]
        self.assertFalse(detect_questionnaire_table(rows))


class TestComputeBlockChecksum(unittest.TestCase):
    """Tests for compute_block_checksum function."""

    def test_paragraph_checksum(self):
        """Compute checksum for paragraph block."""
        block = {"kind": "paragraph", "text": "Hello world", "style": "Normal"}
        checksum = compute_block_checksum(block)
        self.assertEqual(len(checksum), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in checksum))

    def test_table_checksum(self):
        """Compute checksum for table block."""
        block = {
            "kind": "table",
            "rows": [["A", "B"], ["C", "D"]],
        }
        checksum = compute_block_checksum(block)
        self.assertEqual(len(checksum), 16)

    def test_checksum_stability(self):
        """Same block produces same checksum."""
        block = {"kind": "paragraph", "text": "Test", "style": "Normal"}
        checksum1 = compute_block_checksum(block)
        checksum2 = compute_block_checksum(block)
        self.assertEqual(checksum1, checksum2)

    def test_different_text_different_checksum(self):
        """Different text produces different checksum."""
        block1 = {"kind": "paragraph", "text": "Hello", "style": "Normal"}
        block2 = {"kind": "paragraph", "text": "World", "style": "Normal"}
        self.assertNotEqual(compute_block_checksum(block1), compute_block_checksum(block2))


class TestComputeStructureChecksum(unittest.TestCase):
    """Tests for compute_structure_checksum function."""

    def test_structure_checksum(self):
        """Compute structure checksum for blocks list."""
        blocks = [
            {"kind": "paragraph", "text": "Hello", "style": "Normal"},
            {"kind": "table", "rows": [["A", "B"]]},
        ]
        checksum = compute_structure_checksum(blocks)
        self.assertEqual(len(checksum), 16)

    def test_structure_ignores_text(self):
        """Structure checksum ignores text content."""
        blocks1 = [
            {"kind": "paragraph", "text": "Hello", "style": "Normal"},
            {"kind": "table", "rows": [["A", "B"]]},
        ]
        blocks2 = [
            {"kind": "paragraph", "text": "World", "style": "Normal"},  # Different text
            {"kind": "table", "rows": [["A", "B"]]},
        ]
        # Structure checksums should be the same (same structure)
        self.assertEqual(compute_structure_checksum(blocks1), compute_structure_checksum(blocks2))

    def test_different_structure_different_checksum(self):
        """Different structure produces different checksum."""
        blocks1 = [
            {"kind": "paragraph", "text": "A", "style": "Normal"},
        ]
        blocks2 = [
            {"kind": "paragraph", "text": "A", "style": "Normal"},
            {"kind": "paragraph", "text": "B", "style": "Normal"},
        ]
        self.assertNotEqual(compute_structure_checksum(blocks1), compute_structure_checksum(blocks2))


class TestQuestionInfoDataclass(unittest.TestCase):
    """Tests for QuestionInfo dataclass."""

    def test_to_dict(self):
        """Convert to dictionary."""
        q = QuestionInfo(
            question_id="q:1",
            text="Test question?",
            domain="Test Domain",
            row_index=1,
            response_scale=["1", "2", "3", "4", "5"],
        )
        result = q.to_dict()
        self.assertEqual(result["question_id"], "q:1")
        self.assertEqual(result["text"], "Test question?")
        self.assertEqual(result["domain"], "Test Domain")
        self.assertEqual(result["response_scale"], ["1", "2", "3", "4", "5"])


class TestQuestionnaireInfoDataclass(unittest.TestCase):
    """Tests for QuestionnaireInfo dataclass."""

    def test_to_dict(self):
        """Convert to dictionary."""
        info = QuestionnaireInfo(
            is_questionnaire=True,
            total_questions=3,
            question_ids=["q:1", "q:2", "q:3"],
            domains=["Domain A"],
            response_scale=["1", "2", "3", "4", "5"],
        )
        result = info.to_dict()
        self.assertTrue(result["is_questionnaire"])
        self.assertEqual(result["total_questions"], 3)
        self.assertEqual(result["question_ids"], ["q:1", "q:2", "q:3"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Questionnaire detection module for structured document analysis.

Detects questionnaire-style tables in documents (e.g., Likert scales,
survey forms) and extracts question information with stable identifiers.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any


# Common Likert-style response patterns
LIKERT_PATTERNS = [
    # Numeric scales: 1, 2, 3, 4, 5 or 1-5
    r"^[1-5]$",
    # Frequency scales
    r"^(Never|Rarely|Sometimes|Often|Always)$",
    r"^(Not at all|Rarely|Sometimes|Often|Very often)$",
    # Agreement scales
    r"^(Strongly disagree|Disagree|Neutral|Agree|Strongly agree)$",
    r"^(Strongly Disagree|Disagree|Neutral|Agree|Strongly Agree)$",
    # Development/progress scales
    r"^(Not Yet|Emerging|Developing|Proficient|Leading)$",
    r"^(Not yet|Emerging|Developing|Proficient|Leading)$",
    # Arabic equivalents
    r"^(لم يبدأ|ناشئ|متطور|متمكن|رائد)$",
    r"^(أبداً|نادراً|أحياناً|غالباً|دائماً)$",
    # Boolean
    r"^(Yes|No|NA|N/A)$",
    r"^(نعم|لا|لا ينطبق)$",
]

# Compile patterns for efficiency
_COMPILED_LIKERT = [re.compile(p, re.IGNORECASE) for p in LIKERT_PATTERNS]


@dataclass
class QuestionInfo:
    """Information about a single questionnaire question."""

    question_id: str  # Stable ID (e.g., "q:1", "q:domain:1")
    text: str  # Question text
    domain: str | None = None  # Domain/category if applicable
    row_index: int = 0  # Row index in table
    response_scale: list[str] = field(default_factory=list)  # Available responses

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "text": self.text,
            "domain": self.domain,
            "row_index": self.row_index,
            "response_scale": self.response_scale,
        }


@dataclass
class QuestionnaireInfo:
    """Information about a detected questionnaire."""

    is_questionnaire: bool = False
    total_questions: int = 0
    question_ids: list[str] = field(default_factory=list)
    questions: list[QuestionInfo] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    response_scale: list[str] = field(default_factory=list)
    table_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_questionnaire": self.is_questionnaire,
            "total_questions": self.total_questions,
            "question_ids": self.question_ids,
            "questions": [q.to_dict() for q in self.questions],
            "domains": self.domains,
            "response_scale": self.response_scale,
            "table_index": self.table_index,
        }


def detect_response_scale(cells: list[str]) -> list[str]:
    """Detect Likert-style rating scale from header cells.

    Args:
        cells: List of cell texts from a header row

    Returns:
        List of recognized scale values, empty if no scale detected
    """
    scale: list[str] = []
    for cell in cells:
        cell_text = str(cell or "").strip()
        if not cell_text:
            continue
        # Check if this cell matches a Likert pattern
        for pattern in _COMPILED_LIKERT:
            if pattern.match(cell_text):
                scale.append(cell_text)
                break
    return scale


def is_likely_question_text(text: str) -> bool:
    """Determine if text is likely a question stem.

    Question stems are typically:
    - Non-empty text
    - Not purely numeric
    - Not matching Likert scale values
    - Often longer than a few characters

    Args:
        text: Text to check

    Returns:
        True if text looks like a question stem
    """
    text = str(text or "").strip()
    if not text:
        return False
    if len(text) < 3:
        return False
    # Skip purely numeric cells
    if text.isdigit():
        return False
    # Skip Likert scale values
    for pattern in _COMPILED_LIKERT:
        if pattern.match(text):
            return False
    return True


def is_domain_header(cells: list[str], expected_columns: int = 0) -> str | None:
    """Detect if a row is a domain/category header.

    Domain headers typically:
    - Span multiple columns (often just one non-empty cell)
    - Are not questions themselves
    - Represent categories like "AI Literacy & Foundations"

    Args:
        cells: List of cell texts from a row
        expected_columns: Expected number of columns in questionnaire (including question column)

    Returns:
        Domain name if detected, None otherwise
    """
    # Count non-empty cells
    non_empty = [str(c or "").strip() for c in cells if str(c or "").strip()]

    # If we have expected column count, check if this row matches questionnaire structure
    # Questionnaire rows have: [question_text, response1, response2, ...]
    # Domain headers have: [domain_title, empty, empty, ...]
    if expected_columns > 1:
        # A domain header has only 1 non-empty cell and matches the table width
        if len(non_empty) == 1 and len(cells) == expected_columns:
            text = non_empty[0]
            # Domain headers are typically short title phrases (not questions)
            # They usually don't start with verbs and are shorter
            # Questions often describe behaviors: "Demonstrates AI knowledge"
            # Domain headers are categories: "AI Literacy", "Technical Skills"

            # Heuristic: domain headers are usually short (< 50 chars) and don't contain action words
            text_lower = text.lower()

            # Common verbs that indicate a question/statement, not a domain header
            action_verbs = [
                "demonstrates", "uses", "applies", "shows", "understands",
                "implements", "creates", "analyzes", "evaluates", "designs",
                "develops", "maintains", "manages", "leads", "communicates",
                "can ", "able to", "how ", "what ", "why ", "when ",
                "do you", "are you", "is the", "does the",
            ]

            # If the text starts with or contains action verbs, it's likely a question
            for verb in action_verbs:
                if text_lower.startswith(verb) or f" {verb}" in text_lower:
                    return None

            # Domain headers are typically short and don't end with '?'
            if len(text) < 50 and not text.endswith("?"):
                return text
        return None

    # Fallback for when we don't know expected columns
    # Domain headers often have just 1-2 non-empty cells
    if len(non_empty) == 1:
        text = non_empty[0]
        # Domain headers are typically title-cased phrases
        if is_likely_question_text(text) and len(text) > 5:
            # Check it's not just a number or scale value
            return text

    return None


def extract_questions_from_table(
    rows: list[list[str]],
    table_index: int = 0,
) -> QuestionnaireInfo:
    """Extract questionnaire questions from a table.

    Args:
        rows: Table rows (each row is list of cell texts)
        table_index: Index of this table in document

    Returns:
        QuestionnaireInfo with detected questions
    """
    if not rows or len(rows) < 2:
        return QuestionnaireInfo(is_questionnaire=False, table_index=table_index)

    # Try to detect response scale from first row (header)
    header_cells = rows[0] if rows else []
    detected_scale = detect_response_scale(header_cells)

    # If no scale detected, this might not be a questionnaire
    if not detected_scale:
        return QuestionnaireInfo(is_questionnaire=False, table_index=table_index)

    questions: list[QuestionInfo] = []
    domains: list[str] = []
    current_domain: str | None = None
    question_counter = 0
    domain_question_counter = 0

    # Determine expected column count from header
    expected_columns = len(header_cells) if header_cells else 0

    # Process rows (skip header)
    for row_idx, row in enumerate(rows[1:], start=1):
        if not row:
            continue

        cells = [str(c or "").strip() for c in row]
        non_empty_cells = [c for c in cells if c]

        if not non_empty_cells:
            continue

        # Check if this is a domain header (pass expected columns for better detection)
        domain_name = is_domain_header(cells, expected_columns=expected_columns)
        if domain_name:
            current_domain = domain_name
            if domain_name not in domains:
                domains.append(domain_name)
            domain_question_counter = 0
            continue

        # Check if first cell is a question stem
        first_cell = cells[0] if cells else ""
        if is_likely_question_text(first_cell):
            question_counter += 1
            domain_question_counter += 1

            # Generate stable question ID
            if current_domain:
                # Use domain-normalized ID
                domain_slug = re.sub(r"[^a-zA-Z0-9]+", "_", current_domain)[:20].lower()
                qid = f"q:{domain_slug}:{domain_question_counter}"
            else:
                qid = f"q:{question_counter}"

            question = QuestionInfo(
                question_id=qid,
                text=first_cell,
                domain=current_domain,
                row_index=row_idx,
                response_scale=detected_scale,
            )
            questions.append(question)

    # Determine if this is truly a questionnaire
    is_questionnaire = len(questions) >= 1

    return QuestionnaireInfo(
        is_questionnaire=is_questionnaire,
        total_questions=len(questions),
        question_ids=[q.question_id for q in questions],
        questions=questions,
        domains=domains,
        response_scale=detected_scale,
        table_index=table_index,
    )


def detect_questionnaire_table(rows: list[list[str]]) -> bool:
    """Quick check if a table is questionnaire-type.

    Args:
        rows: Table rows

    Returns:
        True if table appears to be a questionnaire
    """
    info = extract_questions_from_table(rows)
    return info.is_questionnaire


def compute_structure_checksum(blocks: list[dict[str, Any]]) -> str:
    """Compute a checksum representing document structure.

    This checksum captures structural elements (paragraphs, tables)
    but is insensitive to text content changes, allowing detection
    of structural drift.

    Args:
        blocks: List of block dictionaries from extract_structure

    Returns:
        16-character hex checksum
    """
    structural_elements: list[dict[str, Any]] = []
    for block in blocks:
        kind = block.get("kind", "unknown")
        struct_elem: dict[str, Any] = {"kind": kind}
        if kind == "paragraph":
            # Only capture style, not text
            struct_elem["style"] = block.get("style", "")
        elif kind == "table":
            # Capture table dimensions
            rows = block.get("rows", [])
            struct_elem["row_count"] = len(rows)
            if rows:
                struct_elem["col_count"] = max(len(r) for r in rows)
        structural_elements.append(struct_elem)

    payload = hashlib.sha256(
        __import__("json").dumps(structural_elements, ensure_ascii=False).encode()
    ).hexdigest()
    return payload[:16]


def compute_block_checksum(block: dict[str, Any]) -> str:
    """Compute checksum for a single block.

    Args:
        block: Block dictionary

    Returns:
        16-character hex checksum
    """
    # Create a normalized representation for checksum
    checksum_data: dict[str, Any] = {"kind": block.get("kind", "unknown")}

    if block.get("kind") == "paragraph":
        checksum_data["text"] = block.get("text", "")
        checksum_data["style"] = block.get("style", "")
    elif block.get("kind") == "table":
        # For tables, use row count and cell count for structural checksum
        rows = block.get("rows", [])
        checksum_data["row_count"] = len(rows)
        checksum_data["col_count"] = max(len(r) for r in rows) if rows else 0
        # Also hash cell content for content checksum
        cell_texts = []
        for row in rows:
            for cell in row:
                cell_texts.append(str(cell or "").strip())
        checksum_data["cells_hash"] = hashlib.sha256(
            "|".join(cell_texts).encode()
        ).hexdigest()[:8]

    return hashlib.sha256(
        __import__("json").dumps(checksum_data, ensure_ascii=False).encode()
    ).hexdigest()[:16]

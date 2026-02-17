#!/usr/bin/env python3
"""Extract ordered paragraph/table structure from a DOCX file as JSON.

Produces StructuredDoc format with:
- Block-level checksums for precise change detection
- Questionnaire detection for survey-style tables
- Structure checksums for drift detection
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

# Import questionnaire detection
try:
    from scripts.questionnaire_detector import (
        compute_block_checksum,
        compute_structure_checksum,
        extract_questions_from_table,
    )
except ImportError:
    # Fallback for direct script execution
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "questionnaire_detector",
        Path(__file__).parent / "questionnaire_detector.py",
    )
    if spec and spec.loader:
        qd_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(qd_module)
        compute_block_checksum = qd_module.compute_block_checksum
        compute_structure_checksum = qd_module.compute_structure_checksum
        extract_questions_from_table = qd_module.extract_questions_from_table
    else:

        def compute_block_checksum(block: dict[str, Any]) -> str:
            return hashlib.sha256(json.dumps(block, ensure_ascii=False).encode()).hexdigest()[:16]

        def compute_structure_checksum(blocks: list[dict[str, Any]]) -> str:
            return hashlib.sha256(json.dumps(blocks, ensure_ascii=False).encode()).hexdigest()[:16]

        def extract_questions_from_table(rows: list[list[str]], table_index: int = 0) -> dict[str, Any]:
            return {"is_questionnaire": False, "total_questions": 0}


def normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def has_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06ff]", text))


def extract_structure(input_path: Path) -> dict[str, Any]:
    """Extract structure from a DOCX file with checksums and questionnaire detection.

    Returns a StructuredDoc with:
    - blocks: List of paragraphs/tables with block-level checksums
    - checksums: Document-level checksums (content, structure, counts)
    - questionnaire_info: Detected questionnaire information (if applicable)
    """
    doc = Document(str(input_path))
    blocks: list[dict[str, Any]] = []
    paragraph_count = 0
    table_count = 0
    block_index = 0
    questionnaire_info: dict[str, Any] | None = None

    for child in doc.element.body.iterchildren():
        block_index += 1
        if isinstance(child, CT_P):
            para = Paragraph(child, doc)
            text = normalize_text(para.text)
            if not text:
                continue
            paragraph_count += 1
            block = {
                "kind": "paragraph",
                "index": block_index,
                "style": para.style.name if para.style else "",
                "text": text,
            }
            # Add block-level checksum
            block["checksum"] = compute_block_checksum(block)
            blocks.append(block)
        elif isinstance(child, CT_Tbl):
            table = Table(child, doc)
            table_count += 1
            rows: list[list[str]] = []
            for row in table.rows:
                cells = [normalize_text(cell.text.replace("\n", " / ")) for cell in row.cells]
                rows.append(cells)
            block = {
                "kind": "table",
                "index": block_index,
                "table_index": table_count,
                "rows": rows,
            }
            # Add block-level checksum
            block["checksum"] = compute_block_checksum(block)
            blocks.append(block)

            # Check if this table is a questionnaire
            if questionnaire_info is None:
                q_info = extract_questions_from_table(rows, table_index=table_count)
                if q_info.is_questionnaire:
                    questionnaire_info = q_info.to_dict()

    sample_text = " ".join(
        item["text"]
        for item in blocks
        if item["kind"] == "paragraph" and item.get("text")
    )[:2500]

    # Content hash (all block content)
    content_checksum = hashlib.sha256(
        json.dumps(blocks, ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    # Structure hash (structural elements only, not text content)
    structure_checksum = compute_structure_checksum(blocks)

    # Build checksums object
    checksums = {
        "content_checksum": content_checksum,
        "structure_checksum": structure_checksum,
        "paragraph_count": paragraph_count,
        "table_count": table_count,
        "block_count": len(blocks),
        "question_count": questionnaire_info.get("total_questions", 0) if questionnaire_info else 0,
    }

    result: dict[str, Any] = {
        "source_file": str(input_path.resolve()),
        "file_name": input_path.name,
        "language_hint": "ar" if has_arabic(sample_text) else "en",
        "content_hash": content_checksum,
        "checksums": checksums,
        "blocks": blocks,
    }

    # Add questionnaire info if detected
    if questionnaire_info:
        result["questionnaire_info"] = questionnaire_info

    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to DOCX file")
    parser.add_argument("--output", help="Output JSON file path")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(json.dumps({"ok": False, "error": f"Missing file: {input_path}"}), file=sys.stderr)
        return 2

    if input_path.suffix.lower() != ".docx":
        print(json.dumps({"ok": False, "error": "Input must be .docx"}), file=sys.stderr)
        return 2

    payload = extract_structure(input_path)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"ok": True, "data": payload}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

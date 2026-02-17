#!/usr/bin/env python3
"""Revision pack builder for REVISION_UPDATE tasks.

Creates a structured pack that identifies:
- Which English paragraphs need translation (correspond to changed Arabic sections)
- Which English paragraphs should be preserved exactly (unchanged Arabic sections)
- New sections in Arabic V2 that have no English equivalent
- Structure integrity issues (e.g., baseline file incomplete)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from typing import Any

from scripts.build_delta_pack import build_delta, flatten_blocks
from scripts.paragraph_aligner import (
    AlignmentMatch,
    align_paragraphs,
    build_block_map,
)

log = logging.getLogger(__name__)


@dataclass
class RevisionPack:
    """Structured data for REVISION_UPDATE translation tasks."""

    # Original delta between Arabic V1 and V2
    delta: dict[str, Any]

    # Alignment between Arabic V1 and English V1
    alignment: list[dict[str, Any]]

    # English paragraphs that need retranslation (Arabic changed)
    english_to_update: list[dict[str, Any]]

    # English paragraphs to preserve exactly (Arabic unchanged)
    english_to_preserve: list[dict[str, Any]]

    # Map of unit_id -> exact English text to preserve
    preserved_text_map: dict[str, str]

    # New sections in Arabic V2 (not in V1)
    new_sections: list[dict[str, Any]]

    # Modified paragraph IDs from delta
    modified_ids: set[str]

    # Structure integrity issues detected during build
    structure_issues: list[dict[str, Any]] = field(default_factory=list)

    # Source checksums for verification
    source_checksums: dict[str, Any] = field(default_factory=dict)

    # Target (baseline) checksums for verification
    target_checksums: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = asdict(self)
        # Convert set to list for JSON
        result["modified_ids"] = sorted(list(self.modified_ids))
        return result

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _extract_modified_ids_from_delta(delta: dict[str, Any]) -> set[str]:
    """Extract the set of modified paragraph IDs from a delta pack.

    For modified blocks, we use the v1_range indices to identify which
    source paragraphs were changed.
    """
    modified_ids: set[str] = set()

    # Modified blocks contain v1_range [start, end]
    for mod in delta.get("modified", []):
        v1_range = mod.get("v1_range", [])
        if len(v1_range) >= 2:
            start, end = v1_range[0], v1_range[1]
            for idx in range(start, end):
                # Use p: prefix for paragraph indices
                modified_ids.add(f"p:{idx}")

    # Removed blocks are also "modified" from perspective of needing attention
    for rem in delta.get("removed", []):
        idx = rem.get("index")
        if idx is not None:
            modified_ids.add(f"p:{idx - 1}")  # index is 1-based in build_delta

    return modified_ids


def _alignment_to_dict(match: AlignmentMatch) -> dict[str, Any]:
    """Convert AlignmentMatch to dictionary."""
    return {
        "source_id": match.source_id,
        "target_id": match.target_id,
        "confidence": match.confidence,
        "source_text": match.source_text,
        "target_text": match.target_text,
        "source_index": match.source_index,
        "target_index": match.target_index,
    }


def _validate_structure_integrity(
    arabic_v1_structure: dict[str, Any],
    english_v1_structure: dict[str, Any],
) -> list[dict[str, Any]]:
    """Validate structure integrity between source and baseline documents.

    Checks for:
    - Question count mismatch (incomplete baseline)
    - Block count discrepancy
    - Structure drift

    Args:
        arabic_v1_structure: Structure of source (Arabic V1)
        english_v1_structure: Structure of baseline (English V1)

    Returns:
        List of structure issues found
    """
    issues: list[dict[str, Any]] = []

    ar_checksums = arabic_v1_structure.get("checksums", {})
    en_checksums = english_v1_structure.get("checksums", {})

    # Check question count (for questionnaires)
    ar_question_count = ar_checksums.get("question_count", 0)
    en_question_count = en_checksums.get("question_count", 0)

    if ar_question_count > 0 and en_question_count > 0:
        if ar_question_count != en_question_count:
            issues.append({
                "type": "question_count_mismatch",
                "severity": "error",
                "source_count": ar_question_count,
                "baseline_count": en_question_count,
                "message": (
                    f"Source document has {ar_question_count} questions, "
                    f"but baseline has {en_question_count}. "
                    f"Output will be incomplete. "
                    f"Provide a complete baseline document."
                ),
            })
            log.warning(
                "Structure drift: Source has %d questions, baseline has %d. "
                "Output may be incomplete.",
                ar_question_count,
                en_question_count,
            )

    # Check block count discrepancy
    ar_block_count = ar_checksums.get("block_count", 0)
    en_block_count = en_checksums.get("block_count", 0)

    if ar_block_count > 0 and en_block_count > 0:
        # Allow some tolerance for minor formatting differences
        ratio = en_block_count / ar_block_count if ar_block_count > 0 else 0
        if ratio < 0.7:  # Baseline has less than 70% of source blocks
            issues.append({
                "type": "block_count_discrepancy",
                "severity": "warning",
                "source_count": ar_block_count,
                "baseline_count": en_block_count,
                "ratio": round(ratio, 2),
                "message": (
                    f"Baseline document has significantly fewer blocks ({en_block_count}) "
                    f"than source ({ar_block_count}). Ratio: {ratio:.0%}. "
                    f"Parts of the output may be missing."
                ),
            })

    # Check paragraph count
    ar_para_count = ar_checksums.get("paragraph_count", 0)
    en_para_count = en_checksums.get("paragraph_count", 0)

    if ar_para_count > 0 and en_para_count > 0:
        para_ratio = en_para_count / ar_para_count if ar_para_count > 0 else 0
        if para_ratio < 0.6:  # Baseline has less than 60% of source paragraphs
            issues.append({
                "type": "paragraph_count_discrepancy",
                "severity": "warning",
                "source_count": ar_para_count,
                "baseline_count": en_para_count,
                "ratio": round(para_ratio, 2),
                "message": (
                    f"Baseline has fewer paragraphs ({en_para_count}) "
                    f"than source ({ar_para_count}). Some content may be lost."
                ),
            })

    return issues


def build_revision_pack(
    arabic_v1_structure: dict[str, Any],
    arabic_v2_structure: dict[str, Any],
    english_v1_structure: dict[str, Any],
    job_id: str = "unknown",
) -> RevisionPack:
    """Build a revision pack for REVISION_UPDATE translation tasks.

    This function:
    1. Validates structure integrity (checks for incomplete baseline)
    2. Builds delta between Arabic V1 and V2
    3. Aligns Arabic V1 to English V1 (establishes correspondence)
    4. Categorizes English paragraphs based on whether their Arabic source changed

    Args:
        arabic_v1_structure: Structure of Arabic V1 document
        arabic_v2_structure: Structure of Arabic V2 document
        english_v1_structure: Structure of English V1 document (baseline)
        job_id: Job identifier for tracking

    Returns:
        RevisionPack with all categorization data
    """
    # Step 1: Validate structure integrity
    structure_issues = _validate_structure_integrity(
        arabic_v1_structure,
        english_v1_structure,
    )

    # Extract checksums for reference
    source_checksums = arabic_v1_structure.get("checksums", {})
    target_checksums = english_v1_structure.get("checksums", {})

    # Log any issues found
    for issue in structure_issues:
        log.warning("[%s] %s", issue.get("type", "unknown"), issue.get("message", ""))

    # Step 2: Build delta between Arabic V1 and V2
    v1_rows = flatten_blocks(arabic_v1_structure)
    v2_rows = flatten_blocks(arabic_v2_structure)
    delta = build_delta(job_id, v1_rows, v2_rows)

    # Step 3: Align Arabic V1 to English V1
    alignment = align_paragraphs(arabic_v1_structure, english_v1_structure)

    # Step 4: Extract modified IDs from delta
    modified_ids = _extract_modified_ids_from_delta(delta)

    # Step 5: Categorize English paragraphs
    english_to_update: list[dict[str, Any]] = []
    english_to_preserve: list[dict[str, Any]] = []
    preserved_text_map: dict[str, str] = {}

    for match in alignment:
        match_dict = _alignment_to_dict(match)

        # Check if this source paragraph was modified
        if match.source_id in modified_ids:
            # Source changed, need to retranslate
            english_to_update.append(match_dict)
        else:
            # Source unchanged, preserve English text exactly
            english_to_preserve.append(match_dict)
            preserved_text_map[match.target_id] = match.target_text

    # Step 6: Identify new sections in V2 (not in V1)
    new_sections: list[dict[str, Any]] = []
    for added in delta.get("added", []):
        new_sections.append({
            "type": "new_paragraph",
            "index": added.get("index"),
            "text": added.get("text", "")[:500],  # Truncate for context
            "kind": added.get("kind", "paragraph"),
        })

    return RevisionPack(
        delta=delta,
        alignment=[_alignment_to_dict(m) for m in alignment],
        english_to_update=english_to_update,
        english_to_preserve=english_to_preserve,
        preserved_text_map=preserved_text_map,
        new_sections=new_sections,
        modified_ids=modified_ids,
        structure_issues=structure_issues,
        source_checksums=source_checksums,
        target_checksums=target_checksums,
    )


def format_revision_context_for_prompt(revision_pack: RevisionPack) -> str:
    """Format revision pack data for inclusion in LLM prompt.

    Creates a clear, structured prompt section that tells the LLM:
    1. Which texts to copy exactly (unchanged)
    2. Which texts to translate (changed)
    3. New content to translate

    Args:
        revision_pack: The revision pack to format

    Returns:
        Formatted string for LLM prompt
    """
    sections: list[str] = []

    # Preserved sections
    if revision_pack.preserved_text_map:
        sections.append("## UNCHANGED SECTIONS (PRESERVE EXACTLY)")
        sections.append("")
        sections.append("The following English paragraphs correspond to Arabic sections that did NOT change.")
        sections.append("Copy these texts EXACTLY - do not modify, paraphrase, or improve:")
        sections.append("")
        for unit_id, text in sorted(revision_pack.preserved_text_map.items()):
            # Truncate long texts in prompt but keep full text in map
            display_text = text if len(text) <= 300 else text[:300] + "..."
            sections.append(f"[{unit_id}]: {display_text}")
        sections.append("")

    # Sections to translate
    if revision_pack.english_to_update:
        sections.append("## SECTIONS TO TRANSLATE")
        sections.append("")
        sections.append("The following sections correspond to Arabic V2 text that has CHANGED.")
        sections.append("Translate these based on the new Arabic V2 text provided in context:")
        sections.append("")
        for item in revision_pack.english_to_update:
            old_text = item.get("target_text", "")
            display = old_text if len(old_text) <= 200 else old_text[:200] + "..."
            sections.append(f"[{item['target_id']}] OLD: {display}")
        sections.append("")

    # New sections
    if revision_pack.new_sections:
        sections.append("## NEW SECTIONS (FROM ARABIC V2)")
        sections.append("")
        sections.append("The following are new sections added in Arabic V2 that have no English equivalent.")
        sections.append("Translate these based on the Arabic V2 text:")
        sections.append("")
        for item in revision_pack.new_sections:
            sections.append(f"[NEW @ index {item.get('index')}]: {item.get('text', '')[:200]}")
        sections.append("")

    # Summary stats
    sections.append("## REVISION SUMMARY")
    sections.append("")
    sections.append(f"- Paragraphs to preserve exactly: {len(revision_pack.preserved_text_map)}")
    sections.append(f"- Paragraphs requiring retranslation: {len(revision_pack.english_to_update)}")
    sections.append(f"- New paragraphs to translate: {len(revision_pack.new_sections)}")
    sections.append(f"- Total alignment matches: {len(revision_pack.alignment)}")

    return "\n".join(sections)

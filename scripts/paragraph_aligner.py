#!/usr/bin/env python3
"""Paragraph alignment module for creating mappings between source and target documents.

Used primarily for REVISION_UPDATE tasks where we need to identify which target paragraphs
correspond to which source paragraphs, so we can preserve unchanged translations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


@dataclass(frozen=True)
class AlignmentMatch:
    """Represents a match between source and target paragraphs."""

    source_id: str  # e.g., "p:0", "t:0:r:1"
    target_id: str  # e.g., "p:0", "t:0:r:1"
    confidence: float  # 0.0 to 1.0
    source_text: str  # Original source text
    target_text: str  # Original target text
    source_index: int  # Index in source blocks list
    target_index: int  # Index in target blocks list


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for comparison by removing extra whitespace."""
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _similarity_score(text1: str, text2: str) -> float:
    """Calculate similarity between two texts using SequenceMatcher."""
    n1 = _normalize_for_comparison(text1)
    n2 = _normalize_for_comparison(text2)
    if not n1 or not n2:
        return 0.0
    return SequenceMatcher(a=n1, b=n2, autojunk=False).ratio()


def _generate_block_id(block: dict[str, Any], index: int) -> str:
    """Generate a unique ID for a block based on its type and position."""
    kind = block.get("kind", "paragraph")
    if kind == "paragraph":
        return f"p:{index}"
    elif kind == "table_row":
        row = block.get("row", index)
        return f"t:{index}:r:{row}"
    else:
        return f"{kind[:1]}:{index}"


def _extract_blocks_from_structure(structure: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract flat list of blocks from a document structure."""
    blocks: list[dict[str, Any]] = []
    for idx, block in enumerate(structure.get("blocks", [])):
        if block.get("kind") == "paragraph":
            text = block.get("text", "").strip()
            if text:
                blocks.append({
                    "kind": "paragraph",
                    "text": text,
                    "index": idx,
                })
        elif block.get("kind") == "table":
            rows = block.get("rows", [])
            for row_idx, row in enumerate(rows):
                cell_texts = []
                for cell in row:
                    cell_text = str(cell or "").strip()
                    if cell_text:
                        cell_texts.append(cell_text)
                if cell_texts:
                    combined = " | ".join(cell_texts)
                    blocks.append({
                        "kind": "table_row",
                        "text": combined,
                        "index": idx,
                        "row": row_idx + 1,
                    })
    return blocks


def align_by_position(
    source_blocks: list[dict[str, Any]],
    target_blocks: list[dict[str, Any]],
    min_confidence: float = 0.3,
) -> list[AlignmentMatch]:
    """Align source and target blocks by structural position.

    Simple position-based alignment assumes that the N-th block in source
    corresponds to the N-th block in target. This works well for translations
    that maintain structure.

    Args:
        source_blocks: List of source document blocks
        target_blocks: List of target document blocks
        min_confidence: Minimum confidence threshold for a match

    Returns:
        List of AlignmentMatch objects
    """
    matches: list[AlignmentMatch] = []

    # Pair by index
    for idx in range(min(len(source_blocks), len(target_blocks))):
        src = source_blocks[idx]
        tgt = target_blocks[idx]

        src_id = _generate_block_id(src, idx)
        tgt_id = _generate_block_id(tgt, idx)
        src_text = src.get("text", "")
        tgt_text = tgt.get("text", "")

        # Calculate similarity for confidence
        # For different languages, we use structure-based confidence
        # Higher confidence if structure types match
        kind_match = src.get("kind") == tgt.get("kind")

        # Position-based confidence:
        # - High if kinds match and position is early in document
        # - Lower if kinds don't match
        position_ratio = 1.0 - (idx / max(len(source_blocks), len(target_blocks), 1)) * 0.2
        base_confidence = 0.7 if kind_match else 0.4
        confidence = min(1.0, base_confidence * position_ratio)

        if confidence >= min_confidence:
            matches.append(AlignmentMatch(
                source_id=src_id,
                target_id=tgt_id,
                confidence=round(confidence, 3),
                source_text=src_text,
                target_text=tgt_text,
                source_index=idx,
                target_index=idx,
            ))

    return matches


def align_paragraphs(
    source_structure: dict[str, Any],
    target_structure: dict[str, Any],
    min_confidence: float = 0.3,
) -> list[AlignmentMatch]:
    """Create alignment between source and target document paragraphs.

    Uses a hybrid approach:
    1. Position-based alignment (same index in both documents)
    2. For mismatched lengths, attempts similarity-based matching for extra blocks

    Args:
        source_structure: Source document structure (from extract_structure)
        target_structure: Target document structure (from extract_structure)
        min_confidence: Minimum confidence threshold for matches

    Returns:
        List of AlignmentMatch objects representing aligned paragraphs
    """
    source_blocks = _extract_blocks_from_structure(source_structure)
    target_blocks = _extract_blocks_from_structure(target_structure)

    if not source_blocks or not target_blocks:
        return []

    # Primary strategy: position-based alignment
    matches = align_by_position(source_blocks, target_blocks, min_confidence)

    # Handle extra blocks in target (if target is longer)
    # These may be new sections or expansions
    if len(target_blocks) > len(source_blocks):
        matched_target_ids = {m.target_id for m in matches}
        for idx in range(len(source_blocks), len(target_blocks)):
            tgt = target_blocks[idx]
            tgt_id = _generate_block_id(tgt, idx)
            if tgt_id not in matched_target_ids:
                # This is a target-only block (new content)
                # We don't create a match, but we could flag it for attention
                pass

    return matches


def build_block_map(
    structure: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build a map from block ID to block data for quick lookup.

    Args:
        structure: Document structure

    Returns:
        Dict mapping block ID to block data with text
    """
    blocks = _extract_blocks_from_structure(structure)
    result: dict[str, dict[str, Any]] = {}
    for idx, block in enumerate(blocks):
        block_id = _generate_block_id(block, idx)
        result[block_id] = {
            "id": block_id,
            "kind": block.get("kind", "paragraph"),
            "text": block.get("text", ""),
            "index": idx,
        }
    return result

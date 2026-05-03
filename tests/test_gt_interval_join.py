"""Tests for IntervalTree-based GT join in tests/shared/gt_translation.py.

Covers:
- build_gt_index: builds correct per-doc interval trees
- resolve_gt_for_chunk: returns right GT entries; handles overlap edge cases
- translate_gt_mentions_to_chunk_frame: mention span translation at score time
"""

from __future__ import annotations

import pytest

pytest.importorskip("intervaltree", reason="intervaltree not installed")

from tests.shared.gt_translation import (
    build_gt_index,
    resolve_gt_for_chunk,
    translate_gt_mentions_to_chunk_frame,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gt_entry(
    doc_id: str,
    start: int,
    end: int,
    mentions: list[dict] | None = None,
    text_excerpt: str = "",
) -> dict:
    return {
        "doc_id": doc_id,
        "doc_char_start": start,
        "doc_char_end": end,
        "text_excerpt": text_excerpt,
        "mentions": mentions or [],
        "propositions": [],
    }


def _chunk(
    doc_id: str,
    offset: int,
    text: str,
    chunk_id: str = "test-chunk",
) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "text": text,
        "metadata": {"chunk_char_offset": offset},
    }


# ---------------------------------------------------------------------------
# build_gt_index
# ---------------------------------------------------------------------------


class TestBuildGTIndex:
    def test_single_entry(self):
        entries = [_gt_entry("doc-a", 0, 100)]
        idx = build_gt_index(entries)
        assert "doc-a" in idx
        # The interval [0:100) should be in the tree
        matches = idx["doc-a"][50]  # query single point
        assert len(matches) == 1

    def test_multiple_docs(self):
        entries = [
            _gt_entry("doc-a", 0, 100),
            _gt_entry("doc-b", 200, 400),
        ]
        idx = build_gt_index(entries)
        assert set(idx.keys()) == {"doc-a", "doc-b"}

    def test_multiple_entries_same_doc(self):
        entries = [
            _gt_entry("doc-a", 0, 100),
            _gt_entry("doc-a", 200, 300),
        ]
        idx = build_gt_index(entries)
        # Query in first range — should return one entry
        matches = idx["doc-a"][50:60]
        assert len(matches) == 1
        # Query in second range
        matches = idx["doc-a"][250:260]
        assert len(matches) == 1

    def test_missing_fields_skipped(self):
        entries = [
            {"doc_id": "doc-a"},  # missing doc_char_start/end
            {"doc_char_start": 0, "doc_char_end": 100},  # missing doc_id
            _gt_entry("doc-a", 0, 100),  # valid
        ]
        idx = build_gt_index(entries)
        assert "doc-a" in idx
        assert len(idx["doc-a"]) == 1

    def test_zero_length_entry_skipped(self):
        entries = [_gt_entry("doc-a", 100, 100)]  # start == end
        idx = build_gt_index(entries)
        # Either not in index or empty tree
        assert "doc-a" not in idx or len(idx.get("doc-a", [])) == 0

    def test_empty_input(self):
        idx = build_gt_index([])
        assert idx == {}


# ---------------------------------------------------------------------------
# resolve_gt_for_chunk
# ---------------------------------------------------------------------------


class TestResolveGTForChunk:
    def test_exact_match(self):
        entries = [_gt_entry("doc-a", 0, 100)]
        idx = build_gt_index(entries)
        chunk = _chunk("doc-a", offset=0, text="x" * 100)
        results = resolve_gt_for_chunk(chunk, idx)
        assert len(results) == 1
        assert results[0]["doc_char_start"] == 0
        assert results[0]["doc_char_end"] == 100

    def test_chunk_within_gt_entry(self):
        """Chunk is a subset of the GT entry range."""
        entries = [_gt_entry("doc-a", 0, 500)]
        idx = build_gt_index(entries)
        chunk = _chunk("doc-a", offset=100, text="x" * 200)  # [100, 300)
        results = resolve_gt_for_chunk(chunk, idx)
        assert len(results) == 1

    def test_gt_entry_within_chunk(self):
        """GT entry is fully contained within the chunk."""
        entries = [_gt_entry("doc-a", 50, 80)]
        idx = build_gt_index(entries)
        chunk = _chunk("doc-a", offset=0, text="x" * 200)  # [0, 200)
        results = resolve_gt_for_chunk(chunk, idx)
        assert len(results) == 1

    def test_no_overlap(self):
        entries = [_gt_entry("doc-a", 0, 100)]
        idx = build_gt_index(entries)
        chunk = _chunk("doc-a", offset=500, text="x" * 100)  # [500, 600) — no overlap
        results = resolve_gt_for_chunk(chunk, idx)
        assert len(results) == 0

    def test_wrong_doc_id(self):
        entries = [_gt_entry("doc-a", 0, 100)]
        idx = build_gt_index(entries)
        chunk = _chunk("doc-b", offset=0, text="x" * 100)
        results = resolve_gt_for_chunk(chunk, idx)
        assert len(results) == 0

    def test_missing_offset_returns_empty(self):
        entries = [_gt_entry("doc-a", 0, 100)]
        idx = build_gt_index(entries)
        chunk = {
            "chunk_id": "no-offset",
            "document_id": "doc-a",
            "text": "x" * 100,
            "metadata": {},  # no chunk_char_offset
        }
        results = resolve_gt_for_chunk(chunk, idx)
        assert results == []

    def test_overlap_edge_case_multiple_matches(self):
        """Two GT entries overlap the same chunk — both returned."""
        entries = [
            _gt_entry("doc-a", 0, 200),  # covers chunk
            _gt_entry("doc-a", 100, 300),  # also overlaps chunk
        ]
        idx = build_gt_index(entries)
        chunk = _chunk("doc-a", offset=150, text="x" * 100)  # [150, 250)
        results = resolve_gt_for_chunk(chunk, idx)
        assert len(results) == 2

    def test_larger_overlap_comes_first(self):
        """GT entry with larger overlap is returned first."""
        entries = [
            _gt_entry("doc-a", 0, 210, text_excerpt="big"),  # big overlap with [200,300)
            _gt_entry("doc-a", 190, 220, text_excerpt="small"),  # small overlap with [200,300)
        ]
        idx = build_gt_index(entries)
        chunk = _chunk("doc-a", offset=200, text="x" * 100)  # [200, 300)
        results = resolve_gt_for_chunk(chunk, idx)
        assert len(results) == 2
        # First result is the one with smaller doc_char_start (0) which has overlap 10
        # Second result has overlap 20 (190→220, overlap with 200→300 = 200→220 = 20)
        # Hmm: big entry [0,210) intersect [200,300) = 10 chars
        # small entry [190,220) intersect [200,300) = 20 chars → small has bigger overlap
        assert results[0]["text_excerpt"] == "small"


# ---------------------------------------------------------------------------
# translate_gt_mentions_to_chunk_frame
# ---------------------------------------------------------------------------


class TestTranslateGTMentions:
    def test_basic_translation(self):
        gt_entry = {
            "mentions": [
                {
                    "text": "Alice",
                    "mention_type": "PERSON",
                    "doc_char_start": 110,
                    "doc_char_end": 115,
                    "confidence": 0.9,
                }
            ],
            "propositions": [],
        }
        meta = {"chunk_char_offset": 100, "chunk_text_len": 500}
        result = translate_gt_mentions_to_chunk_frame(gt_entry, meta)
        assert len(result) == 1
        assert result[0]["span_start"] == 10  # 110 - 100
        assert result[0]["span_end"] == 15  # 115 - 100
        assert result[0]["text"] == "Alice"

    def test_mention_outside_chunk_dropped(self):
        gt_entry = {
            "mentions": [
                {
                    "text": "Bob",
                    "mention_type": "PERSON",
                    "doc_char_start": 10,
                    "doc_char_end": 13,
                    "confidence": 0.8,
                }
            ]
        }
        # Chunk starts at 100, so doc offset 10 is before this chunk
        meta = {"chunk_char_offset": 100, "chunk_text_len": 200}
        result = translate_gt_mentions_to_chunk_frame(gt_entry, meta)
        assert result == []

    def test_no_offset_returns_mentions_as_is(self):
        gt_entry = {
            "mentions": [
                {
                    "text": "Carol",
                    "mention_type": "PERSON",
                    "doc_char_start": 50,
                    "doc_char_end": 55,
                    "confidence": 1.0,
                }
            ]
        }
        meta = {}  # no chunk_char_offset
        result = translate_gt_mentions_to_chunk_frame(gt_entry, meta)
        assert len(result) == 1
        assert result[0]["text"] == "Carol"

    def test_legacy_mention_without_doc_span(self):
        """Mentions without doc_char_* fields pass through with span=None."""
        gt_entry = {
            "mentions": [
                {
                    "text": "Dave",
                    "mention_type": "PERSON",
                    "confidence": 0.7,
                    # no doc_char_start / doc_char_end
                }
            ]
        }
        meta = {"chunk_char_offset": 100}
        result = translate_gt_mentions_to_chunk_frame(gt_entry, meta)
        assert len(result) == 1
        assert result[0]["span_start"] is None
        assert result[0]["span_end"] is None

    def test_empty_mentions(self):
        gt_entry = {"mentions": [], "propositions": []}
        meta = {"chunk_char_offset": 0}
        result = translate_gt_mentions_to_chunk_frame(gt_entry, meta)
        assert result == []

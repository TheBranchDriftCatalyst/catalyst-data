"""Scoring invariant test: old GT (chunk-keyed) vs migrated GT (doc-anchored).

Phase 0 acceptance criterion (CD-9wno, point 3):
    Scoring the same model output against pre-migration GT (chunk-keyed) and
    post-migration GT (doc-anchored) on the same chunker must yield identical
    precision/recall/f1.

The test constructs a synthetic corpus — a handful of chunks with known
chunk_char_offset metadata, a "model output" fixture with mentions, and a
matching GT file in the old chunk-keyed format — then:

1. Scores the model output against the old GT directly (using
   tests.shared.extraction_scoring.score_mentions).
2. Migrates the GT in-memory to doc-anchored format.
3. Resolves the new GT back to chunk-frame using the IntervalTree join, then
   scores the same model output.
4. Asserts strict/relaxed F1, precision, and recall are identical.

No external services required; pure in-process.
"""

from __future__ import annotations

import pytest

pytest.importorskip("intervaltree", reason="intervaltree not installed")

from tests.shared.extraction_scoring import score_mentions
from tests.shared.gt_translation import (
    build_gt_index,
    chunk_to_doc,
    resolve_gt_for_chunk,
    translate_gt_mentions_to_chunk_frame,
)

# ---------------------------------------------------------------------------
# Synthetic corpus builder
# ---------------------------------------------------------------------------


# Three chunks from a single document, laid out contiguously in doc space.
# The text is chosen so find_best_span-style matching works deterministically
# without an NLP library (the scorer does text-match, not span-match for F1).
DOC_TEXT = (
    "Alice met Bob at the conference. "  # 0..32
    "Bob later spoke to Carol about it. "  # 33..67
    "Carol thanked Dave for the invite."  # 68..101
)

CHUNKS = [
    {
        "chunk_id": "test-doc:chunk-0",
        "document_id": "test-doc",
        "text": DOC_TEXT[0:50],
        "metadata": {"chunk_char_offset": 0},
    },
    {
        "chunk_id": "test-doc:chunk-1",
        "document_id": "test-doc",
        "text": DOC_TEXT[40:90],  # overlapping slightly
        "metadata": {"chunk_char_offset": 40},
    },
    {
        "chunk_id": "test-doc:chunk-2",
        "document_id": "test-doc",
        "text": DOC_TEXT[80:],
        "metadata": {"chunk_char_offset": 80},
    },
]


# Old GT (chunk-keyed).  We include mentions for chunk-0 and chunk-1.
# Spans are relative to each chunk's text.


def _old_gt_mentions_for_chunk(cid: str) -> list[dict]:
    """Return chunk-relative GT mentions keyed to chunk_id."""
    if cid == "test-doc:chunk-0":
        text_0 = CHUNKS[0]["text"]
        alice_start = text_0.index("Alice")
        bob_start = text_0.index("Bob")
        return [
            {
                "text": "Alice",
                "mention_type": "PERSON",
                "span_start": alice_start,
                "span_end": alice_start + len("Alice"),
                "confidence": 1.0,
                "chunk_id": cid,
            },
            {
                "text": "Bob",
                "mention_type": "PERSON",
                "span_start": bob_start,
                "span_end": bob_start + len("Bob"),
                "confidence": 1.0,
                "chunk_id": cid,
            },
        ]
    if cid == "test-doc:chunk-1":
        text_1 = CHUNKS[1]["text"]
        carol_start = text_1.index("Carol")
        return [
            {
                "text": "Carol",
                "mention_type": "PERSON",
                "span_start": carol_start,
                "span_end": carol_start + len("Carol"),
                "confidence": 1.0,
                "chunk_id": cid,
            },
        ]
    return []


OLD_GT_CHUNKS = [
    {
        "chunk_id": cid,
        "text": chunk["text"],
        "mentions": _old_gt_mentions_for_chunk(cid),
        "propositions": [],
    }
    for cid, chunk in ((c["chunk_id"], c) for c in CHUNKS)
]


# "Model output": correctly predicts Alice, Carol; misses Bob; adds a
# hallucinated "Dave" in chunk-2.  These are flat lists with chunk_id tags
# as the extraction pipeline emits.
MODEL_MENTIONS = [
    {
        "text": "Alice",
        "mention_type": "PERSON",
        "span_start": 0,
        "span_end": 5,
        "confidence": 0.95,
        "chunk_id": "test-doc:chunk-0",
    },
    {
        "text": "Carol",
        "mention_type": "PERSON",
        "span_start": 0,
        "span_end": 5,
        "confidence": 0.90,
        "chunk_id": "test-doc:chunk-1",
    },
    {
        "text": "Dave",
        "mention_type": "PERSON",
        "span_start": 0,
        "span_end": 4,
        "confidence": 0.70,
        "chunk_id": "test-doc:chunk-2",
    },
]


# ---------------------------------------------------------------------------
# Utility: flatten old GT to a simple mention list (no chunk_id join needed
# for the aggregate scorer — just pile all GT mentions together).
# ---------------------------------------------------------------------------


def _flatten_old_gt_mentions() -> list[dict]:
    out = []
    for c in OLD_GT_CHUNKS:
        out.extend(c["mentions"])
    return out


# ---------------------------------------------------------------------------
# Utility: migrate old GT → new doc-anchored shape (in-memory, no S3)
# ---------------------------------------------------------------------------


def _migrate_gt_to_doc_anchored() -> list[dict]:
    """Translate old chunk-keyed GT to doc-anchored GT using chunk metadata."""
    chunk_map = {c["chunk_id"]: c for c in CHUNKS}
    new_entries = []
    for old_chunk in OLD_GT_CHUNKS:
        cid = old_chunk["chunk_id"]
        chunk = chunk_map.get(cid)
        if chunk is None:
            continue
        metadata = chunk.get("metadata", {})
        offset = metadata.get("chunk_char_offset")
        chunk_text = chunk["text"]

        doc_start = offset if offset is not None else 0
        doc_end = doc_start + len(chunk_text)

        new_mentions = []
        for m in old_chunk["mentions"]:
            s, e = m.get("span_start"), m.get("span_end")
            if s is not None and e is not None and offset is not None:
                ds, de = chunk_to_doc(metadata, s, e)
            else:
                ds, de = None, None
            new_mentions.append(
                {
                    **{k: v for k, v in m.items() if k not in ("span_start", "span_end", "chunk_id")},
                    "doc_char_start": ds,
                    "doc_char_end": de,
                }
            )

        new_entries.append(
            {
                "doc_id": chunk["document_id"],
                "doc_char_start": doc_start,
                "doc_char_end": doc_end,
                "text_excerpt": chunk_text,
                "legacy_chunk_id": cid,
                "mentions": new_mentions,
                "propositions": old_chunk["propositions"],
            }
        )

    return new_entries


# ---------------------------------------------------------------------------
# Score using the new doc-anchored GT
# ---------------------------------------------------------------------------


def _score_new_gt(model_mentions: list[dict]) -> dict:
    """Score model mentions against doc-anchored GT using IntervalTree join."""
    new_gt_entries = _migrate_gt_to_doc_anchored()
    gt_index = build_gt_index(new_gt_entries)

    # Bucket model mentions by chunk_id (same as the scorer does)
    model_by_chunk: dict[str, list[dict]] = {}
    for m in model_mentions:
        cid = m.get("chunk_id", "")
        model_by_chunk.setdefault(cid, []).append(m)

    # For each chunk, resolve matching GT entries and aggregate mentions
    all_gt_mentions_translated: list[dict] = []
    seen_gt_entries: set[int] = set()  # avoid double-counting shared GT entries

    for chunk in CHUNKS:
        cid = chunk["chunk_id"]
        metadata = chunk["metadata"]
        meta_with_len = {**metadata, "chunk_text_len": len(chunk["text"])}
        gt_matches = resolve_gt_for_chunk(chunk, gt_index)
        for gt_entry in gt_matches:
            entry_id = id(gt_entry)
            if entry_id in seen_gt_entries:
                continue
            seen_gt_entries.add(entry_id)
            translated = translate_gt_mentions_to_chunk_frame(gt_entry, meta_with_len)
            all_gt_mentions_translated.extend(translated)

    # Now score the full model output against the translated GT
    return score_mentions(model_mentions, all_gt_mentions_translated)


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


class TestGTScoringInvariant:
    """Same model output scored against old vs. new GT must yield identical F1."""

    def test_mention_scores_are_identical(self):
        old_gt_mentions = _flatten_old_gt_mentions()
        old_scores = score_mentions(MODEL_MENTIONS, old_gt_mentions)

        new_scores = _score_new_gt(MODEL_MENTIONS)

        assert old_scores["strict_f1"] == pytest.approx(new_scores["strict_f1"], abs=1e-6), (
            f"strict_f1 mismatch: old={old_scores['strict_f1']} new={new_scores['strict_f1']}"
        )
        assert old_scores["relaxed_f1"] == pytest.approx(new_scores["relaxed_f1"], abs=1e-6), (
            f"relaxed_f1 mismatch: old={old_scores['relaxed_f1']} new={new_scores['relaxed_f1']}"
        )
        assert old_scores["strict_precision"] == pytest.approx(new_scores["strict_precision"], abs=1e-6)
        assert old_scores["strict_recall"] == pytest.approx(new_scores["strict_recall"], abs=1e-6)

    def test_gt_count_preserved(self):
        """Total GT mention count must be the same before and after migration."""
        old_count = len(_flatten_old_gt_mentions())
        new_entries = _migrate_gt_to_doc_anchored()
        new_count = sum(len(e["mentions"]) for e in new_entries)
        assert old_count == new_count, f"Mention count changed during migration: old={old_count} new={new_count}"

    def test_doc_anchoring_survives_offset_change(self):
        """If we shift all chunks by a constant offset, scores stay the same.

        Simulates a chunker change that shifts byte positions but preserves
        the relative layout — exactly why we need doc-anchored GT.
        """
        shift = 1000  # simulate a new document header prepended

        # Build shifted chunks
        shifted_chunks = [
            {
                **c,
                "metadata": {**c["metadata"], "chunk_char_offset": c["metadata"]["chunk_char_offset"] + shift},
            }
            for c in CHUNKS
        ]

        # The doc-anchored GT is anchored at the *original* doc positions,
        # so we also need to shift the GT entries by the same amount.
        new_gt_entries = _migrate_gt_to_doc_anchored()
        shifted_gt_entries = [
            {
                **e,
                "doc_char_start": e["doc_char_start"] + shift,
                "doc_char_end": e["doc_char_end"] + shift,
                "mentions": [
                    {
                        **m,
                        "doc_char_start": m["doc_char_start"] + shift if m.get("doc_char_start") is not None else None,
                        "doc_char_end": m["doc_char_end"] + shift if m.get("doc_char_end") is not None else None,
                    }
                    for m in e["mentions"]
                ],
            }
            for e in new_gt_entries
        ]

        shifted_gt_index = build_gt_index(shifted_gt_entries)

        # Bucket model mentions by chunk_id
        model_by_chunk: dict[str, list[dict]] = {}
        for m in MODEL_MENTIONS:
            cid = m.get("chunk_id", "")
            model_by_chunk.setdefault(cid, []).append(m)

        all_gt_mentions_shifted: list[dict] = []
        seen: set[int] = set()
        for chunk in shifted_chunks:
            cid = chunk["chunk_id"]
            meta_with_len = {**chunk["metadata"], "chunk_text_len": len(chunk["text"])}
            gt_matches = resolve_gt_for_chunk(chunk, shifted_gt_index)
            for gt_entry in gt_matches:
                entry_id = id(gt_entry)
                if entry_id in seen:
                    continue
                seen.add(entry_id)
                translated = translate_gt_mentions_to_chunk_frame(gt_entry, meta_with_len)
                all_gt_mentions_shifted.extend(translated)

        shifted_scores = score_mentions(MODEL_MENTIONS, all_gt_mentions_shifted)
        original_scores = score_mentions(MODEL_MENTIONS, _flatten_old_gt_mentions())

        assert shifted_scores["strict_f1"] == pytest.approx(original_scores["strict_f1"], abs=1e-6), (
            "strict_f1 changed when chunk offsets shifted — doc-anchoring broken"
        )

"""Behavioral test for the ``candidates`` filter in ``generate_ensemble_ground_truth``.

The whole point of ``candidates`` is that consensus voting only runs over the
sampler's chosen chunk_ids — not the full 3.6M-chunk medallion pool. This test
verifies the filter actually scopes the work, not just that the parameter is
accepted.

Anti-tautology: the assertion fails if a buggy implementation silently ignores
``candidates`` and processes every chunk, OR if the filter accidentally drops
all candidates and returns None.
"""

from __future__ import annotations

from unittest.mock import patch

from tests.shared.ground_truth import generate_ensemble_ground_truth


def test_candidates_filter_scopes_chunks_processed():
    """When ``candidates`` is set, the function should:
    1. Only build GT for chunks whose chunk_id is in the candidate list
    2. Skip chunks outside the list even when extraction fixtures cover them
    3. Return a GT object whose ``chunks`` array length is at most len(candidates)
    """
    fake_pool = [
        {"chunk_id": "alpha", "document_id": "doc-1", "text": "Alpha text", "metadata": {}},
        {"chunk_id": "beta", "document_id": "doc-1", "text": "Beta text", "metadata": {}},
        {"chunk_id": "gamma", "document_id": "doc-2", "text": "Gamma text", "metadata": {}},
        {"chunk_id": "delta", "document_id": "doc-2", "text": "Delta text", "metadata": {}},
    ]

    fake_ner_extractions = {
        "modelA": {
            "mentions": [
                {"chunk_id": cid, "text": "X", "mention_type": "PERSON", "span_start": 0, "span_end": 1}
                for cid in ("alpha", "beta", "gamma", "delta")
            ],
            "model": "modelA",
        },
        "modelB": {
            "mentions": [
                {"chunk_id": cid, "text": "X", "mention_type": "PERSON", "span_start": 0, "span_end": 1}
                for cid in ("alpha", "beta", "gamma", "delta")
            ],
            "model": "modelB",
        },
    }
    fake_spo_extractions: dict = {}

    with (
        patch("tests.shared.medallion.load_chunks", return_value=fake_pool),
        patch(
            "tests.shared.ground_truth._load_available_extractions",
            side_effect=lambda store, models: fake_ner_extractions if "modelA" in models else fake_spo_extractions,
        ),
    ):
        gt_full = generate_ensemble_ground_truth(
            ner_models=["modelA", "modelB"],
            spo_models=[],
            candidates=None,
        )
        gt_filtered = generate_ensemble_ground_truth(
            ner_models=["modelA", "modelB"],
            spo_models=[],
            candidates=["alpha", "gamma"],
        )

    assert gt_full is not None
    assert {c["chunk_id"] for c in gt_full["chunks"]} == {"alpha", "beta", "gamma", "delta"}

    assert gt_filtered is not None
    filtered_ids = {c["chunk_id"] for c in gt_filtered["chunks"]}
    assert filtered_ids == {"alpha", "gamma"}, f"expected exactly 2 candidates, got {filtered_ids}"
    assert "beta" not in filtered_ids
    assert "delta" not in filtered_ids


def test_candidates_filter_with_no_matches_returns_none():
    """Filter that excludes every chunk should return None, not silently process all."""
    fake_pool = [
        {"chunk_id": "alpha", "document_id": "doc-1", "text": "x", "metadata": {}},
    ]

    with (
        patch("tests.shared.medallion.load_chunks", return_value=fake_pool),
        patch("tests.shared.ground_truth._load_available_extractions", return_value={}),
    ):
        gt = generate_ensemble_ground_truth(
            ner_models=["modelA", "modelB"],
            spo_models=[],
            candidates=["nonexistent-chunk-id"],
        )

    assert gt is None

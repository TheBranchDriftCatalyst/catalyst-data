"""Tests for ``dagster_io.concordance`` — CrossSourceAligner intra-source
and cross-source entity alignment (CD-0sc).

Covers:
- Intra-source same-name collapse within one code location
- Mixed intra-source + cross-source producing a single cluster
- Different entity types staying separate within intra-source
- No regression on existing cross-source behavior
"""

from __future__ import annotations

from dagster_io.concordance import CrossSourceAligner, _UnionFind
from dagster_io.models import EntityCandidate, MentionType


def test_intra_source_same_name_collapses():
    """3 EntityCandidates with identical name in one source -> 1 canonical with summed mentions.

    Each candidate gets a unique candidate_id to simulate the real scenario where
    ConcordanceEngine.resolve() in separate partitions produces distinct candidate
    objects that happen to share the same canonical name.
    """
    candidates = [
        EntityCandidate(
            candidate_id=f"cand-same-{i}",
            canonical_name="Joe Biden",
            candidate_type=MentionType.PERSON,
            aliases=[],
            mention_ids=[f"m-{i}"],
            mention_count=10 + i,
            source_documents=[f"doc-{i}"],
            code_location="media_ingest",
        )
        for i in range(3)
    ]

    aligner = CrossSourceAligner()
    edges = aligner.intra_source_align(candidates, "media_ingest")

    # All 3 same-name candidates should produce edges (3 choose 2 = 3 pairs)
    assert len(edges) == 3
    assert all(e.alignment_type.value == "sameAs" for e in edges)

    # Union-find should collapse to 1 cluster
    uf = _UnionFind()
    for c in candidates:
        uf.find(c.candidate_id)
    for e in edges:
        if e.alignment_type.value == "sameAs":
            uf.union(e.source_entity_id, e.target_entity_id)
    clusters = uf.clusters()
    assert len(clusters) == 1

    # Mention count sums
    member_ids = list(clusters.values())[0]
    total = sum(c.mention_count for c in candidates if c.candidate_id in member_ids)
    assert total == 33  # 10 + 11 + 12


def test_mixed_intra_and_cross_source():
    """Intra-source edges within source A + cross-source edge from A to B -> single cluster."""
    media_cands = [
        EntityCandidate(
            candidate_id=f"cand-media-{i}",
            canonical_name="Joe Biden",
            candidate_type=MentionType.PERSON,
            aliases=["Biden"],
            mention_ids=[f"m-media-{i}"],
            mention_count=5,
            source_documents=[f"video-{i}"],
            code_location="media_ingest",
        )
        for i in range(3)
    ]
    congress_cands = [
        EntityCandidate(
            candidate_id="cand-congress-0",
            canonical_name="Joseph R. Biden",
            candidate_type=MentionType.PERSON,
            aliases=["Joe Biden", "Biden"],
            mention_ids=["m-congress-0"],
            mention_count=50,
            source_documents=["bill-1"],
            code_location="congress_data",
        )
    ]

    aligner = CrossSourceAligner()

    # Intra-source
    intra = aligner.intra_source_align(media_cands, "media_ingest")
    assert len(intra) == 3  # 3 choose 2

    # Cross-source
    sources = {"media_ingest": media_cands, "congress_data": congress_cands}
    cross = aligner.align(sources)
    assert len(cross) >= 1  # at least one cross-source edge

    # Combine into union-find
    uf = _UnionFind()
    all_cands = media_cands + congress_cands
    for c in all_cands:
        uf.find(c.candidate_id)
    for e in intra + cross:
        if e.alignment_type.value == "sameAs":
            uf.union(e.source_entity_id, e.target_entity_id)

    clusters = uf.clusters()
    # All 4 candidates should collapse into 1 cluster
    assert len(clusters) == 1
    total_mentions = sum(c.mention_count for c in all_cands)
    assert total_mentions == 65  # 5*3 + 50


def test_intra_source_different_types_stay_separate():
    """Same-name candidates of different types should NOT merge."""
    candidates = [
        EntityCandidate(
            candidate_id="cand-person-wash",
            canonical_name="Washington",
            candidate_type=MentionType.PERSON,
            aliases=[],
            mention_ids=["m-person"],
            mention_count=5,
            source_documents=["doc-1"],
            code_location="media_ingest",
        ),
        EntityCandidate(
            candidate_id="cand-location-wash",
            canonical_name="Washington",
            candidate_type=MentionType.LOC,
            aliases=[],
            mention_ids=["m-location"],
            mention_count=3,
            source_documents=["doc-1"],
            code_location="media_ingest",
        ),
    ]

    aligner = CrossSourceAligner()
    edges = aligner.intra_source_align(candidates, "media_ingest")
    assert len(edges) == 0  # different types, no alignment


def test_cross_source_align_unchanged():
    """Cross-source align still produces edges between different sources."""
    sources = {
        "media_ingest": [
            EntityCandidate(
                candidate_id="cand-media-aipac",
                canonical_name="AIPAC",
                candidate_type=MentionType.ORG,
                aliases=["American Israel Public Affairs Committee"],
                mention_ids=["m-media-0"],
                mention_count=10,
                source_documents=["video-0"],
                code_location="media_ingest",
            )
        ],
        "congress_data": [
            EntityCandidate(
                candidate_id="cand-congress-aipac",
                canonical_name="AIPAC",
                candidate_type=MentionType.ORG,
                aliases=[],
                mention_ids=["m-congress-0"],
                mention_count=20,
                source_documents=["bill-0"],
                code_location="congress_data",
            )
        ],
    }

    aligner = CrossSourceAligner()
    edges = aligner.align(sources)
    assert len(edges) >= 1
    assert edges[0].alignment_type.value == "sameAs"

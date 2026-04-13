"""Layer 3: Integration tests — full Mention → Engine → Aligner pipeline.

Uses realistic multi-source fixture data. Tests the composition of
ConcordanceEngine + CrossSourceAligner + _UnionFind clustering.
"""

from __future__ import annotations

import pytest
from concordance_helpers import make_candidate, make_mention

from dagster_io.concordance import ConcordanceEngine, CrossSourceAligner, _UnionFind
from dagster_io.models import EntityCandidate, Mention, MentionType


def _run_full_pipeline(
    media_mentions: list[Mention],
    congress_candidates: list[EntityCandidate],
    embeddings: dict[str, list[float]] | None = None,
) -> dict:
    """Run the full Mention → ConcordanceEngine → CrossSourceAligner pipeline."""
    engine = ConcordanceEngine()
    media_cands = engine.resolve(media_mentions, "media_ingest", embeddings=embeddings)

    aligner = CrossSourceAligner()
    sources = {
        "media_ingest": media_cands,
        "congress_data": congress_candidates,
    }

    intra_edges = []
    for loc, cands in sources.items():
        if len(cands) > 1:
            intra_edges.extend(aligner.intra_source_align(cands, loc))

    cross_edges = aligner.align(sources)
    all_edges = intra_edges + cross_edges

    all_candidates = media_cands + congress_candidates
    uf = _UnionFind()
    for c in all_candidates:
        uf.find(c.candidate_id)
    for e in all_edges:
        if e.alignment_type.value == "sameAs":
            uf.union(e.source_entity_id, e.target_entity_id)

    return {
        "media_candidates": media_cands,
        "congress_candidates": congress_candidates,
        "intra_edges": intra_edges,
        "cross_edges": cross_edges,
        "all_edges": all_edges,
        "clusters": uf.clusters(),
        "all_candidates": all_candidates,
    }


def test_full_pipeline_political_dataset(political_mentions, congress_candidates):
    """Full pipeline with realistic political data produces sensible results."""
    results = _run_full_pipeline(political_mentions, congress_candidates)
    assert len(results["media_candidates"]) > 0
    assert len(results["all_edges"]) > 0
    assert len(results["clusters"]) < len(results["all_candidates"])


def test_full_pipeline_with_embeddings_no_false_merge(
    political_mentions,
    congress_candidates,
    political_embeddings,
):
    """Embeddings should NOT introduce false merges."""
    _run_full_pipeline(political_mentions, congress_candidates)  # baseline
    with_emb = _run_full_pipeline(
        political_mentions,
        congress_candidates,
        embeddings=political_embeddings,
    )
    # Embeddings may add TRUE merges (fewer candidates) but should not create
    # false merges. Check that no cluster mixes Trump and Rumsfeld.
    cand_by_id = {c.candidate_id: c for c in with_emb["all_candidates"]}
    for _root, members in with_emb["clusters"].items():
        names = set()
        for mid in members:
            c = cand_by_id.get(mid)
            if c:
                names.add(c.canonical_name.lower())
                names.update(a.lower() for a in c.aliases)
        has_trump = any("trump" in n for n in names)
        has_rumsfeld = any("rumsfeld" in n for n in names)
        assert not (has_trump and has_rumsfeld), f"False merge: cluster contains both Trump and Rumsfeld: {names}"


def test_false_merge_gauntlet():
    """Gauntlet of pairs that must NEVER merge within ConcordanceEngine."""
    engine = ConcordanceEngine()
    gauntlet = [
        ("Donald Trump", "Donald Rumsfeld", MentionType.PERSON),
        ("President Biden", "President Trump", MentionType.PERSON),
        ("Iran", "Iranian Americans", MentionType.GPE),
        ("AI", "AIPAC", MentionType.ORG),
    ]
    for text_a, text_b, mtype in gauntlet:
        mentions = [
            make_mention(text_a, mtype, "doc-1", "c0"),
            make_mention(text_b, mtype, "doc-1", "c1"),
        ]
        candidates = engine.resolve(mentions, "test")
        assert len(candidates) == 2, (
            f"False merge gauntlet FAILED: '{text_a}' merged with '{text_b}'. "
            f"Got {len(candidates)} candidates: {[c.canonical_name for c in candidates]}"
        )


def test_true_merge_gauntlet():
    """Gauntlet of pairs that MUST merge within ConcordanceEngine."""
    engine = ConcordanceEngine()
    gauntlet = [
        ("Donald Trump", "donald trump", MentionType.PERSON),  # Pass 1: exact
        ("Nancy Pelosi", "Speaker Nancy Pelosi", MentionType.PERSON),  # Pass 2: substring + 2 tokens
        ("Joe Biden", "President Joe Biden", MentionType.PERSON),  # Pass 2: substring + 2 tokens
    ]
    for text_a, text_b, mtype in gauntlet:
        mentions = [
            make_mention(text_a, mtype, "doc-1", "c0"),
            make_mention(text_b, mtype, "doc-1", "c1"),
        ]
        candidates = engine.resolve(mentions, "test")
        assert len(candidates) == 1, (
            f"True merge gauntlet FAILED: '{text_a}' did NOT merge with '{text_b}'. "
            f"Got {len(candidates)} candidates: {[c.canonical_name for c in candidates]}"
        )


def test_score_distribution_no_dead_zone(political_mentions, congress_candidates):
    """Score distribution should be continuous — no dead zone."""
    results = _run_full_pipeline(political_mentions, congress_candidates)
    edges = results["all_edges"]
    if len(edges) < 3:
        pytest.skip("Too few edges to check distribution")

    scores = [e.score for e in edges]
    unique_scores = set(round(s, 2) for s in scores)
    assert len(unique_scores) >= 2, f"Score diversity too low: {unique_scores}"

    for edge in edges:
        if edge.alignment_type.value == "sameAs":
            assert edge.score >= 0.50


def test_cluster_quality_assertions(political_mentions, congress_candidates):
    """Clusters should be reasonable: no mega-clusters, no cross-type pollution."""
    results = _run_full_pipeline(political_mentions, congress_candidates)
    cand_by_id = {c.candidate_id: c for c in results["all_candidates"]}

    for _root, members in results["clusters"].items():
        # No mega-clusters
        assert len(members) <= 20, f"Oversized cluster: {len(members)} members"

        # No cluster mixes Trump and Rumsfeld
        names = set()
        for mid in members:
            c = cand_by_id.get(mid)
            if c:
                names.add(c.canonical_name.lower())
                names.update(a.lower() for a in c.aliases)
        has_trump = any("trump" in n for n in names)
        has_rumsfeld = any("rumsfeld" in n for n in names)
        assert not (has_trump and has_rumsfeld), f"Trump+Rumsfeld in same cluster: {names}"


def test_three_source_alignment(political_mentions, congress_candidates):
    """Three sources (media, congress, leaks) produce correct alignment."""
    engine = ConcordanceEngine()
    media_cands = engine.resolve(political_mentions, "media_ingest")

    leak_cands = [
        make_candidate("AIPAC", MentionType.ORG, "open_leaks", mention_count=15),
        make_candidate("Joe Biden", MentionType.PERSON, "open_leaks", aliases=["Biden"], mention_count=25),
    ]

    aligner = CrossSourceAligner()
    sources = {
        "media_ingest": media_cands,
        "congress_data": congress_candidates,
        "open_leaks": leak_cands,
    }

    cross_edges = aligner.align(sources)
    # Should find AIPAC and Biden matches across sources
    assert len(cross_edges) >= 1, "Expected at least one cross-source edge from 3 sources"

    # Verify edges span multiple source pairs
    source_pairs = set()
    cand_by_id = {}
    for loc, cands in sources.items():
        for c in cands:
            cand_by_id[c.candidate_id] = (c, loc)

    for e in cross_edges:
        s_loc = cand_by_id.get(e.source_entity_id, (None, "?"))[1]
        t_loc = cand_by_id.get(e.target_entity_id, (None, "?"))[1]
        source_pairs.add(tuple(sorted([s_loc, t_loc])))

    assert len(source_pairs) >= 1, f"Expected edges across source pairs, got: {source_pairs}"

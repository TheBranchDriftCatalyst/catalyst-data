"""Tests for Sprint 2 concordance improvements: IDF weighting + cluster coherence."""

from __future__ import annotations

from dagster_io.concordance import (
    CrossSourceAligner,
    _idf_weighted_jaccard,
    _tokenize,
    check_cluster_coherence,
    compute_idf,
)
from dagster_io.models import AlignmentEdge, AlignmentType, EntityCandidate, MentionType


def _cand(name: str, **kwargs) -> EntityCandidate:
    return EntityCandidate(
        canonical_name=name,
        candidate_type=kwargs.get("entity_type", MentionType.PERSON),
        aliases=kwargs.get("aliases", []),
        mention_ids=["m1"],
        mention_count=kwargs.get("mention_count", 1),
        source_documents=["doc-1"],
        code_location=kwargs.get("code_location", "test"),
        embedding=kwargs.get("embedding"),
    )


# ── IDF Tests ────────────────────────────────────────────────────────────


class TestComputeIDF:
    def test_basic_idf(self):
        """Common tokens get lower IDF, rare tokens get higher."""
        candidates = [
            _cand("John Smith"),
            _cand("John Doe"),
            _cand("John Adams"),
            _cand("Nancy Pelosi"),
        ]
        idf = compute_idf(candidates)
        # "john" appears in 3/4 entities → low IDF
        # "pelosi" appears in 1/4 entities → high IDF
        assert idf["john"] < idf["pelosi"]
        assert idf["john"] < idf["smith"]

    def test_empty_candidates(self):
        assert compute_idf([]) == {}

    def test_single_candidate(self):
        idf = compute_idf([_cand("Joe Biden")])
        assert "joe" in idf
        assert "biden" in idf

    def test_idf_includes_aliases(self):
        """IDF computation includes alias tokens."""
        candidates = [_cand("Donald Trump", aliases=["Trump", "The Donald"])]
        idf = compute_idf(candidates)
        assert "the" in idf
        assert "donald" in idf


class TestIDFWeightedJaccard:
    def test_rare_tokens_boost_score(self):
        """Shared rare tokens produce higher IDF-weighted jaccard than shared common tokens."""
        idf = {"john": 1.2, "smith": 2.5, "doe": 2.5, "pelosi": 4.0, "nancy": 3.5}

        # Two names sharing common "john" → lower score
        a1 = _tokenize("john smith")
        b1 = _tokenize("john doe")
        score_common = _idf_weighted_jaccard(a1, b1, idf)

        # Two names sharing rare "pelosi" (hypothetical) → higher score
        a2 = {"nancy", "pelosi"}
        b2 = {"pelosi"}
        score_rare = _idf_weighted_jaccard(a2, b2, idf)

        # Rare shared token should give a higher ratio
        assert score_rare > score_common

    def test_empty_sets(self):
        assert _idf_weighted_jaccard(set(), {"a"}, {}) == 0.0
        assert _idf_weighted_jaccard({"a"}, set(), {}) == 0.0

    def test_identical_sets(self):
        idf = {"a": 2.0, "b": 3.0}
        assert _idf_weighted_jaccard({"a", "b"}, {"a", "b"}, idf) == 1.0

    def test_no_overlap(self):
        idf = {"a": 2.0, "b": 3.0, "c": 2.5, "d": 1.5}
        assert _idf_weighted_jaccard({"a", "b"}, {"c", "d"}, idf) == 0.0


# ── IDF modulation in CrossSourceAligner ─────────────────────────────────


class TestIDFInAligner:
    def test_common_token_substring_penalized(self):
        """Substring containing common token 'National' gets lower score."""
        # Many candidates with "National" → low IDF for "national"
        candidates_src_a = [
            _cand("National Guard", code_location="a"),
            _cand("National Security Council", code_location="a"),
            _cand("National Park Service", code_location="a"),
        ]
        candidates_src_b = [
            _cand("National Intelligence", code_location="b"),
        ]

        aligner = CrossSourceAligner()
        edges = aligner.align({"a": candidates_src_a, "b": candidates_src_b})

        # "National Intelligence" should NOT get a sameAs with "National Guard"
        # because "national" is common (low IDF) and they don't share enough
        same_as = [e for e in edges if e.alignment_type == AlignmentType.SAME_AS]
        # At most possibleSameAs, not sameAs
        assert len(same_as) == 0

    def test_rare_token_boosts_alignment(self):
        """Candidates sharing rare token get stronger alignment."""
        aligner = CrossSourceAligner()
        sources = {
            "a": [_cand("Pelosi", code_location="a", aliases=["Nancy Pelosi"])],
            "b": [_cand("Nancy Pelosi", code_location="b")],
        }
        edges = aligner.align(sources)
        # Exact name match via alias — should still work
        assert any(e.alignment_type == AlignmentType.SAME_AS for e in edges)


# ── Cluster Coherence Tests ──────────────────────────────────────────────


def _edge(src: str, tgt: str, score: float) -> AlignmentEdge:
    return AlignmentEdge(
        source_entity_id=src,
        target_entity_id=tgt,
        alignment_type=AlignmentType.SAME_AS,
        score=score,
        evidence=["exact_name"],
        method="test",
    )


class TestClusterCoherence:
    def test_small_cluster_passthrough(self):
        """Clusters <= 2 members always pass coherence."""
        result = check_cluster_coherence(["a", "b"], [])
        assert result == ["a", "b"]

    def test_singleton_passthrough(self):
        result = check_cluster_coherence(["a"], [])
        assert result == ["a"]

    def test_coherent_cluster_unchanged(self):
        """All members have strong edges → no ejection."""
        edges = [_edge("a", "b", 0.9), _edge("b", "c", 0.8), _edge("a", "c", 0.7)]
        result = check_cluster_coherence(["a", "b", "c"], edges)
        assert set(result) == {"a", "b", "c"}

    def test_weak_member_ejected(self):
        """Member with no strong edge to cluster gets ejected."""
        edges = [
            _edge("a", "b", 0.9),
            _edge("b", "c", 0.8),
            # "d" only has a very weak edge to "c"
            _edge("c", "d", 0.2),
        ]
        result = check_cluster_coherence(["a", "b", "c", "d"], edges, min_pairwise_score=0.45)
        assert "d" not in result
        assert set(result) == {"a", "b", "c"}

    def test_no_edges_keeps_all(self):
        """If no edges at all (degenerate), keep all rather than eject all."""
        result = check_cluster_coherence(["a", "b", "c"], [])
        assert set(result) == {"a", "b", "c"}

    def test_all_weak_keeps_all(self):
        """If all edges are weak, keep all (degenerate case)."""
        edges = [_edge("a", "b", 0.1), _edge("b", "c", 0.1)]
        result = check_cluster_coherence(["a", "b", "c"], edges, min_pairwise_score=0.5)
        # All would be ejected → fallback keeps all
        assert set(result) == {"a", "b", "c"}

    def test_transitive_closure_weak_link(self):
        """Classic transitive closure problem: A↔B strong, B↔C strong, A↔C absent."""
        edges = [
            _edge("a", "b", 0.9),
            _edge("b", "c", 0.8),
            # No direct a↔c edge
        ]
        result = check_cluster_coherence(["a", "b", "c"], edges, min_pairwise_score=0.45)
        # a has edge to b (0.9) ✓, b has edges to a+c ✓, c has edge to b (0.8) ✓
        # All members have at least one qualifying edge
        assert set(result) == {"a", "b", "c"}

    def test_isolated_member_ejected(self):
        """Member with no edges at all in the cluster gets ejected."""
        edges = [_edge("a", "b", 0.9)]
        result = check_cluster_coherence(["a", "b", "c"], edges, min_pairwise_score=0.45)
        assert "c" not in result
        assert set(result) == {"a", "b"}

    def test_custom_threshold(self):
        """Higher threshold ejects more members."""
        edges = [
            _edge("a", "b", 0.9),
            _edge("b", "c", 0.5),
        ]
        # With threshold 0.45: c's best edge is 0.5 ≥ 0.45 → keep
        result_low = check_cluster_coherence(["a", "b", "c"], edges, min_pairwise_score=0.45)
        assert "c" in result_low

        # With threshold 0.6: c's best edge is 0.5 < 0.6 → eject
        result_high = check_cluster_coherence(["a", "b", "c"], edges, min_pairwise_score=0.6)
        assert "c" not in result_high

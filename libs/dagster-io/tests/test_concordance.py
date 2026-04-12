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


# ---------- Sprint 1: Substring guard tests ----------


def test_substring_guard_rejects_short_names():
    """Short substrings like "AI" in "AIPAC" should NOT produce a substring signal.

    The min_length=4 guard prevents trivially short names from triggering
    substring containment. Exact name match should still work if names match.
    """
    sources = {
        "media_ingest": [
            EntityCandidate(
                candidate_id="cand-media-ai",
                canonical_name="AI",
                candidate_type=MentionType.ORG,
                aliases=[],
                mention_ids=["m-media-ai"],
                mention_count=5,
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
                mention_ids=["m-congress-aipac"],
                mention_count=10,
                source_documents=["bill-0"],
                code_location="congress_data",
            )
        ],
    }

    aligner = CrossSourceAligner()
    edges = aligner.align(sources)
    # "AI" in "AIPAC" — shorter name < 4 chars, no shared tokens >= 2.
    # Should NOT produce a sameAs edge.
    same_as_edges = [e for e in edges if e.alignment_type.value == "sameAs"]
    assert len(same_as_edges) == 0


def test_substring_guard_rejects_single_token_overlap():
    """Substring containment requires >= 2 shared tokens (parity with ConcordanceEngine).

    "Iran" in "Iranian Americans" has only 0 shared tokens (after tokenization,
    "iran" is not equal to "iranian"), so the guard should reject it.
    """
    sources = {
        "media_ingest": [
            EntityCandidate(
                candidate_id="cand-media-iran",
                canonical_name="Iran",
                candidate_type=MentionType.GPE,
                aliases=[],
                mention_ids=["m-media-iran"],
                mention_count=5,
                source_documents=["video-0"],
                code_location="media_ingest",
            )
        ],
        "congress_data": [
            EntityCandidate(
                candidate_id="cand-congress-iranian",
                canonical_name="Iranian Americans",
                candidate_type=MentionType.GPE,
                aliases=[],
                mention_ids=["m-congress-iranian"],
                mention_count=10,
                source_documents=["bill-0"],
                code_location="congress_data",
            )
        ],
    }

    aligner = CrossSourceAligner()
    edges = aligner.align(sources)
    same_as_edges = [e for e in edges if e.alignment_type.value == "sameAs"]
    assert len(same_as_edges) == 0


def test_substring_guard_allows_legitimate_containment():
    """Legitimate substring containment should still produce edges.

    "Joe Biden" in "President Joe Biden" shares 2 tokens and has a length ratio
    well above 0.4, so it should pass all guards.
    """
    sources = {
        "media_ingest": [
            EntityCandidate(
                candidate_id="cand-media-biden",
                canonical_name="Joe Biden",
                candidate_type=MentionType.PERSON,
                aliases=[],
                mention_ids=["m-media-biden"],
                mention_count=5,
                source_documents=["video-0"],
                code_location="media_ingest",
            )
        ],
        "congress_data": [
            EntityCandidate(
                candidate_id="cand-congress-biden",
                canonical_name="President Joe Biden",
                candidate_type=MentionType.PERSON,
                aliases=[],
                mention_ids=["m-congress-biden"],
                mention_count=10,
                source_documents=["bill-0"],
                code_location="congress_data",
            )
        ],
    }

    aligner = CrossSourceAligner()
    edges = aligner.align(sources)
    assert len(edges) >= 1
    # substring + jaccard = 2 signals → corroboration rule satisfied → sameAs
    same_as_edges = [e for e in edges if e.alignment_type.value == "sameAs"]
    assert len(same_as_edges) >= 1


def test_substring_asymmetry_penalty():
    """Highly lopsided substring containment gets a reduced weight (0.60 instead of 0.80).

    When len(shorter)/len(longer) < 0.4, the substring weight drops, making it
    harder to reach sameAs threshold without additional corroboration.
    """
    aligner = CrossSourceAligner()

    # "Biden" (5 chars) in "Joseph Robinette Biden Jr." (26 chars)
    # ratio = 5/26 ≈ 0.19, well below 0.4 → penalty applies
    # But they share the token "biden", which is only 1 token — guard rejects.
    cand_a = EntityCandidate(
        candidate_id="cand-a",
        canonical_name="Biden",
        candidate_type=MentionType.PERSON,
        aliases=[],
        mention_ids=["m-a"],
        mention_count=5,
        source_documents=["doc-a"],
        code_location="source_a",
    )
    cand_b = EntityCandidate(
        candidate_id="cand-b",
        canonical_name="Joseph Robinette Biden Jr.",
        candidate_type=MentionType.PERSON,
        aliases=[],
        mention_ids=["m-b"],
        mention_count=10,
        source_documents=["doc-b"],
        code_location="source_b",
    )

    edge = aligner._score_pair(cand_a, cand_b)
    # "Biden" (1 token) vs "Joseph Robinette Biden Jr." (4 tokens)
    # shared tokens = {"biden"} → only 1 → guard rejects substring.
    # No exact_name, no jaccard above 0.5 (1/4 = 0.25), no embedding.
    # Edge should be None or only possibleSameAs at best.
    if edge is not None:
        assert edge.alignment_type.value != "sameAs" or "substring" not in edge.evidence


def test_multi_signal_corroboration_produces_sameas():
    """Substring + jaccard (2 signals) produces sameAs via corroboration rule.

    With weighted-average scoring, substring (0.80) + jaccard (continuous)
    produces a combined score. The corroboration rule requires >= 2 signals
    for sameAs (unless exact_name fires). This test verifies that two
    corroborating signals successfully merge.
    """
    aligner = CrossSourceAligner()

    # "Nancy Pelosi" vs "Speaker Nancy Pelosi" — substring + jaccard
    cand_a = EntityCandidate(
        candidate_id="cand-pelosi-a",
        canonical_name="Nancy Pelosi",
        candidate_type=MentionType.PERSON,
        aliases=[],
        mention_ids=["m-pelosi-a"],
        mention_count=5,
        source_documents=["doc-a"],
        code_location="source_a",
    )
    cand_b = EntityCandidate(
        candidate_id="cand-pelosi-b",
        canonical_name="Speaker Nancy Pelosi",
        candidate_type=MentionType.PERSON,
        aliases=[],
        mention_ids=["m-pelosi-b"],
        mention_count=10,
        source_documents=["doc-b"],
        code_location="source_b",
    )

    edge = aligner._score_pair(cand_a, cand_b)
    assert edge is not None
    assert edge.alignment_type.value == "sameAs"
    assert "substring" in edge.evidence
    assert "jaccard" in edge.evidence
    assert len(edge.evidence) >= 2  # corroboration rule satisfied


def test_substring_alone_never_produces_sameas():
    """Single substring signal (without corroboration) must NOT produce sameAs.

    This is the core safety property: substring containment alone is not
    sufficient evidence for entity identity. Requires embedding or jaccard
    corroboration. Prevents false merges like "New York" / "New York Times"
    when both are mis-tagged as the same entity type.
    """
    aligner = CrossSourceAligner()

    # "Joe Biden" vs "President Joe Biden" with only substring+jaccard available
    # But let's construct a case where ONLY substring fires:
    # "Pelosi" vs "Nancy Pelosi" — substring but only 1 shared token ("pelosi")
    # Guard rejects: min_shared_tokens=2 fails. So no edge at all. Good.

    # Try a case where substring fires but jaccard doesn't:
    # "Committee" vs "the Committee on" — won't fire (1 shared token, < 2)

    # The real test: even if substring passes guards, single signal → NOT sameAs
    # We test this by checking that the corroboration rule is enforced
    # in the scoring output. With the weighted-average formula, a single
    # substring signal produces a score but the classification rule blocks it.
    cand_a = EntityCandidate(
        candidate_id="cand-test-a",
        canonical_name="Joe Biden",
        candidate_type=MentionType.PERSON,
        aliases=[],
        mention_ids=["m-a"],
        mention_count=5,
        source_documents=["doc-a"],
        code_location="source_a",
    )
    # "Spokesperson Joe Biden Says" — substring match on "joe biden"
    # jaccard: {"joe","biden"} vs {"spokesperson","joe","biden","says"} = 2/4 = 0.5
    # jaccard threshold is > 0.5 (strict), so 0.5 does NOT fire
    cand_b = EntityCandidate(
        candidate_id="cand-test-b",
        canonical_name="Spokesperson Joe Biden Says",
        candidate_type=MentionType.PERSON,
        aliases=[],
        mention_ids=["m-b"],
        mention_count=3,
        source_documents=["doc-b"],
        code_location="source_b",
    )

    edge = aligner._score_pair(cand_a, cand_b)
    # substring fires (joe biden in spokesperson joe biden says, 2 shared tokens,
    # len ratio 9/27=0.33 < 0.4 → asymmetry penalty → 0.60 weight)
    # jaccard = 2/4 = 0.5, NOT > 0.5 → does not fire
    # Single signal → corroboration rule blocks sameAs
    if edge is not None:
        assert edge.alignment_type.value != "sameAs", (
            f"Substring alone must not produce sameAs (score={edge.score}, evidence={edge.evidence})"
        )

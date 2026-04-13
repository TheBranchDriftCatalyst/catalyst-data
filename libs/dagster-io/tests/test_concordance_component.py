"""Layer 2: Component tests — one engine end-to-end.

Tests ConcordanceEngine.resolve() and CrossSourceAligner._score_pair()
with controlled inputs and specific behavioral assertions.
"""

from __future__ import annotations

from concordance_helpers import make_candidate, make_embedding, make_mention, make_similar_embedding

from dagster_io.concordance import ConcordanceEngine, CrossSourceAligner
from dagster_io.models import MentionType

# ── ConcordanceEngine.resolve() ────────────────────────────────────────────


def test_resolve_empty_mentions():
    engine = ConcordanceEngine()
    assert engine.resolve([], "test") == []


def test_resolve_single_mention():
    engine = ConcordanceEngine()
    mentions = [make_mention("Donald Trump", MentionType.PERSON)]
    candidates = engine.resolve(mentions, "test")
    assert len(candidates) == 1
    assert candidates[0].mention_count == 1
    assert candidates[0].canonical_name == "Donald Trump"


def test_resolve_exact_merge_across_documents():
    """Same text from different documents merges via Pass 1."""
    engine = ConcordanceEngine()
    mentions = [
        make_mention("Donald Trump", MentionType.PERSON, "doc-1", "c0"),
        make_mention("Donald Trump", MentionType.PERSON, "doc-2", "c0"),
    ]
    candidates = engine.resolve(mentions, "test")
    assert len(candidates) == 1
    assert candidates[0].mention_count == 2
    assert set(candidates[0].source_documents) == {"doc-1", "doc-2"}


def test_resolve_type_separation():
    """Different entity types must produce separate candidates."""
    engine = ConcordanceEngine()
    mentions = [
        make_mention("Washington", MentionType.PERSON, "doc-1", "c0"),
        make_mention("Washington", MentionType.GPE, "doc-1", "c1"),
    ]
    candidates = engine.resolve(mentions, "test")
    assert len(candidates) == 2
    types = {c.candidate_type for c in candidates}
    assert types == {MentionType.PERSON, MentionType.GPE}


def test_resolve_pass2_two_token_container_merges():
    """'New York' + 'New York State' (same GPE) merges — 2 shared tokens."""
    engine = ConcordanceEngine()
    mentions = [
        make_mention("New York", MentionType.GPE, "doc-1", "c0"),
        make_mention("New York State", MentionType.GPE, "doc-1", "c1"),
    ]
    candidates = engine.resolve(mentions, "test")
    assert len(candidates) == 1
    assert candidates[0].mention_count == 2


def test_resolve_pass2_rejects_single_shared_token():
    """'Trump' + 'Donald Trump' — shared token = 1, min_shared_tokens = 2.

    ConcordanceEngine Pass 2 has min_shared_tokens=2 (fixed), so single-token
    names like 'Trump' won't merge via substring with 'Donald Trump'.
    They only merge via Pass 1 (exact) if text matches exactly.
    """
    engine = ConcordanceEngine()
    mentions = [
        make_mention("Trump", MentionType.PERSON, "doc-1", "c0"),
        make_mention("Donald Trump", MentionType.PERSON, "doc-1", "c1"),
    ]
    candidates = engine.resolve(mentions, "test")
    # Without embeddings, these stay separate (Pass 2 rejects, Pass 3 jaccard 1/2 < 0.6)
    assert len(candidates) == 2


def test_resolve_pass3_jaccard_merge():
    """'Joe R Biden' and 'Joe Biden' — jaccard = 2/3 = 0.667 > 0.6."""
    engine = ConcordanceEngine()
    mentions = [
        make_mention("Joe R Biden", MentionType.PERSON, "doc-1", "c0"),
        make_mention("Joe Biden", MentionType.PERSON, "doc-1", "c1"),
    ]
    candidates = engine.resolve(mentions, "test")
    assert len(candidates) == 1
    assert candidates[0].mention_count == 2


def test_resolve_pass4_embedding_no_guards_bug():
    """THE BUG: 'Donald Trump' + 'Donald Rumsfeld' merge via embedding only.

    Current code (before fix): Pass 4 has zero guards, cosine=0.87 > 0.85
    triggers merge. After fix: shared tokens {'donald'} = 1 < 2 → rejected.
    """
    mentions = [
        make_mention("Donald Trump", MentionType.PERSON, "doc-1", "c0"),
        make_mention("Donald Rumsfeld", MentionType.PERSON, "doc-1", "c1"),
    ]

    base = make_embedding(seed=10)
    embeddings = {
        "donald trump": base,
        "donald rumsfeld": make_similar_embedding(base, 0.87),
    }

    engine = ConcordanceEngine()
    candidates = engine.resolve(mentions, "test", embeddings=embeddings)
    assert len(candidates) == 2, (
        f"'Donald Trump' and 'Donald Rumsfeld' must stay separate despite "
        f"embedding cosine=0.87. Got {len(candidates)}: "
        f"{[c.canonical_name for c in candidates]}"
    )


def test_resolve_pass4_embedding_legitimate_merge():
    """'Joe Biden' + 'President Joe Biden' — merges via Pass 4.

    Shared tokens {'joe', 'biden'} = 2 ≥ 2 → guard passes.
    Cosine = 0.92 > 0.85 → merge fires. True positive.
    """
    mentions = [
        make_mention("Joe Biden", MentionType.PERSON, "doc-1", "c0"),
        make_mention("President Joe Biden", MentionType.PERSON, "doc-1", "c1"),
    ]

    base = make_embedding(seed=20)
    embeddings = {
        "joe biden": base,
        "president joe biden": make_similar_embedding(base, 0.92),
    }

    engine = ConcordanceEngine()
    candidates = engine.resolve(mentions, "test", embeddings=embeddings)
    assert len(candidates) == 1, (
        f"'Joe Biden' and 'President Joe Biden' should merge (2 shared tokens). "
        f"Got {len(candidates)}: {[c.canonical_name for c in candidates]}"
    )


def test_resolve_canonical_name_most_frequent():
    """Canonical name should be the most frequent surface form."""
    engine = ConcordanceEngine()
    mentions = [
        make_mention("Donald Trump", MentionType.PERSON, "doc-1", "c0"),
        make_mention("Donald Trump", MentionType.PERSON, "doc-1", "c1"),
        make_mention("Donald Trump", MentionType.PERSON, "doc-1", "c2"),
        make_mention("donald trump", MentionType.PERSON, "doc-1", "c3"),
    ]
    candidates = engine.resolve(mentions, "test")
    assert len(candidates) == 1
    assert candidates[0].canonical_name == "Donald Trump"


# ── CrossSourceAligner._score_pair() ───────────────────────────────────────


def test_score_pair_exact_name_is_sameas():
    aligner = CrossSourceAligner()
    a = make_candidate("AIPAC", MentionType.ORG, "source_a")
    b = make_candidate("AIPAC", MentionType.ORG, "source_b")
    edge = aligner._score_pair(a, b)
    assert edge is not None
    assert edge.alignment_type.value == "sameAs"
    assert "exact_name" in edge.evidence


def test_score_pair_no_signals_returns_none():
    aligner = CrossSourceAligner()
    a = make_candidate("Donald Trump", MentionType.PERSON, "source_a")
    b = make_candidate("Nancy Pelosi", MentionType.PERSON, "source_b")
    edge = aligner._score_pair(a, b)
    assert edge is None

"""Layer 4: Regression tests for specific production bugs.

These tests are written FIRST (TDD red phase) and should FAIL on the
current code, then PASS after the ConcordanceEngine guard fix.
"""

from __future__ import annotations

from concordance_helpers import make_embedding, make_mention, make_similar_embedding

from dagster_io.concordance import ConcordanceEngine
from dagster_io.models import MentionType


def test_regression_trump_rumsfeld_transitive_merge():
    """PRODUCTION BUG: 'Donald Trump' merged with 'Donald Rumsfeld'.

    Root cause: ConcordanceEngine Pass 4 (Embedding) has zero guards.
    Embeddings for political figures have >0.85 cosine similarity,
    causing direct merge. 'Donald' as a short bridge name amplifies
    the problem via transitive closure.

    After fix: shared tokens {'donald'} = 1 < min_required = 2
    → Pass 4 rejects the merge.
    """
    mentions = [
        make_mention("Donald Trump", MentionType.PERSON, "vid-1", "chunk-1"),
        make_mention("Trump", MentionType.PERSON, "vid-1", "chunk-2"),
        make_mention("Donald Rumsfeld", MentionType.PERSON, "vid-2", "chunk-1"),
        make_mention("Rumsfeld", MentionType.PERSON, "vid-2", "chunk-2"),
        make_mention("Donald", MentionType.PERSON, "vid-3", "chunk-1"),
    ]

    base_trump = make_embedding(seed=42)
    embeddings = {
        "donald trump": base_trump,
        "trump": make_similar_embedding(base_trump, 0.92),
        "donald rumsfeld": make_similar_embedding(base_trump, 0.87),  # above 0.85!
        "rumsfeld": make_similar_embedding(base_trump, 0.70),
        "donald": make_similar_embedding(base_trump, 0.88),
    }

    engine = ConcordanceEngine()
    candidates = engine.resolve(mentions, "media_ingest", embeddings=embeddings)

    # Core assertion: no candidate contains both Trump and Rumsfeld
    for cand in candidates:
        all_names = {cand.canonical_name.lower()} | {a.lower() for a in cand.aliases}
        has_trump = any("trump" in n for n in all_names)
        has_rumsfeld = any("rumsfeld" in n for n in all_names)
        assert not (has_trump and has_rumsfeld), (
            f"FALSE MERGE: candidate '{cand.canonical_name}' contains both Trump and Rumsfeld. aliases={cand.aliases}"
        )

    # Should have at least 2 PERSON candidates (Trump cluster + Rumsfeld cluster)
    person_candidates = [c for c in candidates if c.candidate_type == MentionType.PERSON]
    assert len(person_candidates) >= 2, (
        f"Expected at least 2 PERSON candidates, got {len(person_candidates)}: "
        f"{[c.canonical_name for c in person_candidates]}"
    )


def test_regression_new_york_container_same_type():
    """'New York' + 'New York Times' + 'New York State' all tagged as GPE.

    When entity types are mis-tagged (NYT as GPE instead of ORG),
    substring containment with 2 shared tokens merges them.

    This test documents the known limitation: type separation is the
    real guard for this pattern. With correct types, NYT (ORG) stays
    separate from NY (GPE).
    """
    engine = ConcordanceEngine()

    # Correct types — NYT is ORG, NY/NYS are GPE
    correct_mentions = [
        make_mention("New York", MentionType.GPE, "doc-1", "c0"),
        make_mention("New York Times", MentionType.ORG, "doc-1", "c1"),
        make_mention("New York State", MentionType.GPE, "doc-1", "c2"),
    ]
    candidates = engine.resolve(correct_mentions, "test")
    # NYT (ORG) must be separate from NY/NYS (GPE)
    org_cands = [c for c in candidates if c.candidate_type == MentionType.ORG]
    gpe_cands = [c for c in candidates if c.candidate_type == MentionType.GPE]
    assert len(org_cands) >= 1, "NYT (ORG) should produce at least one ORG candidate"
    assert len(gpe_cands) >= 1, "NY/NYS (GPE) should produce at least one GPE candidate"


def test_regression_title_prefix_bridging():
    """'President Biden' and 'President Trump' must NOT merge.

    They share only 1 token ('president'). Without embeddings, Pass 2/3
    cannot merge them. With embeddings, Pass 4 (current code) could
    merge them if cosine > 0.85 due to shared political context.

    After fix: shared tokens {'president'} = 1 < min_required = 2
    → Pass 4 rejects.
    """
    mentions = [
        make_mention("President Biden", MentionType.PERSON, "doc-1", "c0"),
        make_mention("President Trump", MentionType.PERSON, "doc-1", "c1"),
    ]

    # Craft embeddings so they're similar (shared political context)
    base = make_embedding(seed=99)
    embeddings = {
        "president biden": base,
        "president trump": make_similar_embedding(base, 0.86),
    }

    engine = ConcordanceEngine()
    candidates = engine.resolve(mentions, "test", embeddings=embeddings)

    assert len(candidates) == 2, (
        f"'President Biden' and 'President Trump' must stay separate, "
        f"got {len(candidates)}: {[c.canonical_name for c in candidates]}"
    )

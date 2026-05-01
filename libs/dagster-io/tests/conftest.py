"""Shared fixtures for dagster-io concordance tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Suppress OTEL metric exports to unreachable cluster endpoints during tests
os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")

import pytest

from dagster_io.models import MentionType

# Ensure tests/ directory is importable for shared helpers
sys.path.insert(0, str(Path(__file__).parent))

from concordance_helpers import (  # noqa: E402, F401
    make_candidate,
    make_embedding,
    make_mention,
    make_similar_embedding,
)

# ── Production-realistic fixtures ───────────────────────────────────────────


@pytest.fixture
def political_mentions() -> list:
    """Realistic political-domain mention set simulating media_ingest extraction.

    Covers: Trump variants, Rumsfeld, Biden, Pelosi, geographic ambiguity,
    organization names, short names, asymmetric containment.
    """
    return [
        # Trump cluster — should merge into one candidate
        make_mention("Donald Trump", MentionType.PERSON, "vid-1", "chunk-1"),
        make_mention("Trump", MentionType.PERSON, "vid-1", "chunk-2"),
        make_mention("donald trump", MentionType.PERSON, "vid-2", "chunk-1"),
        make_mention("President Trump", MentionType.PERSON, "vid-3", "chunk-1"),
        # Rumsfeld cluster — must stay separate from Trump
        make_mention("Donald Rumsfeld", MentionType.PERSON, "vid-4", "chunk-1"),
        make_mention("Rumsfeld", MentionType.PERSON, "vid-4", "chunk-2"),
        # "Donald" alone — the bridge danger
        make_mention("Donald", MentionType.PERSON, "vid-5", "chunk-1"),
        # Biden cluster
        make_mention("Joe Biden", MentionType.PERSON, "vid-1", "chunk-3"),
        make_mention("Biden", MentionType.PERSON, "vid-2", "chunk-3"),
        make_mention("President Biden", MentionType.PERSON, "vid-3", "chunk-3"),
        # Pelosi
        make_mention("Nancy Pelosi", MentionType.PERSON, "vid-6", "chunk-1"),
        make_mention("Speaker Pelosi", MentionType.PERSON, "vid-6", "chunk-2"),
        # Geographic ambiguity
        make_mention("Washington", MentionType.GPE, "vid-7", "chunk-1"),
        make_mention("Washington D.C.", MentionType.GPE, "vid-7", "chunk-2"),
        make_mention("George Washington", MentionType.PERSON, "vid-7", "chunk-3"),
        # Organization names
        make_mention("New York Times", MentionType.ORG, "vid-8", "chunk-1"),
        make_mention("New York", MentionType.GPE, "vid-8", "chunk-2"),
        make_mention("New York State", MentionType.GPE, "vid-8", "chunk-3"),
        # Short names (should be protected by guards)
        make_mention("AI", MentionType.ORG, "vid-9", "chunk-1"),
        make_mention("AIPAC", MentionType.ORG, "vid-9", "chunk-2"),
        # Asymmetric containment
        make_mention("Iran", MentionType.GPE, "vid-10", "chunk-1"),
        make_mention("Iranian Americans", MentionType.NORP, "vid-10", "chunk-2"),
    ]


@pytest.fixture
def political_embeddings() -> dict[str, list[float]]:
    """Embeddings that simulate the Trump/Rumsfeld production bug.

    "donald trump" and "donald rumsfeld" are set to 0.87 cosine similarity
    (above the 0.85 threshold) because they share political context in
    real embedding spaces.
    """
    base_trump = make_embedding(seed=1)
    base_biden = make_embedding(seed=2)
    base_pelosi = make_embedding(seed=3)
    base_washington = make_embedding(seed=4)
    base_nyt = make_embedding(seed=5)
    return {
        "donald trump": base_trump,
        "trump": make_similar_embedding(base_trump, 0.92),
        "president trump": make_similar_embedding(base_trump, 0.90),
        "donald rumsfeld": make_similar_embedding(base_trump, 0.87),  # THE BUG
        "rumsfeld": make_similar_embedding(base_trump, 0.70),
        "donald": make_similar_embedding(base_trump, 0.88),
        "joe biden": base_biden,
        "biden": make_similar_embedding(base_biden, 0.91),
        "president biden": make_similar_embedding(base_biden, 0.89),
        "nancy pelosi": base_pelosi,
        "speaker pelosi": make_similar_embedding(base_pelosi, 0.88),
        "washington": base_washington,
        "washington d.c.": make_similar_embedding(base_washington, 0.93),
        "george washington": make_similar_embedding(base_washington, 0.80),
        "new york times": base_nyt,
        "new york": make_similar_embedding(base_nyt, 0.86),
        "new york state": make_similar_embedding(base_nyt, 0.84),
    }


@pytest.fixture
def congress_candidates() -> list:
    """EntityCandidates from congress_data for cross-source integration tests."""
    return [
        make_candidate(
            "Donald J. Trump",
            MentionType.PERSON,
            "congress_data",
            aliases=["Donald Trump", "Trump"],
            mention_count=200,
        ),
        make_candidate(
            "Donald Rumsfeld",
            MentionType.PERSON,
            "congress_data",
            aliases=["Rumsfeld", "Secretary Rumsfeld"],
            mention_count=50,
        ),
        make_candidate(
            "Joseph R. Biden",
            MentionType.PERSON,
            "congress_data",
            aliases=["Joe Biden", "Biden", "President Biden"],
            mention_count=300,
        ),
        make_candidate(
            "Nancy Pelosi",
            MentionType.PERSON,
            "congress_data",
            aliases=["Speaker Pelosi"],
            mention_count=100,
        ),
        make_candidate(
            "New York Times",
            MentionType.ORG,
            "congress_data",
            aliases=["NYT", "The New York Times"],
            mention_count=30,
        ),
        make_candidate(
            "New York",
            MentionType.GPE,
            "congress_data",
            aliases=["NY", "New York State"],
            mention_count=150,
        ),
    ]

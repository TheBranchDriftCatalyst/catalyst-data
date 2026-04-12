"""Tests for speaker embedding + profile models and clustering logic.

These tests exercise pure functions — no Dagster, pyannote, or postgres
required. They validate the Pydantic models roundtrip and the
cluster_embeddings() function merges/separates voices correctly.
"""

import numpy as np
import pytest
from media_ingest.assets.speaker_profiles import (
    _cosine_distance,
    _make_profile_id,
    cluster_embeddings,
)

from dagster_io.models import SpeakerEmbedding, SpeakerProfile


def _random_unit_vector(dim: int = 192, seed: int | None = None) -> list[float]:
    """Generate a random unit vector for testing."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim)
    v = v / np.linalg.norm(v)
    return v.tolist()


def _similar_vector(base: list[float], noise_scale: float = 0.02, seed: int | None = None) -> list[float]:
    """Generate a vector similar to base by adding small noise."""
    rng = np.random.default_rng(seed)
    v = np.array(base) + rng.standard_normal(len(base)) * noise_scale
    v = v / np.linalg.norm(v)
    return v.tolist()


# ── Model roundtrip tests ──────────────────────────────────────────────────────


def test_speaker_embedding_model():
    """Verify SpeakerEmbedding Pydantic model roundtrips."""
    centroid = _random_unit_vector(192, seed=42)
    emb = SpeakerEmbedding(
        partition_key="doc-001",
        local_label="SPEAKER_00",
        centroid=centroid,
        segment_count=5,
        total_duration_s=120.5,
    )
    data = emb.model_dump()
    restored = SpeakerEmbedding(**data)
    assert restored.partition_key == "doc-001"
    assert restored.local_label == "SPEAKER_00"
    assert len(restored.centroid) == 192
    assert restored.segment_count == 5
    assert restored.total_duration_s == 120.5


def test_speaker_profile_model():
    """Verify SpeakerProfile Pydantic model roundtrips."""
    centroid = _random_unit_vector(192, seed=99)
    prof = SpeakerProfile(
        profile_id="abc123def456",
        centroid=centroid,
        display_name="Alice",
        member_count=3,
        total_duration_s=600.0,
        first_seen="2026-01-01T00:00:00+00:00",
        last_seen="2026-04-01T00:00:00+00:00",
        members=[
            {"document_id": "doc-001", "local_label": "SPEAKER_00", "segment_count": 5},
            {"document_id": "doc-002", "local_label": "SPEAKER_01", "segment_count": 3},
        ],
    )
    data = prof.model_dump()
    restored = SpeakerProfile(**data)
    assert restored.profile_id == "abc123def456"
    assert restored.display_name == "Alice"
    assert restored.member_count == 3
    assert len(restored.members) == 2
    assert restored.members[0]["document_id"] == "doc-001"


# ── Clustering logic tests ─────────────────────────────────────────────────────


def test_cluster_merges_same_voice():
    """Three embeddings with very similar centroids should merge into 1 profile."""
    base = _random_unit_vector(192, seed=10)
    embeddings = [
        SpeakerEmbedding(
            partition_key=f"doc-{i:03d}",
            local_label="SPEAKER_00",
            centroid=_similar_vector(base, noise_scale=0.01, seed=i + 100),
            segment_count=3,
            total_duration_s=60.0,
        )
        for i in range(3)
    ]

    profiles, merge_distances = cluster_embeddings(embeddings, existing_profiles=[], threshold=0.25)

    assert len(profiles) == 1, f"Expected 1 profile, got {len(profiles)}"
    assert profiles[0].member_count == 3
    assert len(profiles[0].members) == 3
    # All merge distances should be small
    for d in merge_distances:
        assert d < 0.25


def test_cluster_separates_different_voices():
    """Two embeddings with very different centroids should become 2 profiles."""
    # Use orthogonal-ish vectors
    v1 = np.zeros(192)
    v1[0] = 1.0  # unit vector along axis 0
    v2 = np.zeros(192)
    v2[1] = 1.0  # unit vector along axis 1

    embeddings = [
        SpeakerEmbedding(
            partition_key="doc-001",
            local_label="SPEAKER_00",
            centroid=v1.tolist(),
            segment_count=5,
            total_duration_s=100.0,
        ),
        SpeakerEmbedding(
            partition_key="doc-002",
            local_label="SPEAKER_01",
            centroid=v2.tolist(),
            segment_count=4,
            total_duration_s=80.0,
        ),
    ]

    profiles, merge_distances = cluster_embeddings(embeddings, existing_profiles=[], threshold=0.25)

    assert len(profiles) == 2, f"Expected 2 profiles, got {len(profiles)}"
    assert len(merge_distances) == 0  # no merges


def test_profile_id_deterministic():
    """Same centroid + first_seen should produce the same profile_id."""
    centroid = _random_unit_vector(192, seed=77)
    first_seen = "2026-01-15T12:00:00+00:00"

    id1 = _make_profile_id(centroid, first_seen)
    id2 = _make_profile_id(centroid, first_seen)

    assert id1 == id2
    assert len(id1) == 16  # sha1 truncated to 16 hex chars


def test_profile_id_varies_with_input():
    """Different centroid or first_seen should produce different IDs."""
    centroid_a = _random_unit_vector(192, seed=1)
    centroid_b = _random_unit_vector(192, seed=2)
    ts = "2026-01-15T12:00:00+00:00"

    assert _make_profile_id(centroid_a, ts) != _make_profile_id(centroid_b, ts)
    assert _make_profile_id(centroid_a, ts) != _make_profile_id(centroid_a, "2026-02-01T00:00:00+00:00")


def test_sticky_clustering_preserves_existing_profiles():
    """Existing profiles are not renumbered; new embeddings merge into them or seed new ones."""
    # Create an existing profile
    base_centroid = _random_unit_vector(192, seed=50)
    existing = SpeakerProfile(
        profile_id="existing_prof_01",
        centroid=base_centroid,
        display_name="Known Speaker",
        member_count=2,
        total_duration_s=200.0,
        first_seen="2026-01-01T00:00:00+00:00",
        last_seen="2026-03-01T00:00:00+00:00",
        members=[
            {"document_id": "doc-old-1", "local_label": "SPEAKER_00", "segment_count": 4},
            {"document_id": "doc-old-2", "local_label": "SPEAKER_01", "segment_count": 3},
        ],
    )

    # A similar embedding that should merge into the existing profile
    similar_emb = SpeakerEmbedding(
        partition_key="doc-new-1",
        local_label="SPEAKER_00",
        centroid=_similar_vector(base_centroid, noise_scale=0.01, seed=200),
        segment_count=5,
        total_duration_s=90.0,
    )

    # A very different embedding that should seed a new profile
    different_centroid = _random_unit_vector(192, seed=999)
    different_emb = SpeakerEmbedding(
        partition_key="doc-new-2",
        local_label="SPEAKER_01",
        centroid=different_centroid,
        segment_count=2,
        total_duration_s=30.0,
    )

    profiles, merge_distances = cluster_embeddings(
        [similar_emb, different_emb],
        existing_profiles=[existing],
        threshold=0.25,
    )

    assert len(profiles) == 2, f"Expected 2 profiles (1 existing + 1 new), got {len(profiles)}"

    # The existing profile should still be first with the same ID
    merged_prof = next(p for p in profiles if p.profile_id == "existing_prof_01")
    assert merged_prof.member_count == 3  # was 2, +1 merge
    assert merged_prof.display_name == "Known Speaker"  # preserved
    assert len(merged_prof.members) == 3  # 2 original + 1 new
    assert merged_prof.total_duration_s == pytest.approx(290.0, abs=0.1)

    # The new profile should have a fresh ID
    new_prof = next(p for p in profiles if p.profile_id != "existing_prof_01")
    assert new_prof.member_count == 1
    assert len(new_prof.members) == 1
    assert new_prof.members[0]["document_id"] == "doc-new-2"


def test_cosine_distance_identical_vectors():
    """Identical vectors should have cosine distance ~0."""
    v = _random_unit_vector(192, seed=42)
    assert _cosine_distance(v, v) == pytest.approx(0.0, abs=1e-10)


def test_cosine_distance_orthogonal_vectors():
    """Orthogonal vectors should have cosine distance ~1."""
    v1 = [1.0] + [0.0] * 191
    v2 = [0.0, 1.0] + [0.0] * 190
    assert _cosine_distance(v1, v2) == pytest.approx(1.0, abs=1e-10)


def test_empty_embeddings_returns_existing():
    """No embeddings should return copies of existing profiles unchanged."""
    existing = SpeakerProfile(
        profile_id="keep_me",
        centroid=_random_unit_vector(192, seed=1),
        member_count=1,
        total_duration_s=50.0,
        first_seen="2026-01-01T00:00:00+00:00",
        last_seen="2026-01-01T00:00:00+00:00",
    )
    profiles, distances = cluster_embeddings([], [existing])
    assert len(profiles) == 1
    assert profiles[0].profile_id == "keep_me"
    assert len(distances) == 0

"""Tests for speaker embedding + profile clustering logic."""

import numpy as np
import pytest
from media_ingest.assets.speaker_profiles import (
    _cosine_distance,
    _make_profile_id,
    cluster_embeddings,
)

from dagster_io.models import SpeakerEmbedding, SpeakerProfile


def _random_unit_vector(dim: int = 192, seed: int | None = None) -> list[float]:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim)
    return (v / np.linalg.norm(v)).tolist()


def _similar_vector(base: list[float], noise_scale: float = 0.02, seed: int | None = None) -> list[float]:
    rng = np.random.default_rng(seed)
    v = np.array(base) + rng.standard_normal(len(base)) * noise_scale
    return (v / np.linalg.norm(v)).tolist()


class TestModels:
    def test_speaker_embedding_roundtrip(self):
        centroid = _random_unit_vector(192, seed=42)
        emb = SpeakerEmbedding(
            partition_key="doc-001",
            local_label="SPEAKER_00",
            centroid=centroid,
            segment_count=5,
            total_duration_s=120.5,
        )
        restored = SpeakerEmbedding(**emb.model_dump())
        assert restored.partition_key == "doc-001"
        assert len(restored.centroid) == 192

    def test_speaker_profile_roundtrip(self):
        prof = SpeakerProfile(
            profile_id="abc123def456",
            centroid=_random_unit_vector(192, seed=99),
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
        restored = SpeakerProfile(**prof.model_dump())
        assert restored.display_name == "Alice"
        assert len(restored.members) == 2


class TestClustering:
    def test_merges_same_voice(self):
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
        assert len(profiles) == 1
        assert profiles[0].member_count == 3
        for d in merge_distances:
            assert d < 0.25

    def test_separates_different_voices(self):
        v1 = np.zeros(192)
        v1[0] = 1.0
        v2 = np.zeros(192)
        v2[1] = 1.0
        embeddings = [
            SpeakerEmbedding(
                partition_key="doc-001", local_label="S0", centroid=v1.tolist(), segment_count=5, total_duration_s=100.0
            ),
            SpeakerEmbedding(
                partition_key="doc-002", local_label="S1", centroid=v2.tolist(), segment_count=4, total_duration_s=80.0
            ),
        ]
        profiles, merge_distances = cluster_embeddings(embeddings, existing_profiles=[], threshold=0.25)
        assert len(profiles) == 2
        assert len(merge_distances) == 0

    def test_sticky_preserves_existing(self):
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
        similar = SpeakerEmbedding(
            partition_key="doc-new-1",
            local_label="S0",
            centroid=_similar_vector(base_centroid, noise_scale=0.01, seed=200),
            segment_count=5,
            total_duration_s=90.0,
        )
        different = SpeakerEmbedding(
            partition_key="doc-new-2",
            local_label="S1",
            centroid=_random_unit_vector(192, seed=999),
            segment_count=2,
            total_duration_s=30.0,
        )
        profiles, _ = cluster_embeddings([similar, different], existing_profiles=[existing], threshold=0.25)
        assert len(profiles) == 2
        merged = next(p for p in profiles if p.profile_id == "existing_prof_01")
        assert merged.member_count == 3
        assert merged.display_name == "Known Speaker"

    def test_empty_embeddings_returns_existing(self):
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


class TestCosineDistance:
    def test_identical(self):
        v = _random_unit_vector(192, seed=42)
        assert _cosine_distance(v, v) == pytest.approx(0.0, abs=1e-10)

    def test_orthogonal(self):
        v1 = [1.0] + [0.0] * 191
        v2 = [0.0, 1.0] + [0.0] * 190
        assert _cosine_distance(v1, v2) == pytest.approx(1.0, abs=1e-10)


class TestProfileId:
    def test_deterministic(self):
        centroid = _random_unit_vector(192, seed=77)
        assert _make_profile_id(centroid, "2026-01-15T12:00:00+00:00") == _make_profile_id(
            centroid, "2026-01-15T12:00:00+00:00"
        )

    def test_varies_with_input(self):
        a = _random_unit_vector(192, seed=1)
        b = _random_unit_vector(192, seed=2)
        ts = "2026-01-15T12:00:00+00:00"
        assert _make_profile_id(a, ts) != _make_profile_id(b, ts)

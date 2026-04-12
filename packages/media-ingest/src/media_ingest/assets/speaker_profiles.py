"""Gold: cross-file speaker profiles via sticky agglomerative clustering.

Unpartitioned fan-in asset consuming all partitions of
``media_speaker_embeddings`` via ``AllPartitionMapping``. Merges
per-document speaker centroids into stable cross-file profiles using
cosine distance with a configurable threshold (default 0.25).

The clustering logic is extracted into a pure function
``cluster_embeddings()`` that can be unit-tested without Dagster,
pyannote, or postgres.

Gated behind ``SPEAKER_PROFILE_ENABLED`` env var (default off).
"""

import hashlib
import os
from datetime import UTC, datetime
from typing import Any

import numpy as np
from dagster import AllPartitionMapping, AssetExecutionContext, AssetIn, MetadataValue, Output, asset

from dagster_io import SpeakerEmbedding, SpeakerProfile
from dagster_io.logging import get_logger
from dagster_io.metrics import (
    ASSET_RECORDS_PROCESSED,
    SPEAKER_PROFILE_MERGE_DISTANCE,
    SPEAKER_PROFILES_TOTAL,
)
from dagster_io.observability import get_tracer, trace_operation

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# Default cosine distance threshold for merging into existing profile.
# Per pyannote docs, ~0.25 is typical for same-speaker determination.
DEFAULT_MERGE_THRESHOLD = 0.25


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """Compute cosine distance between two vectors.

    Returns a value in [0, 2] where 0 = identical, 2 = opposite.
    """
    va = np.array(a)
    vb = np.array(b)
    dot = np.dot(va, vb)
    norm_a = np.linalg.norm(va)
    norm_b = np.linalg.norm(vb)
    if norm_a == 0 or norm_b == 0:
        return 1.0  # undefined, treat as maximally distant
    return 1.0 - (dot / (norm_a * norm_b))


def _make_profile_id(centroid: list[float], first_seen: str) -> str:
    """Deterministic profile ID from centroid bytes + first_seen ISO string."""
    centroid_bytes = np.array(centroid, dtype=np.float32).tobytes()
    payload = centroid_bytes + first_seen.encode("utf-8")
    return hashlib.sha1(payload).hexdigest()[:16]


def cluster_embeddings(
    embeddings: list[SpeakerEmbedding],
    existing_profiles: list[SpeakerProfile],
    threshold: float = DEFAULT_MERGE_THRESHOLD,
) -> list[SpeakerProfile]:
    """Sticky agglomerative clustering of speaker embeddings into profiles.

    For each embedding:
    1. Compute cosine distance to all existing profile centroids.
    2. If nearest distance < threshold: merge (weighted centroid update).
    3. Else: create a new profile.

    Existing profiles are preserved (sticky) — they are never renumbered
    or removed by this function.

    This is a pure function with no side effects — no DB, no pyannote,
    no Dagster context required. Designed for unit testing.
    """
    # Work on mutable copies of existing profiles
    profiles: list[SpeakerProfile] = [p.model_copy(deep=True) for p in existing_profiles]
    merge_distances: list[float] = []

    now_iso = datetime.now(UTC).isoformat()

    for emb in embeddings:
        if not emb.centroid:
            continue

        best_idx = -1
        best_distance = float("inf")

        for i, prof in enumerate(profiles):
            dist = _cosine_distance(emb.centroid, prof.centroid)
            if dist < best_distance:
                best_distance = dist
                best_idx = i

        if best_idx >= 0 and best_distance < threshold:
            # Merge into existing profile via weighted centroid average
            prof = profiles[best_idx]
            merge_distances.append(best_distance)

            old_weight = prof.member_count
            new_weight = 1
            total_weight = old_weight + new_weight

            old_centroid = np.array(prof.centroid)
            new_centroid = np.array(emb.centroid)
            merged = (old_centroid * old_weight + new_centroid * new_weight) / total_weight

            # Re-normalize
            norm = np.linalg.norm(merged)
            if norm > 0:
                merged = merged / norm

            prof.centroid = merged.tolist()
            prof.member_count = total_weight
            prof.total_duration_s = round(prof.total_duration_s + emb.total_duration_s, 2)
            prof.last_seen = now_iso
            prof.members.append(
                {
                    "document_id": emb.partition_key,
                    "local_label": emb.local_label,
                    "segment_count": emb.segment_count,
                }
            )
        else:
            # Create new profile
            first_seen = now_iso
            profile_id = _make_profile_id(emb.centroid, first_seen)

            new_prof = SpeakerProfile(
                profile_id=profile_id,
                centroid=emb.centroid,
                display_name=None,
                member_count=1,
                total_duration_s=emb.total_duration_s,
                first_seen=first_seen,
                last_seen=first_seen,
                members=[
                    {
                        "document_id": emb.partition_key,
                        "local_label": emb.local_label,
                        "segment_count": emb.segment_count,
                    }
                ],
            )
            profiles.append(new_prof)

    return profiles, merge_distances


def _flatten_partition_fanin(value, model_cls=None) -> list:
    """Flatten a partition fan-in dict into a flat list.

    Same helper as in canonical_entities.py — duplicated here to avoid
    cross-package imports between media_ingest and knowledge_graph.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        flat: list = []
        for part in value.values():
            if part is None:
                continue
            if isinstance(part, list):
                flat.extend(part)
            else:
                flat.append(part)
    elif isinstance(value, list):
        flat = value
    else:
        flat = [value]

    if model_cls is not None:
        flat = [model_cls(**item) if isinstance(item, dict) else item for item in flat]
    return flat


@asset(
    group_name="media_ingest",
    description="Cross-file speaker profiles via sticky agglomerative clustering",
    compute_kind="python",
    metadata={"layer": "gold"},
    ins={
        "media_speaker_embeddings": AssetIn(
            partition_mapping=AllPartitionMapping(),
            input_manager_key="optional_io_manager",
        ),
    },
)
def media_speaker_profiles(
    context: AssetExecutionContext,
    media_speaker_embeddings: Any,
) -> Output[list[SpeakerProfile]]:
    # Gate behind env var — skip if not enabled
    if os.environ.get("SPEAKER_PROFILE_ENABLED", "").lower() not in ("1", "true"):
        context.log.info("SPEAKER_PROFILE_ENABLED not set — skipping speaker profile clustering")
        return Output(
            [],
            metadata={
                "skipped": True,
                "reason": "SPEAKER_PROFILE_ENABLED not set",
            },
        )

    with trace_operation(
        "media_speaker_profiles",
        tracer,
        {
            "code_location": "media_ingest",
            "layer": "gold",
        },
    ):
        # Flatten fan-in dict into flat list of SpeakerEmbeddings
        all_embeddings = _flatten_partition_fanin(media_speaker_embeddings, SpeakerEmbedding)
        context.log.info(f"Received {len(all_embeddings)} speaker embeddings from all partitions")

        if not all_embeddings:
            return Output(
                [],
                metadata={
                    "embedding_count": 0,
                    "profile_count": 0,
                    "skipped": True,
                    "reason": "no_embeddings",
                },
            )

        # TODO (CD-34j.1 follow-up): Load existing profiles from pgvector
        # via GraphDBResource.load_speaker_profiles() for sticky clustering
        # across materializations. For v1, we cluster from scratch each run.
        # The pgvector dual-write below is best-effort for query performance;
        # the canonical output is the asset return value (S3 via MinioIOManager).
        existing_profiles: list[SpeakerProfile] = []

        profiles, merge_distances = cluster_embeddings(
            all_embeddings,
            existing_profiles,
            threshold=DEFAULT_MERGE_THRESHOLD,
        )

        # Emit metrics
        SPEAKER_PROFILES_TOTAL.set(len(profiles))
        for dist in merge_distances:
            SPEAKER_PROFILE_MERGE_DISTANCE.observe(dist)

        ASSET_RECORDS_PROCESSED.labels(
            code_location="media_ingest",
            asset_key="media_speaker_profiles",
            layer="gold",
        ).inc(len(profiles))

        context.log.info(
            f"Produced {len(profiles)} speaker profiles from {len(all_embeddings)} embeddings "
            f"({len(merge_distances)} merges, {len(all_embeddings) - len(merge_distances)} new)"
        )

        # TODO (CD-34j.1 follow-up): Best-effort pgvector dual-write.
        # Wrap in try/except so failure doesn't block asset materialization.
        # The primary persistence path is MinioIOManager → S3.

        return Output(
            profiles,
            metadata={
                "profile_count": len(profiles),
                "embedding_count": len(all_embeddings),
                "merge_count": len(merge_distances),
                "new_profile_count": len(all_embeddings) - len(merge_distances),
                "merge_distances": MetadataValue.json(
                    [round(d, 4) for d in merge_distances] if merge_distances else []
                ),
            },
        )

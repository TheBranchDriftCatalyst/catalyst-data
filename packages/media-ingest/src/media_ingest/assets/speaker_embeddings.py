"""Gold: per-speaker centroid embeddings from pyannote diarization.

Partitioned by document_id. Reads media_diarization segments + source
audio, then computes a 192-d centroid for each SPEAKER_XX by mean-pooling
per-segment embeddings from ``pyannote/embedding``.

Gated behind ``SPEAKER_PROFILE_ENABLED`` env var (default off) so the
asset is safe to deploy before GPU nodes are provisioned.

All pyannote imports are lazy (inside functions) so the module loads
without the GPU dependency installed.
"""

import os
import time
from typing import Any

import numpy as np
from dagster import AssetExecutionContext, AssetIn, MetadataValue, Output, asset

from dagster_io import SpeakerEmbedding
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED, MODEL_LOAD_DURATION
from dagster_io.observability import get_tracer, trace_operation
from media_ingest.assets.diarization import DIARIZATION_K8S_CONFIG
from media_ingest.assets.transcription import extract_audio_to_wav
from media_ingest.partitions import media_partitions

logger = get_logger(__name__)
tracer = get_tracer(__name__)

# Minimum segment duration to include in centroid computation.
# Segments shorter than this are too noisy for reliable embeddings.
_MIN_SEGMENT_DURATION_S = 3.0


def _load_embedding_model():
    """Lazy-load pyannote speaker embedding model.

    Importing pyannote.audio at module level would fail when the GPU
    dependency is not installed (e.g. in dev/test environments).
    """
    from pyannote.audio import Inference as SpeakerInference
    from pyannote.audio import Model

    t0 = time.monotonic()
    model = Model.from_pretrained("pyannote/embedding", use_auth_token=os.environ.get("HF_TOKEN", ""))
    inference = SpeakerInference(model, window="whole")
    MODEL_LOAD_DURATION.labels(model_type="pyannote_embedding").observe(time.monotonic() - t0)
    return inference


def _extract_speaker_centroids(
    diarization_output: dict[str, Any],
    source_path: str,
) -> list[SpeakerEmbedding]:
    """Compute per-speaker centroid embeddings from diarization segments.

    Returns a SpeakerEmbedding for each SPEAKER_XX that has at least one
    segment >= _MIN_SEGMENT_DURATION_S.
    """
    import soundfile as sf

    segments = diarization_output.get("segments", [])
    if not segments:
        return []

    # Group segments by speaker label
    speaker_segments: dict[str, list[dict]] = {}
    for seg in segments:
        spk = seg.get("speaker")
        if not spk:
            continue
        speaker_segments.setdefault(spk, []).append(seg)

    if not speaker_segments:
        return []

    # Extract audio to wav for soundfile reading
    wav_path = extract_audio_to_wav(source_path)
    try:
        audio_data, sample_rate = sf.read(wav_path)
        if audio_data.ndim > 1:
            audio_data = audio_data.mean(axis=1)  # mono

        inference = _load_embedding_model()

        results: list[SpeakerEmbedding] = []
        partition_key = diarization_output.get("document_id", "unknown")

        for label, segs in speaker_segments.items():
            # Filter short segments
            valid_segs = [s for s in segs if (s.get("end", 0) - s.get("start", 0)) >= _MIN_SEGMENT_DURATION_S]
            if not valid_segs:
                logger.info("Skipping speaker %s — no segments >= %.1fs", label, _MIN_SEGMENT_DURATION_S)
                continue

            embeddings = []
            total_duration = 0.0
            for seg in valid_segs:
                start_sample = int(seg["start"] * sample_rate)
                end_sample = int(seg["end"] * sample_rate)
                segment_audio = audio_data[start_sample:end_sample]
                duration = seg["end"] - seg["start"]
                total_duration += duration

                if len(segment_audio) < sample_rate:  # < 1s of actual audio
                    continue

                # pyannote Inference expects dict-like with "waveform" and "sample_rate"
                import torch

                waveform = torch.tensor(segment_audio, dtype=torch.float32).unsqueeze(0)
                embedding = inference({"waveform": waveform, "sample_rate": sample_rate})
                embeddings.append(np.array(embedding))

            if not embeddings:
                continue

            # Mean-pool segment embeddings to get centroid
            centroid = np.mean(embeddings, axis=0)
            # Normalize to unit vector for cosine distance
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroid = centroid / norm

            results.append(
                SpeakerEmbedding(
                    partition_key=partition_key,
                    local_label=label,
                    centroid=centroid.tolist(),
                    segment_count=len(valid_segs),
                    total_duration_s=round(total_duration, 2),
                )
            )

        return results
    finally:
        if os.path.exists(wav_path):
            os.unlink(wav_path)


@asset(
    group_name="media_ingest",
    description="Per-speaker centroid embeddings from pyannote diarization",
    compute_kind="ml",
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    ins={
        "media_diarization": AssetIn(partition_mapping=None),
        "media_documents": AssetIn(partition_mapping=None),
    },
    op_tags=DIARIZATION_K8S_CONFIG,
)
def media_speaker_embeddings(
    context: AssetExecutionContext,
    media_diarization: dict[str, Any],
    media_documents: Any,
) -> Output[list[SpeakerEmbedding]]:
    partition_key = context.partition_key

    # Gate behind env var — skip if not enabled
    if os.environ.get("SPEAKER_PROFILE_ENABLED", "").lower() not in ("1", "true"):
        context.log.info("SPEAKER_PROFILE_ENABLED not set — skipping speaker embedding extraction")
        return Output(
            [],
            metadata={
                "document_id": partition_key,
                "skipped": True,
                "reason": "SPEAKER_PROFILE_ENABLED not set",
            },
        )

    with trace_operation(
        "media_speaker_embeddings",
        tracer,
        {
            "code_location": "media_ingest",
            "layer": "gold",
            "partition_key": partition_key,
        },
    ):
        # Skip if diarization had no speakers
        speakers = media_diarization.get("speakers", [])
        if not speakers:
            context.log.info(f"No speakers in diarization for partition={partition_key}")
            return Output(
                [],
                metadata={
                    "document_id": partition_key,
                    "speaker_count": 0,
                    "skipped": True,
                    "reason": "no_speakers",
                },
            )

        source_path = media_diarization.get("source_path", "")
        if not source_path or not os.path.exists(source_path):
            context.log.warning(f"Source audio not found: {source_path}")
            return Output(
                [],
                metadata={
                    "document_id": partition_key,
                    "skipped": True,
                    "reason": "source_not_found",
                },
            )

        # Add document_id to diarization dict for _extract_speaker_centroids
        diarization_with_id = {**media_diarization, "document_id": partition_key}

        try:
            embeddings = _extract_speaker_centroids(diarization_with_id, source_path)
            context.log.info(
                f"Extracted {len(embeddings)} speaker embeddings "
                f"from {len(speakers)} speakers in partition={partition_key}"
            )
        except Exception as e:
            context.log.warning(f"Speaker embedding extraction failed: {e}")
            logger.error("Speaker embedding extraction failed partition=%s error=%s", partition_key, str(e))
            return Output(
                [],
                metadata={
                    "document_id": partition_key,
                    "skipped": True,
                    "reason": f"extraction_error: {e}",
                },
            )

        ASSET_RECORDS_PROCESSED.labels(
            code_location="media_ingest",
            asset_key="media_speaker_embeddings",
            layer="gold",
        ).inc(len(embeddings))

        return Output(
            embeddings,
            metadata={
                "document_id": partition_key,
                "embedding_count": len(embeddings),
                "speakers": MetadataValue.json([e.local_label for e in embeddings]),
                "total_segments": sum(e.segment_count for e in embeddings),
            },
        )

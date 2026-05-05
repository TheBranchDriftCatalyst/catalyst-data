from media_ingest.assets.bench_overrides_snapshot import bench_overrides_snapshot
from media_ingest.assets.chunks import media_chunks
from media_ingest.assets.diarization import media_diarization, media_segment_merge
from media_ingest.assets.discovery import media_files
from media_ingest.assets.documents import media_documents
from media_ingest.assets.entity_candidates import media_entity_candidates
from media_ingest.assets.gold import media_gold_assets
from media_ingest.assets.metadata import media_metadata
from media_ingest.assets.persist_artifacts import mention_proposition_artifacts
from media_ingest.assets.speaker_embeddings import media_speaker_embeddings
from media_ingest.assets.speaker_profiles import media_speaker_profiles
from media_ingest.assets.training import dpo_dataset, sft_dataset
from media_ingest.assets.transcode import media_transcode
from media_ingest.assets.transcription import media_transcriptions

__all__ = [
    # Unpartitioned discovery + processing
    "media_files",
    "media_metadata",
    "media_transcode",
    "media_documents",
    # Partitioned per-document
    "media_transcriptions",
    "media_diarization",
    "media_segment_merge",
    "media_chunks",
    "media_gold_assets",
    "media_entity_candidates",
    # Audit events (cold-path persist_artifacts emission)
    "mention_proposition_artifacts",
    # Speaker identity (CD-34j.1)
    "media_speaker_embeddings",
    "media_speaker_profiles",
    # Bench/training data plumbing (Phase 2/3)
    "bench_overrides_snapshot",
    "sft_dataset",
    "dpo_dataset",
]

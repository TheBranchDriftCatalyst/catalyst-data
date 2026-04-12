from media_ingest.assets.assertions import media_assertions
from media_ingest.assets.chunks import media_chunks
from media_ingest.assets.diarization import media_diarization
from media_ingest.assets.discovery import media_files
from media_ingest.assets.documents import media_documents
from media_ingest.assets.embeddings import media_embeddings
from media_ingest.assets.entity_candidates import media_entity_candidates
from media_ingest.assets.mentions import media_mentions
from media_ingest.assets.metadata import media_metadata
from media_ingest.assets.speaker_embeddings import media_speaker_embeddings
from media_ingest.assets.speaker_profiles import media_speaker_profiles
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
    "media_chunks",
    "media_mentions",
    "media_assertions",
    "media_embeddings",
    "media_entity_candidates",
    # Speaker identity (CD-34j.1)
    "media_speaker_embeddings",
    "media_speaker_profiles",
]

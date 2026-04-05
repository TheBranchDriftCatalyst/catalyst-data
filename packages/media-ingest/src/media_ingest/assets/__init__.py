from media_ingest.assets.assertions import media_assertions
from media_ingest.assets.chunks import media_chunks
from media_ingest.assets.discovery import media_files
from media_ingest.assets.documents import media_documents
from media_ingest.assets.embeddings import media_embeddings
from media_ingest.assets.mentions import media_mentions
from media_ingest.assets.metadata import media_metadata
from media_ingest.assets.transcription import media_transcriptions

__all__ = [
    # Unpartitioned discovery assets
    "media_files",
    "media_metadata",
    "media_documents",
    # Partitioned per-document assets
    "media_transcriptions",
    "media_chunks",
    "media_mentions",
    "media_assertions",
    "media_embeddings",
]

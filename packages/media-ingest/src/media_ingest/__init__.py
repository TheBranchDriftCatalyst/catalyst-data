"""Media file processing pipeline — Dagster code location."""

from dagster import Definitions
from dagster_io import ChunkingResource, EmbeddingResource, LLMResource, MinioIOManager

from media_ingest.assets import (
    media_chunks,
    media_documents,
    media_embeddings,
    media_files,
    media_metadata,
    media_transcriptions,
)

defs = Definitions(
    assets=[
        media_files,
        media_metadata,
        media_documents,
        media_transcriptions,
        media_chunks,
        media_embeddings,
    ],
    resources={
        "io_manager": MinioIOManager(),
        "chunking": ChunkingResource(),
        "llm": LLMResource(),
        "embeddings": EmbeddingResource(),
    },
)

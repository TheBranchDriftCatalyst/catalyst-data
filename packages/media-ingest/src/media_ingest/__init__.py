"""Media file processing pipeline — Dagster code location."""

from dagster_io.logging import configure_logging
from dagster_io.metrics import start_metrics_server
from dagster_io.observability import configure_tracing

configure_logging()
configure_tracing(service_name="catalyst-data.media_ingest")
start_metrics_server()

from dagster import Definitions
from dagster_k8s import k8s_job_executor
from dagster_io import ChunkingResource, EmbeddingResource, LLMResource, MinioIOManager

from media_ingest.assets import (
    media_assertions,
    media_chunks,
    media_diarization,
    media_documents,
    media_embeddings,
    media_files,
    media_mentions,
    media_metadata,
    media_transcode,
    media_transcriptions,
)
from media_ingest.sensors import media_document_sensor

defs = Definitions(
    assets=[
        # Unpartitioned: discovery + transcode
        media_files,
        media_metadata,
        media_transcode,
        media_documents,
        # Partitioned per-document
        media_transcriptions,
        media_diarization,
        media_chunks,
        media_mentions,
        media_assertions,
        media_embeddings,
    ],
    sensors=[
        media_document_sensor,
    ],
    executor=k8s_job_executor,
    resources={
        "io_manager": MinioIOManager(),
        "chunking": ChunkingResource(),
        "llm": LLMResource(),
        "embeddings": EmbeddingResource(),
    },
)

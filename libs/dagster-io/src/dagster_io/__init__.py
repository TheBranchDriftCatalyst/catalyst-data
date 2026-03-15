"""Shared IO manager, resources, and utilities for Dagster pipelines."""

from dagster_io.chunking import ChunkingResource, TextChunk, chunk_document, chunk_text
from dagster_io.concordance import ConcordanceEngine, CrossSourceAligner
from dagster_io.document import Document
from dagster_io.embedding_config import EmbeddingConfig, EmbeddingConfigResource
from dagster_io.io_manager import MinioIOManager
from dagster_io.llm import EmbeddingResource, LLMResource
from dagster_io.logging import configure_logging, get_logger
from dagster_io.manifest import AssetManifest, MaterializationRecord
from dagster_io.metrics import (
    ACTIVE_ASSET_MATERIALIZATIONS,
    ASSET_MATERIALIZATION_DURATION,
    ASSET_RECORDS_PROCESSED,
    ASSERTIONS_CREATED,
    CHUNK_PROCESSING_DURATION,
    CHUNKS_CREATED,
    EMBEDDING_BATCH_DURATION,
    EMBEDDING_VECTORS_CREATED,
    ENTITIES_EXTRACTED,
    GRAPH_DB_OPERATION_DURATION,
    GRAPH_DB_OPERATIONS,
    LLM_REQUEST_DURATION,
    LLM_REQUESTS,
    LLM_TOKENS_USED,
    S3_BYTES_TRANSFERRED,
    S3_OPERATION_DURATION,
    S3_OPERATIONS,
    start_metrics_server,
    track_asset_materialization,
    track_duration,
)
from dagster_io.models import (
    AlignmentEdge,
    AlignmentType,
    Assertion,
    CanonicalEntity,
    EntityCandidate,
    ExtractionMethod,
    Mention,
    MentionType,
    Provenance,
)
from dagster_io.observability import configure_tracing, get_tracer, trace_operation
from dagster_io.processing_tracker import ProcessingTracker
from dagster_io.prompts import load_prompt, parse_prompt_file

__all__ = [
    # IO
    "MinioIOManager",
    # Resources
    "LLMResource",
    "EmbeddingResource",
    "ChunkingResource",
    # Chunking
    "TextChunk",
    "chunk_document",
    "chunk_text",
    # Manifest
    "AssetManifest",
    "MaterializationRecord",
    # Embedding config
    "EmbeddingConfig",
    "EmbeddingConfigResource",
    # Document
    "Document",
    # EDC models
    "Provenance",
    "Mention",
    "MentionType",
    "EntityCandidate",
    "CanonicalEntity",
    "Assertion",
    "AlignmentEdge",
    "AlignmentType",
    "ExtractionMethod",
    # Concordance
    "ConcordanceEngine",
    "CrossSourceAligner",
    # Processing
    "ProcessingTracker",
    # Logging
    "configure_logging",
    "get_logger",
    # Metrics
    "ASSET_MATERIALIZATION_DURATION",
    "ASSET_RECORDS_PROCESSED",
    "ACTIVE_ASSET_MATERIALIZATIONS",
    "LLM_REQUEST_DURATION",
    "LLM_TOKENS_USED",
    "LLM_REQUESTS",
    "S3_OPERATION_DURATION",
    "S3_OPERATIONS",
    "S3_BYTES_TRANSFERRED",
    "EMBEDDING_BATCH_DURATION",
    "EMBEDDING_VECTORS_CREATED",
    "CHUNK_PROCESSING_DURATION",
    "CHUNKS_CREATED",
    "ENTITIES_EXTRACTED",
    "ASSERTIONS_CREATED",
    "GRAPH_DB_OPERATIONS",
    "GRAPH_DB_OPERATION_DURATION",
    "start_metrics_server",
    "track_duration",
    "track_asset_materialization",
    # Tracing
    "configure_tracing",
    "get_tracer",
    "trace_operation",
    # Prompts
    "load_prompt",
    "parse_prompt_file",
]

"""Shared IO manager, resources, and utilities for Dagster pipelines."""

from dagster_io import event_tail
from dagster_io.append_io_manager import AppendIOManager
from dagster_io.asset_factories import LLM_ASSET_K8S_CONFIG
from dagster_io.asset_factory import EMBEDDING_ASSET_K8S_CONFIG, PipelineConfig, extraction_assets
from dagster_io.bench_store import S3BenchmarkStore, S3RunStore
from dagster_io.chunking import (
    PRESET_CONFIGS,
    ChunkConfig,
    ChunkingResource,
    TextChunk,
    TokenCounter,
    chunk_document,
    chunk_text,
)
from dagster_io.concordance import ConcordanceEngine, CrossSourceAligner
from dagster_io.document import Document
from dagster_io.embedding_config import EmbeddingConfig, EmbeddingConfigResource
from dagster_io.extraction_schemas import (
    AssertionExtractionResult,
    AssertionQualifiers,
    MentionExtraction,
    MentionExtractionResult,
    normalize_predicate,
    parse_mention_type,
)
from dagster_io.io_backend import select_io_managers
from dagster_io.io_manager import MinioIOManager, OptionalMinioIOManager
from dagster_io.llm import EmbeddingResource, LLMResource
from dagster_io.local_io_manager import LocalAppendIOManager, LocalJsonIOManager, LocalOptionalIOManager
from dagster_io.logging import configure_logging, get_logger
from dagster_io.manifest import AssetManifest, MaterializationRecord
from dagster_io.metrics import (
    ACTIVE_ASSET_MATERIALIZATIONS,
    ALIGNMENT_EDGES_TOTAL,
    ASSERTIONS_CREATED,
    ASSET_LAST_MATERIALIZED_TIMESTAMP_SECONDS,
    ASSET_MATERIALIZATION_DURATION,
    ASSET_RECORDS_PROCESSED,
    CANONICAL_ENTITIES_TOTAL,
    CHUNK_PROCESSING_DURATION,
    CHUNKS_CREATED,
    DAGSTER_RUN_DURATION_SECONDS,
    DAGSTER_RUN_STATUS_TOTAL,
    DAGSTER_SENSOR_TICK_TOTAL,
    DIARIZATION_DURATION,
    EMBEDDING_BATCH_DURATION,
    EMBEDDING_VECTORS_CREATED,
    ENTITIES_EXTRACTED,
    ENTITY_REDUCTION_RATIO,
    GRAPH_DB_OPERATION_DURATION,
    GRAPH_DB_OPERATIONS,
    LLM_REQUEST_DURATION,
    LLM_REQUESTS,
    LLM_TOKENS_CACHED_TOTAL,
    LLM_TOKENS_USED,
    MODEL_LOAD_DURATION,
    S3_BYTES_TRANSFERRED,
    S3_OPERATION_DURATION,
    S3_OPERATIONS,
    SPEAKER_PROFILE_MERGE_DISTANCE,
    SPEAKER_PROFILES_TOTAL,
    TRANSCODE_COMPRESSION_RATIO,
    TRANSCODE_DURATION,
    TRANSCODE_SAVED_BYTES,
    TRANSCRIPTION_DURATION,
    TRANSCRIPTION_REALTIME_FACTOR,
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
    SpeakerEmbedding,
    SpeakerProfile,
)
from dagster_io.observability import configure_tracing, get_tracer, trace_operation
from dagster_io.processing_tracker import ProcessingTracker
from dagster_io.prompts import load_prompt, parse_prompt_file
from dagster_io.run_status_sensor import make_run_status_sensor
from dagster_io.semantic_seed import (
    SemanticChunkingSeed,
    attach_seed,
    attach_seeds_batch,
    build_seed,
    get_seed,
)

__all__ = [
    # IO
    "MinioIOManager",
    "OptionalMinioIOManager",
    "AppendIOManager",
    "LocalJsonIOManager",
    "LocalOptionalIOManager",
    "LocalAppendIOManager",
    "select_io_managers",
    # Run-status sensor factory
    "make_run_status_sensor",
    # Resources
    "LLMResource",
    "EmbeddingResource",
    "ChunkingResource",
    # Chunking
    "ChunkConfig",
    "TokenCounter",
    "PRESET_CONFIGS",
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
    "SpeakerEmbedding",
    "SpeakerProfile",
    # Extraction schemas
    "MentionExtraction",
    "MentionExtractionResult",
    "parse_mention_type",
    "AssertionQualifiers",
    "AssertionExtractionResult",
    "normalize_predicate",
    # Asset config
    "LLM_ASSET_K8S_CONFIG",
    "EMBEDDING_ASSET_K8S_CONFIG",
    # Asset factory
    "PipelineConfig",
    "extraction_assets",
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
    "ALIGNMENT_EDGES_TOTAL",
    "CANONICAL_ENTITIES_TOTAL",
    "ENTITY_REDUCTION_RATIO",
    "LLM_TOKENS_CACHED_TOTAL",
    "GRAPH_DB_OPERATIONS",
    "GRAPH_DB_OPERATION_DURATION",
    "TRANSCRIPTION_DURATION",
    "TRANSCRIPTION_REALTIME_FACTOR",
    "DIARIZATION_DURATION",
    "TRANSCODE_DURATION",
    "TRANSCODE_COMPRESSION_RATIO",
    "TRANSCODE_SAVED_BYTES",
    "MODEL_LOAD_DURATION",
    "SPEAKER_PROFILES_TOTAL",
    "SPEAKER_PROFILE_MERGE_DISTANCE",
    # DAG health (CD-59v)
    "DAGSTER_RUN_STATUS_TOTAL",
    "DAGSTER_RUN_DURATION_SECONDS",
    "DAGSTER_SENSOR_TICK_TOTAL",
    "ASSET_LAST_MATERIALIZED_TIMESTAMP_SECONDS",
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
    # Unified event stream
    "event_tail",
    # S3-backed benchmark store (Phase 2 — single backend across dev/prod)
    "S3BenchmarkStore",
    "S3RunStore",
    # Semantic chunking seeds (deterministic GT sampling — see CD-wnu5)
    "SemanticChunkingSeed",
    "build_seed",
    "attach_seed",
    "attach_seeds_batch",
    "get_seed",
]

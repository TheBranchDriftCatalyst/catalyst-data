"""Shared IO manager, resources, and utilities for Dagster pipelines."""

from dagster_io.chunking import ChunkingResource, TextChunk, chunk_document, chunk_text
from dagster_io.concordance import ConcordanceEngine, CrossSourceAligner
from dagster_io.document import Document
from dagster_io.embedding_config import EmbeddingConfig, EmbeddingConfigResource
from dagster_io.io_manager import MinioIOManager
from dagster_io.llm import EmbeddingResource, LLMResource
from dagster_io.manifest import AssetManifest, MaterializationRecord
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
from dagster_io.processing_tracker import ProcessingTracker

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
]

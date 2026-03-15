import logging
import os
import time
from contextlib import contextmanager
from functools import wraps
from typing import Callable

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)

logger = logging.getLogger(__name__)

# Shared registry
REGISTRY = CollectorRegistry()

# ── Asset metrics ──
ASSET_MATERIALIZATION_DURATION = Histogram(
    "catalyst_asset_materialization_duration_seconds",
    "Duration of asset materializations",
    ["code_location", "asset_key", "layer"],
    registry=REGISTRY,
)

ASSET_RECORDS_PROCESSED = Counter(
    "catalyst_asset_records_processed_total",
    "Number of records processed per asset materialization",
    ["code_location", "asset_key", "layer"],
    registry=REGISTRY,
)

ACTIVE_ASSET_MATERIALIZATIONS = Gauge(
    "catalyst_active_asset_materializations",
    "Number of asset materializations currently running",
    ["code_location"],
    registry=REGISTRY,
)

# ── LLM metrics ──
LLM_REQUEST_DURATION = Histogram(
    "catalyst_llm_request_duration_seconds",
    "Duration of LLM API calls",
    ["model", "operation"],
    registry=REGISTRY,
)

LLM_TOKENS_USED = Counter(
    "catalyst_llm_tokens_total",
    "Total tokens used in LLM calls",
    ["model", "token_type"],
    registry=REGISTRY,
)

LLM_REQUESTS = Counter(
    "catalyst_llm_requests_total",
    "Total LLM requests",
    ["model", "operation", "status"],
    registry=REGISTRY,
)

# ── S3/MinIO metrics ──
S3_OPERATION_DURATION = Histogram(
    "catalyst_s3_operation_duration_seconds",
    "Duration of S3 operations",
    ["operation", "bucket"],
    registry=REGISTRY,
)

S3_OPERATIONS = Counter(
    "catalyst_s3_operations_total",
    "S3 operations performed",
    ["operation", "bucket"],
    registry=REGISTRY,
)

S3_BYTES_TRANSFERRED = Counter(
    "catalyst_s3_bytes_total",
    "Bytes transferred to/from S3",
    ["direction", "bucket"],
    registry=REGISTRY,
)

# ── Embedding metrics ──
EMBEDDING_BATCH_DURATION = Histogram(
    "catalyst_embedding_batch_duration_seconds",
    "Duration of embedding batch operations",
    ["provider", "model"],
    registry=REGISTRY,
)

EMBEDDING_VECTORS_CREATED = Counter(
    "catalyst_embedding_vectors_total",
    "Total embedding vectors created",
    ["provider", "model"],
    registry=REGISTRY,
)

# ── Chunking metrics ──
CHUNK_PROCESSING_DURATION = Histogram(
    "catalyst_chunk_processing_duration_seconds",
    "Duration of chunk processing operations",
    ["code_location", "asset_key"],
    registry=REGISTRY,
)

CHUNKS_CREATED = Counter(
    "catalyst_chunks_created_total",
    "Total chunks created",
    ["code_location"],
    registry=REGISTRY,
)

# ── Entity/NER metrics ──
ENTITIES_EXTRACTED = Counter(
    "catalyst_entities_extracted_total",
    "Total entities extracted",
    ["code_location", "entity_type", "method"],
    registry=REGISTRY,
)

ASSERTIONS_CREATED = Counter(
    "catalyst_assertions_created_total",
    "Total assertions (S-P-O triples) created",
    ["code_location"],
    registry=REGISTRY,
)

# ── Graph DB metrics ──
GRAPH_DB_OPERATIONS = Counter(
    "catalyst_graph_db_operations_total",
    "Graph database operations",
    ["operation", "backend"],
    registry=REGISTRY,
)

GRAPH_DB_OPERATION_DURATION = Histogram(
    "catalyst_graph_db_operation_duration_seconds",
    "Graph database operation duration",
    ["operation", "backend"],
    registry=REGISTRY,
)


@contextmanager
def track_duration(histogram, labels: dict):
    """Context manager to track operation duration.

    Usage:
        with track_duration(LLM_REQUEST_DURATION, {"model": "gpt-4", "operation": "extract"}):
            result = llm.complete(prompt)
    """
    start = time.monotonic()
    try:
        yield
    finally:
        duration = time.monotonic() - start
        histogram.labels(**labels).observe(duration)


def track_asset_materialization(code_location: str, layer: str):
    """Decorator to track asset materialization duration and active count.

    Usage:
        @track_asset_materialization("open_leaks", "gold")
        def my_asset(context):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            asset_key = func.__name__
            gauge = ACTIVE_ASSET_MATERIALIZATIONS.labels(code_location=code_location)
            gauge.inc()
            start = time.monotonic()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                duration = time.monotonic() - start
                gauge.dec()
                ASSET_MATERIALIZATION_DURATION.labels(
                    code_location=code_location,
                    asset_key=asset_key,
                    layer=layer,
                ).observe(duration)
        return wrapper
    return decorator


_metrics_server_started = False


def start_metrics_server(port: int | None = None) -> None:
    """Start Prometheus metrics HTTP server (idempotent)."""
    global _metrics_server_started
    if _metrics_server_started:
        return
    port = port or int(os.getenv("METRICS_PORT", "9090"))
    try:
        start_http_server(port, registry=REGISTRY)
        _metrics_server_started = True
        logger.info("Prometheus metrics server started on port %d", port)
    except OSError as e:
        logger.warning("Could not start metrics server on port %d: %s", port, e)

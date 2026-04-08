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
    ["strategy"],
    registry=REGISTRY,
)

CHUNKS_CREATED = Counter(
    "catalyst_chunks_created_total",
    "Total chunks created",
    ["strategy"],
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

# ── Media Ingest metrics ──
TRANSCRIPTION_DURATION = Histogram(
    "catalyst_transcription_duration_seconds",
    "Duration of audio transcription",
    ["backend", "model"],
    buckets=(10, 30, 60, 120, 300, 600, 1200, 1800, 3600),
    registry=REGISTRY,
)

DIARIZATION_DURATION = Histogram(
    "catalyst_diarization_duration_seconds",
    "Duration of speaker diarization",
    [],
    buckets=(10, 30, 60, 120, 300, 600, 1200, 1800, 3600),
    registry=REGISTRY,
)

TRANSCODE_DURATION = Histogram(
    "catalyst_transcode_duration_seconds",
    "Duration of video transcode operations",
    [],
    buckets=(10, 30, 60, 120, 300, 600, 1800, 3600, 7200),
    registry=REGISTRY,
)

TRANSCODE_COMPRESSION_RATIO = Histogram(
    "catalyst_transcode_compression_ratio",
    "Compression ratio achieved by transcode (original / compressed)",
    [],
    buckets=(1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.5, 10.0, 15.0, 20.0),
    registry=REGISTRY,
)

TRANSCODE_SAVED_BYTES = Counter(
    "catalyst_transcode_saved_bytes_total",
    "Total bytes saved by transcoding",
    [],
    registry=REGISTRY,
)

MODEL_LOAD_DURATION = Histogram(
    "catalyst_model_load_duration_seconds",
    "Duration to load ML models",
    ["model_type"],
    buckets=(1, 5, 10, 30, 60, 120, 300),
    registry=REGISTRY,
)

TRANSCRIPTION_REALTIME_FACTOR = Histogram(
    "catalyst_transcription_realtime_factor",
    "Ratio of audio duration to transcription time (>1 means faster than realtime)",
    ["backend"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0),
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


def push_metrics(job_name: str = "dagster_step") -> None:
    """Push all collected metrics to Alloy via OTLP, then flush.

    Call this at the end of ephemeral step pods (k8s_job_executor)
    so metrics survive pod cleanup. Uses the same OTLP endpoint as tracing.
    """
    try:
        from prometheus_client.openmetrics.exposition import generate_latest
        from prometheus_client.parser import text_string_to_metric_families

        # Collect all current metric values from the registry
        metrics_text = generate_latest(REGISTRY).decode("utf-8")

        # Push via OTLP using the remote write approach
        endpoint = os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "http://alloy.monitoring.svc.cluster.local:4317",
        )

        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({
            "service.name": os.getenv("OTEL_SERVICE_NAME", "catalyst-data"),
            "service.namespace": "catalyst-data",
            "job": job_name,
        })

        exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
        reader = PeriodicExportingMetricReader(exporter, export_interval_millis=1000)
        provider = MeterProvider(resource=resource, metric_readers=[reader])

        # Create OTLP metrics from prometheus_client values
        meter = provider.get_meter("catalyst-data")
        for family in text_string_to_metric_families(metrics_text):
            for sample in family.samples:
                name = sample.name
                labels = sample.labels
                value = sample.value
                if value == 0:
                    continue
                # Record as gauge (point-in-time snapshot)
                gauge = meter.create_gauge(name, description=family.documentation)
                gauge.set(value, labels)

        # Force flush and shutdown
        provider.force_flush()
        provider.shutdown()
        logger.info("Pushed metrics to Alloy OTLP at %s (job=%s)", endpoint, job_name)
    except ImportError:
        logger.warning("OpenTelemetry metrics packages not installed, skipping push")
    except Exception as e:
        logger.warning("Failed to push metrics via OTLP: %s", e)


def start_metrics_server(port: int | None = None) -> None:
    """Initialize metrics: start HTTP server for scraping AND register push-on-exit.

    Every process pushes metrics via OTLP to Alloy on exit. The HTTP server
    is a bonus for live debugging but not the primary collection path.
    """
    import atexit

    global _metrics_server_started
    if _metrics_server_started:
        return
    _metrics_server_started = True

    # Always register push-on-exit — this is the primary collection path
    atexit.register(push_metrics, job_name=os.getenv("OTEL_SERVICE_NAME", "dagster_step"))
    logger.info("Registered atexit metric push via OTLP")

    # Also try to start HTTP server for live debugging (non-critical)
    port = port or int(os.getenv("METRICS_PORT", "9090"))
    try:
        start_http_server(port, registry=REGISTRY)
        logger.info("Prometheus metrics server started on port %d", port)
    except OSError:
        pass  # Fine — push-on-exit handles collection

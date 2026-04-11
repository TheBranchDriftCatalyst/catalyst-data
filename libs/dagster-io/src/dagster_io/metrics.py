import logging
import os
import time
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps

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

# ── Concordance / entity resolution metrics ──
ALIGNMENT_EDGES_TOTAL = Counter(
    "catalyst_alignment_edges_total",
    "Cross-source entity alignment edges produced by CrossSourceAligner",
    # source_location/target_location: e.g. media_ingest / congress_data
    # alignment_type: sameAs | possibleSameAs
    # top_signal: exact_name | substring | jaccard | embedding — the highest-weight
    #             signal on the edge, used to track which signal is actually firing.
    ["source_location", "target_location", "alignment_type", "top_signal"],
    registry=REGISTRY,
)

CANONICAL_ENTITIES_TOTAL = Counter(
    "catalyst_canonical_entities_total",
    "Canonical entities produced by the platinum resolver, bucketed by cross-source merge count",
    # source_count_bucket: "1" (no merge — singleton) | "2" | "3+"
    # A dominance of "1" means the aligner isn't finding cross-source matches
    # and the platinum layer is acting as a pass-through.
    ["entity_type", "source_count_bucket"],
    registry=REGISTRY,
)

ENTITY_REDUCTION_RATIO = Histogram(
    "catalyst_entity_reduction_ratio",
    "Ratio of candidates to mentions in per-document concordance (lower = more dedup)",
    ["code_location"],
    buckets=(0.1, 0.25, 0.5, 0.75, 0.9, 1.0),
    registry=REGISTRY,
)

LLM_TOKENS_CACHED_TOTAL = Counter(
    "catalyst_llm_tokens_cached_total",
    "Prompt tokens served from LLM prompt cache (cache hit)",
    # Pulled from LangChain usage_metadata.input_token_details.cache_read (nested
    # path in current langchain-core) with a fallback to the legacy flat
    # `cache_read_input_tokens` field. Only populated when the underlying model +
    # proxy support prompt caching (Anthropic prompt caching, OpenAI cached_tokens).
    ["model"],
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
    # `device` records the *resolved* device the model actually ran on, not the
    # requested one. OpenVINO silently falls back from GPU to CPU on init
    # failure (transcription.py:110-114) and without this label that fallback
    # is invisible in Grafana — both paths look like the same `backend`.
    # `model` distinguishes faster-whisper vs openvino-whisper-large vs etc.
    ["backend", "device", "model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0),
    registry=REGISTRY,
)

# ── DAG health metrics (CD-59v) ──
# Run-level + freshness metrics emitted by the shared RunStatusSensor factory
# in dagster_io.run_status_sensor.make_run_status_sensor. Before these
# existed, the Grafana dashboard could not answer "did anything run today?"
# — there were no run-level metrics at all, only per-asset duration.
DAGSTER_RUN_STATUS_TOTAL = Counter(
    "catalyst_dagster_run_status_total",
    "Dagster run terminal outcomes by code location and job",
    # status ∈ {success, failure, canceled}
    ["code_location", "job_name", "status"],
    registry=REGISTRY,
)

DAGSTER_RUN_DURATION_SECONDS = Histogram(
    "catalyst_dagster_run_duration_seconds",
    "End-to-end Dagster run wall-clock duration",
    ["code_location", "job_name"],
    # Bucket layout tuned for our pipelines: fastest partitioned runs
    # (discovery, metadata) finish in <60s, media transcode/transcription
    # runs land in the 5-60 min range, and multi-hour batch runs (knowledge
    # graph platinum fan-in, congress bulk load) land in the 1-8 hour range.
    buckets=(10, 30, 60, 300, 900, 1800, 3600, 7200, 14400, 28800),
    registry=REGISTRY,
)

DAGSTER_SENSOR_TICK_TOTAL = Counter(
    "catalyst_dagster_sensor_tick_total",
    "Dagster sensor tick outcomes",
    # outcome ∈ {success, skipped, failure}
    #
    # NOTE: this counter is defined here but NOT emitted by
    # make_run_status_sensor — a run-status sensor cannot observe its own
    # ticks without circular logic. Emission is a follow-up: wire it from
    # an instance-level sensor or a Dagster instance hook (see CD-59v
    # follow-up).
    ["code_location", "sensor_name", "outcome"],
    registry=REGISTRY,
)

ASSET_LAST_MATERIALIZED_TIMESTAMP_SECONDS = Gauge(
    "catalyst_asset_last_materialized_timestamp_seconds",
    "Unix timestamp of last successful materialization — subtract from time() for freshness SLO",
    ["code_location", "asset_key"],
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
    """Push all collected metrics to Alloy via synchronous OTLP export.

    Uses InMemoryMetricReader + direct exporter.export() — no background
    threads, no PeriodicExportingMetricReader. Safe to call during atexit
    or SIGTERM handling.
    """
    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from opentelemetry.sdk.resources import Resource
        from prometheus_client.openmetrics.exposition import generate_latest
        from prometheus_client.parser import text_string_to_metric_families

        endpoint = os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "http://alloy.monitoring.svc.cluster.local:4317",
        )

        metrics_text = generate_latest(REGISTRY).decode("utf-8")

        resource = Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", "catalyst-data"),
                "service.namespace": "catalyst-data",
                "job": job_name,
            }
        )

        reader = InMemoryMetricReader()
        provider = MeterProvider(resource=resource, metric_readers=[reader])
        meter = provider.get_meter("catalyst-data")

        for family in text_string_to_metric_families(metrics_text):
            for sample in family.samples:
                if sample.value == 0:
                    continue
                gauge = meter.create_gauge(sample.name, description=family.documentation)
                gauge.set(sample.value, sample.labels)

        # Synchronous: read collected metrics, then export directly (no threads)
        exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
        metrics_data = reader.get_metrics_data()
        exporter.export(metrics_data, timeout_millis=10000)
        exporter.shutdown()
        provider.shutdown()
        logger.info("Pushed metrics to Alloy OTLP at %s (job=%s)", endpoint, job_name)
    except ImportError:
        logger.warning("OpenTelemetry metrics packages not installed, skipping push")
    except Exception as e:
        logger.warning("Failed to push metrics via OTLP: %s", e)


def start_metrics_server(port: int | None = None) -> None:
    """Initialize metrics: start HTTP server for scraping AND register push-on-exit.

    Every process pushes metrics via synchronous OTLP on exit. Also registers
    a SIGTERM handler so metrics are pushed when K8s terminates step pods.
    The HTTP server is a bonus for live debugging but not the primary path.
    """
    import atexit
    import signal

    global _metrics_server_started
    if _metrics_server_started:
        return
    _metrics_server_started = True

    _job_name = os.getenv("OTEL_SERVICE_NAME", "dagster_step")

    # Register push-on-exit (atexit fires on clean sys.exit)
    atexit.register(push_metrics, job_name=_job_name)

    # Register SIGTERM handler (K8s sends SIGTERM before SIGKILL)
    _prev_handler = signal.getsignal(signal.SIGTERM)

    def _sigterm_handler(signum, frame):
        push_metrics(job_name=_job_name)
        # Chain to previous handler if it was callable
        if callable(_prev_handler) and _prev_handler not in (
            signal.SIG_DFL,
            signal.SIG_IGN,
        ):
            _prev_handler(signum, frame)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _sigterm_handler)
    logger.info("Registered atexit + SIGTERM metric push via OTLP")

    # Also try to start HTTP server for live debugging (non-critical)
    port = port or int(os.getenv("METRICS_PORT", "9090"))
    try:
        start_http_server(port, registry=REGISTRY)
        logger.info("Prometheus metrics server started on port %d", port)
    except OSError:
        pass  # Fine — push-on-exit handles collection

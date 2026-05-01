"""Catalyst-data metrics — OTLP export via OpenTelemetry.

Metrics are exported to Alloy (gRPC :4317) which forwards to Mimir.
PeriodicExportingMetricReader flushes every 30s AND on shutdown,
so Grafana sees data while jobs run — not just at exit.

Provides prometheus_client-compatible wrappers (Counter, Histogram, Gauge)
so the 91+ call sites using .labels(**kwargs).inc()/.observe() keep working.
"""

import logging
import os
import time
from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps

logger = logging.getLogger(__name__)

# ── OTEL metric setup ──────────────────────────────────────────────────────

_meter = None
_provider = None


def _get_meter():
    """Lazy-init the OTEL MeterProvider + exporter on first metric use."""
    global _meter, _provider
    if _meter is not None:
        return _meter

    # Auto-suppress outside Dagster: CLI scripts and dev tooling that import
    # this library shouldn't try to ship metrics to alloy.monitoring (it's
    # only reachable from inside the talos cluster). Power users can force
    # telemetry on via CATALYST_TELEMETRY=1.
    from dagster_io._runtime_context import telemetry_enabled

    if not telemetry_enabled():
        logger.info("OTEL metrics disabled (running outside Dagster; set CATALYST_TELEMETRY=1 to override)")
        _meter = _NoOpMeter()
        return _meter

    try:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        endpoint = os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "http://alloy.monitoring.svc.cluster.local:4317",
        )
        svc_name = os.getenv("OTEL_SERVICE_NAME", "catalyst-data")

        resource = Resource.create(
            {
                "service.name": svc_name,
                "service.namespace": "catalyst-data",
            }
        )

        exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=30_000,  # flush every 30s
        )
        _provider = MeterProvider(resource=resource, metric_readers=[reader])
        _meter = _provider.get_meter("catalyst-data-metrics", version="1.0.0")
        logger.info("OTEL metrics configured: endpoint=%s, service=%s, flush=30s", endpoint, svc_name)
    except ImportError:
        logger.warning("OpenTelemetry metrics packages not installed, using no-op metrics")
        _meter = _NoOpMeter()
    except Exception as e:
        logger.warning("Failed to configure OTEL metrics: %s, using no-op", e)
        _meter = _NoOpMeter()

    return _meter


# ── Prometheus-compatible wrappers ──────────────────────────────────────────
# These wrap OTEL instruments with the .labels(**kwargs).inc()/.observe()
# API that all 91+ call sites use. Zero changes needed at call sites.


class _LabeledCounter:
    """Bound counter with fixed attributes."""

    def __init__(self, otel_counter, attributes: dict):
        self._counter = otel_counter
        self._attrs = attributes

    def inc(self, amount=1):
        self._counter.add(amount, self._attrs)


class _LabeledHistogram:
    """Bound histogram with fixed attributes."""

    def __init__(self, otel_histogram, attributes: dict):
        self._histogram = otel_histogram
        self._attrs = attributes

    def observe(self, value):
        self._histogram.record(value, self._attrs)


class _LabeledGauge:
    """Bound gauge with fixed attributes."""

    def __init__(self, otel_gauge, attributes: dict):
        self._gauge = otel_gauge
        self._attrs = attributes
        self._value = 0

    def set(self, value):
        self._value = value

    def inc(self, amount=1):
        self._value += amount

    def dec(self, amount=1):
        self._value -= amount


class OTELCounter:
    """prometheus_client.Counter-compatible wrapper over OTEL Counter."""

    def __init__(self, name: str, description: str, label_names: list[str], **kwargs):
        self._name = name
        self._description = description
        self._label_names = label_names
        self._counter = None

    def _ensure_counter(self):
        if self._counter is None:
            meter = _get_meter()
            self._counter = meter.create_counter(
                name=self._name,
                description=self._description,
                unit="1",
            )

    def labels(self, *args, **kwargs) -> _LabeledCounter:
        self._ensure_counter()
        attrs = dict(zip(self._label_names, args, strict=False)) if args else kwargs
        return _LabeledCounter(self._counter, attrs)


class OTELHistogram:
    """prometheus_client.Histogram-compatible wrapper over OTEL Histogram."""

    def __init__(self, name: str, description: str, label_names: list[str], buckets=None, **kwargs):
        self._name = name
        self._description = description
        self._label_names = label_names
        self._histogram = None

    def _ensure_histogram(self):
        if self._histogram is None:
            meter = _get_meter()
            self._histogram = meter.create_histogram(
                name=self._name,
                description=self._description,
                unit="s" if "seconds" in self._name or "duration" in self._name else "1",
            )

    def labels(self, *args, **kwargs) -> _LabeledHistogram:
        self._ensure_histogram()
        attrs = dict(zip(self._label_names, args, strict=False)) if args else kwargs
        return _LabeledHistogram(self._histogram, attrs)

    def observe(self, value):
        """Unlabeled observe (for histograms with no labels)."""
        self._ensure_histogram()
        self._histogram.record(value, {})


class OTELGauge:
    """prometheus_client.Gauge-compatible wrapper over OTEL ObservableGauge.

    OTEL doesn't have a simple settable gauge — it uses observable callbacks.
    We store the value internally and register a callback that returns it.
    """

    def __init__(self, name: str, description: str, label_names: list[str], **kwargs):
        self._name = name
        self._description = description
        self._label_names = label_names
        self._values: dict[tuple, float] = {}
        self._gauge = None

    def _ensure_gauge(self):
        if self._gauge is None:
            meter = _get_meter()

            def _callback(_options):
                from opentelemetry.metrics import Observation

                results = []
                for attr_tuple, value in self._values.items():
                    attrs = dict(zip(self._label_names, attr_tuple, strict=False))
                    results.append(Observation(value=value, attributes=attrs))
                return results

            self._gauge = meter.create_observable_gauge(
                name=self._name,
                description=self._description,
                callbacks=[_callback],
            )

    def labels(self, *args, **kwargs) -> _LabeledGauge:
        self._ensure_gauge()
        if args:
            attr_tuple = tuple(args)
            attrs = dict(zip(self._label_names, args, strict=False))
        else:
            attr_tuple = tuple(kwargs.get(k, "") for k in self._label_names)
            attrs = kwargs

        labeled = _LabeledGauge(self._gauge, attrs)

        def _set(value):
            self._values[attr_tuple] = value

        def _inc(amount=1):
            self._values[attr_tuple] = self._values.get(attr_tuple, 0) + amount

        def _dec(amount=1):
            self._values[attr_tuple] = self._values.get(attr_tuple, 0) - amount

        labeled.set = _set
        labeled.inc = _inc
        labeled.dec = _dec
        return labeled

    def set(self, value):
        """Unlabeled set (for gauges with no labels)."""
        self._ensure_gauge()
        self._values[()] = value


# ── No-op fallback ──────────────────────────────────────────────────────────


class _NoOpMeter:
    def create_counter(self, **kwargs):
        return _NoOpInstrument()

    def create_histogram(self, **kwargs):
        return _NoOpInstrument()

    def create_observable_gauge(self, **kwargs):
        return _NoOpInstrument()


class _NoOpInstrument:
    def add(self, *args, **kwargs):
        pass

    def record(self, *args, **kwargs):
        pass


# Use factory aliases matching prometheus_client constructor signature
Counter = OTELCounter
Histogram = OTELHistogram
Gauge = OTELGauge


# ── Metric declarations (same names/labels as before) ──────────────────────
# Call sites use: METRIC_NAME.labels(key=val).inc() or .observe(val)
# This API is identical to prometheus_client — zero call-site changes needed.

# Asset metrics
ASSET_MATERIALIZATION_DURATION = Histogram(
    "catalyst_asset_materialization_duration_seconds",
    "Duration of asset materializations",
    ["code_location", "asset_key", "layer"],
)

ASSET_RECORDS_PROCESSED = Counter(
    "catalyst_asset_records_processed",
    "Number of records processed per asset materialization",
    ["code_location", "asset_key", "layer"],
)

ACTIVE_ASSET_MATERIALIZATIONS = Gauge(
    "catalyst_active_asset_materializations",
    "Number of asset materializations currently running",
    ["code_location"],
)

ASSET_SOFT_FAILURES = Counter(
    "catalyst_asset_soft_failures",
    "Asset materializations that completed with errors (partial/empty output)",
    ["code_location", "asset_key", "reason"],
)

# LLM metrics
LLM_REQUEST_DURATION = Histogram(
    "catalyst_llm_request_duration_seconds",
    "Duration of LLM API calls",
    ["model", "operation"],
)

LLM_TOKENS_USED = Counter(
    "catalyst_llm_tokens",
    "Total tokens used in LLM calls",
    ["model", "token_type"],
)

LLM_REQUESTS = Counter(
    "catalyst_llm_requests",
    "Total LLM requests",
    ["model", "operation", "status"],
)

# S3/MinIO metrics
S3_OPERATION_DURATION = Histogram(
    "catalyst_s3_operation_duration_seconds",
    "Duration of S3 operations",
    ["operation", "bucket"],
)

S3_OPERATIONS = Counter(
    "catalyst_s3_operations",
    "S3 operations performed",
    ["operation", "bucket"],
)

S3_BYTES_TRANSFERRED = Counter(
    "catalyst_s3_bytes",
    "Bytes transferred to/from S3",
    ["direction", "bucket"],
)

# Embedding metrics
EMBEDDING_BATCH_DURATION = Histogram(
    "catalyst_embedding_batch_duration_seconds",
    "Duration of embedding batch operations",
    ["provider", "model"],
)

EMBEDDING_VECTORS_CREATED = Counter(
    "catalyst_embedding_vectors",
    "Total embedding vectors created",
    ["provider", "model"],
)

# Chunking metrics
CHUNK_PROCESSING_DURATION = Histogram(
    "catalyst_chunk_processing_duration_seconds",
    "Duration of chunk processing operations",
    ["strategy"],
)

CHUNKS_CREATED = Counter(
    "catalyst_chunks_created",
    "Total chunks created",
    ["strategy"],
)

# Entity/NER metrics
ENTITIES_EXTRACTED = Counter(
    "catalyst_entities_extracted",
    "Total entities extracted",
    ["code_location", "entity_type", "method"],
)

ASSERTIONS_CREATED = Counter(
    "catalyst_assertions_created",
    "Total assertions (S-P-O triples) created",
    ["code_location"],
)

# Concordance / entity resolution metrics
ALIGNMENT_EDGES_TOTAL = Counter(
    "catalyst_alignment_edges",
    "Cross-source entity alignment edges produced by CrossSourceAligner",
    ["source_location", "target_location", "alignment_type", "top_signal"],
)

CANONICAL_ENTITIES_TOTAL = Counter(
    "catalyst_canonical_entities",
    "Canonical entities produced by the platinum resolver",
    ["entity_type", "source_count_bucket"],
)

ENTITY_REDUCTION_RATIO = Histogram(
    "catalyst_entity_reduction_ratio",
    "Ratio of candidates to mentions in per-document concordance",
    ["code_location"],
    buckets=(0.1, 0.25, 0.5, 0.75, 0.9, 1.0),
)

LLM_TOKENS_CACHED_TOTAL = Counter(
    "catalyst_llm_tokens_cached",
    "Prompt tokens served from LLM prompt cache (cache hit)",
    ["model"],
)

# Graph DB metrics
GRAPH_DB_OPERATIONS = Counter(
    "catalyst_graph_db_operations",
    "Graph database operations",
    ["operation", "backend"],
)

GRAPH_DB_OPERATION_DURATION = Histogram(
    "catalyst_graph_db_operation_duration_seconds",
    "Graph database operation duration",
    ["operation", "backend"],
)

# Media Ingest metrics
TRANSCRIPTION_DURATION = Histogram(
    "catalyst_transcription_duration_seconds",
    "Duration of audio transcription",
    ["backend", "model"],
    buckets=(10, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)

DIARIZATION_DURATION = Histogram(
    "catalyst_diarization_duration_seconds",
    "Duration of speaker diarization",
    [],
    buckets=(10, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)

DIARIZATION_REALTIME_FACTOR = Histogram(
    "catalyst_diarization_realtime_factor",
    "Ratio of audio duration to diarization time (>1 = faster than realtime)",
    ["device"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0),
)

TRANSCODE_DURATION = Histogram(
    "catalyst_transcode_duration_seconds",
    "Duration of video transcode operations",
    [],
    buckets=(10, 30, 60, 120, 300, 600, 1800, 3600, 7200),
)

TRANSCODE_COMPRESSION_RATIO = Histogram(
    "catalyst_transcode_compression_ratio",
    "Compression ratio achieved by transcode",
    [],
    buckets=(1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.5, 10.0, 15.0, 20.0),
)

TRANSCODE_SAVED_BYTES = Counter(
    "catalyst_transcode_saved_bytes",
    "Total bytes saved by transcoding",
    [],
)

MODEL_LOAD_DURATION = Histogram(
    "catalyst_model_load_duration_seconds",
    "Duration to load ML models",
    ["model_type"],
    buckets=(1, 5, 10, 30, 60, 120, 300),
)

SPEAKER_PROFILES_TOTAL = Gauge(
    "catalyst_speaker_profiles",
    "Number of speaker profiles after clustering",
    [],
)

SPEAKER_PROFILE_MERGE_DISTANCE = Histogram(
    "catalyst_speaker_profile_merge_distance",
    "Cosine distance of merges into existing speaker profiles",
    [],
    buckets=(0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50),
)

TRANSCRIPTION_REALTIME_FACTOR = Histogram(
    "catalyst_transcription_realtime_factor",
    "Ratio of audio duration to transcription time (>1 = faster than realtime)",
    ["backend", "device", "model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0),
)

# DAG health metrics (CD-59v)
DAGSTER_RUN_STATUS_TOTAL = Counter(
    "catalyst_dagster_run_status",
    "Dagster run terminal outcomes by code location and job",
    ["code_location", "job_name", "status"],
)

DAGSTER_RUN_DURATION_SECONDS = Histogram(
    "catalyst_dagster_run_duration_seconds",
    "End-to-end Dagster run wall-clock duration",
    ["code_location", "job_name"],
    buckets=(10, 30, 60, 300, 900, 1800, 3600, 7200, 14400, 28800),
)

DAGSTER_SENSOR_TICK_TOTAL = Counter(
    "catalyst_dagster_sensor_tick",
    "Dagster sensor tick outcomes",
    ["code_location", "sensor_name", "outcome"],
)

ASSET_LAST_MATERIALIZED_TIMESTAMP_SECONDS = Gauge(
    "catalyst_asset_last_materialized_timestamp_seconds",
    "Unix timestamp of last successful materialization",
    ["code_location", "asset_key"],
)


# ── Helpers ─────────────────────────────────────────────────────────────────


@contextmanager
def track_duration(histogram, labels: dict):
    """Context manager to track operation duration."""
    start = time.monotonic()
    try:
        yield
    finally:
        duration = time.monotonic() - start
        histogram.labels(**labels).observe(duration)


def track_asset_materialization(code_location: str, layer: str):
    """Decorator to track asset materialization duration and active count."""

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


_metrics_initialized = False


def start_metrics_server(port: int | None = None) -> None:
    """Initialize OTEL metrics export.

    The PeriodicExportingMetricReader flushes every 30s automatically
    and on process shutdown. No atexit/SIGTERM hacks needed. When running
    outside Dagster (e.g. CLI scripts), this is a silent no-op via the
    ``_get_meter()`` runtime-context check.
    """
    global _metrics_initialized
    if _metrics_initialized:
        return
    _metrics_initialized = True

    # Force initialization of the meter (creates exporter + reader, or
    # returns a _NoOpMeter when telemetry is disabled).
    meter = _get_meter()
    if isinstance(meter, _NoOpMeter):
        return
    logger.info("OTEL metrics initialized — flushing every 30s to Alloy")

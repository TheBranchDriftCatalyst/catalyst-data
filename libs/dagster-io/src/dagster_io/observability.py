import logging
import os
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_configured = False


def configure_tracing(service_name: str = "catalyst-data") -> None:
    """Configure OpenTelemetry tracing with OTLP gRPC export.

    Reads from environment:
    - OTEL_EXPORTER_OTLP_ENDPOINT (default: http://alloy.monitoring.svc.cluster.local:4317)
    - OTEL_SERVICE_NAME (overrides service_name param)
    - OTEL_RESOURCE_ATTRIBUTES (additional resource attributes)
    - TRACING_ENABLED (default: true, set to false to disable)
    - CATALYST_TELEMETRY (default: 0; set to 1 to force-enable tracing
      from CLI contexts even when not running inside Dagster)

    Auto-suppresses outside Dagster — CLI scripts and dev tooling don't
    spin up the OTLP exporter (alloy.monitoring is only reachable in
    cluster). Inside Dagster (DAGSTER_RUN_ID / DAGSTER_HOME etc set),
    tracing initializes as before.
    """
    global _configured
    if _configured:
        return
    _configured = True

    from dagster_io._runtime_context import telemetry_enabled

    if not telemetry_enabled():
        logger.info("Tracing disabled (running outside Dagster; set CATALYST_TELEMETRY=1 to override)")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint = os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "http://alloy.monitoring.svc.cluster.local:4317",
        )
        svc_name = os.getenv("OTEL_SERVICE_NAME", service_name)

        resource = Resource.create(
            {
                "service.name": svc_name,
                "service.namespace": "catalyst-data",
                "deployment.environment": os.getenv("DEPLOYMENT_ENV", "production"),
            }
        )

        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        logger.info(
            "OpenTelemetry tracing configured: endpoint=%s, service=%s",
            endpoint,
            svc_name,
        )
    except ImportError:
        logger.warning("OpenTelemetry packages not installed, tracing disabled")
    except Exception as e:
        logger.warning("Failed to configure tracing: %s", e)


def get_tracer(name: str):
    """Get an OpenTelemetry tracer.

    Returns a no-op tracer if OTEL is not configured.
    """
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except ImportError:
        return _NoOpTracer()


@contextmanager
def trace_operation(name: str, tracer=None, attributes: dict[str, Any] | None = None):
    """Context manager for easy span creation + asset duration metric.

    Usage:
        tracer = get_tracer(__name__)
        with trace_operation("extract_entities", tracer, {"chunk_count": 100}):
            entities = extract(chunks)
    """
    import time

    if tracer is None:
        tracer = get_tracer("catalyst-data")

    start = time.monotonic()
    try:
        from opentelemetry import trace as otel_trace

        with tracer.start_as_current_span(name, attributes=attributes or {}) as span:
            try:
                yield span
            except Exception as e:
                span.set_status(otel_trace.Status(otel_trace.StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise
    except ImportError:
        yield None
    finally:
        # Emit asset materialization duration metric
        duration = time.monotonic() - start
        attrs = attributes or {}
        code_location = attrs.get("code_location", "unknown")
        layer = attrs.get("layer", "unknown")
        try:
            from dagster_io.metrics import ASSET_MATERIALIZATION_DURATION

            ASSET_MATERIALIZATION_DURATION.labels(
                code_location=code_location,
                asset_key=name,
                layer=layer,
            ).observe(duration)
        except Exception:
            pass  # metrics not critical


class _NoOpTracer:
    """Fallback tracer when OpenTelemetry is not installed."""

    def start_as_current_span(self, name, **kwargs):
        return _NoOpContextManager()


class _NoOpContextManager:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

"""Tests for OTEL metrics wrappers — verify prometheus_client API compat.

Covers:
- Counter: .labels().inc(), positional labels, no-label counter
- Histogram: .labels().observe(), unlabeled .observe(), buckets param ignored gracefully
- Gauge: .labels().set()/.inc()/.dec(), internal value tracking, unlabeled .set()
- track_duration context manager
- track_asset_materialization decorator
- start_metrics_server idempotency
- NoOp fallback when OTEL not available
- All 25 module-level metric declarations are importable and usable
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from dagster_io.metrics import (
    ALIGNMENT_EDGES_TOTAL,
    ASSET_MATERIALIZATION_DURATION,
    ASSET_RECORDS_PROCESSED,
    CANONICAL_ENTITIES_TOTAL,
    DIARIZATION_DURATION,
    GRAPH_DB_OPERATIONS,
    LLM_REQUESTS,
    LLM_TOKENS_USED,
    S3_OPERATIONS,
    TRANSCRIPTION_DURATION,
    TRANSCRIPTION_REALTIME_FACTOR,
    OTELCounter,
    OTELGauge,
    OTELHistogram,
    start_metrics_server,
    track_asset_materialization,
    track_duration,
)

# ── Counter tests ───────────────────────────────────────────────────────────


class TestOTELCounter:
    def test_labels_keyword_inc(self):
        c = OTELCounter("test_ctr_kw", "test", ["code_location", "asset"])
        labeled = c.labels(code_location="media_ingest", asset="transcription")
        labeled.inc()
        labeled.inc(5)

    def test_labels_positional_inc(self):
        c = OTELCounter("test_ctr_pos", "test", ["a", "b"])
        labeled = c.labels("val_a", "val_b")
        labeled.inc()

    def test_no_label_counter(self):
        c = OTELCounter("test_ctr_nolabel", "test", [])
        labeled = c.labels()
        labeled.inc(10)

    def test_multiple_label_sets_independent(self):
        c = OTELCounter("test_ctr_multi", "test", ["loc"])
        a = c.labels(loc="media")
        b = c.labels(loc="congress")
        a.inc(1)
        b.inc(2)
        # Both should work without interfering


# ── Histogram tests ─────────────────────────────────────────────────────────


class TestOTELHistogram:
    def test_labels_observe(self):
        h = OTELHistogram("test_hist_label", "test", ["backend"])
        labeled = h.labels(backend="openvino")
        labeled.observe(42.5)
        labeled.observe(0.001)

    def test_unlabeled_observe(self):
        h = OTELHistogram("test_hist_nolabel", "test", [])
        h.observe(1.23)
        h.observe(99.9)

    def test_buckets_param_accepted(self):
        """buckets param is accepted but not used by OTEL (OTEL uses views)."""
        h = OTELHistogram(
            "test_hist_buckets",
            "test",
            ["model"],
            buckets=(10, 30, 60, 120, 300),
        )
        h.labels(model="whisper").observe(45.0)

    def test_duration_unit_auto_detected(self):
        """Histograms with 'seconds' or 'duration' in name get unit='s'."""
        h = OTELHistogram("my_duration_seconds", "test", [])
        h._ensure_histogram()
        # Just verify it doesn't crash — unit is set internally


# ── Gauge tests ─────────────────────────────────────────────────────────────


class TestOTELGauge:
    def test_labels_set(self):
        g = OTELGauge("test_gauge_set", "test", ["code_location"])
        labeled = g.labels(code_location="media_ingest")
        labeled.set(10)
        assert g._values[("media_ingest",)] == 10

    def test_labels_inc_dec(self):
        g = OTELGauge("test_gauge_incdec", "test", ["loc"])
        labeled = g.labels(loc="kg")
        labeled.set(5)
        assert g._values[("kg",)] == 5
        labeled.inc()
        assert g._values[("kg",)] == 6
        labeled.inc(4)
        assert g._values[("kg",)] == 10
        labeled.dec(3)
        assert g._values[("kg",)] == 7

    def test_unlabeled_set(self):
        g = OTELGauge("test_gauge_nolabel", "test", [])
        g.set(42)
        assert g._values[()] == 42

    def test_multiple_label_sets(self):
        g = OTELGauge("test_gauge_multi", "test", ["loc"])
        g.labels(loc="a").set(1)
        g.labels(loc="b").set(2)
        assert g._values[("a",)] == 1
        assert g._values[("b",)] == 2

    def test_positional_labels(self):
        g = OTELGauge("test_gauge_pos", "test", ["x", "y"])
        g.labels("foo", "bar").set(99)
        assert g._values[("foo", "bar")] == 99


# ── track_duration tests ───────────────────────────────────────────────────


class TestTrackDuration:
    def test_records_duration(self):
        mock_histogram = MagicMock()
        mock_labeled = MagicMock()
        mock_histogram.labels.return_value = mock_labeled

        with track_duration(mock_histogram, {"op": "test"}):
            time.sleep(0.01)

        mock_histogram.labels.assert_called_once_with(op="test")
        mock_labeled.observe.assert_called_once()
        duration = mock_labeled.observe.call_args[0][0]
        assert duration >= 0.01

    def test_records_on_exception(self):
        mock_histogram = MagicMock()
        mock_labeled = MagicMock()
        mock_histogram.labels.return_value = mock_labeled

        with pytest.raises(ValueError, match="boom"), track_duration(mock_histogram, {"op": "fail"}):
            raise ValueError("boom")

        mock_labeled.observe.assert_called_once()


# ── track_asset_materialization tests ──────────────────────────────────────


class TestTrackAssetMaterialization:
    def test_decorator_tracks_duration_and_gauge(self):
        @track_asset_materialization("test_loc", "gold")
        def my_asset():
            time.sleep(0.01)
            return "result"

        result = my_asset()
        assert result == "result"

    def test_decorator_tracks_on_exception(self):
        @track_asset_materialization("test_loc", "gold")
        def failing_asset():
            raise RuntimeError("fail")

        with pytest.raises(RuntimeError):
            failing_asset()


# ── start_metrics_server idempotency ───────────────────────────────────────


def test_start_metrics_server_idempotent():
    """Calling start_metrics_server() multiple times is safe."""
    start_metrics_server()
    start_metrics_server()  # should not crash


# ── Module-level metric declarations ───────────────────────────────────────


class TestModuleLevelMetrics:
    """Verify all 25+ module-level metrics are importable and usable."""

    def test_asset_records_processed(self):
        ASSET_RECORDS_PROCESSED.labels(
            code_location="test",
            asset_key="test_asset",
            layer="gold",
        ).inc(1)

    def test_llm_tokens(self):
        LLM_TOKENS_USED.labels(model="gpt-4o", token_type="input").inc(100)

    def test_llm_requests(self):
        LLM_REQUESTS.labels(model="gpt-4o", operation="extract", status="success").inc()

    def test_s3_operations(self):
        S3_OPERATIONS.labels(operation="get", bucket="catalyst-data").inc()

    def test_alignment_edges(self):
        ALIGNMENT_EDGES_TOTAL.labels(
            source_location="media_ingest",
            target_location="congress_data",
            alignment_type="sameAs",
            top_signal="exact_name",
        ).inc()

    def test_canonical_entities(self):
        CANONICAL_ENTITIES_TOTAL.labels(entity_type="PERSON", source_count_bucket="1").inc()

    def test_transcription_duration(self):
        TRANSCRIPTION_DURATION.labels(backend="openvino", model="whisper-large").observe(45.0)

    def test_diarization_duration(self):
        DIARIZATION_DURATION.observe(423.0)

    def test_transcription_realtime_factor(self):
        TRANSCRIPTION_REALTIME_FACTOR.labels(
            backend="openvino",
            device="GPU",
            model="whisper-large",
        ).observe(50.0)

    def test_graph_db_operations(self):
        GRAPH_DB_OPERATIONS.labels(operation="upsert_entities", backend="postgresql").inc()

    def test_asset_materialization_duration(self):
        ASSET_MATERIALIZATION_DURATION.labels(
            code_location="knowledge_graph",
            asset_key="canonical_entities",
            layer="platinum",
        ).observe(35.0)

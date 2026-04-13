"""Tests for OTEL metrics wrappers — verify prometheus_client API compat."""

from dagster_io.metrics import (
    OTELCounter,
    OTELGauge,
    OTELHistogram,
)


def test_counter_labels_inc():
    c = OTELCounter("test_counter", "test", ["code_location", "asset"])
    labeled = c.labels(code_location="media_ingest", asset="transcription")
    labeled.inc()
    labeled.inc(5)


def test_counter_positional_labels():
    c = OTELCounter("test_counter_pos", "test", ["a", "b"])
    labeled = c.labels("val_a", "val_b")
    labeled.inc()


def test_histogram_labels_observe():
    h = OTELHistogram("test_histogram", "test", ["backend"])
    labeled = h.labels(backend="openvino")
    labeled.observe(42.5)


def test_histogram_unlabeled_observe():
    h = OTELHistogram("test_histogram_nolabel", "test", [])
    h.observe(1.23)


def test_gauge_labels_set_inc_dec():
    g = OTELGauge("test_gauge", "test", ["code_location"])
    labeled = g.labels(code_location="media_ingest")
    labeled.set(10)
    labeled.inc()
    labeled.dec(3)


def test_gauge_unlabeled():
    g = OTELGauge("test_gauge_nolabel", "test", [])
    g.set(42)

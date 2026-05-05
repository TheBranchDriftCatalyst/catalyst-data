"""Harness ``asset_read`` event emission (CD-d7tb).

Verifies ``tests/shared/medallion.load_chunks`` emits one
``source=harness, node_name=asset_read`` event per ``data.jsonl`` key it
will read, tagging the upstream Dagster materialization (run_id +
asset_key + partition + upstream_assets + timestamp) so StateInspector
can trace harness consensus events back to the run that produced their
chunks.

Mirrors the structure of ``libs/dagster-io/tests/test_run_status_sensor_assets.py``
(the Dagster-side counterpart from CD-7pr0): configure → emit → read
back via ``event_store.read_events_for_test`` → close.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dagster_io.bench import event_store
from tests.shared import medallion

# ── fixture data ─────────────────────────────────────────────────────────

# A partitioned media-ingest chunks key + its sidecar — mirrors what
# MinioIOManager writes for ``media_chunks/<doc_id>/data.jsonl``.
_MEDIA_KEY = "silver/media_ingest/media/media_chunks/demo/data.jsonl"
_MEDIA_SIDECAR = {
    "format": "jsonl",
    "type": "list[TextChunk]",
    "count": 12,
    "timestamp": "2026-05-04T00:00:00+00:00",
    "schema": {"chunk_id": "str", "text": "str"},
    "size_bytes": 4096,
    "run_id": "dagster-abc123",
    "code_location": "media_ingest",
    "asset_key": "media_chunks",
    "partition": "demo",
    "layer": "silver",
    "upstream_assets": ["media_documents"],
}

# An unpartitioned open-leaks chunks key + its sidecar — single
# ``data.jsonl`` directly under the asset prefix, no partition segment.
_LEAK_KEY = "silver/open_leaks/leaks/leak_chunks/data.jsonl"
_LEAK_SIDECAR = {
    "format": "jsonl",
    "type": "list[TextChunk]",
    "count": 7,
    "timestamp": "2026-05-04T00:00:01+00:00",
    "schema": {"chunk_id": "str", "text": "str"},
    "size_bytes": 2048,
    "run_id": "dagster-leak-xyz",
    "code_location": "open_leaks",
    "asset_key": "leak_chunks",
    "partition": None,
    "layer": "silver",
    "upstream_assets": ["leak_documents"],
}


def _make_fake_client() -> MagicMock:
    """Stand-in for ``S3Client`` covering the surface ``load_chunks`` uses:

    - ``list_all_objects(prefix)`` for the silver/gold listing
    - ``get_object(key)`` for the ``_metadata.json`` sidecar reads
    - ``bucket`` attribute for the ``output_path`` field on the event

    ``data.jsonl`` keys are NOT served — ``_read_jsonl`` is patched out
    so the harness body never tries to actually parse jsonl payloads.
    """
    client = MagicMock()
    client.bucket = "test-bucket"

    def _list(prefix: str) -> list[str]:
        if prefix == "silver/":
            return [_MEDIA_KEY, _LEAK_KEY]
        return []

    client.list_all_objects.side_effect = _list

    def _get(key: str) -> bytes:
        if key == _MEDIA_KEY.replace("data.jsonl", "_metadata.json"):
            return json.dumps(_MEDIA_SIDECAR).encode("utf-8")
        if key == _LEAK_KEY.replace("data.jsonl", "_metadata.json"):
            return json.dumps(_LEAK_SIDECAR).encode("utf-8")
        # ``data.jsonl`` keys: the harness body is patched out, so a
        # call here means something tried to read raw chunks despite
        # the patch — fail loud rather than silently swallow the bug.
        raise AssertionError(f"unexpected get_object({key!r}) — _read_jsonl should be patched")

    client.get_object.side_effect = _get
    return client


# ── tests ────────────────────────────────────────────────────────────────


def test_load_chunks_emits_asset_read_events_per_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``load_chunks`` emits one ``asset_read`` event per ``data.jsonl`` key,
    tagging Dagster lineage from the ``_metadata.json`` sidecar.
    """
    event_store.configure(run_id="t-asset-read", run_dir=tmp_path)
    try:
        client = _make_fake_client()
        monkeypatch.setattr(medallion, "_build_client", lambda: client)
        # Patch the read body so we don't try to parse fake jsonl —
        # we're testing event emission, not data flow.
        monkeypatch.setattr(medallion, "_read_jsonl", lambda c, k, d: [])

        medallion.load_chunks()

        rows = [
            r for r in event_store.read_events_for_test() if r["source"] == "harness" and r["node_name"] == "asset_read"
        ]
        assert len(rows) == 2, f"expected one asset_read per key, got {len(rows)}: {rows!r}"

        media = next(r for r in rows if r["details"]["asset_key"] == "media_chunks")
        assert media["doc_id"] == "demo"
        assert media["code_location"] == "media_ingest"
        assert media["status"] == "ok"
        d = media["details"]
        assert d["partition_key"] == "demo"
        assert d["dagster_run_id"] == "dagster-abc123"
        assert d["layer"] == "silver"
        # output_path uses ``DAGSTER_S3_BUCKET`` env (matches the
        # _build_client wiring) — conftest pins it to "test".
        bucket = os.environ.get("DAGSTER_S3_BUCKET", "dagster")
        assert d["output_path"] == f"s3://{bucket}/{_MEDIA_KEY}"
        assert d["row_count"] == 12
        assert d["size_bytes"] == 4096
        assert d["upstream_assets"] == ["media_documents"]
        assert d["materialized_at"] == "2026-05-04T00:00:00+00:00"

        leak = next(r for r in rows if r["details"]["asset_key"] == "leak_chunks")
        # Unpartitioned: append() received doc_id=None, which the
        # event_store maps to the synthetic ``__run__`` hive partition.
        # ``read_events_for_test`` reads back through the parquet's
        # native ``doc_id`` column (NULL) — but DuckDB's
        # ``hive_partitioning=true`` projects the partition key over
        # the NULL column, so the round-tripped row reads ``__run__``.
        # The authoritative "no partition" signal is therefore on
        # ``details.partition_key`` (which the harness sets directly).
        assert leak["doc_id"] in (None, "__run__")
        assert leak["details"]["partition_key"] is None
        assert leak["code_location"] == "open_leaks"
        assert leak["details"]["dagster_run_id"] == "dagster-leak-xyz"
        assert leak["details"]["upstream_assets"] == ["leak_documents"]
    finally:
        event_store.close()


def test_load_chunks_no_event_when_event_store_unconfigured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``event_store.configure``, ``load_chunks`` is a silent
    no-op on the audit log — unit tests and ad-hoc scripts that don't
    set up a run shouldn't be forced to.
    """
    # Make sure no leftover store from a sibling test bleeds in.
    if event_store.is_configured():
        event_store.close()

    client = _make_fake_client()
    monkeypatch.setattr(medallion, "_build_client", lambda: client)
    monkeypatch.setattr(medallion, "_read_jsonl", lambda c, k, d: [])

    # No exception, no events — and crucially, no get_object calls for
    # the sidecar since we shouldn't pay for sidecar reads when
    # nothing's listening.
    medallion.load_chunks()
    sidecar_keys_fetched = [
        call.args[0]
        for call in client.get_object.call_args_list
        if call.args and call.args[0].endswith("_metadata.json")
    ]
    assert sidecar_keys_fetched == [], f"sidecar fetched without event_store configured: {sidecar_keys_fetched!r}"


def test_load_chunks_tolerates_missing_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A legacy materialization without a ``_metadata.json`` sidecar
    must not kill the bench run — the event still emits with whatever
    ``_KEY_RE`` could parse out of the key path.
    """
    event_store.configure(run_id="t-asset-read-missing", run_dir=tmp_path)
    try:
        client = _make_fake_client()

        def _get_no_sidecar(key: str) -> bytes:
            # Simulate every sidecar fetch failing (legacy bucket).
            if key.endswith("_metadata.json"):
                raise FileNotFoundError(key)
            raise AssertionError(f"unexpected get_object({key!r})")

        client.get_object.side_effect = _get_no_sidecar
        monkeypatch.setattr(medallion, "_build_client", lambda: client)
        monkeypatch.setattr(medallion, "_read_jsonl", lambda c, k, d: [])

        medallion.load_chunks()

        rows = [
            r for r in event_store.read_events_for_test() if r["source"] == "harness" and r["node_name"] == "asset_read"
        ]
        assert len(rows) == 2

        # Sidecar fields land as None / [] but key-derived fields still
        # populate from the regex match.
        for r in rows:
            d = r["details"]
            assert d["dagster_run_id"] is None
            assert d["row_count"] is None
            assert d["upstream_assets"] == []
            assert d["materialized_at"] is None
            # ``asset_key`` falls back to the regex group when sidecar
            # missing — stays useful for StateInspector grouping.
            assert d["asset_key"] in {"media_chunks", "leak_chunks"}
    finally:
        event_store.close()

"""Regression test for CD-5vd0: cold-path emit_persist_artifacts wiring.

Verifies the ``mention_proposition_artifacts`` asset emits a
``persist_artifacts`` audit event with the right counts, S3 paths, and
``from_cache=False`` after a cold-path run materializes mentions +
assertions.

The asset's runtime contract is just ``len(media_mentions)`` +
``len(media_assertions)`` → emit. We exercise that with plain list
inputs (the asset doesn't introspect element shape) so the test stays
decoupled from the Mention / Assertion pydantic schemas.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from dagster import build_asset_context

from dagster_io import Assertion, Mention
from dagster_io.bench import event_store


def _mk_mention(doc_id: str, chunk_id: str, text: str) -> Mention:
    return Mention(document_id=doc_id, chunk_id=chunk_id, text=text, mention_type="PERSON")


def _mk_assertion(subj: str, obj: str) -> Assertion:
    return Assertion(subject_text=subj, predicate="works_at", object_text=obj)


@pytest.fixture(autouse=True)
def _configure_event_store(tmp_path: Path):
    event_store.close()
    event_store.configure(run_id="test-run-cold", run_dir=tmp_path)
    yield tmp_path
    event_store.close()


def _persist_events_for_doc(run_dir: Path, doc_id: str) -> list[dict]:
    """Read every ``persist_artifacts`` event the store wrote for ``doc_id``.

    The bench event_store writes parquet shards under
    ``run_dir/events/doc_id=<doc_id>/`` — close() flushes them.
    """
    import pyarrow.parquet as pq

    out: list[dict] = []
    for p in run_dir.rglob("*.parquet"):
        for row in pq.ParquetFile(str(p)).read().to_pylist():
            if row.get("node_name") == "persist_artifacts" and row.get("doc_id") == doc_id:
                out.append(row)
    return out


def test_cold_path_emits_persist_artifacts(_configure_event_store: Path):
    from media_ingest.assets.persist_artifacts import mention_proposition_artifacts

    doc_id = "test-doc-1"
    ctx = build_asset_context(partition_key=doc_id)

    mentions = [_mk_mention(doc_id, "c0", t) for t in ("Alice", "Bob", "Carol")]
    assertions = [
        _mk_assertion("Alice", "Acme"),
        _mk_assertion("Bob", "Acme"),
        _mk_assertion("Carol", "Globex"),
        _mk_assertion("Alice", "Globex"),
    ]

    out = mention_proposition_artifacts(ctx, media_mentions=mentions, media_assertions=assertions)
    assert out.metadata["mentions_written"].value == 3
    assert out.metadata["propositions_written"].value == 4

    event_store.close()  # flush parquet shards before we scan them
    events = _persist_events_for_doc(_configure_event_store, doc_id)
    assert len(events) == 1, f"expected one persist_artifacts event, got {len(events)}"
    ev = events[0]
    details = ev.get("details") or {}
    if isinstance(details, str):
        details = json.loads(details)

    assert details.get("mentions_written") == 3
    assert details.get("propositions_written") == 4
    assert details.get("from_cache") is False

    row_counts = details.get("row_counts") or {}
    assert row_counts.get("media_ingest/mention_artifacts") == 3
    assert row_counts.get("media_ingest/proposition_artifacts") == 4

    output_paths = details.get("output_paths") or {}
    assert output_paths.get("media_ingest/mention_artifacts", "").startswith("s3://")
    assert output_paths.get("media_ingest/proposition_artifacts", "").startswith("s3://")


def test_cold_path_zero_assertions_still_emits(_configure_event_store: Path):
    from media_ingest.assets.persist_artifacts import mention_proposition_artifacts

    doc_id = "test-doc-empty"
    ctx = build_asset_context(partition_key=doc_id)

    mentions = [_mk_mention(doc_id, "c0", t) for t in ("Alice", "Bob")]
    assertions: list[Assertion] = []

    out = mention_proposition_artifacts(ctx, media_mentions=mentions, media_assertions=assertions)
    assert out.metadata["mentions_written"].value == 2
    assert out.metadata["propositions_written"].value == 0

    event_store.close()  # flush parquet shards before we scan them
    events = _persist_events_for_doc(_configure_event_store, doc_id)
    assert len(events) == 1
    details = events[0].get("details") or {}
    if isinstance(details, str):
        details = json.loads(details)
    assert details.get("propositions_written") == 0
    # The proposition_artifacts row_count + path should be absent when
    # there are zero assertions (asset only adds them conditionally).
    row_counts = details.get("row_counts") or {}
    assert "media_ingest/proposition_artifacts" not in row_counts

"""Parity test for the bench audit log dual-write (CD-jzkg, Phase 1).

This is the **Phase 1 exit gate**. The test drives a synthetic 1-doc /
1-model bench that exercises every emit-site shape the real harness +
exgraph stack produces, with both writers live, and asserts the six
parity invariants from §7 of ``docs/plans/duckdb-audit-log.md``:

1. Row-count parity        — len(jsonl) == duckdb_count
2. (node_name, status) histogram equality
3. Field-by-field typed-column equality (ts modulo coercion)
4. ``state``/``details`` JSON equality (post json.loads)
5. ``chunk_loaded`` text fidelity (byte-identical text + truncated flag)
6. Idempotent consolidate (same parquet sha256 on two passes)

We deliberately drive the writers through ``event_tail.append`` (and
its ``emit_chunk_text`` / ``emit_chunk_extracted`` helpers) — that's
the single funnel the harness, exgraph nodes, and pipeline.py all hit.
A synthetic event corpus exercises every shape the bench emits today
(harness model_run lifecycle, chunk_loaded with full text, exgraph
extract/validate, consensus mention_decision, ner_encoder_completed)
without needing MinIO / Ollama / real model inference. If a future
emit site picks up a new shape, it'll go through the same funnel and
parity will hold.

Why not a full ``task bench`` invocation: the harness needs MinIO,
benchmark fixtures, and possibly Ollama. Those are session-state, not
dual-write contract — and they break the test on any dev machine
without that stack up. The contract we care about for Phase 1 is
"every event_tail row also lands in DuckDB, byte-identical." This
test verifies exactly that, with the same code path the bench uses.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dagster_io.bench import event_store, event_tail


@pytest.fixture(autouse=True)
def _reset_module_globals(tmp_path: Path) -> None:
    """Both writers are module-globals — reset them around each test
    so a leaked configure(...) doesn't bleed across cases."""
    # Forcibly clear event_tail's globals (no public API for this
    # because production never re-targets mid-process).
    event_tail._path = None  # noqa: SLF001
    event_tail._run_id = None  # noqa: SLF001
    event_tail._seen_chunks = set()  # noqa: SLF001
    event_store.close()
    yield
    event_tail._path = None  # noqa: SLF001
    event_tail._run_id = None  # noqa: SLF001
    event_tail._seen_chunks = set()  # noqa: SLF001
    event_store.close()


def _drive_synthetic_bench(
    *, run_dir: Path, run_id: str, doc_id: str, model: str, chunk_id: str, chunk_text: str
) -> None:
    """Emit the cross-section of event shapes a real 1-doc/1-model run produces.

    Goes through ``event_tail.append`` and the ``emit_chunk_*`` helpers —
    the same funnel the bench harness, exgraph nodes, and pipeline.py
    all use. Dual-write to event_store is automatic because we
    pre-configure both module-globals.
    """
    # 1. run lifecycle — harness emits this before any model loop
    event_tail.append(
        source="harness",
        node_name="run_start",
        status="started",
        details={"pipeline": "exgraph", "model_count": 1, "bus_port": 0},
    )

    # 2. model lifecycle — harness wraps each model run with
    # started/completed events that the StateInspector's gantt uses
    event_tail.append(
        source="harness",
        node_name="model_run",
        status="started",
        model=model,
        details={"tier": "encoder", "tags": ["encoder"]},
    )

    # 3. chunk_loaded — emitted once per chunk via emit_chunk_text. The
    # text is what the StateInspector renders in the input pane.
    event_tail.emit_chunk_text(
        chunk_id,
        chunk_text,
        doc_id=doc_id,
        model=model,
        domain="transcript",
        speaker_label="HOST_00",
        temporal_start_ms=0.0,
        temporal_end_ms=12_500.0,
        chunk_index=0,
        total_chunks=1,
        chunk_metadata={"chunker": "semantic", "size": 400},
    )

    # 4. exgraph node lifecycle — extract → validate → repair
    event_tail.append(
        source="exgraph",
        node_name="extract_ner",
        status="started",
        model=model,
        doc_id=doc_id,
        chunk_id=chunk_id,
        retry_count=0,
        state={"candidate_count": 0, "candidate_sample": []},
        details={"input_len": len(chunk_text)},
    )
    event_tail.append(
        source="exgraph",
        node_name="extract_ner",
        status="completed",
        model=model,
        doc_id=doc_id,
        chunk_id=chunk_id,
        retry_count=0,
        state={
            "candidate_count": 3,
            "candidate_sample": [
                {"text": "Acme Corp", "type": "ORGANIZATION", "span": [10, 19], "conf": 0.92},
            ],
        },
        details={"duration_s": 0.42},
    )
    event_tail.append(
        source="exgraph",
        node_name="validate_ner",
        status="completed",
        model=model,
        doc_id=doc_id,
        chunk_id=chunk_id,
        state={"verdict": "valid", "valid_count": 3, "invalid_count": 0, "errors": []},
        details={"duration_s": 0.04},
    )

    # 5. ner_encoder_started/completed — Phase A NER ensemble emit shape
    event_tail.append(
        source="harness",
        node_name="ner_encoder_started",
        status="started",
        model="gliner-medium",
        doc_id=doc_id,
        chunk_id=chunk_id,
        details={"encoder": "gliner-medium"},
    )
    event_tail.append(
        source="harness",
        node_name="ner_encoder_completed",
        status="completed",
        model="gliner-medium",
        doc_id=doc_id,
        chunk_id=chunk_id,
        details={"encoder": "gliner-medium", "mention_count": 3, "duration_s": 0.18},
    )

    # 6. consensus events — exercises the consensus.py emit path with a
    # synthetic chunk_id of f"{doc_id}:_consensus"
    consensus_chunk_id = f"{doc_id}:_consensus"
    event_tail.append(
        source="consensus",
        node_name="consensus_started",
        status="started",
        doc_id=doc_id,
        chunk_id=consensus_chunk_id,
        details={"n_encoders": 3, "total_input_mentions": 9},
    )
    event_tail.append(
        source="consensus",
        node_name="mention_decision",
        status="accepted",
        doc_id=doc_id,
        chunk_id=consensus_chunk_id,
        details={
            "text": "Acme Corp",
            "canonical_type": "ORGANIZATION",
            "vote_count": 3,
            "n_encoders": 3,
            "source_models": ["gliner-medium", "gliner-pii", "gpt-4o"],
            "mean_confidence": 0.91,
            "type_votes": {"ORGANIZATION": 3},
        },
    )
    event_tail.append(
        source="consensus",
        node_name="consensus_completed",
        status="completed",
        doc_id=doc_id,
        chunk_id=consensus_chunk_id,
        details={
            "accepted_count": 3,
            "rejected_count": 0,
            "mean_vote_count": 3.0,
            "type_distribution": {"ORGANIZATION": 1, "PERSON": 2},
            "span_disagreement_rate": 0.0,
        },
    )

    # 7. chunk_extracted — terminal event tying chunk text to final NER+SPO
    event_tail.emit_chunk_extracted(
        chunk_id,
        model=model,
        doc_id=doc_id,
        mentions=[{"text": "Acme Corp", "type": "ORG"}],
        propositions=[],
    )

    # 8. error path — common emit shape
    event_tail.append(
        source="exgraph",
        node_name="repair_ner",
        status="error",
        model=model,
        doc_id=doc_id,
        chunk_id=chunk_id,
        retry_count=1,
        details={"reason": "TimeoutError", "message": "deadline exceeded"},
    )

    # 9. model_run completed
    event_tail.append(
        source="harness",
        node_name="model_run",
        status="completed",
        model=model,
        details={"duration_s": 1.84, "stats": {"mention_count": 3, "assertion_count": 0}},
    )

    # 10. run_end
    event_tail.append(
        source="harness",
        node_name="run_end",
        status="completed",
        details={"results": 1, "models": 1},
    )


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _hash_parquet_rows(parquet_path: Path) -> str:
    """Hash row data only — parquet header timestamps aren't part of
    the data contract we care about."""
    import duckdb

    conn = duckdb.connect(":memory:")
    rows = conn.execute(
        "SELECT * FROM read_parquet(?) ORDER BY seq, writer_pid",
        (str(parquet_path),),
    ).fetchall()
    h = hashlib.sha256()
    for r in rows:
        h.update(repr(r).encode("utf-8"))
    return h.hexdigest()


def test_dual_write_parity(tmp_path: Path) -> None:
    """The Phase 1 exit gate.

    Drives a synthetic 1-doc / 1-model bench through ``event_tail`` (which
    fans out to ``event_store``), then asserts the six parity invariants.
    """
    run_id = "parity-1"
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    # Configure both writers — same shape as benchmark_harness.py:1373-1378
    jsonl_path = run_dir / "events.jsonl"
    jsonl_path.write_text("")
    event_tail.configure(jsonl_path, run_id=run_id)
    event_store.configure(run_id=run_id, run_dir=run_dir)

    # Drive a representative cross-section of bench emit shapes.
    chunk_text = "Acme Corp announced a partnership today. " * 60  # ~2.5 KB — under the 4 KiB cap
    _drive_synthetic_bench(
        run_dir=run_dir,
        run_id=run_id,
        doc_id="doc-parity-1",
        model="gliner-medium",
        chunk_id="doc-parity-1:0",
        chunk_text=chunk_text,
    )

    # Tear down event_store and consolidate so events.parquet exists
    # for the parity reads. (This mirrors what
    # benchmark_harness.py:1786 does at run end.)
    event_store.flush()
    event_store.close()
    parquet_out = event_store.BenchEventStore.consolidate(run_dir)
    assert parquet_out.exists()

    # ── Read both sides ────────────────────────────────────────────────
    jsonl_rows = _read_jsonl(jsonl_path)

    import duckdb

    duck_conn = duckdb.connect(":memory:")
    duck_conn.execute(f"CREATE VIEW e AS SELECT * FROM read_parquet('{parquet_out}')")
    duck_rows_raw = duck_conn.execute("SELECT * FROM e ORDER BY seq, writer_pid").fetchall()
    duck_cols = [d[0] for d in duck_conn.description]
    duck_rows = [dict(zip(duck_cols, r, strict=False)) for r in duck_rows_raw]

    # ── Assertion 1: row-count parity ──────────────────────────────────
    assert len(jsonl_rows) == len(duck_rows), f"row count mismatch: jsonl={len(jsonl_rows)} duckdb={len(duck_rows)}"
    # Must be > 0 — guards against the test silently no-op'ing if both
    # writers are misconfigured.
    assert len(jsonl_rows) > 10, f"too few events emitted: {len(jsonl_rows)}"

    # ── Assertion 2: (node_name, status) histogram equality ────────────
    jsonl_hist = Counter((r["node_name"], r["status"]) for r in jsonl_rows)
    duck_hist = Counter((r["node_name"], r["status"]) for r in duck_rows)
    assert jsonl_hist == duck_hist, (
        f"histogram mismatch:\n"
        f"  jsonl-only: {set(jsonl_hist) - set(duck_hist)}\n"
        f"  duckdb-only: {set(duck_hist) - set(jsonl_hist)}\n"
        f"  count diffs: { {k: (jsonl_hist[k], duck_hist[k]) for k in (jsonl_hist | duck_hist) if jsonl_hist[k] != duck_hist[k]} }"
    )

    # ── Assertion 3: typed-column equality ─────────────────────────────
    typed_cols = (
        "source",
        "node_name",
        "status",
        "model",
        "doc_id",
        "chunk_idx",
        "chunk_id",
        "retry_count",
        "code_location",
        "evidence_window_id",
    )
    # Order-stable pairing — the parquet rows are sorted by (seq,
    # writer_pid); the jsonl rows are append-ordered which equals
    # emit order which equals the seq we stamped. Same-process so
    # writer_pid is constant. So a positional zip is correct.
    for i, (j_row, d_row) in enumerate(zip(jsonl_rows, duck_rows, strict=False)):
        for col in typed_cols:
            jv = j_row.get(col)
            dv = d_row.get(col)
            assert jv == dv, f"row {i} col {col!r}: jsonl={jv!r} duckdb={dv!r}"
        # ts equality modulo type coercion — jsonl is ISO-8601 string,
        # duckdb is a datetime. Compare as datetimes after parse.
        j_ts = datetime.fromisoformat(j_row["ts"])
        d_ts = d_row["ts"]
        if d_ts.tzinfo is None:
            d_ts = d_ts.replace(tzinfo=UTC)
        # Sub-millisecond differences are OK — both sides stamp
        # their own ts off the same datetime.now() call inside
        # event_tail.append (the dual-write reuses the same string),
        # so they should actually be byte-identical here.
        assert abs((j_ts - d_ts).total_seconds()) < 0.01, f"row {i} ts: jsonl={j_ts} duckdb={d_ts}"

    # ── Assertion 4: state + details JSON equality ─────────────────────
    for i, (j_row, d_row) in enumerate(zip(jsonl_rows, duck_rows, strict=False)):
        j_state = j_row.get("state") or {}
        j_details = j_row.get("details") or {}
        d_state = json.loads(d_row.get("state") or "{}")
        d_details = json.loads(d_row.get("details") or "{}")
        assert j_state == d_state, f"row {i} state mismatch:\n  jsonl={j_state}\n  duckdb={d_state}"
        assert j_details == d_details, f"row {i} details mismatch:\n  jsonl={j_details}\n  duckdb={d_details}"

    # ── Assertion 5: chunk_loaded text fidelity ────────────────────────
    j_chunk_loaded = [r for r in jsonl_rows if r["node_name"] == "chunk_loaded"]
    d_chunk_loaded = [r for r in duck_rows if r["node_name"] == "chunk_loaded"]
    assert len(j_chunk_loaded) == len(d_chunk_loaded) == 1
    j_text = j_chunk_loaded[0]["details"]["text"]
    d_text = json.loads(d_chunk_loaded[0]["details"])["text"]
    assert j_text == d_text, "chunk_loaded text bytes diverged"
    assert j_chunk_loaded[0]["details"]["truncated"] == json.loads(d_chunk_loaded[0]["details"])["truncated"]

    # ── Assertion 6: idempotent consolidate ────────────────────────────
    sha_a = _hash_parquet_rows(parquet_out)
    parquet_out_2 = event_store.BenchEventStore.consolidate(run_dir)
    sha_b = _hash_parquet_rows(parquet_out_2)
    assert sha_a == sha_b, f"consolidate not idempotent: {sha_a} vs {sha_b}"

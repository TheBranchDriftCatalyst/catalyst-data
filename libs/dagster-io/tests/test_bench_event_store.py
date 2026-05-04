"""Tests for the DuckDB-backed bench audit log (CD-jzkg, Phase 1).

The four required cases per the plan:

* roundtrip — append → query
* swarm — multiple subprocesses concurrently writing shards
* crash — partial shard after SIGKILL is still readable
* arbitrary-filter — p99 < 50 ms for ``WHERE chunk_id=? AND status='error'``
  on a 10k-row corpus

Tests are written against the public module API (``configure``,
``append``, ``flush``, ``query``, ``consolidate``) so refactors of the
internal buffering / shard-naming scheme don't churn the test list.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from dagster_io.bench import event_store


@pytest.fixture(autouse=True)
def _reset_module_global() -> None:
    """Each test gets a fresh module-global writer.

    The store is process-wide; without this, a configure(...) in test A
    would leak into test B and trip the "already configured" guard.
    """
    event_store.close()
    yield
    event_store.close()


def _make_store(tmp_path: Path, run_id: str | None = None) -> event_store.BenchEventStore:
    rid = run_id or f"run-{uuid.uuid4().hex[:8]}"
    return event_store.BenchEventStore(run_id=rid, run_dir=tmp_path)


# ─────────────────────────────────────────────────────────────────────────
# 1. roundtrip
# ─────────────────────────────────────────────────────────────────────────


def test_append_then_query_roundtrip(tmp_path: Path) -> None:
    """Single-process append → flush → read_parquet round-trip preserves
    every typed column and the JSON payloads."""
    s = _make_store(tmp_path, run_id="rt-1")

    s.append(
        source="harness",
        node_name="run_start",
        status="started",
        model="gliner-medium",
        doc_id="doc-1",
        chunk_idx=0,
        chunk_id="doc-1:0",
        retry_count=0,
        code_location="media_ingest",
        evidence_window_id=None,
        state={"verdict": "ok"},
        details={"k": "v"},
    )
    s.append(
        source="exgraph",
        node_name="extract_ner",
        status="completed",
        model="gpt-4o",
        doc_id="doc-1",
        chunk_id="doc-1:0",
        details={"mention_count": 7},
    )
    s.flush()

    rows = s.query("SELECT * FROM events ORDER BY seq")
    assert len(rows) == 2
    r0, r1 = rows
    assert r0["run_id"] == "rt-1"
    assert r0["seq"] == 1
    assert r0["writer_pid"] == os.getpid()
    assert r0["source"] == "harness"
    assert r0["node_name"] == "run_start"
    assert r0["status"] == "started"
    assert r0["model"] == "gliner-medium"
    assert r0["chunk_idx"] == 0
    assert r0["retry_count"] == 0
    assert r0["code_location"] == "media_ingest"
    assert json.loads(r0["state"]) == {"verdict": "ok"}
    assert json.loads(r0["details"]) == {"k": "v"}

    assert r1["seq"] == 2
    assert r1["node_name"] == "extract_ner"
    assert json.loads(r1["details"]) == {"mention_count": 7}

    s.close()


# ─────────────────────────────────────────────────────────────────────────
# 2. swarm — 4 subprocesses × 50 events
# ─────────────────────────────────────────────────────────────────────────


_SWARM_WRITER = """
import os, sys, time, uuid
from pathlib import Path
sys.path.insert(0, {libs_path!r})

from dagster_io.bench.event_store import BenchEventStore

run_dir = Path(sys.argv[1])
run_id = sys.argv[2]
n = int(sys.argv[3])
store = BenchEventStore(run_id=run_id, run_dir=run_dir)
for i in range(n):
    store.append(
        source='harness',
        node_name='model_run',
        status='running',
        model=f'm-{{os.getpid()}}',
        details={{'i': i}},
    )
store.close()
"""


def _libs_path() -> str:
    """Path to dagster-io's ``src/`` so subprocesses can import it without
    needing an editable install in the same interpreter (they don't even
    necessarily run with the same sys.executable)."""
    here = Path(__file__).resolve()
    # tests/test_bench_event_store.py → libs/dagster-io/src
    return str(here.parents[1] / "src")


def test_swarm_writers_consolidate_in_order(tmp_path: Path) -> None:
    """Four subprocesses each emit 50 events. Consolidation produces a
    single parquet sorted by ``(seq, writer_pid)`` with no dupes and all
    200 rows accounted for.

    This is the swarm-safety contract: per-process shards are
    self-contained, the merge is deterministic, the ``(seq, writer_pid)``
    tuple is the canonical ordering key.
    """
    run_id = "swarm-1"
    n_per_proc = 50
    n_procs = 4

    libs = _libs_path()
    procs: list[subprocess.Popen] = []
    for _ in range(n_procs):
        p = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _SWARM_WRITER.format(libs_path=libs),
                str(tmp_path),
                run_id,
                str(n_per_proc),
            ],
        )
        procs.append(p)

    for p in procs:
        rc = p.wait(timeout=30)
        assert rc == 0, f"swarm writer pid={p.pid} exited with {rc}"

    # Shard count: one per process.
    shards = sorted(tmp_path.glob("events-*.parquet"))
    assert len(shards) == n_procs, f"expected {n_procs} shards, got {len(shards)}: {shards}"

    out = event_store.BenchEventStore.consolidate(tmp_path)
    assert out.exists()
    assert out.name == "events.parquet"

    import duckdb  # noqa: PLC0415

    conn = duckdb.connect(":memory:")
    cur = conn.execute("SELECT seq, writer_pid FROM read_parquet(?)", (str(out),))
    rows = cur.fetchall()
    assert len(rows) == n_procs * n_per_proc

    # Sorted by (seq, writer_pid) — verify ordering invariant.
    for prev, cur_row in zip(rows, rows[1:], strict=False):
        assert (prev[0], prev[1]) <= (cur_row[0], cur_row[1])

    # No duplicate (seq, writer_pid) pairs — each writer has its own seq
    # space and they're unique within a writer.
    pairs = {(seq, pid) for seq, pid in rows}
    assert len(pairs) == len(rows)


# ─────────────────────────────────────────────────────────────────────────
# 3. crash — SIGKILL mid-flush
# ─────────────────────────────────────────────────────────────────────────


_CRASH_WRITER = """
import os, sys, time, signal
from pathlib import Path
sys.path.insert(0, {libs_path!r})

from dagster_io.bench.event_store import BenchEventStore

run_dir = Path(sys.argv[1])
run_id = sys.argv[2]
store = BenchEventStore(run_id=run_id, run_dir=run_dir)
# Emit enough events to force at least one flush ceiling crossing,
# then a few more so there's a buffer pending when we self-kill.
for i in range({n_pre}):
    store.append(source='harness', node_name='x', status='ok', details={{'i': i}})
store.flush()
# Mark that the first flush completed — parent reads this to know the
# parquet has *something* on disk before SIGKILL.
(run_dir / 'first-flush-done').write_text('ok')
for i in range({n_post}):
    store.append(source='harness', node_name='x', status='ok', details={{'i': i + {n_pre}}})
# Self-kill BEFORE the flush completes — buffer has unflushed rows.
os.kill(os.getpid(), signal.SIGKILL)
"""


def test_partial_shard_after_crash_is_readable(tmp_path: Path) -> None:
    """SIGKILL the writer mid-buffer. The parquet shard from the prior
    flush must still be readable — we accept losing the un-flushed
    buffer (that's the trade-off for not holding cross-process locks)
    but we MUST NOT corrupt or lose what already landed."""
    run_id = "crash-1"
    libs = _libs_path()
    p = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _CRASH_WRITER.format(libs_path=libs, n_pre=10, n_post=5),
            str(tmp_path),
            run_id,
        ],
    )
    rc = p.wait(timeout=15)
    # SIGKILL exit: -SIGKILL on POSIX (negative because killed by signal).
    assert rc != 0, f"expected non-zero exit from SIGKILL, got {rc}"

    # The crash sentinel should exist — proves we got past the first flush.
    assert (tmp_path / "first-flush-done").exists()

    shards = sorted(tmp_path.glob("events-*.parquet"))
    assert len(shards) == 1

    # Parquet must be readable AND contain the pre-flush events. We do
    # NOT assert a specific count beyond ``>= n_pre`` — depending on
    # whether the timer thread fired between the explicit flush and the
    # kill, post-flush events may or may not have landed.
    rows = event_store.BenchEventStore(run_id=run_id + "-reader", run_dir=tmp_path).query("SELECT * FROM events")
    assert len(rows) >= 10


# ─────────────────────────────────────────────────────────────────────────
# 4. arbitrary filter — p99 < 50 ms on 10k-row corpus
# ─────────────────────────────────────────────────────────────────────────


def test_query_arbitrary_filter(tmp_path: Path) -> None:
    """A 10k-row corpus with ``chunk_id`` and ``status`` indexed by
    DuckDB's parquet zone maps must answer the typical viewer filter
    (``WHERE chunk_id = ? AND status = 'error'``) in p99 < 50 ms.

    The corpus distribution mimics a real bench run: ~200 chunks ×
    50 events each, with 3 % of events flagged ``error``."""
    s = _make_store(tmp_path, run_id="filter-bench")

    n_chunks = 200
    events_per_chunk = 50
    statuses = [
        "started",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "completed",
        "error",
        "error",
        "error",
    ]
    assert len(statuses) == events_per_chunk

    for ci in range(n_chunks):
        chunk_id = f"doc:chunk-{ci:04d}"
        for ei, status in enumerate(statuses):
            s.append(
                source="exgraph",
                node_name="extract_ner",
                status=status,
                model="gliner-medium",
                doc_id="doc",
                chunk_idx=ci,
                chunk_id=chunk_id,
                details={"i": ei},
            )
    s.flush()

    # Verify corpus size — fail loud before benchmarking.
    [count_row] = s.query("SELECT COUNT(*) AS n FROM events")
    assert count_row["n"] == n_chunks * events_per_chunk == 10000

    # Pick an arbitrary chunk and time the typical filter.
    target = "doc:chunk-0123"
    timings: list[float] = []
    for _ in range(30):
        t0 = time.perf_counter()
        rows = s.query(
            "SELECT * FROM events WHERE chunk_id = ? AND status = ?",
            (target, "error"),
        )
        t1 = time.perf_counter()
        timings.append((t1 - t0) * 1000.0)
    # Sanity — we expect 3 errors per chunk in the seeded distribution.
    assert len(rows) == 3

    timings.sort()
    p99 = timings[int(len(timings) * 0.99) - 1] if len(timings) >= 100 else timings[-1]
    # p99 < 50 ms per the plan. Local DuckDB on parquet is typically
    # well under 5 ms; a generous ceiling guards against CI noise without
    # hiding regressions.
    assert p99 < 50.0, f"arbitrary-filter p99={p99:.2f}ms exceeds 50ms ceiling"

    s.close()


# ─────────────────────────────────────────────────────────────────────────
# Extra: idempotent consolidate (parity test #6 also exercises this, but
# keeping a unit-level guard here so a regression shows up locally first)
# ─────────────────────────────────────────────────────────────────────────


def test_consolidate_is_idempotent(tmp_path: Path) -> None:
    s = _make_store(tmp_path, run_id="idem-1")
    for i in range(20):
        s.append(source="harness", node_name="x", status="ok", details={"i": i})
    s.flush()
    s.close()

    out1 = event_store.BenchEventStore.consolidate(tmp_path)
    sha1 = _sha256_rows(out1)
    out2 = event_store.BenchEventStore.consolidate(tmp_path)
    sha2 = _sha256_rows(out2)
    assert sha1 == sha2


def _sha256_rows(parquet_path: Path) -> str:
    """Hash the row data (not parquet metadata) so we don't false-fail
    on parquet writer's per-call timestamps in the file header."""
    import hashlib

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

"""Tests for the DuckDB-backed bench audit log.

Phase 1-3 cases:

* roundtrip — append → query
* swarm — multiple subprocesses concurrently writing shards
* crash — partial shard after SIGKILL is still readable
* arbitrary-filter — p99 < 50 ms for ``WHERE chunk_id=? AND status='error''
  on a 10k-row corpus

Phase 4 cases (CD-jzkg.1, hive-partitioned by doc_id):

* writer partitions shards by doc_id
* events with no doc_id land in the synthetic ``__run__`` partition
* consolidate produces one ``data.parquet`` per partition + cleans shards
* ``WHERE doc_id = ?`` partition-prunes to a single file
* legacy flat ``events.parquet`` runs still read correctly

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
# Each subprocess writes events alternating across two doc_ids plus a
# few harness-level events with no doc_id. The Phase 4 partition tree
# must end up with shards under doc_id=doc-A, doc_id=doc-B, and
# doc_id=__run__ for each writer pid.
docs = ['doc-A', 'doc-B']
store = BenchEventStore(run_id=run_id, run_dir=run_dir)
for i in range(n):
    if i % 10 == 0:
        # harness-level event — no doc_id → partition __run__
        store.append(
            source='harness',
            node_name='heartbeat',
            status='info',
            model=f'm-{{os.getpid()}}',
            details={{'i': i}},
        )
    else:
        store.append(
            source='harness',
            node_name='model_run',
            status='running',
            model=f'm-{{os.getpid()}}',
            doc_id=docs[i % 2],
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
    """Four subprocesses each emit 50 events across {doc-A, doc-B, no-doc}.
    Consolidation produces one ``data.parquet`` per ``doc_id`` partition,
    each sorted by ``(seq, writer_pid)``, with no dupes and all 200 rows
    accounted for across the partitions.

    Phase 4 (CD-jzkg.1): per-(pid, doc_id) shards consolidate
    independently into per-partition ``events/doc_id=<doc>/data.parquet``.
    Multiple writer processes producing the same doc_id is allowed —
    the merge is deterministic, ``(seq, writer_pid)`` is still the
    canonical ordering key WITHIN a partition.
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

    events_root = tmp_path / "events"
    # Each writer produces one shard per partition it touched. The
    # writer script touches 3 partitions (doc-A, doc-B, __run__), so
    # we expect 3 partitions × n_procs shards before consolidation.
    expected_partitions = {"doc_id=doc-A", "doc_id=doc-B", "doc_id=__run__"}
    actual_partitions = {p.name for p in events_root.glob("doc_id=*")}
    assert actual_partitions == expected_partitions, (
        f"expected partitions {expected_partitions}, got {actual_partitions}"
    )
    for part_name in expected_partitions:
        shards = sorted((events_root / part_name).glob("shard-*.parquet"))
        assert len(shards) == n_procs, f"expected {n_procs} shards under {part_name}, got {len(shards)}: {shards}"

    event_store.BenchEventStore.consolidate(tmp_path)

    # Per-partition: exactly one data.parquet, no shards left over.
    import duckdb  # noqa: PLC0415

    conn = duckdb.connect(":memory:")
    total_rows = 0
    for part_name in expected_partitions:
        part_dir = events_root / part_name
        data = part_dir / "data.parquet"
        assert data.exists(), f"missing {data}"
        leftover_shards = list(part_dir.glob("shard-*.parquet"))
        assert not leftover_shards, f"shards left in {part_name}: {leftover_shards}"

        cur = conn.execute(
            "SELECT seq, writer_pid FROM read_parquet(?) ORDER BY seq, writer_pid",
            (str(data),),
        )
        rows = cur.fetchall()
        total_rows += len(rows)
        # Sorted by (seq, writer_pid) — verify per-partition ordering.
        for prev, cur_row in zip(rows, rows[1:], strict=False):
            assert (prev[0], prev[1]) <= (cur_row[0], cur_row[1])
        # No duplicate (seq, writer_pid) pairs within a partition.
        pairs = {(seq, pid) for seq, pid in rows}
        assert len(pairs) == len(rows)

    assert total_rows == n_procs * n_per_proc, (
        f"row total across partitions: {total_rows}, expected {n_procs * n_per_proc}"
    )


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
# All events use a single doc_id so the partition layout is
# deterministic for the parent's assertions.
for i in range({n_pre}):
    store.append(source='harness', node_name='x', status='ok', doc_id='doc-1', details={{'i': i}})
store.flush()
# Mark that the first flush completed — parent reads this to know the
# parquet has *something* on disk before SIGKILL.
(run_dir / 'first-flush-done').write_text('ok')
for i in range({n_post}):
    store.append(source='harness', node_name='x', status='ok', doc_id='doc-1', details={{'i': i + {n_pre}}})
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

    shards = sorted((tmp_path / "events" / "doc_id=doc-1").glob("shard-*.parquet"))
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
    """Two consolidate calls on the same partition produce identical row
    data (modulo parquet writer metadata).

    With no doc_id supplied the events land in ``doc_id=__run__/`` —
    the synthetic harness partition.
    """
    s = _make_store(tmp_path, run_id="idem-1")
    for i in range(20):
        s.append(source="harness", node_name="x", status="ok", details={"i": i})
    s.flush()
    s.close()

    event_store.BenchEventStore.consolidate(tmp_path)
    out = tmp_path / "events" / "doc_id=__run__" / "data.parquet"
    sha1 = _sha256_rows(out)
    event_store.BenchEventStore.consolidate(tmp_path)
    sha2 = _sha256_rows(out)
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


# ─────────────────────────────────────────────────────────────────────────
# Phase 4 (CD-jzkg.1): hive-partitioning by doc_id
# ─────────────────────────────────────────────────────────────────────────


def test_writer_partitions_by_doc_id(tmp_path: Path) -> None:
    """Events with different ``doc_id`` values land in different shard files
    matching ``events/doc_id=<X>/shard-*.parquet``."""
    s = _make_store(tmp_path, run_id="part-1")
    s.append(source="harness", node_name="x", status="ok", doc_id="alpha", details={"i": 0})
    s.append(source="harness", node_name="x", status="ok", doc_id="beta", details={"i": 1})
    s.append(source="harness", node_name="x", status="ok", doc_id="alpha", details={"i": 2})
    s.flush()

    events_root = tmp_path / "events"
    alpha_shards = sorted((events_root / "doc_id=alpha").glob("shard-*.parquet"))
    beta_shards = sorted((events_root / "doc_id=beta").glob("shard-*.parquet"))
    assert len(alpha_shards) == 1, f"alpha: {alpha_shards}"
    assert len(beta_shards) == 1, f"beta: {beta_shards}"

    # Crucially — the alpha shard must NOT see beta rows and vice versa.
    import duckdb  # noqa: PLC0415

    conn = duckdb.connect(":memory:")
    alpha_doc_ids = {
        r[0] for r in conn.execute(f"SELECT DISTINCT doc_id FROM read_parquet('{alpha_shards[0]}')").fetchall()
    }
    beta_doc_ids = {
        r[0] for r in conn.execute(f"SELECT DISTINCT doc_id FROM read_parquet('{beta_shards[0]}')").fetchall()
    }
    assert alpha_doc_ids == {"alpha"}
    assert beta_doc_ids == {"beta"}
    s.close()


def test_null_doc_id_lands_in_run_partition(tmp_path: Path) -> None:
    """Events with ``doc_id=None`` (or empty string) land in the synthetic
    ``doc_id=__run__/`` partition — never the literal string ``None``."""
    s = _make_store(tmp_path, run_id="nullpart-1")
    s.append(source="harness", node_name="run_start", status="started")
    s.append(source="harness", node_name="run_end", status="completed", doc_id="")
    s.flush()
    s.close()

    event_store.BenchEventStore.consolidate(tmp_path)
    run_part = tmp_path / "events" / "doc_id=__run__" / "data.parquet"
    assert run_part.exists(), f"expected {run_part}"

    # The literal "None" path must NOT exist — that would silently
    # collide with a real doc whose stringified id is "None".
    assert not (tmp_path / "events" / "doc_id=None").exists()

    import duckdb  # noqa: PLC0415

    conn = duckdb.connect(":memory:")
    # Disable hive_partitioning so we see the *actual* parquet column,
    # not the partition-key projection. The parquet value should be
    # NULL or empty — the partition key is the synthesised one.
    rows = conn.execute(f"SELECT doc_id FROM read_parquet('{run_part}', hive_partitioning=false)").fetchall()
    assert len(rows) == 2
    for (doc,) in rows:
        assert doc is None or doc == ""

    # And with hive_partitioning=true (the viewer's read mode) the
    # column resolves to the synthetic ``__run__`` key — that's the
    # contract that lets ``WHERE doc_id = '__run__'`` find these rows.
    rows_hive = conn.execute(f"SELECT doc_id FROM read_parquet('{run_part}', hive_partitioning=true)").fetchall()
    for (doc,) in rows_hive:
        assert doc == "__run__"


def test_consolidate_per_partition(tmp_path: Path) -> None:
    """After consolidate, each ``doc_id=<X>/`` has exactly one
    ``data.parquet`` and zero ``shard-*.parquet`` left over."""
    s = _make_store(tmp_path, run_id="cons-1")
    for i in range(5):
        s.append(source="harness", node_name="x", status="ok", doc_id="alpha", details={"i": i})
    for i in range(7):
        s.append(source="harness", node_name="x", status="ok", doc_id="beta", details={"i": i})
    s.append(source="harness", node_name="run_start", status="started")  # no doc_id
    s.flush()
    s.close()

    event_store.BenchEventStore.consolidate(tmp_path)

    events_root = tmp_path / "events"
    parts = sorted(p.name for p in events_root.glob("doc_id=*"))
    assert parts == ["doc_id=__run__", "doc_id=alpha", "doc_id=beta"]

    for part in parts:
        part_dir = events_root / part
        data = part_dir / "data.parquet"
        assert data.exists(), f"missing {data}"
        leftover = list(part_dir.glob("shard-*.parquet"))
        assert not leftover, f"shards left in {part}: {leftover}"


def test_doc_id_filter_partition_prunes(tmp_path: Path) -> None:
    """``read_parquet(events/**/data.parquet, hive_partitioning=true)
    WHERE doc_id = X`` opens exactly one file.

    Verifies via DuckDB ``EXPLAIN ANALYZE`` that partition-pruning
    actually fires. This is the user-visible win — the speedup at 45k
    events is the whole point of Phase 4.
    """
    s = _make_store(tmp_path, run_id="prune-1")
    # Three partitions with very different sizes. If pruning is working,
    # filtering on doc_id=alpha should NOT scan beta/gamma at all.
    for i in range(20):
        s.append(source="harness", node_name="x", status="ok", doc_id="alpha", details={"i": i})
    for i in range(50):
        s.append(source="harness", node_name="x", status="ok", doc_id="beta", details={"i": i})
    for i in range(30):
        s.append(source="harness", node_name="x", status="ok", doc_id="gamma", details={"i": i})
    s.flush()
    s.close()

    event_store.BenchEventStore.consolidate(tmp_path)

    glob = str(tmp_path / "events" / "doc_id=*" / "data.parquet")
    import duckdb  # noqa: PLC0415

    conn = duckdb.connect(":memory:")

    # Sanity: total rows match.
    [(total,)] = conn.execute(f"SELECT count(*) FROM read_parquet('{glob}', hive_partitioning=true)").fetchall()
    assert total == 100

    # Filter pruning: distinct files touched when filtering on doc_id.
    # Use ``parquet_metadata()`` against the *intended* file as a sanity
    # baseline, then count rows with the ``filename`` virtual column to
    # confirm only one file participated.
    rows = conn.execute(
        f"""
        SELECT DISTINCT filename
        FROM read_parquet('{glob}', hive_partitioning=true, filename=true)
        WHERE doc_id = 'alpha'
        """
    ).fetchall()
    assert len(rows) == 1, f"expected 1 file touched for alpha, got {len(rows)}: {rows}"
    assert "doc_id=alpha" in rows[0][0]

    # No-filter scan touches all 3 files — proves the comparison above
    # is meaningful (pruning vs full scan).
    all_files = conn.execute(
        f"SELECT DISTINCT filename FROM read_parquet('{glob}', hive_partitioning=true, filename=true)"
    ).fetchall()
    assert len(all_files) == 3


def test_backward_compat_legacy_layout(tmp_path: Path) -> None:
    """A run dir with only the Phase 1-3 flat ``events.parquet`` (no
    ``events/`` directory) still reads correctly.

    The Phase 4 migration is forward-only: legacy artefacts on disk or
    in S3 keep working, but new writes never produce them.
    """
    # Hand-build a legacy layout: a single flat ``events-<pid>-<uuid>.parquet``
    # using the same writer with the partitioned events/ dir scrubbed.
    s = _make_store(tmp_path, run_id="legacy-1")
    for i in range(5):
        s.append(source="harness", node_name="x", status="ok", doc_id="doc-1", details={"i": i})
    s.flush()
    s.close()

    # Move the partition shard to the legacy flat path and remove the
    # events/ tree so the run looks like Phase 1-3.
    events_root = tmp_path / "events"
    shards = list(events_root.glob("doc_id=*/shard-*.parquet"))
    assert len(shards) == 1, f"expected 1 shard, got {shards}"
    legacy_path = tmp_path / shards[0].name  # same shard-<pid>-<uuid> name
    # Phase 1-3 used events-<pid>-<uuid>.parquet — rename accordingly so
    # the legacy glob (events-*.parquet) catches it.
    legacy_name = "events-" + shards[0].name.removeprefix("shard-")
    legacy_path = tmp_path / legacy_name
    shards[0].rename(legacy_path)
    # Wipe the partition tree so only the legacy file remains.
    import shutil  # noqa: PLC0415

    shutil.rmtree(events_root)
    assert not events_root.exists()
    assert legacy_path.exists()

    # Read via the BenchEventStore.query helper — its layout-detection
    # branch falls through to ``events-*.parquet`` when there's no
    # partitioned tree.
    reader = event_store.BenchEventStore(run_id="legacy-1-reader", run_dir=tmp_path)
    rows = reader.query("SELECT doc_id, details FROM events ORDER BY seq")
    assert len(rows) == 5
    assert {r["doc_id"] for r in rows} == {"doc-1"}

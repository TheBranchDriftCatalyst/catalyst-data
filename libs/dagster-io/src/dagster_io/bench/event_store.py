"""DuckDB-backed bench audit log writer (CD-jzkg).

Single source of truth for the harness/exgraph/langgraph/dagster event
stream. Buffered in-process DuckDB appends to a per-(pid, doc_id)
Parquet shard at
``<run_dir>/events/doc_id=<doc_id>/shard-<pid>-<uuid>.parquet``;
shards consolidate at run-end into per-partition
``<run_dir>/events/doc_id=<doc_id>/data.parquet`` files and archive
to ``s3://<bucket>/bench/runs/<run_id>/events/doc_id=<doc_id>/data.parquet``.

Hive partitioning (Phase 4): events are partitioned by ``doc_id`` —
the dominant filter axis on the viewer (StateInspector, AuditViewer,
ChunkRail all scope to a single doc). DuckDB's
``read_parquet(..., hive_partitioning=true)`` partition-prunes when
the query filters by ``doc_id``, so ``/events?doc_id=X`` opens
exactly one file regardless of total run size. Events with no
``doc_id`` (harness-level run_start/run_end and similar) land in
the synthetic ``doc_id=__run__`` partition — never a literal
``"None"`` path component.

Concurrency model: per-(pid, doc_id) Parquet shards, consolidated at
run-end. Multiple writer processes producing the same ``doc_id`` is
allowed (consolidation merges them). No cross-process locks;
crash-safe; the viewer reads via DuckDB ``read_parquet`` against the
current shard set during a live run, and the consolidated partitioned
parquet after.

Buffering ceiling: ≤512 events or ≤1.0 s wall-clock — whichever
fires first. A daemon timer thread enforces the time bound so
low-rate runs don't go silent for minutes.

Module-global accessor (configure / append / is_configured /
emit_chunk_text / emit_chunk_extracted / configure_from_env) is the
single funnel every emit site hits. Phase 3 of CD-jzkg removed the
former ``event_tail`` jsonl path; this module is now the canonical
writer.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dagster_io.bench.store import S3BenchmarkStore, S3RunStore


# Buffering ceilings — keep the live shard fresh for the viewer's 3 s poll.
_BUFFER_MAX_EVENTS = 512
_BUFFER_MAX_SECONDS = 1.0

# Synthetic partition for events with no doc_id (harness-level run_start /
# run_end / similar). The literal string "None" is intentionally NOT used
# as a path component — that would silently collide with a doc whose
# stringified id is "None" and break partition-pruned reads.
_RUN_PARTITION = "__run__"

# DuckDB column order matches the schema in §1 of docs/plans/duckdb-audit-log.md.
# Kept here as the single source of truth for both the in-memory table and the
# parquet writer.
_COLUMNS: tuple[str, ...] = (
    "ts",
    "run_id",
    "seq",
    "writer_pid",
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
    "state",
    "details",
)


def _safe_partition_key(doc_id: str | None) -> str:
    """Map a raw ``doc_id`` to its on-disk partition key.

    ``None`` / empty string → ``__run__`` (harness-level events).
    Real doc_ids are returned verbatim. We deliberately do NOT
    sanitise illegal path chars here — doc_ids are produced by the
    pipeline, not user input, and the rest of the codebase already
    treats them as path-safe (S3 keys, filesystem caches). If a
    pathological doc_id slips through, fail loud at write time
    rather than silently mangling the partition.
    """
    if doc_id is None or doc_id == "":
        return _RUN_PARTITION
    return doc_id


def _partition_dir(run_dir: Path, doc_id: str | None) -> Path:
    return run_dir / "events" / f"doc_id={_safe_partition_key(doc_id)}"


class BenchEventStore:
    """Per-process buffered DuckDB writer that emits hive-partitioned Parquet shards.

    One instance per process. Module-global wiring lives at the bottom
    of this file (``configure``/``append``/``flush``) so emit sites
    don't need to thread an instance through every call site.

    Phase 4 (CD-jzkg.1): events are partitioned by ``doc_id`` on disk
    under ``<run_dir>/events/doc_id=<doc_id>/shard-<pid>-<uuid>.parquet``.
    Multiple doc_ids in the same process get separate shards; events
    without a doc_id land in ``doc_id=__run__/`` (never the literal
    string ``None``).

    Thread-safety: ``append`` and ``flush`` hold a single buffer lock
    so multiple threads inside one process can safely emit. Across
    processes, each writer owns its own shard file (the ``<pid>-<uuid>``
    suffix prevents path collisions even if the same pid recycles, and
    multiple processes producing the same doc_id is allowed —
    consolidation merges them).
    """

    def __init__(
        self,
        *,
        run_id: str,
        run_dir: Path,
        writer_pid: int | None = None,
    ) -> None:
        # Lazy duckdb import keeps this module importable in environments
        # that don't have duckdb yet (e.g. IDE indexers, stale CI pyenvs).
        # The first ``append`` fails loud if duckdb isn't installed.
        self._duckdb_module: Any = None
        self._conn: Any = None
        self._closed = False

        self.run_id = run_id
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.writer_pid = writer_pid if writer_pid is not None else os.getpid()
        # Unique-per-instance suffix — survives pid recycling and lets a
        # single pid spawn multiple writers (tests do this).
        self._shard_uuid = uuid.uuid4().hex[:8]
        # Partition root + per-(pid, doc_id) shard suffix. Concrete shard
        # paths are built lazily in ``_shard_path_for`` because the
        # set of doc_ids isn't known until events flow.
        self.events_root: Path = self.run_dir / "events"

        # Monotonic per-(run_id, writer_pid) sequence — the canonical
        # ordering key when shards merge. Shared across all partitions
        # for this writer so merges across docs preserve write order.
        self._seq = 0

        # Per-doc_id buffer. Key is the safe partition key (so None/""
        # collapse into ``__run__``).
        self._buffers: dict[str, list[tuple[Any, ...]]] = {}
        self._lock: Lock = Lock()

        # Timer thread that enforces the wall-clock flush ceiling. Daemon
        # so process exit doesn't hang on it; explicit ``close()``
        # joins for clean shutdown.
        self._timer_stop = threading.Event()
        self._timer_thread: threading.Thread | None = None

    # ── shard path helpers ──────────────────────────────────────────────

    def _shard_path_for(self, partition_key: str) -> Path:
        """Resolve the shard parquet path for a given safe partition key.

        Mkdirs the partition directory lazily so we don't create
        ``doc_id=`` dirs until at least one event lands there.
        """
        part_dir = self.events_root / f"doc_id={partition_key}"
        part_dir.mkdir(parents=True, exist_ok=True)
        return part_dir / f"shard-{self.writer_pid}-{self._shard_uuid}.parquet"

    # ── duckdb lazy init ────────────────────────────────────────────────

    def _ensure_conn(self) -> Any:
        if self._conn is not None:
            return self._conn
        if self._duckdb_module is None:
            try:
                import duckdb  # noqa: PLC0415 — lazy import on hot path
            except ImportError as exc:  # pragma: no cover — install issue
                raise RuntimeError(
                    "BenchEventStore requires the 'duckdb' package. Install via "
                    "`pip install duckdb` or add it to the dagster-io optional "
                    "deps and re-sync."
                ) from exc
            self._duckdb_module = duckdb
        # In-memory DuckDB — we never persist via duckdb's own format. The
        # parquet shard is the on-disk artefact.
        self._conn = self._duckdb_module.connect(":memory:")
        return self._conn

    def _ensure_timer(self) -> None:
        if self._timer_thread is not None:
            return

        def _loop() -> None:
            while not self._timer_stop.wait(_BUFFER_MAX_SECONDS):
                try:
                    self.flush()
                except Exception as e:  # noqa: BLE001 — never let the timer kill the run
                    print(f"BenchEventStore timer flush: {e}", file=sys.stderr)

        t = threading.Thread(target=_loop, daemon=True, name="bench-event-store-flush")
        t.start()
        self._timer_thread = t

    # ── public API ──────────────────────────────────────────────────────

    def append(
        self,
        *,
        source: str,
        node_name: str,
        status: str,
        model: str | None = None,
        doc_id: str | None = None,
        chunk_idx: int | None = None,
        chunk_id: str | None = None,
        retry_count: int | None = None,
        code_location: str | None = None,
        evidence_window_id: str | None = None,
        state: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
        ts: datetime | None = None,
    ) -> None:
        """Append a single event to the buffer; flush on size ceiling.

        Required fields (``source``, ``node_name``, ``status``) plus the
        optional context fields define the unified event shape that the
        viewer's LiveGantt, AuditViewer, and StateInspector all consume.
        ``ts`` is optional — production callers leave it None and we
        stamp ``datetime.now(UTC)``; tests pass an explicit timestamp
        when verifying ordering.
        """
        # Ensure backing structures exist before any state mutation so a
        # duckdb-import failure surfaces here, not after we've split the
        # buffer.
        self._ensure_conn()
        self._ensure_timer()

        if ts is None:
            ts = datetime.now(UTC)

        # JSON columns are written as VARCHAR strings — DuckDB happily
        # casts them to JSON in queries via ``::JSON`` and the parquet
        # round-trips fine. Explicitly serialising here keeps the buffer
        # row plain (no nested dict pyarrow conversion gotchas).
        import json  # noqa: PLC0415 — stdlib, hot path is fine

        state_json = json.dumps(state or {}, default=str)
        details_json = json.dumps(details or {}, default=str)

        partition_key = _safe_partition_key(doc_id)
        with self._lock:
            self._seq += 1
            row = (
                ts,
                self.run_id,
                self._seq,
                self.writer_pid,
                source,
                node_name,
                status,
                model,
                doc_id,
                chunk_idx,
                chunk_id,
                retry_count,
                code_location,
                evidence_window_id,
                state_json,
                details_json,
            )
            buf = self._buffers.setdefault(partition_key, [])
            buf.append(row)
            # Flush ceiling is per-partition: once any single doc_id's
            # buffer crosses the limit we drain everything. This keeps
            # the buffer-size invariant tight without forcing
            # cross-partition coordination on every append.
            should_flush = len(buf) >= _BUFFER_MAX_EVENTS

        if should_flush:
            self.flush()

    def flush(self) -> None:
        """Drain every per-partition buffer to its parquet shard.

        Each non-empty ``doc_id`` partition writes to its own
        ``events/doc_id=<part>/shard-<pid>-<uuid>.parquet``.
        Append-mode parquet is implemented by reading the existing shard
        (if any), unioning with the buffer, and rewriting — DuckDB
        doesn't support incremental parquet appends. The per-shard size
        is bounded by what one writer process emits for one doc_id, so
        this stays cheap.
        """
        with self._lock:
            if not self._buffers:
                return
            # Snapshot + clear under the lock. Empty buckets are dropped
            # so a partition that never receives more events stops
            # appearing in flush iterations.
            partitions: dict[str, list[tuple[Any, ...]]] = {k: v for k, v in self._buffers.items() if v}
            self._buffers.clear()

        if not partitions:
            return

        conn = self._ensure_conn()
        col_list = ", ".join(_COLUMNS)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        # ``ts`` is TIMESTAMPTZ so the round-trip through parquet
        # preserves the UTC offset (plain TIMESTAMP is wall-local on
        # read, which would silently shift timestamps for downstream
        # consumers).
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE _events_buf ("
            "ts TIMESTAMPTZ, run_id VARCHAR, seq BIGINT, writer_pid INTEGER, "
            "source VARCHAR, node_name VARCHAR, status VARCHAR, model VARCHAR, "
            "doc_id VARCHAR, chunk_idx INTEGER, chunk_id VARCHAR, retry_count INTEGER, "
            "code_location VARCHAR, evidence_window_id VARCHAR, state VARCHAR, details VARCHAR"
            ")"
        )

        for partition_key, rows in partitions.items():
            shard_path = self._shard_path_for(partition_key)
            # Reset staging table contents per partition.
            conn.execute("DELETE FROM _events_buf")
            conn.executemany(
                f"INSERT INTO _events_buf ({col_list}) VALUES ({placeholders})",
                rows,
            )

            # DuckDB's COPY ... TO and read_parquet(...) do not support
            # the "?" parameter binding we use for INSERT; they want a
            # literal. The shard path is constructed from sanitised
            # pid+uuid + safe partition key, never user input directly,
            # so embedding it in the SQL is safe with quote escaping.
            shard_lit = "'" + str(shard_path).replace("'", "''") + "'"
            if shard_path.exists():
                # Write to a tmp path then atomic-rename — avoids the
                # "read and write the same parquet" undefined behaviour
                # DuckDB tripped on under in-memory caching.
                tmp_path = shard_path.with_suffix(shard_path.suffix + ".tmp")
                tmp_lit = "'" + str(tmp_path).replace("'", "''") + "'"
                # ``hive_partitioning=false`` keeps the parquet's native
                # ``doc_id`` column authoritative; auto-detection would
                # overwrite it with the partition path key.
                conn.execute(
                    f"COPY ("
                    f"  SELECT {col_list} FROM read_parquet({shard_lit}, hive_partitioning=false) "
                    f"  UNION ALL SELECT {col_list} FROM _events_buf "
                    f"  ORDER BY seq"
                    f") TO {tmp_lit} (FORMAT PARQUET)"
                )
                os.replace(tmp_path, shard_path)
            else:
                conn.execute(f"COPY (SELECT {col_list} FROM _events_buf ORDER BY seq) TO {shard_lit} (FORMAT PARQUET)")

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        """Run a read query against the current shard set.

        The view ``events`` binds to whichever layout is on disk:

        - Phase 4 partitioned writes: ``events/**/shard-*.parquet`` plus
          any consolidated ``events/**/data.parquet`` already produced.
        - Legacy (Phase 1-3) flat layout: ``events-*.parquet`` directly
          under ``run_dir`` — read for backward-compat when a test or
          older run hasn't migrated.

        Whichever path resolves to at least one file wins; if both
        coexist (mid-migration in tests) we prefer the partitioned
        layout because that's where new writes land. Callers write
        ``SELECT ... FROM events WHERE ...`` without knowing about the
        shard layout.
        """
        conn = self._ensure_conn()
        partitioned = list(self.events_root.glob("doc_id=*/*.parquet"))
        legacy = list(self.run_dir.glob("events-*.parquet"))
        if partitioned:
            glob = str(self.events_root / "doc_id=*/*.parquet")
            hive = "true"
        elif legacy:
            glob = str(self.run_dir / "events-*.parquet")
            hive = "false"
        else:
            # No data on disk at all — bind an empty view so SELECT
            # COUNT(*) FROM events returns 0 rather than failing on a
            # missing-glob error.
            conn.execute(
                "CREATE OR REPLACE TEMP VIEW events AS "
                "SELECT NULL::TIMESTAMPTZ AS ts, NULL::VARCHAR AS run_id, NULL::BIGINT AS seq, "
                "NULL::INTEGER AS writer_pid, NULL::VARCHAR AS source, NULL::VARCHAR AS node_name, "
                "NULL::VARCHAR AS status, NULL::VARCHAR AS model, NULL::VARCHAR AS doc_id, "
                "NULL::INTEGER AS chunk_idx, NULL::VARCHAR AS chunk_id, NULL::INTEGER AS retry_count, "
                "NULL::VARCHAR AS code_location, NULL::VARCHAR AS evidence_window_id, "
                "NULL::VARCHAR AS state, NULL::VARCHAR AS details "
                "WHERE FALSE"
            )
            cur = conn.execute(sql, tuple(params))
            cols = [d[0] for d in (cur.description or [])]
            return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

        # read_parquet doesn't accept prepared parameters — embed the
        # glob as a literal. Path is from a Path object we constructed,
        # never user-supplied, so quote-escaping is sufficient defence.
        glob_lit = "'" + glob.replace("'", "''") + "'"
        # union_by_name=true tolerates schema evolution between shards
        # written at different points in the run (see §9 of the plan).
        # hive_partitioning=true projects the doc_id partition column
        # so it's queryable; with false (legacy layout) the parquet
        # column is the source of truth.
        conn.execute(
            f"CREATE OR REPLACE TEMP VIEW events AS "
            f"SELECT * FROM read_parquet({glob_lit}, union_by_name=true, hive_partitioning={hive})"
        )
        cur = conn.execute(sql, tuple(params))
        cols = [d[0] for d in (cur.description or [])]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]

    def close(self) -> None:
        """Final flush + tear down the timer thread.

        Idempotent. Called by ``consolidate_and_archive`` and by the
        module-level ``flush()`` helper at run end.
        """
        if self._closed:
            return
        self._closed = True
        self._timer_stop.set()
        if self._timer_thread is not None:
            self._timer_thread.join(timeout=2.0)
            self._timer_thread = None
        # Final flush AFTER the timer is stopped so we don't race with it.
        try:
            self.flush()
        except Exception as e:  # noqa: BLE001
            print(f"BenchEventStore close flush: {e}", file=sys.stderr)
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None

    # ── classmethods: consolidate + archive ────────────────────────────

    @classmethod
    def consolidate(cls, run_dir: Path) -> Path:
        """Merge per-partition shards under ``run_dir/events/`` into a single
        ``data.parquet`` per ``doc_id`` partition.

        For each ``events/doc_id=<doc>/`` directory found, reads every
        ``shard-*.parquet`` and writes a consolidated
        ``events/doc_id=<doc>/data.parquet`` sorted by
        ``(seq, writer_pid)``. The shards are then removed so subsequent
        consolidate passes don't re-process them.

        Returns the ``run_dir`` path. Idempotent — running twice on a
        partition set with no shards is a no-op.

        For backward-compat with the Phase 1-3 flat layout, when no
        partitioned ``events/`` directory exists we leave any existing
        flat ``events-*.parquet`` shards alone — the old read path
        still picks them up and a forward-only migration must not
        rewrite legacy archives.
        """
        run_dir = Path(run_dir)
        events_root = run_dir / "events"
        if not events_root.is_dir():
            # No partitioned data. Either this is a legacy run (caller
            # handles those via the old read path) or no events were
            # ever emitted. Nothing to consolidate.
            return run_dir

        partitions = sorted(p for p in events_root.glob("doc_id=*") if p.is_dir())
        # Drop partitions that have nothing to consolidate (either empty
        # or already consolidated with no shards left).
        work: list[tuple[Path, list[Path]]] = []
        for part in partitions:
            shards = sorted(part.glob("shard-*.parquet"))
            if shards:
                work.append((part, shards))
        if not work:
            return run_dir

        try:
            import duckdb  # noqa: PLC0415 — lazy import keeps module-load light
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("BenchEventStore.consolidate requires the 'duckdb' package.") from exc

        conn = duckdb.connect(":memory:")
        try:
            col_list = ", ".join(_COLUMNS)
            for part_dir, shards in work:
                out = part_dir / "data.parquet"
                # ``data.parquet`` may exist from a prior consolidate;
                # include it in the read so a re-run after additional
                # shards lands them all into a single output.
                inputs: list[Path] = list(shards)
                if out.exists():
                    inputs.append(out)
                shard_lits = ", ".join("'" + str(s).replace("'", "''") + "'" for s in inputs)
                # Write to a tmp in the same partition dir then atomic
                # rename so a crash during COPY can't leave a torn
                # data.parquet that the next read would treat as
                # consolidated.
                tmp = part_dir / "data.parquet.tmp"
                tmp_lit = "'" + str(tmp).replace("'", "''") + "'"
                # ``hive_partitioning=false`` is critical: shard files
                # live under ``doc_id=<part>/`` paths, and DuckDB would
                # otherwise auto-detect that and overwrite the parquet's
                # native ``doc_id`` column with the partition key (so a
                # NULL doc_id event would resurface as ``__run__``).
                # We want the column-as-stored to round-trip; the
                # partition key is for read-time pruning, not authority.
                conn.execute(
                    f"COPY ("
                    f"  SELECT {col_list} FROM read_parquet([{shard_lits}], "
                    f"  union_by_name=true, hive_partitioning=false) "
                    f"  ORDER BY seq, writer_pid"
                    f") TO {tmp_lit} (FORMAT PARQUET)"
                )
                os.replace(tmp, out)
                # Drop the per-pid shards now that data.parquet is the
                # canonical artefact for this partition.
                for s in shards:
                    with contextlib.suppress(FileNotFoundError):
                        s.unlink()
        finally:
            conn.close()
        return run_dir

    @classmethod
    def archive_to_s3(
        cls,
        run_dir: Path,
        store: S3BenchmarkStore,
        run: S3RunStore,
    ) -> str | None:
        """Upload every consolidated partition's ``data.parquet`` to S3.

        Mirrors the local layout: each
        ``<run_dir>/events/doc_id=<doc>/data.parquet`` uploads to
        ``s3://<bucket>/bench/runs/<run_id>/events/doc_id=<doc>/data.parquet``.

        Returns the events prefix (e.g.
        ``bench/runs/<run_id>/events/``) when at least one partition
        was uploaded, or ``None`` if no partitioned data was found
        locally — matching the prior contract that returned ``None``
        for runs with no events.
        """
        events_root = Path(run_dir) / "events"
        if not events_root.is_dir():
            return None
        uploaded = 0
        for part_dir in sorted(events_root.glob("doc_id=*")):
            data = part_dir / "data.parquet"
            if not data.exists():
                continue
            # Build the S3 key by mirroring the partition directory name
            # one-for-one. ``run.events_parquet_prefix`` ends with a
            # slash so concatenation lands the partition under it.
            key = f"{run.events_parquet_prefix}{part_dir.name}/data.parquet"
            store.client.put_object_file(key, str(data))
            uploaded += 1
        if uploaded == 0:
            return None
        return run.events_parquet_prefix

    @classmethod
    def consolidate_and_archive(
        cls,
        run: S3RunStore,
        *,
        run_dir: Path,
    ) -> tuple[Path, str | None]:
        """Convenience wrapper: ``consolidate`` then ``archive_to_s3``.

        Returns ``(run_dir, events_prefix_or_None)`` — the events prefix
        replaces the old single-file key now that the archive is a
        directory of partitions.
        """
        out = cls.consolidate(run_dir)
        key = cls.archive_to_s3(run_dir, run._store, run)  # noqa: SLF001 — same package
        return out, key


# ─────────────────────────────────────────────────────────────────────────
# Module-global accessor — emit sites use the configure/append funnel
# rather than threading an instance through every call site.
# ─────────────────────────────────────────────────────────────────────────

_store: BenchEventStore | None = None
_module_lock = Lock()

# Per-process idempotency set for ``emit_chunk_text`` — the same
# ``chunk_id`` should only emit one ``chunk_loaded`` event per run, even
# if multiple stages encounter it.
_seen_chunks: set[str] = set()


def configure(*, run_id: str, run_dir: Path | str) -> BenchEventStore:
    """Bind the module-global writer to a run. Idempotent for the same
    ``(run_id, run_dir)``; raises if reconfigured to a different target
    mid-run.

    Returns the store instance so callers that want a typed handle can
    grab one without re-importing the class.
    """
    global _store
    new_dir = Path(run_dir)
    with _module_lock:
        if _store is not None:
            if _store.run_id != run_id or _store.run_dir != new_dir:
                raise RuntimeError(
                    f"event_store already configured for run_id={_store.run_id!r} "
                    f"at {_store.run_dir}; refusing to retarget to "
                    f"run_id={run_id!r} at {new_dir}"
                )
            return _store
        _store = BenchEventStore(run_id=run_id, run_dir=new_dir)
        return _store


def is_configured() -> bool:
    return _store is not None


def current_run_id() -> str | None:
    return _store.run_id if _store is not None else None


def append(**kwargs: Any) -> None:
    """Forward to the module-global store, no-op if unconfigured.

    Emit sites call this unconditionally; if the harness hasn't run
    ``configure(...)`` (e.g. a unit test that imports a node without a
    bench fixture), we silently drop the event rather than fail loud.
    """
    if _store is None:
        return
    _store.append(**kwargs)


def emit_chunk_text(
    chunk_id: str,
    text: str,
    *,
    doc_id: str | None = None,
    model: str | None = None,
    domain: str | None = None,
    speaker_label: str | None = None,
    temporal_start_ms: float | None = None,
    temporal_end_ms: float | None = None,
    chunk_index: int | None = None,
    total_chunks: int | None = None,
    chunk_metadata: dict[str, Any] | None = None,
    max_chars: int = 4096,
) -> None:
    """Emit a one-shot ``chunk_loaded`` event the first time a chunk_id
    is seen in this process. Idempotent — subsequent calls for the same
    chunk_id are no-ops, so repair retries don't re-emit. The text is
    capped at ``max_chars`` (default 4 KiB) and a ``truncated`` flag is
    set when the source was longer; the StateInspector uses the inline
    text directly without a side fetch.

    ``chunk_metadata`` carries the chunker's strategy + size/overlap /
    char-offset / content-hash so the StateInspector right-pane can
    surface "why is this chunk shaped this way" without re-reading the
    silver layer. Index + total flow through separately because they're
    promoted onto the TextChunk model itself, not the metadata bag.
    """
    if not chunk_id or chunk_id in _seen_chunks:
        return
    _seen_chunks.add(chunk_id)
    truncated = len(text) > max_chars
    append(
        source="harness",
        node_name="chunk_loaded",
        status="info",
        model=model,
        doc_id=doc_id,
        chunk_id=chunk_id,
        details={
            "text": text[:max_chars],
            "char_count": len(text),
            "truncated": truncated,
            "domain": domain,
            "speaker_label": speaker_label,
            "temporal_start_ms": temporal_start_ms,
            "temporal_end_ms": temporal_end_ms,
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "chunk_metadata": chunk_metadata or {},
        },
    )


def emit_chunk_extracted(
    chunk_id: str,
    *,
    model: str | None = None,
    doc_id: str | None = None,
    mentions: list[dict[str, Any]] | None = None,
    propositions: list[dict[str, Any]] | None = None,
) -> None:
    """Emit a terminal ``chunk_extracted`` event with the final NER +
    SPO output for a (model, chunk) pair. Lets the StateInspector tie
    the chunk text directly to what each model produced without
    reconstructing it from intermediate validate/repair events.
    """
    append(
        source="harness",
        node_name="chunk_extracted",
        status="completed",
        model=model,
        doc_id=doc_id,
        chunk_id=chunk_id,
        details={
            "mentions": mentions or [],
            "propositions": propositions or [],
            "mention_count": len(mentions) if mentions else 0,
            "proposition_count": len(propositions) if propositions else 0,
        },
    )


def flush() -> None:
    """Force a buffer flush. No-op if unconfigured."""
    if _store is None:
        return
    _store.flush()


def close() -> None:
    """Close the module-global store. Used by the harness at run-end
    after consolidation; tests use it to release file handles between
    cases."""
    global _store
    _seen_chunks.clear()
    if _store is None:
        return
    _store.close()
    _store = None


def read_events_for_test() -> list[dict[str, Any]]:
    """Drain the module-global store's buffered + flushed events.

    Test-only helper — reads back every row the current process has
    emitted to the active shard, deserialising ``state``/``details``
    JSON columns to dicts so assertions can compare against the input
    dict shape callers passed into ``append``. Mirrors the shape the
    viewer's ``/events`` endpoint produces (see ``_row_to_event_dict``
    in ``viewer/routes/bench.py``).

    Raises ``RuntimeError`` if no module-global store is configured —
    use ``configure(...)`` in a test fixture before calling.
    """
    if _store is None:
        raise RuntimeError("event_store.read_events_for_test() requires configure() first")
    # Force a flush so the shard reflects every appended row, then
    # query the current shard back through the same ``read_parquet``
    # path the viewer uses. ``ORDER BY seq, writer_pid`` matches the
    # consolidate(...) sort.
    _store.flush()
    rows = _store.query("SELECT * FROM events ORDER BY seq, writer_pid")
    import json as _json  # noqa: PLC0415

    out: list[dict[str, Any]] = []
    for row in rows:
        rec = dict(row)
        ts = rec.get("ts")
        if ts is not None and not isinstance(ts, str):
            rec["ts"] = ts.isoformat()
        for k in ("state", "details"):
            v = rec.get(k)
            if isinstance(v, str):
                try:
                    rec[k] = _json.loads(v) if v else {}
                except _json.JSONDecodeError:
                    rec[k] = {}
            elif v is None:
                rec[k] = {}
        out.append(rec)
    return out


def consolidate_and_archive(run: S3RunStore, *, run_dir: Path | str) -> tuple[Path, str | None]:
    """Convenience for the harness: flush+close the global writer, then
    consolidate shards and PUT the canonical parquet to S3."""
    flush()
    close()
    return BenchEventStore.consolidate_and_archive(run, run_dir=Path(run_dir))


def configure_from_env() -> None:
    """Subprocess entrypoint for harness fan-out.

    Reads ``CATALYST_RUN_DIR`` and ``CATALYST_RUN_ID``. No-op if either
    is unset, so direct script invocations outside a configured run
    don't bind the writer.
    """
    run_dir = os.environ.get("CATALYST_RUN_DIR")
    run_id = os.environ.get("CATALYST_RUN_ID")
    if run_dir and run_id and not is_configured():
        configure(run_id=run_id, run_dir=Path(run_dir))


# Fork-safety: a forked child inheriting the parent's buffer would
# double-write. Reset the module global in the child so it must
# re-configure (or fall back to no-op).
def _reset_after_fork() -> None:
    global _store
    _store = None
    _seen_chunks.clear()


with contextlib.suppress(AttributeError, RuntimeError):  # pragma: no cover — non-POSIX or restricted env
    os.register_at_fork(after_in_child=_reset_after_fork)


# Tiny diagnostic for tests / harness logs.
def _stats() -> dict[str, Any]:
    if _store is None:
        return {"configured": False}
    buffered = sum(len(buf) for buf in _store._buffers.values())  # noqa: SLF001 — module-internal
    return {
        "configured": True,
        "run_id": _store.run_id,
        "writer_pid": _store.writer_pid,
        "events_root": str(_store.events_root),
        "buffered": buffered,
        "partitions_buffered": len(_store._buffers),  # noqa: SLF001
        "seq": _store._seq,  # noqa: SLF001
        "ts": time.time(),
    }


__all__ = [
    "BenchEventStore",
    "append",
    "close",
    "configure",
    "configure_from_env",
    "consolidate_and_archive",
    "current_run_id",
    "emit_chunk_extracted",
    "emit_chunk_text",
    "flush",
    "is_configured",
    "read_events_for_test",
]

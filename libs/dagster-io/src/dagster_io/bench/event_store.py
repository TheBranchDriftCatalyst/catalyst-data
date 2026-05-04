"""DuckDB-backed bench audit log writer (CD-jzkg, Phase 1).

Strangler-fig replacement for ``event_tail``'s JSONL writer. Buffered
in-process DuckDB appends to a per-process Parquet shard
(``<run_dir>/events-<pid>-<uuid>.parquet``); shards consolidate at
run-end into ``events.parquet`` and archive to
``s3://<bucket>/bench/runs/<run_id>/events.parquet``.

Concurrency model (per the plan §2): per-process Parquet shards,
consolidated at run-end. No cross-process locks; crash-safe; the
viewer reads via DuckDB ``read_parquet`` against the current shard
set during a live run, and the consolidated parquet after.

Buffering ceiling: ≤512 events or ≤1.0 s wall-clock — whichever
fires first. A daemon timer thread enforces the time bound so
low-rate runs don't go silent for minutes.

Module-global accessor mirrors ``event_tail.configure(...)`` /
``is_configured()`` so emit-site changes are one import line + one
call.

Phase 1 scope: dual-write only. Reader integration (Phase 2) and
``event_tail`` removal (Phase 3) follow.
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


class BenchEventStore:
    """Per-process buffered DuckDB writer that emits Parquet shards.

    One instance per process. Module-global wiring lives at the bottom
    of this file (``configure``/``append``/``flush``) so emit sites
    don't need to thread an instance through every call site — same
    pattern as ``event_tail``.

    Thread-safety: ``append`` and ``flush`` hold a single buffer lock
    so multiple threads inside one process can safely emit. Across
    processes, each writer owns its own shard file (the ``<pid>-<uuid>``
    suffix prevents path collisions even if the same pid recycles).
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
        self.shard_path: Path = self.run_dir / f"events-{self.writer_pid}-{self._shard_uuid}.parquet"

        # Monotonic per-(run_id, writer_pid) sequence — the canonical
        # ordering key when shards merge.
        self._seq = 0

        self._buffer: list[tuple[Any, ...]] = []
        self._lock: Lock = Lock()

        # Timer thread that enforces the wall-clock flush ceiling. Daemon
        # so process exit doesn't hang on it; explicit ``close()``
        # joins for clean shutdown.
        self._timer_stop = threading.Event()
        self._timer_thread: threading.Thread | None = None

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

        Same kwargs as ``event_tail.append`` so dual-write at emit sites
        is mechanical. ``ts`` is optional — passed in by the parity test
        when reconciling jsonl rows; production callers leave it None
        and we stamp ``datetime.now(UTC)``.
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
            self._buffer.append(row)
            should_flush = len(self._buffer) >= _BUFFER_MAX_EVENTS

        if should_flush:
            self.flush()

    def flush(self) -> None:
        """Drain the buffer to the parquet shard.

        Append-mode parquet is implemented by reading the existing shard
        (if any), unioning with the buffer, and rewriting — DuckDB
        doesn't support incremental parquet appends. The shard is
        bounded (one process worth of events for one run), so this is
        cheap; the alternative is keeping a duckdb table on-disk, which
        would lock the file and break crash-recovery via ``read_parquet``.
        """
        with self._lock:
            if not self._buffer:
                return
            rows = list(self._buffer)
            self._buffer.clear()

        conn = self._ensure_conn()
        # Drop & rebuild a staging table from the buffer rows. Using
        # parameterised INSERTs over executemany keeps us safely away
        # from any SQL-string formatting on user data.
        col_list = ", ".join(_COLUMNS)
        placeholders = ", ".join("?" for _ in _COLUMNS)
        # ``ts`` is TIMESTAMPTZ so the round-trip through parquet
        # preserves the UTC offset event_tail.append stamps. Plain
        # TIMESTAMP is wall-local on read which would diverge from the
        # jsonl iso-8601 string under the parity test (CD-jzkg §7.3).
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE _events_buf ("
            "ts TIMESTAMPTZ, run_id VARCHAR, seq BIGINT, writer_pid INTEGER, "
            "source VARCHAR, node_name VARCHAR, status VARCHAR, model VARCHAR, "
            "doc_id VARCHAR, chunk_idx INTEGER, chunk_id VARCHAR, retry_count INTEGER, "
            "code_location VARCHAR, evidence_window_id VARCHAR, state VARCHAR, details VARCHAR"
            ")"
        )
        conn.executemany(
            f"INSERT INTO _events_buf ({col_list}) VALUES ({placeholders})",
            rows,
        )

        # DuckDB's COPY ... TO and read_parquet(...) do not support the
        # "?" parameter binding we use for INSERT; they want a literal.
        # The shard path is constructed from sanitised pid+uuid, never
        # user input, so embedding it in the SQL is safe. Use single
        # quotes and escape any embedded single quotes defensively.
        shard_lit = "'" + str(self.shard_path).replace("'", "''") + "'"
        if self.shard_path.exists():
            # Write to a tmp path then atomic-rename — avoids the
            # "read and write the same parquet" undefined behaviour
            # DuckDB tripped on under in-memory caching.
            tmp_path = self.shard_path.with_suffix(self.shard_path.suffix + ".tmp")
            tmp_lit = "'" + str(tmp_path).replace("'", "''") + "'"
            conn.execute(
                f"COPY ("
                f"  SELECT {col_list} FROM read_parquet({shard_lit}) "
                f"  UNION ALL SELECT {col_list} FROM _events_buf "
                f"  ORDER BY seq"
                f") TO {tmp_lit} (FORMAT PARQUET)"
            )
            os.replace(tmp_path, self.shard_path)
        else:
            conn.execute(f"COPY (SELECT {col_list} FROM _events_buf ORDER BY seq) TO {shard_lit} (FORMAT PARQUET)")

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        """Run a read query against the current shard set.

        The view ``events`` is auto-bound to ``read_parquet('events-*.parquet')``
        so callers write ``SELECT ... FROM events WHERE ...`` without
        knowing about the shard layout. Returns a list of dicts (column
        name → value).
        """
        conn = self._ensure_conn()
        glob = str(self.run_dir / "events-*.parquet")
        # read_parquet doesn't accept prepared parameters — embed the
        # glob as a literal. Path is from a Path object we constructed,
        # never user-supplied, so quote-escaping is sufficient defence.
        glob_lit = "'" + glob.replace("'", "''") + "'"
        # union_by_name=true tolerates schema evolution between shards
        # written at different points in the run (see §9 of the plan).
        conn.execute(
            f"CREATE OR REPLACE TEMP VIEW events AS SELECT * FROM read_parquet({glob_lit}, union_by_name=true)"
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
        """Merge ``events-*.parquet`` shards under ``run_dir`` into a single
        ``events.parquet`` sorted by ``(seq, writer_pid)``.

        Idempotent — running twice on the same shard set produces a
        bytewise-identical output (modulo parquet metadata; the row data
        is stable). Skips silently if no shards exist (no-op for runs
        that never emitted via the duckdb path).
        """
        run_dir = Path(run_dir)
        out = run_dir / "events.parquet"
        shards = sorted(run_dir.glob("events-*.parquet"))
        # The consolidated file matches the shard glob — exclude it so
        # repeated consolidation doesn't keep re-reading its own output.
        shards = [s for s in shards if s.name != "events.parquet"]
        if not shards:
            return out

        try:
            import duckdb  # noqa: PLC0415 — lazy import keeps module-load light
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("BenchEventStore.consolidate requires the 'duckdb' package.") from exc

        conn = duckdb.connect(":memory:")
        try:
            col_list = ", ".join(_COLUMNS)
            # read_parquet accepts a SQL list literal of file paths;
            # this lets us exclude the consolidated file (if a previous
            # run produced one) by simply not including it. COPY ... TO
            # also takes a literal target, not a prepared parameter.
            shard_lits = ", ".join("'" + str(s).replace("'", "''") + "'" for s in shards)
            out_lit = "'" + str(out).replace("'", "''") + "'"
            conn.execute(
                f"COPY ("
                f"  SELECT {col_list} FROM read_parquet([{shard_lits}], union_by_name=true) "
                f"  ORDER BY seq, writer_pid"
                f") TO {out_lit} (FORMAT PARQUET)"
            )
        finally:
            conn.close()
        return out

    @classmethod
    def archive_to_s3(
        cls,
        run_dir: Path,
        store: S3BenchmarkStore,
        run: S3RunStore,
    ) -> str | None:
        """Upload the consolidated ``events.parquet`` to S3.

        Returns the S3 key (or ``None`` if no parquet exists locally).
        Mirrors ``S3RunStore.archive_events()``'s contract.
        """
        out = run_dir / "events.parquet"
        if not out.exists():
            return None
        key = run.events_parquet_key
        store.client.put_object(key, out.read_bytes())
        return key

    @classmethod
    def consolidate_and_archive(
        cls,
        run: S3RunStore,
        *,
        run_dir: Path,
    ) -> tuple[Path, str | None]:
        """Convenience wrapper: ``consolidate`` then ``archive_to_s3``."""
        out = cls.consolidate(run_dir)
        key = cls.archive_to_s3(run_dir, run._store, run)  # noqa: SLF001 — same package
        return out, key


# ─────────────────────────────────────────────────────────────────────────
# Module-global accessor — mirrors event_tail's shape so emit-site
# refactors are one extra import + one extra call.
# ─────────────────────────────────────────────────────────────────────────

_store: BenchEventStore | None = None
_module_lock = Lock()


def configure(*, run_id: str, run_dir: Path | str) -> BenchEventStore:
    """Bind the module-global writer to a run. Idempotent for the same
    ``(run_id, run_dir)``; raises if reconfigured to a different target
    mid-run (mirrors ``event_tail.configure``).

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

    Phase 1 dual-write is unconditional at every emit site — when the
    harness hasn't run ``configure(...)`` (e.g. a unit test that imports
    a node), we silently drop the event rather than fail loud. This
    matches the strangler-fig contract: the jsonl path is still
    authoritative until Phase 3.
    """
    if _store is None:
        return
    _store.append(**kwargs)


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
    if _store is None:
        return
    _store.close()
    _store = None


def consolidate_and_archive(run: S3RunStore, *, run_dir: Path | str) -> tuple[Path, str | None]:
    """Convenience for the harness: flush+close the global writer, then
    consolidate shards and PUT the canonical parquet to S3."""
    flush()
    close()
    return BenchEventStore.consolidate_and_archive(run, run_dir=Path(run_dir))


def configure_from_env() -> None:
    """Subprocess entrypoint — mirrors ``event_tail.configure_from_env``.

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


with contextlib.suppress(AttributeError, RuntimeError):  # pragma: no cover — non-POSIX or restricted env
    os.register_at_fork(after_in_child=_reset_after_fork)


# Tiny diagnostic for tests / harness logs.
def _stats() -> dict[str, Any]:
    if _store is None:
        return {"configured": False}
    return {
        "configured": True,
        "run_id": _store.run_id,
        "writer_pid": _store.writer_pid,
        "shard_path": str(_store.shard_path),
        "buffered": len(_store._buffer),  # noqa: SLF001 — module-internal
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
    "flush",
    "is_configured",
]

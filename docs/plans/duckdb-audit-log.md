# DuckDB-backed Bench Audit Log — Implementation Plan

**Status: Phase 3 complete.** The legacy `events.jsonl` writer, the
periodic S3 uploader, the `S3RunStore.archive_events()` helper, the
`/events.jsonl` route, the `useRunStream` jsonl fallback, and
`tests/shared/run_bus.py` were all deleted. DuckDB / parquet is the
only audit-log path; no shim, no env-var flag, no fallback.

Tracking issue: **CD-jzkg**. Strangler-fig replacement for the previous
`events.jsonl` pipeline used by the bench harness, exgraph nodes, and the
viewer's `AuditViewer` / `StateInspector`.

## Survey summary (current state)

- **Writer**: `libs/dagster-io/src/dagster_io/bench/event_tail.py` — single
  module-global JSONL writer; all `append(...)` calls hold a
  `threading.Lock` and `open("a")` the same file (`event_tail.py:118-119`).
  Emit sites: harness
  (`tests/benchmark_harness.py:531,582,603,618,638,1352,1595,1636,1648,1708`),
  exgraph nodes (`libs/catalyst-exgraph/src/catalyst_exgraph/nodes/_audit.py:121`,
  `consensus.py:158,178,236,267,289,310,323`, `ner_ensemble.py:98,126,143,165,184`,
  `extract.py:104`, `pipeline.py:382`).
- **Concurrency today**: one process, module-global lock. Subprocesses
  re-bind via `event_tail.configure_from_env()` (`event_tail.py:270-281`).
  Lines >PIPE_BUF (`chunk_loaded` carries up to 4 KiB of text) *can*
  interleave under multi-process append. No swarm story yet.
- **Archival**: daemon thread armed by
  `event_tail.configure_periodic_upload(...)`
  (`benchmark_harness.py:1336-1341`) PUTs to
  `s3://<bucket>/bench/runs/<run_id>/events.jsonl` every 5 s. Final flush
  at `:1723-1729` + canonical archive in `bench/store.py:203-210`.
- **Reader (viewer)**: `useRunStream`
  (`packages/media-ingest/viewer-ui/src/hooks/useRunStream.ts`) polls
  `GET /viewer/api/bench/runs/<id>/events.jsonl` every 3 s
  (`packages/media-ingest/src/media_ingest/viewer/routes/bench.py:211-234`).
  Consumers: `AuditViewer.tsx`, `StateInspector{,V2}.tsx`, `EventStream.tsx`,
  `ChunkRail.tsx`, `ChunkTextPanel.tsx`. Shape: `RunEvent` in
  `packages/media-ingest/viewer-ui/src/types/benchmark.ts:133-152`.
- **Run-bus**: already retired; polling is the only live path.

## 1. Schema

One **wide events table** plus a tiny **runs** dimension. State and details
stay JSON because every node emits a different shape and we don't want
schema migrations every time a node grows a field.

```sql
CREATE TABLE events (
  -- ordering / partitioning
  ts            TIMESTAMP   NOT NULL,
  run_id        VARCHAR     NOT NULL,
  seq           BIGINT      NOT NULL,   -- per-(run_id, writer_pid) monotonic
  writer_pid    INTEGER     NOT NULL,   -- helps debug shard merges

  -- typed columns (every field on RunEvent that the viewer filters/groups by)
  source        VARCHAR     NOT NULL,   -- 'harness' | 'exgraph' | 'langgraph' | 'dagster'
  node_name     VARCHAR     NOT NULL,
  status        VARCHAR     NOT NULL,
  model         VARCHAR,
  doc_id        VARCHAR,
  chunk_idx     INTEGER,
  chunk_id      VARCHAR,
  retry_count   INTEGER,
  code_location VARCHAR,
  evidence_window_id VARCHAR,

  -- semi-structured payloads
  state         JSON,                   -- DuckDB native JSON, queryable via ->
  details       JSON,
);

CREATE TABLE runs (
  run_id     VARCHAR PRIMARY KEY,
  started_at TIMESTAMP NOT NULL,
  ended_at   TIMESTAMP,
  pipeline   VARCHAR,
  git_sha    VARCHAR,
  config     JSON
);

-- Indexes — DuckDB auto-indexes via min/max zone maps but explicit indexes
-- help the typical "events for this run/model/chunk" query.
CREATE INDEX events_run_idx       ON events(run_id);
CREATE INDEX events_run_model_idx ON events(run_id, model);
CREATE INDEX events_run_chunk_idx ON events(run_id, chunk_id);
CREATE INDEX events_run_node_idx  ON events(run_id, node_name);
```

Decisions:

- One wide table — viewer queries are predominantly `WHERE run_id=? AND
  (model=? OR chunk_id=?)`; a normalised state table doubles join cost for
  no real win until we get >10⁸ events.
- `state` and `details` as `JSON`, not `STRUCT` — the shape of `state`
  varies by `node_name` (see `_audit.py:_state_summary`).
- `seq` + `writer_pid` are the canonical ordering keys for the swarm case
  (`ts` alone collides under sub-ms event rates).
- Parquet archive uses the same column list. Schema additions are
  parquet-evolution-safe (DuckDB's `read_parquet(union_by_name=true)` covers
  it).

## 2. Concurrency model

**Recommendation: (A) per-process Parquet shards, consolidated at run-end.**

Each writer process appends to `<run_dir>/events-<pid>-<uuid>.parquet`
via an in-process `:memory:` DuckDB, flushing every N=512 events or 1 s.
At run-end the harness runs
`COPY (SELECT * FROM read_parquet('events-*.parquet') ORDER BY seq, writer_pid)
TO 'events.parquet' (FORMAT PARQUET);` and uploads.

Why (A): no cross-process locks (swarm-safe); crash-safe (a partial shard
is still readable); DuckDB's single-writer rule kills (C) under swarm;
(B)'s IPC layer adds nothing the OS isn't already giving us; the viewer
reads via `httpfs` against the consolidated parquet anyway, so DuckDB
never needs to be an inter-process server. Trade-off: live tail must
`read_parquet` the current shard set, so we hold flush latency at ≤1 s.

## 3. New library code

### `libs/dagster-io/src/dagster_io/bench/event_store.py` (new)

```python
class BenchEventStore:
    def __init__(self, *, run_id: str, run_dir: Path, writer_pid: int = ...): ...

    def append(self, event: dict) -> None:
        """Buffered append to in-memory DuckDB; flush on size or interval."""

    def flush(self) -> None:
        """Write the in-memory buffer to events-<pid>-<uuid>.parquet."""

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Run a read query against the current shard set
        (read_parquet('events-*.parquet'))."""

    @classmethod
    def consolidate(cls, run_dir: Path) -> Path:
        """Merge shards → events.parquet, sorted by (seq, writer_pid)."""

    @classmethod
    def archive_to_s3(cls, run_dir: Path, store: S3BenchmarkStore,
                      run: S3RunStore) -> str | None:
        """Upload events.parquet to s3://<bucket>/bench/runs/<id>/events.parquet."""
```

- **Init**: lazy on first `append`; buffers ≤512 events / ≤1 s; timer
  thread enforces the ceiling.
- **Teardown**: harness calls `flush()` + `consolidate()` +
  `archive_to_s3()` at run end, in parallel with (not gated by) the
  existing `event_tail.stop_periodic_upload` + `run.archive_events()`.
- **Module-global accessor**: mirror `event_tail.configure(...)` /
  `is_configured()` so emit sites change by one import line.
- **Tests**: `libs/dagster-io/tests/test_bench_event_store.py`
  - `test_append_then_query_roundtrip`
  - `test_swarm_writers_consolidate_in_order` — 4 subprocesses × 50
    events; `(seq, writer_pid)` ordered, no dupes
  - `test_partial_shard_after_crash_is_readable` — SIGKILL mid-flush
  - `test_query_arbitrary_filter` — `WHERE chunk_id = ? AND status =
    'error'` on a 10k-row corpus, p99 < 50 ms

## 4. Harness integration (dual-write)

Every `event_tail.append(...)` site gets a sibling `event_store.append(...)`
with the same kwargs.

Harness (`tests/benchmark_harness.py`):
- `:1329` add `event_store.configure(run_dir=..., run_id=...)` next to
  the `event_tail.configure(...)` call.
- Dual-write at: `:1352` (run_start), `:1595` (model_run started),
  `:1636` (completed), `:1648` (error), `:1708` (run_end), and the
  `_run_model` error sites at `:531,:582,:603,:618,:638`.
- `:1723` add `event_store.flush()` next to `stop_periodic_upload`.
- `:1729` add `event_store.consolidate_and_archive(run, run_dir=...)`
  next to `run.archive_events()`.

Exgraph — change three helpers, not every call site:
- `nodes/_audit.py:121` (`make_audit_event`) — already the funnel for
  `consensus.py:158,178,236,267,289` and `ner_ensemble.py:98,126,165,184`.
- `event_tail.emit_chunk_text` and `event_tail.emit_chunk_extracted`
  (used by `nodes/extract.py:104`, `consensus.py:310,323`,
  `pipeline.py:382`, `ner_ensemble.py:143`).
- Direct `event_tail.append(...)` calls in `consensus.py` and
  `ner_ensemble.py` that bypass the helper get the dual-write inline.

Subprocesses: `event_store.configure_from_env()` reads the same
`CATALYST_RUN_DIR` / `CATALYST_RUN_ID`.

## 5. Viewer integration (dual-read)

### New endpoint

`GET /viewer/api/bench/runs/<run_id>/events` — **parameterised facets,
not raw SQL**. Arbitrary SQL across HTTP is a footgun (injection,
runaway scans, cross-run JOINs); the viewer needs only model, doc_id,
chunk_id, node_name, status, since-ts, limit, order. Keep DuckDB
server-side, expose parameters.

```
GET /viewer/api/bench/runs/<run_id>/events
    ?model=<name>
    &doc_id=<id>
    &chunk_id=<id>
    &node_name=<n>
    &status=<s>
    &since=<iso8601>
    &limit=<int>          (default 5000, max 50000)
    &order=asc|desc       (default asc on (seq, writer_pid))
    &format=json|jsonl    (default jsonl for streaming)
```

The handler resolves the parquet location
(`s3://<bucket>/bench/runs/<id>/events.parquet`, or local
`<run_dir>/events-*.parquet` shards if the run is live) and runs a
parameterised DuckDB query.

### `useRunStream` rewrite

Try DuckDB endpoint first; on `404` or non-2xx, fall back to the existing
`events.jsonl` endpoint. Same in-memory `RunEvent[]` shape, no UI
changes. `connected` flips true on either path.

```ts
const next = await fetchDuckDB(runId) ?? await fetchJsonl(runId);
```

`AuditViewer` and `StateInspector*` consume `useRunStream`'s output —
zero UI changes.

## 6. S3 archival

- DuckDB-side: at run-end, `event_store.consolidate_and_archive(run)`
  writes `events.parquet` and PUTs to
  `s3://<bucket>/bench/runs/<run_id>/events.parquet`.
- Existing jsonl path keeps running unchanged
  (`run.archive_events()` + the periodic uploader).
- Viewer reads parquet via DuckDB `httpfs` —
  `INSTALL httpfs; LOAD httpfs; SET s3_endpoint='...'; SELECT * FROM
  read_parquet('s3://.../events.parquet') WHERE ...` — no client-side
  download.
- Add `events_parquet_key`, `events_parquet_uri`, `archive_events_parquet`
  to `S3RunStore` (`bench/store.py:177-210`) alongside the existing
  `events_key` / `archive_events`.

## 7. Parity test

`libs/dagster-io/tests/test_bench_event_store_parity.py` runs a 1-doc
1-model bench with both writers active; asserts:

1. **Row count parity**: `len(jsonl_lines) ==
   duckdb_count_for(run_id)`.
2. **Per-(node_name, status) histogram equality** — guards against a
   silently-dropped event class.
3. **Field-by-field typed-column equality** for `(ts, source, node_name,
   status, model, doc_id, chunk_idx, chunk_id, retry_count,
   code_location, evidence_window_id)` modulo `ts` type coercion.
4. **`state` and `details` JSON equality** post-`json.loads`.
5. **`chunk_loaded` text fidelity** — `details.text` byte-identical;
   `details.truncated` agrees.
6. **Idempotent consolidate** — same parquet sha256 on two runs.

## 8. Strangler-fig phases with checkable gates

| Phase | Scope | Exit gate |
|---|---|---|
| **1. Dual-write** | `event_store` module + every emit site dual-writes; harness consolidates + archives parquet at run-end. No reader changes. | `test_bench_event_store_parity.py` passes on a fresh `task bench` against the demo video corpus. |
| **2. Dual-read** | New `/events` endpoint live; `useRunStream` prefers DuckDB, falls back to jsonl on 404. Add a viewer-side counter — increment whenever the fallback fires; expose at `/viewer/api/bench/diagnostics`. | Three consecutive full bench runs show **zero** fallback hits in the diagnostics counter. Manual smoke: AuditViewer + StateInspector load identically with parquet path forced and with jsonl path forced (via dev override header). |
| **3. Strangle** | Delete the jsonl file-write in `event_tail`, `configure_periodic_upload`, `S3RunStore.archive_events`, the jsonl route, `tests/shared/run_bus.py`, and the `useRunStream` fallback. Update `docs/dev.md`, blog README. | `task bench` + bench-smoke + `pytest libs/dagster-io/tests` + viewer Playwright smoke all green; no `events.jsonl` grep hits outside historical docs. |

## 9. Risks / unknowns

1. **DuckDB wheel size** (~30 MB). Fine in the dev harness venv; flag if
   it ever lands in Dagster production pods (it shouldn't — `dagster_io.bench`
   is dev-only).
2. **Live read freshness** — DuckDB must discover newly-fsynced shards;
   1-s flush ceiling vs. 3-s poll keeps lag bounded.
3. **Parquet schema evolution** — adding a typed `RunEvent` column means
   mixed-schema shards; `read_parquet(union_by_name=true)` handles it but
   needs tests.
4. **Fork-safety** — a forked subprocess inheriting the parent's buffer
   would double-write; reset via `os.register_at_fork`.
5. **httpfs auth in dev** — push MinIO `s3_access_key_id`/`secret`/`endpoint`
   into the in-process DuckDB at request time, reusing
   `bench/store.py:73-82` env wiring.
6. **AuditEvent vs RunEvent drift** — `_audit.py:107` builds a slightly
   different shape; persist `RunEvent` only, let the viewer derive
   `AuditEvent` (same as `AuditViewer.tsx:56`).

## 10. Estimated work breakdown

| Phase | Files | Days |
|---|---|---|
| 1. Dual-write | +`event_store.py` (~250 LoC), +3 tests; edits to `event_tail.py`, `benchmark_harness.py` (10 sites), `nodes/_audit.py`, `nodes/consensus.py`, `nodes/ner_ensemble.py`, `nodes/extract.py`, `pipeline.py`, `bench/store.py` | **2–3** |
| 2. Dual-read | +`/events` route in `viewer/routes/bench.py` (~120 LoC), edit `useRunStream.ts`, +diagnostics counter | **1–2** |
| 3. Strangle | Delete jsonl writer body, periodic uploader, `archive_events`, `tests/shared/run_bus.py`, jsonl route, `useRunStream` fallback. Update `docs/dev.md`, blog README, `tests/test_phase_a_per_encoder_stats.py` | **1** |

**Total: ~5 engineering days** of code; validation wall-clock (Phase 1 →
Phase 2 gate) is 1-2 weeks of real bench runs.

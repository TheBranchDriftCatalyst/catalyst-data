"""Bench-side artifacts: S3-backed benchmark store + DuckDB audit log.

Group of modules that own the harness/runtime side of catalyst-data —
the medallion-adjacent S3 prefix at ``s3://<bucket>/bench/...`` plus the
DuckDB-backed event-stream writer the StateInspector reads. Distinct
from ``dagster_io``'s production IO managers so the two surfaces don't
entangle.

CD-jzkg Phase 3 deleted the legacy ``event_tail`` jsonl writer; the
event stream is parquet-only (consolidated to ``events.parquet`` and
archived to ``s3://<bucket>/bench/runs/<run_id>/events.parquet``).
"""

from dagster_io.bench import event_store
from dagster_io.bench.store import S3BenchmarkStore, S3RunStore

__all__ = [
    "event_store",
    "S3BenchmarkStore",
    "S3RunStore",
]

"""Bench-side artifacts: S3-backed benchmark store + unified event stream.

Group of modules that own the harness/runtime side of catalyst-data —
the medallion-adjacent S3 prefix at ``s3://<bucket>/bench/...`` plus the
``events.jsonl`` writer the run-bus tails. Distinct from ``dagster_io``'s
production IO managers so the two surfaces don't entangle.

First sub-package landed under CD-satm; remaining flat modules will move
in subsequent passes (assets/, io/, resources/, etc.). Top-level
``dagster_io`` re-exports are stable.
"""

from dagster_io.bench import event_tail
from dagster_io.bench.store import S3BenchmarkStore, S3RunStore

__all__ = [
    "event_tail",
    "S3BenchmarkStore",
    "S3RunStore",
]

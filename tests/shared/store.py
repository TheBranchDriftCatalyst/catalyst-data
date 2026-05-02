"""Thin facade — the canonical impl now lives in ``dagster_io.bench_store``.

Re-exports ``S3BenchmarkStore`` as ``BenchmarkStore`` (and ``S3RunStore`` as
``RunStore``) so the rest of the test layer keeps importing from this module
without churn. There is no local-disk variant — the S3-backed store is the
only impl, pointed at whichever MinIO is running (the local Tilt-managed
container in dev, the cluster Tenant via Tiltfile.prod's port-forward).

Forward-only: callers that previously poked at ``store.root`` / ``run.dir``
(``Path`` objects) need to use the S3-flavored attributes instead:
``store.runs_uri``, ``store.ground_truth_uri``, ``run.run_id``, ``run.s3_uri``,
``run.report_uri``, ``run.events_uri``. The harness has been updated to use
these; new callers should follow the same pattern.
"""

from __future__ import annotations

from dagster_io.bench_store import S3BenchmarkStore, S3RunStore

# Module-level aliases — keep the import surface stable.
BenchmarkStore = S3BenchmarkStore
RunStore = S3RunStore

__all__ = ["BenchmarkStore", "RunStore", "S3BenchmarkStore", "S3RunStore"]

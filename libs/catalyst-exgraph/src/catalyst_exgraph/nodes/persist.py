"""Persist node — terminal materialisation step of the extraction pipeline.

Emits a single ``persist_artifacts`` event per doc once consensus mentions
+ SPO propositions land in their downstream stores. Closes the upstream
``UpstreamPanel`` lineage loop on the State Inspector with a symmetric
**downstream** card per output asset.

Event schema (canonical):

    source       = "harness" | "exgraph"
    node_name    = "persist_artifacts"
    status       = "completed" | "error" | "partial"
    doc_id       = <partition key>
    details      = {
        # Per-asset output paths — the s3:// URI each materialised asset
        # landed at. Key = asset_key string ("media_ingest/mention_artifacts"),
        # value = full s3:// URI.
        "output_paths": dict[str, str],

        # Row counts per output. The two we always emit are
        # ``mentions_written`` + ``propositions_written``; additional
        # row-count fields key off the asset name (e.g. ``windows_written``).
        "mentions_written": int | None,
        "propositions_written": int | None,
        "row_counts": dict[str, int],   # generic per-asset counts for
                                        # the panel's "{n} rows" badge

        # Per-asset byte size when known (best-effort; persist-side IO
        # managers may stat the parquet write to populate this).
        "size_bytes": dict[str, int],

        # Flat list of asset_keys materialised by this persist call —
        # canonical iteration order for the downstream panel cards.
        "asset_keys": list[str],

        # Dagster run id stamped on the materialisations. Without this
        # the State Inspector cannot link to the Dagster UI run page;
        # **always** populate when emitting from a Dagster context.
        "dagster_run_id": str | None,

        # ISO-8601 wall-clock timestamps for the persist op. ``materialized_at``
        # is rendered on the downstream card; ``started_at`` enables
        # duration calculation cross-run for the trend sparkline.
        "started_at": str | None,
        "completed_at": str | None,
        "materialized_at": str | None,   # alias for completed_at; some
                                         # IO managers stamp only this

        # Per-asset success status. Map of asset_key → {"status": "ok"|"error",
        # "reason": str}. Populated even on the happy path with all "ok" so
        # the panel can render per-asset chips uniformly. On a partial-failure
        # run (one asset write succeeded, another raised) the top-level
        # ``status`` is "partial" and the per-asset map identifies the bad
        # one — the panel renders that card amber while keeping the others
        # green.
        "per_asset_status": dict[str, dict[str, str]],
    }

The fields above are additive — every key is optional from the panel's
perspective. Old events that pre-date this schema (status-only
``persist_artifacts: completed`` with empty details) still render: they
fall through the "no data" branch in ``DownstreamPanel`` and the empty
state shows.

This module deliberately does NOT depend on Dagster — both the harness
(``tests/benchmark_harness.py``) and a future Dagster ``persist`` op
will call ``emit_persist_artifacts`` with the same kwargs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    """ISO-8601 timestamp in UTC. Mirrors what BenchEventStore stamps on
    the row's own ``ts`` column — keeping the two consistent simplifies
    cross-event windowing on the viewer.
    """
    return datetime.now(UTC).isoformat()


def _coerce_iso(ts: str | datetime | None) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts.isoformat()
    return str(ts)


def build_persist_details(
    *,
    output_paths: dict[str, str] | None = None,
    row_counts: dict[str, int] | None = None,
    size_bytes: dict[str, int] | None = None,
    mentions_written: int | None = None,
    propositions_written: int | None = None,
    dagster_run_id: str | None = None,
    started_at: str | datetime | None = None,
    completed_at: str | datetime | None = None,
    per_asset_status: dict[str, dict[str, str]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the canonical ``persist_artifacts.details`` dict.

    Pass-through is the contract: any key the panel needs that the caller
    has must reach the event store unchanged. ``asset_keys`` is derived
    from ``output_paths`` keys when not explicitly passed in (kept stable
    sort order for downstream card iteration).
    """
    output_paths = dict(output_paths or {})
    row_counts = dict(row_counts or {})
    size_bytes = dict(size_bytes or {})
    asset_keys = sorted(output_paths.keys())
    completed_iso = _coerce_iso(completed_at)
    started_iso = _coerce_iso(started_at)

    # Default per-asset status: every emitted asset is "ok" unless the
    # caller overrides. Errors must be passed in explicitly — silently
    # marking absent assets as "ok" would mask real persist failures.
    per_asset_status = dict(per_asset_status or {})
    for ak in asset_keys:
        per_asset_status.setdefault(ak, {"status": "ok"})

    details: dict[str, Any] = {
        "output_paths": output_paths,
        "row_counts": row_counts,
        "size_bytes": size_bytes,
        "asset_keys": asset_keys,
        "mentions_written": mentions_written,
        "propositions_written": propositions_written,
        "dagster_run_id": dagster_run_id,
        "started_at": started_iso,
        "completed_at": completed_iso,
        "materialized_at": completed_iso,  # alias for IO managers that
        # stamp only one timestamp on the materialisation event
        "per_asset_status": per_asset_status,
    }
    if extra:
        # Caller-supplied extras (cost, parquet stats, etc.) merge after
        # the canonical fields so they cannot accidentally clobber the
        # documented schema.
        for k, v in extra.items():
            details.setdefault(k, v)
    return details


def derive_status(per_asset_status: dict[str, dict[str, str]] | None) -> str:
    """Roll up per-asset statuses into a single ``persist_artifacts.status``.

    All ok → ``completed``; any error → ``partial`` if at least one asset
    is ok, else ``error``. Empty map (no assets) falls through to
    ``completed`` — the panel renders the empty state in that case.
    """
    if not per_asset_status:
        return "completed"
    n_ok = sum(1 for v in per_asset_status.values() if v.get("status") == "ok")
    n_err = sum(1 for v in per_asset_status.values() if v.get("status") == "error")
    if n_err == 0:
        return "completed"
    if n_ok == 0:
        return "error"
    return "partial"


def emit_persist_artifacts(
    *,
    doc_id: str,
    output_paths: dict[str, str] | None = None,
    row_counts: dict[str, int] | None = None,
    size_bytes: dict[str, int] | None = None,
    mentions_written: int | None = None,
    propositions_written: int | None = None,
    dagster_run_id: str | None = None,
    started_at: str | datetime | None = None,
    completed_at: str | datetime | None = None,
    per_asset_status: dict[str, dict[str, str]] | None = None,
    source: str = "harness",
    code_location: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Emit a single ``persist_artifacts`` bench event with the canonical schema.

    No-op when ``event_store`` is unconfigured (unit tests, scripted
    helpers) — the import is local so this module stays Dagster-free.

    Returns the assembled ``details`` dict for callers that want to log
    or assert on it; ``None`` if no emit happened.
    """
    try:
        from dagster_io.bench import event_store
    except Exception:  # pragma: no cover — dev sandbox without dagster-io
        return None

    if not event_store.is_configured():
        return None

    completed_at = completed_at or _now_iso()
    details = build_persist_details(
        output_paths=output_paths,
        row_counts=row_counts,
        size_bytes=size_bytes,
        mentions_written=mentions_written,
        propositions_written=propositions_written,
        dagster_run_id=dagster_run_id,
        started_at=started_at,
        completed_at=completed_at,
        per_asset_status=per_asset_status,
        extra=extra,
    )
    status = derive_status(details.get("per_asset_status"))
    event_store.append(
        source=source,
        node_name="persist_artifacts",
        status=status,
        doc_id=doc_id,
        code_location=code_location,
        details=details,
    )
    return details


__all__ = [
    "build_persist_details",
    "derive_status",
    "emit_persist_artifacts",
]

"""S3-backed benchmark store — single backend across dev and prod.

Replaces ``tests.shared.store.BenchmarkStore``'s local-disk impl. Same public
surface: ground truths, pipeline-cache artifacts, extractions, timestamped runs,
top-level reports. Everything lives under ``s3://<bucket>/bench/...`` so the
training-dataset Dagster assets can read benchmark output from the same bucket
that holds the live medallion corpus.

The bench audit log writes per-(pid, doc_id) Parquet shards to local disk
under ``local_cache_root`` during a run (CD-jzkg ``BenchEventStore``); at
run end the harness consolidates each ``doc_id`` partition into its own
``data.parquet`` and uploads the partitioned tree to
``s3://<bucket>/bench/runs/<run_id>/events/doc_id=<doc>/data.parquet`` via
:meth:`S3RunStore.archive_events_parquet`. DuckDB partition-prunes via
``read_parquet(..., hive_partitioning=true)`` when the viewer filters by
``doc_id`` (Phase 4 of CD-jzkg).

Path layout:

    bench/
      ground-truth/<name>.json
      pipeline-cache/<doc_id>/<n>_<stage>.json
      extractions/extraction_<model>.json
      runs/<run_id>/
        extractions/extraction_<model>.json
        extractions/<doc_id>/extraction_<model>.json
        events/doc_id=<doc_id>/data.parquet     -- Phase 4 partitioned
        events/doc_id=__run__/data.parquet      -- harness-level events
        benchmark-report.json
        run-config.json
      benchmark-report.json     -- top-level, latest-run snapshot
      overrides/snapshot.json   -- viewer_entity_overrides export
      training/sft/<domain>/data.jsonl
      training/dpo/<domain>/data.jsonl

Legacy runs from Phases 1-3 may still have a flat
``runs/<run_id>/events.parquet``; the read path falls back to that when the
partitioned ``events/`` prefix doesn't resolve.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dagster_io.logging import get_logger
from dagster_io.s3_client import S3Client

logger = get_logger(__name__)

# Stage ordering for pipeline-cache filenames so an `aws s3 ls` shows them in
# execution order. Only the slow audio-model stages are cached.
_STAGE_ORDER: dict[str, int] = {
    "transcription": 0,
    "diarization": 1,
}


def _stage_filename(name: str) -> str:
    if name in _STAGE_ORDER:
        return f"{_STAGE_ORDER[name]}_{name}.json"
    return f"{name}.json"


def _default_local_cache_root() -> Path:
    """Local working directory for ephemeral run artifacts (in-flight
    parquet shards from ``BenchEventStore``). Persists between runs only
    as a side effect — the canonical archive lives in S3.

    store.py lives at ``libs/dagster-io/src/dagster_io/bench/store.py``
    so the repo root is ``parents[5]``. Tracked by CD-vse9 (replace these
    parents[N] hops with a single dagster_io.paths.repo_root() helper).
    """
    repo_root = Path(__file__).resolve().parents[5]
    base = Path(os.environ.get("TEST_OUTPUT_ROOT", str(repo_root / ".test-output")))
    return base / "media-ingest" / "bench-cache"


def _default_s3_client() -> S3Client:
    """Build an S3Client from env. Same defaults as the viewer FastAPI:
    localhost:9000 in dev (Tilt-managed MinIO container), cluster-side
    Tenant via Tiltfile.prod's port-forward in prod ops mode."""
    return S3Client(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
        bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
    )


def _to_bytes(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    return json.dumps(data, indent=2, default=str).encode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# S3RunStore — per-run namespace under bench/runs/<run_id>/
# ─────────────────────────────────────────────────────────────────────────────


class S3RunStore:
    """I/O for a single benchmark run, keyed by ``run_id``.

    The harness uses ``run_id`` (a timestamped folder name) as the canonical
    identifier; ``s3_prefix`` is the corresponding S3 key prefix. The bench
    audit log shards live at ``<local_cache_root>/events-*.parquet`` during
    the run and consolidate to ``events.parquet`` for upload via
    :meth:`archive_events_parquet`.
    """

    def __init__(self, *, run_id: str, store: S3BenchmarkStore):
        self.run_id = run_id
        self._store = store
        self.s3_prefix = f"{store.runs_prefix}/{run_id}"

    @property
    def s3_uri(self) -> str:
        return f"s3://{self._store.bucket}/{self.s3_prefix}"

    @property
    def report_uri(self) -> str:
        return f"s3://{self._store.bucket}/{self.report_key}"

    @property
    def events_parquet_prefix(self) -> str:
        """Prefix under which the partitioned events tree lives.

        Includes the trailing slash so callers can do
        ``f"{prefix}{partition}/data.parquet"`` cleanly. Phase 4 (CD-jzkg)
        replaced the single ``events.parquet`` file with this hive-style
        partitioned tree.
        """
        return f"{self.s3_prefix}/events/"

    @property
    def events_parquet_uri(self) -> str:
        """S3 URI for the partitioned events tree (with trailing slash).

        Suitable as a directory-style reference for tooling that lists
        the partitioned layout. For a DuckDB ``read_parquet`` glob use
        ``f"{events_parquet_uri}**/data.parquet"``.
        """
        return f"s3://{self._store.bucket}/{self.events_parquet_prefix}"

    @property
    def legacy_events_parquet_key(self) -> str:
        """Legacy single-file key from Phases 1-3.

        Read-only fallback: a run that pre-dates Phase 4 still has its
        consolidated ``events.parquet`` at this key. New writes never
        produce this file.
        """
        return f"{self.s3_prefix}/events.parquet"

    @property
    def legacy_events_parquet_uri(self) -> str:
        return f"s3://{self._store.bucket}/{self.legacy_events_parquet_key}"

    # ── Extractions (run-scoped) ────────────────────────────────────────────

    def _extraction_key(self, model: str, doc_id: str | None) -> str:
        if doc_id:
            return f"{self.s3_prefix}/extractions/{doc_id}/extraction_{model}.json"
        return f"{self.s3_prefix}/extractions/extraction_{model}.json"

    def load_extraction(self, model: str, doc_id: str | None = None) -> dict | None:
        key = self._extraction_key(model, doc_id)
        try:
            return json.loads(self._store.client.get_object(key))
        except Exception:
            return None

    def save_extraction(self, model: str, data: dict, doc_id: str | None = None) -> str:
        key = self._extraction_key(model, doc_id)
        self._store.client.put_object(key, _to_bytes(data))
        return key

    def list_extractions(self, doc_id: str | None = None) -> list[str]:
        prefix = f"{self.s3_prefix}/extractions/{doc_id}/" if doc_id else f"{self.s3_prefix}/extractions/"
        keys = self._store.client.list_all_objects(prefix)
        models: list[str] = []
        for k in keys:
            base = k.rsplit("/", 1)[-1]
            if base.startswith("extraction_") and base.endswith(".json"):
                # Skip per-doc subdirs when doc_id is None
                if doc_id is None and k[len(prefix) :].count("/") > 0:
                    continue
                models.append(base[len("extraction_") : -len(".json")])
        return sorted(models)

    def list_doc_ids(self) -> list[str]:
        prefix = f"{self.s3_prefix}/extractions/"
        keys = self._store.client.list_all_objects(prefix)
        doc_ids: set[str] = set()
        for k in keys:
            rest = k[len(prefix) :]
            if "/" in rest:
                doc_ids.add(rest.split("/", 1)[0])
        return sorted(doc_ids)

    def clean_extractions(self) -> None:
        keys = self._store.client.list_all_objects(f"{self.s3_prefix}/extractions/")
        if keys:
            self._store.client.delete_objects(keys)

    # ── Report & Config ─────────────────────────────────────────────────────

    @property
    def report_key(self) -> str:
        return f"{self.s3_prefix}/benchmark-report.json"

    @property
    def config_key(self) -> str:
        return f"{self.s3_prefix}/run-config.json"

    def save_report(self, data: dict) -> str:
        self._store.client.put_object(self.report_key, _to_bytes(data))
        return self.report_key

    def load_report(self) -> dict | None:
        try:
            return json.loads(self._store.client.get_object(self.report_key))
        except Exception:
            return None

    def save_run_config(self, config: dict) -> str:
        self._store.client.put_object(self.config_key, _to_bytes(config))
        return self.config_key

    # ── Events archive (parquet, CD-jzkg) ──────────────────────────────────

    def archive_events_parquet(self, local_run_dir: Path | None = None) -> str | None:
        """Upload every ``events/doc_id=<doc>/data.parquet`` partition under
        ``local_run_dir`` to S3 with the matching partition layout.

        Phase 4 (CD-jzkg.1) replaces the single-file upload with a
        per-partition fan-out. ``local_run_dir`` defaults to
        ``<local_cache_root>`` — the directory
        :class:`BenchEventStore.consolidate` writes the
        ``events/doc_id=*/data.parquet`` tree under.

        Returns the S3 events prefix when at least one partition was
        uploaded, ``None`` when no partitioned data was found locally.
        """
        run_dir = Path(local_run_dir) if local_run_dir else self._store.local_cache_root
        events_root = run_dir / "events"
        if not events_root.is_dir():
            return None
        uploaded = 0
        for part_dir in sorted(events_root.glob("doc_id=*")):
            data = part_dir / "data.parquet"
            if not data.exists():
                continue
            key = f"{self.events_parquet_prefix}{part_dir.name}/data.parquet"
            self._store.client.put_object_file(key, str(data))
            uploaded += 1
        if uploaded == 0:
            return None
        return self.events_parquet_prefix


# ─────────────────────────────────────────────────────────────────────────────
# S3BenchmarkStore — top-level namespace under bench/
# ─────────────────────────────────────────────────────────────────────────────


class S3BenchmarkStore:
    """S3-backed replacement for ``tests.shared.store.BenchmarkStore``.

    Same public method names as the local-disk impl so harness/test wiring
    is a mechanical refactor — but returns S3 keys (strings) where the old
    impl returned ``Path`` objects.
    """

    def __init__(
        self,
        *,
        client: S3Client | None = None,
        local_cache_root: Path | None = None,
    ):
        self.client = client or _default_s3_client()
        self.bucket = self.client.bucket
        self.local_cache_root = local_cache_root or _default_local_cache_root()
        self.local_cache_root.mkdir(parents=True, exist_ok=True)

        # S3 key prefixes (no trailing slash). Public so callers can format
        # them when printing.
        self.root_prefix = "bench"
        self.ground_truth_prefix = f"{self.root_prefix}/ground-truth"
        self.pipeline_cache_prefix = f"{self.root_prefix}/pipeline-cache"
        self.extractions_prefix = f"{self.root_prefix}/extractions"
        self.runs_prefix = f"{self.root_prefix}/runs"
        self.overrides_prefix = f"{self.root_prefix}/overrides"
        self.training_prefix = f"{self.root_prefix}/training"

        # True fixtures shipped with repo (read-only, never written to). The
        # benchmark documents are checked into the repo and don't belong in S3.
        self._repo_fixtures = Path(__file__).resolve().parents[5] / "tests" / "fixtures"

    @property
    def root_uri(self) -> str:
        return f"s3://{self.bucket}/{self.root_prefix}"

    @property
    def ground_truth_uri(self) -> str:
        return f"s3://{self.bucket}/{self.ground_truth_prefix}/"

    @property
    def runs_uri(self) -> str:
        return f"s3://{self.bucket}/{self.runs_prefix}/"

    @property
    def extractions_uri(self) -> str:
        return f"s3://{self.bucket}/{self.extractions_prefix}/"

    @property
    def top_report_uri(self) -> str:
        return f"s3://{self.bucket}/{self.top_report_key}"

    # ═════════════════════════════════════════════════════════════════════
    # Ground truth
    # ═════════════════════════════════════════════════════════════════════

    def list_ground_truths(self) -> list[str]:
        keys = self.client.list_all_objects(f"{self.ground_truth_prefix}/")
        names: list[str] = []
        for k in keys:
            base = k.rsplit("/", 1)[-1]
            if base.endswith(".json"):
                names.append(base[: -len(".json")])
        return sorted(names)

    def load_ground_truth(self, name: str = "active") -> dict | None:
        key = f"{self.ground_truth_prefix}/{name}.json"
        try:
            return json.loads(self.client.get_object(key))
        except Exception:
            return None

    def save_ground_truth(self, name: str, data: dict) -> str:
        key = f"{self.ground_truth_prefix}/{name}.json"
        self.client.put_object(key, _to_bytes(data))
        return key

    def set_active_ground_truth(self, name: str) -> None:
        src_key = f"{self.ground_truth_prefix}/{name}.json"
        dst_key = f"{self.ground_truth_prefix}/active.json"
        # copy_object on the same bucket
        self.client.copy_object(src_key, dst_key)

    # ═════════════════════════════════════════════════════════════════════
    # Pipeline cache (transcription / diarization)
    # ═════════════════════════════════════════════════════════════════════

    def load_benchmark_documents(self) -> list[dict] | None:
        """Reads the per-package benchmark_documents.json fixtures from the
        repo. Not in S3 — these are checked-in test inputs that live in each
        domain's package at ``packages/<domain>/tests/fixtures/``."""
        repo_root = Path(__file__).resolve().parents[5]
        domain_dirs = ["media-ingest", "congress-data", "open-leaks"]
        merged: list[dict] = []
        for d in domain_dirs:
            p = repo_root / "packages" / d / "tests" / "fixtures" / "benchmark_documents.json"
            if p.exists():
                merged.extend(json.loads(p.read_text()))
        return merged if merged else None

    def _pipeline_cache_key(self, name: str, doc_id: str | None) -> str:
        fname = _stage_filename(name)
        if doc_id:
            return f"{self.pipeline_cache_prefix}/{doc_id}/{fname}"
        return f"{self.pipeline_cache_prefix}/{fname}"

    def load_pipeline_artifact(self, name: str, doc_id: str | None = None) -> dict | list | None:
        key = self._pipeline_cache_key(name, doc_id)
        try:
            return json.loads(self.client.get_object(key))
        except Exception:
            return None

    def save_pipeline_artifact(self, name: str, data, doc_id: str | None = None) -> str:
        key = self._pipeline_cache_key(name, doc_id)
        self.client.put_object(key, _to_bytes(data))
        return key

    def list_pipeline_cache_doc_ids(self) -> list[str]:
        """Return doc_ids that have at least one cached pipeline artifact.

        Mirrors the old ``pipeline_cache_dir.iterdir()`` shape: any S3 key
        immediately under ``bench/pipeline-cache/<doc_id>/...`` counts.
        """
        prefix = f"{self.pipeline_cache_prefix}/"
        keys = self.client.list_all_objects(prefix)
        doc_ids: set[str] = set()
        for k in keys:
            rest = k[len(prefix) :]
            if "/" in rest:
                doc_ids.add(rest.split("/", 1)[0])
        return sorted(doc_ids)

    # ═════════════════════════════════════════════════════════════════════
    # Extraction artifacts (top-level, cross-run cache)
    # ═════════════════════════════════════════════════════════════════════

    def load_extraction(self, model: str) -> dict | None:
        key = f"{self.extractions_prefix}/extraction_{model}.json"
        try:
            return json.loads(self.client.get_object(key))
        except Exception:
            return None

    def save_extraction(self, model: str, data: dict) -> str:
        key = f"{self.extractions_prefix}/extraction_{model}.json"
        self.client.put_object(key, _to_bytes(data))
        return key

    def list_extractions(self) -> list[str]:
        keys = self.client.list_all_objects(f"{self.extractions_prefix}/")
        models: list[str] = []
        for k in keys:
            base = k.rsplit("/", 1)[-1]
            if base.startswith("extraction_") and base.endswith(".json"):
                models.append(base[len("extraction_") : -len(".json")])
        return sorted(models)

    # ═════════════════════════════════════════════════════════════════════
    # Runs
    # ═════════════════════════════════════════════════════════════════════

    def create_run(self, label: str | None = None) -> S3RunStore:
        """Create a new timestamp-prefixed run namespace.

        Run IDs are ``YYYY-MM-DD-HHMMSS[-<label>]`` so lex-sort == chrono-sort.
        S3 has no symlinks — ``load_run("latest")`` picks the lex-largest run.
        """
        ts = datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")
        run_id = f"{ts}-{label}" if label else ts
        # No need to "create" anything in S3 — first put_object creates the prefix.
        return S3RunStore(run_id=run_id, store=self)

    def load_run(self, name: str = "latest") -> S3RunStore | None:
        if name == "latest":
            runs = self.list_runs()
            if not runs:
                return None
            return S3RunStore(run_id=runs[-1], store=self)
        # Verify the run exists by listing
        if not self.client.list_objects(f"{self.runs_prefix}/{name}/"):
            return None
        return S3RunStore(run_id=name, store=self)

    def list_runs(self) -> list[str]:
        prefix = f"{self.runs_prefix}/"
        keys = self.client.list_all_objects(prefix)
        run_ids: set[str] = set()
        for k in keys:
            rest = k[len(prefix) :]
            if "/" in rest:
                run_ids.add(rest.split("/", 1)[0])
        return sorted(run_ids)

    # ═════════════════════════════════════════════════════════════════════
    # Top-level report
    # ═════════════════════════════════════════════════════════════════════

    @property
    def top_report_key(self) -> str:
        return f"{self.root_prefix}/benchmark-report.json"

    def save_top_level_report(self, data: dict) -> str:
        self.client.put_object(self.top_report_key, _to_bytes(data))
        return self.top_report_key

    def load_top_level_report(self) -> dict | None:
        try:
            return json.loads(self.client.get_object(self.top_report_key))
        except Exception:
            return None

    def copy_run_report_to_top_level(self, run: S3RunStore) -> str | None:
        try:
            self.client.copy_object(run.report_key, self.top_report_key)
            return self.top_report_key
        except Exception:
            return None

    # ═════════════════════════════════════════════════════════════════════
    # Clean (forward-only — wipes S3 prefixes)
    # ═════════════════════════════════════════════════════════════════════

    def _delete_prefix(self, prefix: str) -> int:
        keys = self.client.list_all_objects(prefix)
        if not keys:
            return 0
        deleted, _errors = self.client.delete_objects(keys)
        return deleted

    def clean_extractions(self) -> int:
        return self._delete_prefix(f"{self.extractions_prefix}/")

    def clean_ground_truth(self) -> int:
        return self._delete_prefix(f"{self.ground_truth_prefix}/")

    def clean_runs(self, keep_recent: int = 0) -> int:
        runs = self.list_runs()
        to_delete = runs if keep_recent == 0 else runs[:-keep_recent]
        count = 0
        for r in to_delete:
            count += self._delete_prefix(f"{self.runs_prefix}/{r}/")
        return count

    def clean_reports(self) -> int:
        try:
            self.client.delete_object(self.top_report_key)
            return 1
        except Exception:
            return 0

    def clean_pipeline_cache(self) -> int:
        return self._delete_prefix(f"{self.pipeline_cache_prefix}/")

    def clean_all(self, tier: str = "standard") -> dict[str, int]:
        result: dict[str, int] = {
            "extractions": self.clean_extractions(),
            "ground_truth": self.clean_ground_truth(),
        }
        if tier in ("standard", "full", "nuclear"):
            result["runs"] = self.clean_runs()
            result["reports"] = self.clean_reports()
        if tier in ("full", "nuclear"):
            result["pipeline_cache"] = self.clean_pipeline_cache()
        return result

    # ═════════════════════════════════════════════════════════════════════
    # Backward-compat fixture-name routing (used by test_extraction_benchmark)
    # ═════════════════════════════════════════════════════════════════════

    def load_fixture(self, name: str) -> dict | list | None:
        if name == "ground_truth_media_ingest":
            return self.load_ground_truth("active")
        if name.startswith("extraction_"):
            return self.load_extraction(name.replace("extraction_", ""))
        if name in ("transcription", "diarization"):
            return self.load_pipeline_artifact(name)
        # Last resort: true fixture in repo
        f = self._repo_fixtures / f"{name}.json"
        return json.loads(f.read_text()) if f.exists() else None

    def save_fixture(self, name: str, data) -> None:
        if name == "ground_truth_media_ingest":
            self.save_ground_truth("active", data)
            return
        if name.startswith("extraction_"):
            self.save_extraction(name.replace("extraction_", ""), data)
            return
        if name in ("transcription", "diarization"):
            self.save_pipeline_artifact(name, data)
            return
        # Fallback: pipeline cache for unknown names
        self.save_pipeline_artifact(name, data)


__all__ = ["S3BenchmarkStore", "S3RunStore"]

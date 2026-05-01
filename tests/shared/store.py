"""Centralized I/O for benchmark data with structured directory layout.

Terminology:
    TRUE FIXTURES = curated test inputs, checked into git, never deleted by --regen.
        Lives in tests/fixtures/ (e.g. benchmark_chunks.json, demo_video.mp4).

    CACHED ARTIFACTS = generated pipeline outputs, gitignored, regenerable.
        Lives in .test-output/media-ingest/ (e.g. extraction_*.json, transcription.json,
        ground_truth, benchmark-report.json).
        Deleted by --regen or bench:clean.

Directory layout under root (typically .test-output/media-ingest):

    pipeline-cache/              -- expensive audio-model outputs
        0_transcription.json     -- Whisper output (slow, cached)
        1_diarization.json       -- pyannote output (slow, cached)
                                    (segment_merge + chunks are fast, recomputed)
    ground-truth/                -- ground truth versions
        ensemble-12model.json
        active.json              -- the one currently used for scoring
    extractions/                 -- per-model extraction artifacts
        extraction_gpt-4o.json
        extraction_mistral:latest.json
    runs/                        -- timestamped benchmark runs
        2026-04-29-exgraph-v2/
            extractions/         -- run-specific copies
            audit-logs/
            benchmark-report.json
            run-config.json
        latest -> ...            -- symlink to most recent
    benchmark-report.json        -- top-level copy for viewer SPA

True fixtures (checked into git, never touched by BenchmarkStore):
    tests/fixtures/
        benchmark_chunks.json    -- curated benchmark subset
        demo_video.mp4           -- source media

Design notes:
    The API is designed to eventually support an S3-backed adapter for running
    benchmarks against the same storage layer as production (MinioIOManager).
    The path structure (runs/<label>/extractions/<model>.json) deliberately
    mirrors the medallion paths used in production (gold/media_ingest/mentions/<id>).
    For now it's local filesystem only, but swapping BenchmarkStore for an
    S3-backed version should be straightforward since all I/O goes through
    load/save methods rather than raw Path operations.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path


def _default_root() -> Path:
    """Resolve the default test-output root for media-ingest benchmarks."""
    repo_root = Path(__file__).resolve().parents[2]
    return Path(os.environ.get("TEST_OUTPUT_ROOT", str(repo_root / ".test-output"))) / "media-ingest"


# Pipeline-stage ordering — prepended to pipeline-cache filenames so an `ls`
# reflects execution order. Only the two slow audio-model stages are cached
# (Whisper transcription, pyannote diarization). Speaker-merge and chunking are
# fast Python passes that re-run from cached transcription+diarization on every
# benchmark invocation.
_STAGE_ORDER: dict[str, int] = {
    "transcription": 0,
    "diarization": 1,
}


def _stage_filename(name: str) -> str:
    """Return the on-disk filename for a pipeline stage artifact."""
    if name in _STAGE_ORDER:
        return f"{_STAGE_ORDER[name]}_{name}.json"
    return f"{name}.json"


class RunStore:
    """I/O for a single benchmark run directory.

    Conceptually mirrors how MinioIOManager stores production artifacts:
    each run is an isolated namespace with its own extractions, audit logs,
    report, and config. The path layout (extractions/<model>.json) parallels
    the production medallion paths (gold/media_ingest/mentions/<document_id>).
    """

    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.extractions_dir = run_dir / "extractions"
        self.audit_dir = run_dir / "audit-logs"

    def _ensure(self, d: Path) -> Path:
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Extractions (cached artifacts) ───────────────────────────────

    def _extractions_root(self, doc_id: str | None) -> Path:
        """Resolve the extractions dir for a given doc_id (multi-video flow).

        - ``doc_id=None`` → flat ``extractions/extraction_<model>.json`` (single-video back-compat)
        - ``doc_id="<slug>"`` → ``extractions/<slug>/extraction_<model>.json`` (multi-video)
        """
        return self.extractions_dir / doc_id if doc_id else self.extractions_dir

    def load_extraction(self, model: str, doc_id: str | None = None) -> dict | None:
        f = self._extractions_root(doc_id) / f"extraction_{model}.json"
        return json.loads(f.read_text()) if f.exists() else None

    def save_extraction(self, model: str, data: dict, doc_id: str | None = None) -> Path:
        root = self._extractions_root(doc_id)
        self._ensure(root)
        p = root / f"extraction_{model}.json"
        p.write_text(json.dumps(data, indent=2, default=str))
        return p

    def list_extractions(self, doc_id: str | None = None) -> list[str]:
        root = self._extractions_root(doc_id)
        if not root.exists():
            return []
        return sorted(f.stem.replace("extraction_", "") for f in root.glob("extraction_*.json"))

    def list_doc_ids(self) -> list[str]:
        """List doc_ids that have at least one extraction in this run (multi-video runs only)."""
        if not self.extractions_dir.exists():
            return []
        return sorted(d.name for d in self.extractions_dir.iterdir() if d.is_dir())

    def clean_extractions(self) -> None:
        """Remove all cached extraction artifacts in this run (flat + per-doc-id)."""
        if not self.extractions_dir.exists():
            return
        for f in self.extractions_dir.glob("extraction_*.json"):
            f.unlink()
        for d in self.extractions_dir.iterdir():
            if d.is_dir():
                for f in d.glob("extraction_*.json"):
                    f.unlink()

    # ── Audit Logs (cached artifacts) ────────────────────────────────

    def save_audit_log(self, model: str, data: dict) -> Path:
        self._ensure(self.audit_dir)
        safe_name = model.replace("/", "_").replace(":", "_").replace(" ", "_")
        p = self.audit_dir / f"{safe_name}.json"
        p.write_text(json.dumps(data, indent=2, default=str))
        return p

    # ── Report & Config (cached artifacts) ───────────────────────────

    def save_report(self, data: dict) -> Path:
        self._ensure(self.dir)
        p = self.dir / "benchmark-report.json"
        p.write_text(json.dumps(data, indent=2, default=str))
        return p

    def load_report(self) -> dict | None:
        p = self.dir / "benchmark-report.json"
        return json.loads(p.read_text()) if p.exists() else None

    def save_run_config(self, config: dict) -> Path:
        self._ensure(self.dir)
        p = self.dir / "run-config.json"
        p.write_text(json.dumps(config, indent=2, default=str))
        return p


class BenchmarkStore:
    """Centralized I/O for all benchmark cached artifacts.

    This class manages CACHED ARTIFACTS only (generated, gitignored, regenerable).
    It never reads or writes to tests/fixtures/ (true fixtures checked into git).

    Provides access to:
    - Ground truth (computed from extractions, shared across runs)
    - Pipeline cache (transcription, diarization, chunks -- expensive to regenerate)
    - Extraction artifacts (LLM outputs per model -- cached between runs)
    - Runs (timestamped benchmark run directories)

    True fixtures (tests/fixtures/benchmark_chunks.json etc.) are accessed via
    _repo_fixtures as a read-only fallback -- never written to.
    """

    def __init__(self, root: Path | None = None):
        self.root = root or _default_root()
        self.ground_truth_dir = self.root / "ground-truth"
        self.pipeline_cache_dir = self.root / "pipeline-cache"
        self.runs_dir = self.root / "runs"
        self.extractions_dir = self.root / "extractions"
        # True fixtures shipped with repo (read-only, never written to)
        self._repo_fixtures = Path(__file__).resolve().parents[1] / "fixtures"
        # Legacy flat layout (read-only fallback for migration, never written to)
        self._legacy_fixtures = self.root / "fixtures"

    def _ensure(self, d: Path) -> Path:
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ═════════════════════════════════════════════════════════════════
    # Ground Truth (cached artifact, shared across runs)
    # ═════════════════════════════════════════════════════════════════

    def list_ground_truths(self) -> list[str]:
        """List available ground truth names (without .json extension)."""
        if not self.ground_truth_dir.exists():
            return []
        return sorted(f.stem for f in self.ground_truth_dir.glob("*.json"))

    def load_ground_truth(self, name: str = "active") -> dict | None:
        """Load a named ground truth, falling back to legacy location."""
        # New location
        f = self.ground_truth_dir / f"{name}.json"
        if f.exists():
            return json.loads(f.read_text())
        # Legacy location (flat fixtures dir -- cached artifact, not true fixture)
        if name == "active":
            legacy = self._legacy_fixtures / "ground_truth_media_ingest.json"
            if legacy.exists():
                return json.loads(legacy.read_text())
        return None

    def save_ground_truth(self, name: str, data: dict) -> Path:
        """Save a named ground truth file (cached artifact)."""
        self._ensure(self.ground_truth_dir)
        p = self.ground_truth_dir / f"{name}.json"
        p.write_text(json.dumps(data, indent=2, default=str))
        return p

    def set_active_ground_truth(self, name: str) -> None:
        """Copy a named ground truth to active.json."""
        src = self.ground_truth_dir / f"{name}.json"
        if not src.exists():
            raise FileNotFoundError(f"Ground truth '{name}' not found at {src}")
        dst = self.ground_truth_dir / "active.json"
        shutil.copy2(src, dst)

    # ═════════════════════════════════════════════════════════════════
    # Pipeline Cache (cached artifacts -- expensive to regenerate)
    # ═════════════════════════════════════════════════════════════════

    def load_benchmark_documents(self) -> list[dict] | None:
        """Load raw benchmark documents from per-domain fixture directories.

        These are full document texts (not pre-chunked). The pipeline's
        ChunkNode splits them adaptively per model context window.
        """
        domain_dirs = ["media-ingest", "congress-data", "open-leaks"]
        merged = []
        for d in domain_dirs:
            p = self._repo_fixtures / d / "benchmark_documents.json"
            if p.exists():
                merged.extend(json.loads(p.read_text()))
        return merged if merged else None

    def _pipeline_cache_root(self, doc_id: str | None) -> Path:
        """Resolve the cache root for a stage artifact, with per-document subdirs.

        - ``doc_id=None`` → flat ``pipeline-cache/`` (back-compat for the single-
          video integration test fixture path).
        - ``doc_id="<slug>"`` → ``pipeline-cache/<slug>/`` so the multi-video
          regen flow doesn't collide artifacts across source files.
        """
        return self.pipeline_cache_dir / doc_id if doc_id else self.pipeline_cache_dir

    def load_pipeline_artifact(self, name: str, doc_id: str | None = None) -> dict | list | None:
        """Load a pipeline stage artifact (transcription, diarization, etc.).

        Cached artifacts -- expensive to regenerate but not true fixtures.
        Pass ``doc_id`` to read from the per-video subdir.
        """
        cache_root = self._pipeline_cache_root(doc_id)
        candidates = [
            cache_root / _stage_filename(name),
            cache_root / f"{name}.json",  # pre-stage-prefix layout
        ]
        # Legacy flat-fixtures fallback only applies to the no-doc-id (single-video) path
        if doc_id is None:
            candidates.append(self._legacy_fixtures / f"{name}.json")
        for p in candidates:
            if p.exists():
                return json.loads(p.read_text())
        return None

    def save_pipeline_artifact(self, name: str, data, doc_id: str | None = None) -> Path:
        """Save a pipeline stage artifact (cached artifact).

        Pass ``doc_id`` to write to the per-video subdir.
        """
        cache_root = self._pipeline_cache_root(doc_id)
        self._ensure(cache_root)
        p = cache_root / _stage_filename(name)
        p.write_text(json.dumps(data, indent=2, default=str))
        return p

    # ═════════════════════════════════════════════════════════════════
    # Extraction Artifacts (cached, per-model)
    # ═════════════════════════════════════════════════════════════════

    def load_extraction(self, model: str) -> dict | None:
        """Load an extraction artifact. Checks extractions/ then legacy fixtures/."""
        for p in [
            self.extractions_dir / f"extraction_{model}.json",
            self._legacy_fixtures / f"extraction_{model}.json",
        ]:
            if p.exists():
                return json.loads(p.read_text())
        return None

    def save_extraction(self, model: str, data: dict) -> Path:
        """Save extraction artifact."""
        self._ensure(self.extractions_dir)
        p = self.extractions_dir / f"extraction_{model}.json"
        p.write_text(json.dumps(data, indent=2, default=str))
        return p

    def list_extractions(self) -> list[str]:
        """List model names with extraction artifacts."""
        names: set[str] = set()
        for d in [self.extractions_dir, self._legacy_fixtures]:
            if d.exists():
                names.update(f.stem.replace("extraction_", "") for f in d.glob("extraction_*.json"))
        return sorted(names)

    # ═════════════════════════════════════════════════════════════════
    # Runs
    # ═════════════════════════════════════════════════════════════════

    def create_run(self, label: str | None = None) -> RunStore:
        """Create a new timestamped run directory.

        Run dir names are timestamp-prefixed (UTC ``YYYY-MM-DD-HHMMSS``) so
        sorting alphabetically == sorting chronologically. ``load_run("latest")``
        picks the alphabetically-largest run rather than reading a symlink.
        """
        ts = datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S")
        name = f"{ts}-{label}" if label else ts
        run_dir = self._ensure(self.runs_dir) / name
        run_dir.mkdir(parents=True, exist_ok=True)
        # Best-effort: clear any leftover legacy `latest` symlink/dir from
        # before this refactor so it doesn't confuse load_run logic below.
        import contextlib as _contextlib

        legacy_latest = self.runs_dir / "latest"
        if legacy_latest.is_symlink() or legacy_latest.exists():
            with _contextlib.suppress(IsADirectoryError, OSError):
                legacy_latest.unlink()
        return RunStore(run_dir)

    def load_run(self, name: str = "latest") -> RunStore | None:
        """Load a run by name. ``name="latest"`` resolves to the most recent
        timestamped run by lexical sort (timestamps sort chronologically)."""
        if name == "latest":
            runs = self.list_runs()
            if not runs:
                return None
            return RunStore(self.runs_dir / runs[-1])
        run_dir = self.runs_dir / name
        if not run_dir.exists():
            return None
        return RunStore(run_dir)

    def list_runs(self) -> list[str]:
        """List timestamped run names, ascending (oldest first)."""
        if not self.runs_dir.exists():
            return []
        # Skip the legacy `latest` symlink/dir if it lingers from older runs.
        return sorted(
            d.name for d in self.runs_dir.iterdir() if d.is_dir() and d.name != "latest" and not d.is_symlink()
        )

    # ═════════════════════════════════════════════════════════════════
    # Top-Level Report (for viewer SPA)
    # ═════════════════════════════════════════════════════════════════

    def save_top_level_report(self, data: dict) -> Path:
        """Save benchmark-report.json at root level for the viewer SPA."""
        self._ensure(self.root)
        p = self.root / "benchmark-report.json"
        p.write_text(json.dumps(data, indent=2, default=str))
        return p

    def copy_run_report_to_top_level(self, run: RunStore) -> Path | None:
        """Copy a run's report to the top level for the viewer."""
        src = run.dir / "benchmark-report.json"
        if not src.exists():
            return None
        dst = self.root / "benchmark-report.json"
        shutil.copy2(src, dst)
        return dst

    # ═════════════════════════════════════════════════════════════════
    # Audit Logs (top-level, backward compat with viewer)
    # ═════════════════════════════════════════════════════════════════

    def copy_audit_logs_to_top_level(self, run: RunStore) -> None:
        """Copy a run's audit logs to the top-level audit-logs/ for the viewer."""
        src = run.audit_dir
        if not src.exists():
            return
        dst = self.root / "audit-logs"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

    # ═════════════════════════════════════════════════════════════════
    # Clean (only cached artifacts, never true fixtures)
    # ═════════════════════════════════════════════════════════════════

    def clean_extractions(self) -> int:
        """Delete all cached extraction artifacts. Returns count deleted.

        Cleans extractions/ (new) and any remaining extraction_*.json in fixtures/ (legacy).
        Never touches tests/fixtures/ (true fixtures).
        """
        count = 0
        for d in [self.extractions_dir, self._legacy_fixtures]:
            if d.exists():
                for f in d.glob("extraction_*.json"):
                    f.unlink()
                    count += 1
        return count

    def clean_ground_truth(self) -> int:
        """Delete all cached ground truth artifacts. Returns count deleted."""
        count = 0
        if self.ground_truth_dir.exists():
            for f in self.ground_truth_dir.glob("*.json"):
                f.unlink()
                count += 1
        # Legacy location
        legacy = self._legacy_fixtures / "ground_truth_media_ingest.json"
        if legacy.exists():
            legacy.unlink()
            count += 1
        return count

    def clean_runs(self, keep_recent: int = 0) -> int:
        """Delete timestamped benchmark runs. Returns count deleted.

        Args:
            keep_recent: Keep the N most recent runs (by dir name sort). 0 = delete all.
        """
        count = 0
        runs_dir = self.root / "runs"
        if not runs_dir.exists():
            return 0
        # Remove latest symlink first
        latest = runs_dir / "latest"
        if latest.is_symlink():
            latest.unlink()
        # Sort by name (timestamp-based, so alphabetical = chronological)
        run_dirs = sorted(
            [d for d in runs_dir.iterdir() if d.is_dir()],
            key=lambda d: d.name,
        )
        to_delete = run_dirs if keep_recent == 0 else run_dirs[:-keep_recent]
        for d in to_delete:
            import shutil

            shutil.rmtree(d)
            count += 1
        # Clean empty runs dir
        if runs_dir.exists() and not any(runs_dir.iterdir()):
            runs_dir.rmdir()
        return count

    def clean_reports(self) -> int:
        """Delete report and audit log artifacts. Returns count deleted."""
        count = 0
        top_report = self.root / "benchmark-report.json"
        if top_report.exists():
            top_report.unlink()
            count += 1
        top_audits = self.root / "audit-logs"
        if top_audits.exists():
            import shutil

            count += len(list(top_audits.glob("*.json")))
            shutil.rmtree(top_audits)
        return count

    def clean_pipeline_cache(self) -> int:
        """Delete pipeline stage cache (transcription, diarization, etc.).

        WARNING: Expensive to regenerate (~2 min for transcription + diarization).
        """
        count = 0
        cache_dir = self.root / "pipeline-cache"
        if cache_dir.exists():
            for f in cache_dir.glob("*.json"):
                f.unlink()
                count += 1
        # Legacy location
        for name in ("transcription", "diarization", "segment_merge", "chunks"):
            legacy = self._legacy_fixtures / f"{name}.json"
            if legacy.exists():
                legacy.unlink()
                count += 1
        return count

    def clean_all(self, tier: str = "standard") -> dict[str, int]:
        """Delete cached artifacts at the specified tier. Returns counts per category.

        Tiers:
            quick:     extractions + ground truth only
            standard:  + runs + reports + audit logs (default)
            full:      + pipeline cache (expensive to regenerate!)
            nuclear:   + everything (even pipeline cache)

        Never touches tests/fixtures/ (true fixtures, checked into git).
        """
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

    # ═════════════════════════════════════════════════════════════════
    # Generic I/O (backward compat wrappers)
    # ═════════════════════════════════════════════════════════════════

    def load_fixture(self, name: str) -> dict | list | None:
        """Load a cached artifact by name -- checks new layout then legacy.

        This is the backward-compatible wrapper for _load_fixture().
        Note: despite the name, this loads CACHED ARTIFACTS, not true fixtures.
        True fixtures live in tests/fixtures/ and are accessed as a read-only
        fallback for benchmark_chunks only.
        """
        # Ground truth (cached artifact)
        if name == "ground_truth_media_ingest":
            return self.load_ground_truth("active")

        # Extraction artifacts (cached)
        if name.startswith("extraction_"):
            model = name.replace("extraction_", "")
            return self.load_extraction(model)

        # Pipeline stage artifacts (cached): transcription, diarization
        if name in ("transcription", "diarization"):
            return self.load_pipeline_artifact(name)

        # Fallback: try legacy cached artifacts dir
        f = self._legacy_fixtures / f"{name}.json"
        if f.exists():
            return json.loads(f.read_text())
        # Last resort: true fixture in repo (read-only)
        f2 = self._repo_fixtures / f"{name}.json"
        return json.loads(f2.read_text()) if f2.exists() else None

    def save_fixture(self, name: str, data) -> None:
        """Save a cached artifact by name -- routes to the correct location.

        This is the backward-compatible wrapper for _save_fixture().
        Note: despite the name, this saves CACHED ARTIFACTS, not true fixtures.
        """
        # Ground truth (cached artifact)
        if name == "ground_truth_media_ingest":
            self.save_ground_truth("active", data)
            return

        # Extraction artifacts (cached)
        if name.startswith("extraction_"):
            model = name.replace("extraction_", "")
            self.save_extraction(model, data)
            return

        # Pipeline stage artifacts (cached): transcription, diarization
        if name in ("transcription", "diarization"):
            self.save_pipeline_artifact(name, data)
            return

        # Fallback: pipeline-cache for unknown names
        self._ensure(self.pipeline_cache_dir)
        (self.pipeline_cache_dir / f"{name}.json").write_text(json.dumps(data, indent=2, default=str))

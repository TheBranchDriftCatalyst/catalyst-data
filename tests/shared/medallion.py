"""Read Dagster asset outputs from the local medallion tree.

Each domain's integration test materializes its chunks asset via
``LocalJsonIOManager`` writing to:

  .test-output/<domain>/<layer>/<code_loc>/<group>/<asset>/[<partition>/]data.jsonl

This helper globs that tree and merges chunks across all domains. Forward-only:
no fallback to legacy fixture paths. On a fresh checkout, run the integration
tests first (``task bench:chunks:regen``) to seed the medallion tree.
"""

from __future__ import annotations

import json
from pathlib import Path

_OUT_ROOT = Path(__file__).resolve().parents[2] / ".test-output"

# Four glob patterns cover the matrix of (layer × partition state):
#   silver/gold layers, partitioned/unpartitioned outputs. Layer is per-asset
#   (media_chunks=gold, bill_chunks/leak_chunks=silver). Partition is per-asset
#   (media + congress are partitioned by doc_id/bill_id; open-leaks is unpartitioned).
_PATTERNS = (
    "*/gold/*/*/*chunks/*/data.jsonl",
    "*/silver/*/*/*chunks/*/data.jsonl",
    "*/gold/*/*/*chunks/data.jsonl",
    "*/silver/*/*/*chunks/data.jsonl",
)


def _domain_of(jsonl_path: Path) -> str:
    """Extract the domain from a medallion path. The first path component
    relative to ``.test-output/`` is the domain dir (e.g. ``open-leaks``)."""
    try:
        rel = jsonl_path.relative_to(_OUT_ROOT)
        return rel.parts[0] if rel.parts else "unknown"
    except ValueError:
        return "unknown"


def load_chunks(
    doc_ids: list[str] | None = None,
    sample_per_domain: int | None = None,
) -> list[dict]:
    """Load chunks from any ``*_chunks`` asset across all domains.

    Args:
        doc_ids: Filter by each chunk's ``document_id`` field. Works uniformly
            for partitioned and unpartitioned outputs.
        sample_per_domain: Cap rows per domain (path-based, not metadata-based,
            so it's robust when a domain's chunks lack a ``metadata.domain`` tag).
            When set, the cap is **distributed across partition files** within a
            domain via round-robin — a domain with 7 partition files and cap=70
            yields ~10 chunks per file rather than the first 70 chunks of file 1.
            Critical for benchmarks that span domain variability — open-leaks's
            3.6M chunks across one file or media-ingest's 1052 chunks across 7
            videos otherwise sample lopsidedly. Default ``None`` (no cap).

    Returns ``[]`` if nothing has been materialized yet.
    """
    # Group jsonl files by domain so we can distribute the cap evenly across files.
    files_by_domain: dict[str, list[Path]] = {}
    for pattern in _PATTERNS:
        for jsonl in _OUT_ROOT.glob(pattern):
            domain = _domain_of(jsonl)
            files_by_domain.setdefault(domain, []).append(jsonl)

    merged: list[dict] = []
    for _domain, files in files_by_domain.items():
        if sample_per_domain is None:
            # No cap — read everything, in stable file order
            for jsonl in sorted(files):
                merged.extend(_read_jsonl(jsonl, doc_ids))
            continue

        # Round-robin: pull from each file iteratively until the per-domain cap is met.
        # Each file gets ~ceil(cap / num_files) rows; if a small file runs out early
        # the remaining cap rolls over to the others.
        files_sorted = sorted(files)
        per_file_quota = max(1, -(-sample_per_domain // len(files_sorted)))  # ceil-div
        per_domain_count = 0
        # Per-file iterators so we can pull lazily
        iters = [_iter_jsonl(jsonl, doc_ids) for jsonl in files_sorted]
        active = list(range(len(iters)))
        per_file_taken = [0] * len(iters)

        while active and per_domain_count < sample_per_domain:
            next_active = []
            for i in active:
                if per_domain_count >= sample_per_domain:
                    break
                if per_file_taken[i] >= per_file_quota:
                    next_active.append(i)
                    continue
                try:
                    merged.append(next(iters[i]))
                    per_file_taken[i] += 1
                    per_domain_count += 1
                    next_active.append(i)
                except StopIteration:
                    pass  # this file is exhausted; drop from active
            # If we ran a full pass and every file hit its quota, raise the quota
            # (round-robin spillover) so we still hit the cap when one file is small.
            if all(per_file_taken[i] >= per_file_quota for i in next_active):
                per_file_quota += 1
            active = next_active

    return merged


def _read_jsonl(path: Path, doc_ids: list[str] | None) -> list[dict]:
    rows: list[dict] = []
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if doc_ids and row.get("document_id") not in doc_ids:
                continue
            rows.append(row)
    return rows


def _iter_jsonl(path: Path, doc_ids: list[str] | None):
    """Yield chunk dicts one at a time; respects doc_ids filter."""
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if doc_ids and row.get("document_id") not in doc_ids:
                continue
            yield row

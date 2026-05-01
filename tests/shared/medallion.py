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
            Useful for benchmark extraction — open-leaks materializes 3.6M+
            chunks so a full extraction pass is intractable. Default ``None``
            (no cap). The harness defaults to a small per-domain cap so a
            cross-domain run completes in reasonable time.

    Returns ``[]`` if nothing has been materialized yet.
    """
    per_domain_count: dict[str, int] = {}
    merged: list[dict] = []

    for pattern in _PATTERNS:
        for jsonl in _OUT_ROOT.glob(pattern):
            domain = _domain_of(jsonl)
            if sample_per_domain is not None and per_domain_count.get(domain, 0) >= sample_per_domain:
                continue
            with open(jsonl) as f:
                for line in f:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if doc_ids and row.get("document_id") not in doc_ids:
                        continue
                    merged.append(row)
                    per_domain_count[domain] = per_domain_count.get(domain, 0) + 1
                    if sample_per_domain is not None and per_domain_count[domain] >= sample_per_domain:
                        break
    return merged

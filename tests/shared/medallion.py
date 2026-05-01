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


def load_chunks(doc_ids: list[str] | None = None) -> list[dict]:
    """Load chunks from any ``*_chunks`` asset across all domains.

    ``doc_ids`` filters by each chunk's ``document_id`` field (works uniformly
    for partitioned and unpartitioned outputs). Returns ``[]`` if nothing has
    been materialized yet.
    """
    merged: list[dict] = []
    for pattern in _PATTERNS:
        for jsonl in _OUT_ROOT.glob(pattern):
            for line in jsonl.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if doc_ids and row.get("document_id") not in doc_ids:
                    continue
                merged.append(row)
    return merged

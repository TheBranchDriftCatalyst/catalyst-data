"""Open-leaks ``leak_chunks`` integration test.

The leak_chunks asset is unpartitioned and consumes ``leak_documents``, which
itself is a deterministic concatenation of three bronze sources (wikileaks,
ICIJ offshore, Epstein court docs). Materializes the full chain end-to-end —
no pre-seeding needed, no API keys.

Run via: ``task bench:chunks:regen:leaks``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dagster import materialize
from open_leaks.assets import (
    epstein_court_docs,
    icij_offshore_entities,
    leak_chunks,
    leak_documents,
    wikileaks_cables,
)

os.environ.setdefault("DAGSTER_CODE_LOCATION", "open_leaks")


def test_leak_chunks_materializes(test_resources, output_dir, dagster_instance):
    """Full chain: bronze → leak_documents → leak_chunks. Output at the
    canonical medallion path under ``.test-output/open-leaks/``.
    """
    result = materialize(
        [
            wikileaks_cables,
            icij_offshore_entities,
            epstein_court_docs,
            leak_documents,
            leak_chunks,
        ],
        resources=test_resources,
        instance=dagster_instance,
    )
    assert result.success, "leak_chunks materialization failed"

    # path_builder derives group from asset key — leak_chunks → "leak"
    out = Path(output_dir) / "silver" / "open_leaks" / "leak" / "leak_chunks" / "data.jsonl"
    assert out.exists(), f"missing leak_chunks output at {out}"
    # 3.6M-row JSONL is huge; sample first 100 lines for sanity, don't read whole file.
    sample: list[dict] = []
    with open(out) as f:
        for i, line in enumerate(f):
            if i >= 100:
                break
            if line.strip():
                sample.append(json.loads(line))
    assert sample, "empty leak_chunks output"
    print(
        f"\n  ✓ leak_chunks: sample of {len(sample)} chunks (file has many more) → {out.relative_to(Path(__file__).resolve().parents[4])}"
    )
    for r in sample:
        assert r.get("document_id"), f"chunk missing document_id: {r}"
        assert r.get("text"), f"chunk missing text: {r.get('chunk_id')}"

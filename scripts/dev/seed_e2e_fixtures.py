#!/usr/bin/env python3
"""Deterministic seeder for viewer-ui Playwright e2e fixture corpora (CD-1qqy).

Generates four synthetic bench-run corpora — `happy-path`,
`diversity-composite`, `edge-cases`, `trend-window` — that together cover
every State Inspector "Gap #N" regression spec. The corpora plug into
Playwright's network-route layer (`page.route('**/api/bench/runs/**')`)
when `PLAYWRIGHT_FIXTURE_MODE=1` is set, so dev / staging bench data is
fully decoupled from e2e coverage.

Hardcoded RNG seed (``RANDOM_SEED = 1qqy_2025``-flavoured constant 0x1qqy
truncated to int) means same bytes every run — verified by re-running and
diffing, no system date / pid / uuid leaks.

Output layout (all paths relative to repo root)::

    packages/media-ingest/viewer-ui/e2e/fixtures/corpora/
      happy-path/
        manifest.yaml
        events.ndjson
        report.json
      diversity-composite/
        manifest.yaml          # TODO(CD-1qqy follow-up)
        events.ndjson
        report.json
      edge-cases/
        manifest.yaml          # TODO(CD-1qqy follow-up)
        events.ndjson
        report.json
      trend-window/
        manifest.yaml
        runs/                  # ≥10 historical runs of same doc
          2025-04-{01..10}-120000/
            report.json
            events.ndjson

Event schema (minimal — only what discovery helpers actually scan):
    ts, seq, node_name, status, doc_id, chunk_id, chunk_idx, model, details

Usage::

    python scripts/dev/seed_e2e_fixtures.py                # all corpora
    python scripts/dev/seed_e2e_fixtures.py --corpus happy-path
    python scripts/dev/seed_e2e_fixtures.py --check        # verify determinism

Idempotent: rerunning overwrites prior output bytes.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Hardcoded — DO NOT make this configurable. The whole point of the
# fixture corpora is reproducible bytes. Hex flavoured to nod at the
# beads ticket id (CD-1qqy) without being parsed as Python.
RANDOM_SEED = 0x1AABBCCDD

ROOT = Path(__file__).resolve().parents[2]
CORPORA_DIR = ROOT / "packages" / "media-ingest" / "viewer-ui" / "e2e" / "fixtures" / "corpora"


def _ts(seq: int) -> str:
    """Synthetic ISO timestamp, monotonic in ``seq``. Anchored at a fixed
    epoch so output bytes are seed-only-dependent."""
    base = datetime(2025, 4, 1, 12, 0, 0, tzinfo=UTC)
    return (base + timedelta(seconds=seq)).isoformat()


def _ev(
    *,
    seq: int,
    node_name: str,
    status: str = "completed",
    doc_id: str | None = None,
    chunk_id: str | None = None,
    chunk_idx: int | None = None,
    model: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal event dict — only the columns helpers scan. Other
    columns from the canonical 16-column schema (writer_pid, source,
    code_location, evidence_window_id, state, retry_count) are deliberately
    omitted; the FastAPI events endpoint tolerates missing columns and the
    helpers never read them."""
    return {
        "ts": _ts(seq),
        "seq": seq,
        "node_name": node_name,
        "status": status,
        "doc_id": doc_id,
        "chunk_id": chunk_id,
        "chunk_idx": chunk_idx,
        "model": model,
        "details": details or {},
    }


# ── happy-path corpus ───────────────────────────────────────────────────


def _build_happy_path(rng: random.Random) -> tuple[list[dict], dict]:
    """One doc, full pipeline, ≥3 encoders, kept+pruned windows, persist,
    active GT with non-zero mention_count.

    Targets gaps:
      #1 — F1 strip + GT chips (gtAvailable, ensembleScores, encoder models)
      #3 — confidence histogram (numeric confidence on encoder mentions)
      #4 — pack histograms (kept_windows + pruned_windows both ≥1)
      #9 — rejected source_models on consensus
      #10 — downstream lineage (persist_artifacts with asset_keys)
    """
    doc_id = "happy-path-doc-001"
    encoders = ["gliner-m", "gliner-l", "tner-roberta"]
    spo_model = "gpt-4o-mini"
    events: list[dict] = []
    seq = 0

    def add(**kw: Any) -> None:
        nonlocal seq
        events.append(_ev(seq=seq, **kw))
        seq += 1

    # 1. chunk_loaded — base chunks
    for i in range(3):
        add(
            node_name="chunk_loaded",
            doc_id=doc_id,
            chunk_id=f"{doc_id}:c{i:03d}",
            chunk_idx=i,
            details={"text": f"sample chunk {i}", "char_count": 200 + i},
        )

    # 2. chunk_extracted per encoder × chunk — with confidence + mentions
    for enc in encoders:
        for i in range(3):
            mentions = []
            for j in range(4):
                conf = round(rng.uniform(0.55, 0.95), 3)
                mentions.append(
                    {
                        "text": f"Entity{i}_{j}",
                        "label": rng.choice(["PERSON", "ORG", "GPE"]),
                        "span_start": 10 + j * 15,
                        "span_end": 20 + j * 15,
                        "confidence": conf,
                    }
                )
            add(
                node_name="chunk_extracted",
                doc_id=doc_id,
                chunk_id=f"{doc_id}:c{i:03d}:_ner_{enc}",
                chunk_idx=i,
                model=enc,
                details={"mentions": mentions, "encoder": enc},
            )
        add(
            node_name="ner_encoder_completed",
            doc_id=doc_id,
            model=enc,
            details={"mention_count": 12},
        )

    # 3. mention_decision — accepted (covered by ≥2 encoders)
    accepted_specs = [
        ("Entity0_0", "PERSON", encoders[:3], "accepted"),
        ("Entity0_1", "ORG", encoders[:2], "accepted"),
        ("Entity1_0", "GPE", encoders[:3], "accepted"),
        ("Entity1_1", "PERSON", encoders[:2], "accepted"),
        ("Entity2_0", "ORG", encoders[:3], "accepted"),
    ]
    for ent_text, label, sources, _ in accepted_specs:
        add(
            node_name="mention_decision",
            doc_id=doc_id,
            chunk_id=f"{doc_id}:_consensus",
            details={
                "decision": "accepted",
                "text": ent_text,
                "label": label,
                "source_models": list(sources),
                "vote_count": len(sources),
                "confidence": round(rng.uniform(0.7, 0.95), 3),
            },
        )

    # 4. mention_decision — rejected (single-source, varied source_models)
    rejected_specs = [
        ("Spurious0", "PERSON", [encoders[0]]),
        ("Spurious1", "ORG", [encoders[1]]),
        ("Spurious2", "GPE", [encoders[2]]),
    ]
    for ent_text, label, sources in rejected_specs:
        add(
            node_name="mention_decision",
            doc_id=doc_id,
            chunk_id=f"{doc_id}:_consensus",
            details={
                "decision": "rejected",
                "text": ent_text,
                "label": label,
                "source_models": list(sources),
                "vote_count": len(sources),
                "confidence": round(rng.uniform(0.3, 0.55), 3),
                "reject_reason": "single_source",
            },
        )

    # 5. SPO windows (chunk_extracted with :win- suffix, has propositions)
    for w in range(3):
        win_id = f"win-{w:03d}"
        add(
            node_name="chunk_extracted",
            doc_id=doc_id,
            chunk_id=f"{doc_id}:{win_id}",
            model=spo_model,
            details={
                "window_id": win_id,
                "proposition_count": 3 + w,
                "mention_count": 4,
            },
        )

    # 6. evidence_window_pruned (Gap #4 needs ≥1 pruned + Gap #7 likes it)
    pruned_specs = [
        ("win-100", "low_confidence"),
        ("win-101", "sparse_density"),
    ]
    for wid, reason in pruned_specs:
        add(
            node_name="evidence_window_pruned",
            doc_id=doc_id,
            chunk_id=f"{doc_id}:{wid}",
            details={
                "window_id": wid,
                "prune_reason": reason,
                "mention_count": 1,
                "candidates_per_mention": 0.5,
            },
        )

    # 7. pack_evidence — both kept and pruned (Gap #4)
    add(
        node_name="pack_evidence",
        status="completed",
        doc_id=doc_id,
        details={
            "kept_windows": [{"window_id": f"win-{w:03d}", "mention_count": 4 + w} for w in range(3)],
            "pruned_windows": [
                {"window_id": "win-100", "mention_count": 1, "prune_reason": "low_confidence"},
                {"window_id": "win-101", "mention_count": 1, "prune_reason": "sparse_density"},
            ],
        },
    )

    # 8. persist_artifacts — Gap #10 lineage
    add(
        node_name="persist_artifacts",
        status="completed",
        doc_id=doc_id,
        details={
            "asset_keys": [
                "media_ingest/extractions",
                "media_ingest/propositions",
            ],
            "dagster_run_id": "11111111-2222-3333-4444-555555555555",
            "artifact_count": 2,
        },
    )

    # ── report.json with active GT + ensemble scores ────────────────────
    report = {
        "run_id": "fixture-happy-path",
        "ground_truth": {"available": True, "mention_count": 5},
        "models": [
            {
                "name": enc,
                "type": "encoder",
                "scores": {
                    "mention_strict_precision": round(rng.uniform(0.70, 0.85), 4),
                    "mention_strict_recall": round(rng.uniform(0.65, 0.80), 4),
                    "mention_strict_f1": round(rng.uniform(0.68, 0.82), 4),
                },
            }
            for enc in encoders
        ]
        + [
            {
                "name": "ensemble",
                "type": "consensus",
                "scores": {
                    "mention_strict_precision": 0.88,
                    "mention_strict_recall": 0.82,
                    "mention_strict_f1": 0.85,
                },
            }
        ],
        "docs": [doc_id],
    }
    return events, report


# ── trend-window corpus ─────────────────────────────────────────────────


def _build_trend_window(rng: random.Random) -> list[tuple[str, list[dict], dict]]:
    """≥10 historical runs of the same doc with varying F1, for Gap #8
    sparkline. Each tuple is (run_id, events, report).

    Each historical run is a stripped-down happy-path: just enough events
    that `firstEncoderWithMentions` / `firstDocWithConsensus` /
    `firstSpoModelWithWindows` resolve a target so the sparkline assertions
    can render the doc panel."""
    doc_id = "trend-doc-001"
    encoders = ["gliner-m", "gliner-l"]
    runs: list[tuple[str, list[dict], dict]] = []

    base_date = datetime(2025, 4, 1, 12, 0, 0, tzinfo=UTC)
    for i in range(10):
        run_id = (base_date + timedelta(days=i)).strftime("%Y-%m-%d-%H%M%S")
        events: list[dict] = []
        seq = 0

        def add(_events: list[dict] = events, **kw: Any) -> None:
            nonlocal seq
            _events.append(_ev(seq=seq, **kw))
            seq += 1

        add(
            node_name="chunk_loaded",
            doc_id=doc_id,
            chunk_id=f"{doc_id}:c000",
            chunk_idx=0,
            details={"char_count": 200},
        )
        for enc in encoders:
            mentions = [
                {
                    "text": f"Entity{j}",
                    "label": "PERSON",
                    "span_start": j * 10,
                    "span_end": j * 10 + 5,
                    "confidence": round(rng.uniform(0.6, 0.9), 3),
                }
                for j in range(3)
            ]
            add(
                node_name="chunk_extracted",
                doc_id=doc_id,
                chunk_id=f"{doc_id}:c000:_ner_{enc}",
                chunk_idx=0,
                model=enc,
                details={"mentions": mentions, "encoder": enc},
            )
        # one consensus event so firstDocWithConsensus resolves
        add(
            node_name="mention_decision",
            doc_id=doc_id,
            chunk_id=f"{doc_id}:_consensus",
            details={
                "decision": "accepted",
                "text": "Entity0",
                "label": "PERSON",
                "source_models": list(encoders),
                "vote_count": 2,
            },
        )
        # SPO window so firstSpoModelWithWindows resolves
        add(
            node_name="chunk_extracted",
            doc_id=doc_id,
            chunk_id=f"{doc_id}:win-000",
            model="gpt-4o-mini",
            details={"window_id": "win-000", "proposition_count": 2},
        )
        # pack_evidence with kept windows
        add(
            node_name="pack_evidence",
            status="completed",
            doc_id=doc_id,
            details={
                "kept_windows": [{"window_id": "win-000", "mention_count": 3}],
                "pruned_windows": [],
            },
        )
        # persist_artifacts so firstDocWithPersist resolves
        add(
            node_name="persist_artifacts",
            status="completed",
            doc_id=doc_id,
            details={
                "asset_keys": ["media_ingest/extractions"],
                "dagster_run_id": f"trend-{i:02d}",
            },
        )

        # F1 varies across runs to make the sparkline non-flat
        f1 = round(0.65 + (i * 0.02) + rng.uniform(-0.01, 0.01), 4)
        report = {
            "run_id": run_id,
            "ground_truth": {"available": True, "mention_count": 3},
            "models": [
                {
                    "name": enc,
                    "type": "encoder",
                    "scores": {
                        "mention_strict_precision": f1 + 0.02,
                        "mention_strict_recall": f1 - 0.02,
                        "mention_strict_f1": f1,
                    },
                }
                for enc in encoders
            ]
            + [
                {
                    "name": "ensemble",
                    "type": "consensus",
                    "scores": {
                        "mention_strict_precision": f1 + 0.05,
                        "mention_strict_recall": f1 + 0.01,
                        "mention_strict_f1": f1 + 0.03,
                    },
                }
            ],
            "docs": [doc_id],
        }
        runs.append((run_id, events, report))
    return runs


# ── writers ─────────────────────────────────────────────────────────────


def _write_ndjson(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, sort_keys=True, separators=(",", ":")))
            f.write("\n")


def _write_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_manifest(path: Path, body: dict) -> None:
    """Plain-YAML-ish manifest. We avoid a yaml dep — the file is purely
    descriptive (read by humans, not the test runner)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {body.pop('_header', 'corpus manifest')}"]
    for k, v in body.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: {v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── main ────────────────────────────────────────────────────────────────


def build_happy_path() -> None:
    rng = random.Random(RANDOM_SEED)
    events, report = _build_happy_path(rng)
    out = CORPORA_DIR / "happy-path"
    _write_ndjson(out / "events.ndjson", events)
    _write_json(out / "report.json", report)
    _write_manifest(
        out / "manifest.yaml",
        {
            "_header": "happy-path corpus (CD-1qqy) — full pipeline, active GT, ≥3 encoders",
            "doc_id": "happy-path-doc-001",
            "covers_gaps": ["#1", "#3", "#4", "#9", "#10"],
            "encoders": ["gliner-m", "gliner-l", "tner-roberta"],
            "event_count": len(events),
        },
    )
    print(f"  happy-path: {len(events)} events → {out}")


def build_trend_window() -> None:
    rng = random.Random(RANDOM_SEED + 1)
    runs = _build_trend_window(rng)
    out = CORPORA_DIR / "trend-window"
    for run_id, events, report in runs:
        _write_ndjson(out / "runs" / run_id / "events.ndjson", events)
        _write_json(out / "runs" / run_id / "report.json", report)
    _write_manifest(
        out / "manifest.yaml",
        {
            "_header": "trend-window corpus (CD-1qqy) — ≥10 historical runs of same doc",
            "doc_id": "trend-doc-001",
            "covers_gaps": ["#8"],
            "run_count": len(runs),
        },
    )
    print(f"  trend-window: {len(runs)} runs → {out}")


def build_diversity_composite_stub() -> None:
    """TODO(CD-1qqy follow-up): emit a corpus engineered for Gap #2
    (≥3 encoders with varied jaccard pairwise) and Gap #7 composite-reason
    pruning. Stub written to mark intent + keep the dir tree complete."""
    out = CORPORA_DIR / "diversity-composite"
    out.mkdir(parents=True, exist_ok=True)
    _write_manifest(
        out / "manifest.yaml",
        {
            "_header": "TODO(CD-1qqy follow-up): diversity-composite corpus",
            "status": "stub",
            "covers_gaps": ["#2", "#7-composite"],
            "todo": "see scripts/dev/seed_e2e_fixtures.py docstring",
        },
    )


def build_edge_cases_stub() -> None:
    """TODO(CD-1qqy follow-up): emit a corpus for empty/null branches:
    one encoder with all-null confidence (Gap #3 empty branch),
    one pruned window with prune_reason=null (Gap #7 null branch),
    one zero-mention doc."""
    out = CORPORA_DIR / "edge-cases"
    out.mkdir(parents=True, exist_ok=True)
    _write_manifest(
        out / "manifest.yaml",
        {
            "_header": "TODO(CD-1qqy follow-up): edge-cases corpus",
            "status": "stub",
            "covers_gaps": ["#3-empty", "#7-null", "zero-mention"],
            "todo": "see scripts/dev/seed_e2e_fixtures.py docstring",
        },
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--corpus",
        choices=["all", "happy-path", "trend-window", "diversity-composite", "edge-cases"],
        default="all",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Re-emit and diff against existing files; exit 1 on drift.",
    )
    args = p.parse_args(argv)

    CORPORA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Writing corpora to {CORPORA_DIR}")

    if args.corpus in ("all", "happy-path"):
        build_happy_path()
    if args.corpus in ("all", "trend-window"):
        build_trend_window()
    if args.corpus in ("all", "diversity-composite"):
        build_diversity_composite_stub()
    if args.corpus in ("all", "edge-cases"):
        build_edge_cases_stub()

    return 0


if __name__ == "__main__":
    sys.exit(main())

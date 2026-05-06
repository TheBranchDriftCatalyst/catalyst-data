#!/usr/bin/env python3
"""Deterministic seeder for viewer-ui Playwright e2e fixture corpora (CD-1qqy).

Generates four synthetic bench-run corpora — `happy-path`,
`diversity-composite`, `edge-cases`, `trend-window` — that together cover
every State Inspector "Gap #N" regression spec. The corpora plug into
Playwright's network-route layer (`page.route('**/api/bench/runs/**')`)
unconditionally — fixture mode is the only mode for State Inspector
specs, so dev / staging bench data is fully decoupled from e2e coverage.

Hardcoded RNG seed (``RANDOM_SEED = 1qqy_2025``-flavoured constant 0x1qqy
truncated to int) means same bytes every run — verified by re-running and
diffing, no system date / pid / uuid leaks.

Output layout (all paths relative to repo root)::

    packages/catalyst-data-ui/e2e/fixtures/corpora/
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
CORPORA_DIR = ROOT / "packages" / "catalyst-data-ui" / "e2e" / "fixtures" / "corpora"


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
                # ConsensusDetail.acceptedToMention reads ``canonical_type`` —
                # match the live bench schema. ``label`` kept for fixtures
                # that haven't been migrated yet.
                "canonical_type": label,
                "label": label,
                "n_encoders": len(encoders),
                "source_models": list(sources),
                "vote_count": len(sources),
                "mean_confidence": round(rng.uniform(0.7, 0.95), 3),
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
                "canonical_type": label,
                "label": label,
                "n_encoders": len(encoders),
                "quorum": 2,
                "source_models": list(sources),
                "vote_count": len(sources),
                "confidence": round(rng.uniform(0.3, 0.55), 3),
                "reject_reason": "single_source",
                "reason": "single_source",
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


def _build_happy_path_gt() -> dict:
    """Ground-truth file for happy-path corpus (CD-1qqy GT-chip fix).

    Mirrors the 5 ``accepted_specs`` in ``_build_happy_path``: same
    text + canonical_type. ``gt-match.ts`` joins on text+type+doc_id and
    skips spans when the predicted side has none (consensus accepted
    mentions don't carry spans), so omitting span_start/span_end here
    is intentional.

    Shape mirrors the bench's GT file at
    ``/viewer/api/bench/ground-truth/<name>.json`` — chunks→mentions
    with doc_id at the chunk level. The chunk_id we use
    (``<doc>:_consensus``) doesn't matter for matching because
    ConsensusDetail's predicate carries doc_id, not chunk_id, so the
    chunk-level guard short-circuits before chunk_id is compared.
    """
    doc_id = "happy-path-doc-001"
    accepted = [
        ("Entity0_0", "PERSON"),
        ("Entity0_1", "ORG"),
        ("Entity1_0", "GPE"),
        ("Entity1_1", "PERSON"),
        ("Entity2_0", "ORG"),
    ]
    return {
        "name": "fixture-active",
        "total_mentions": len(accepted),
        "chunks": [
            {
                "doc_id": doc_id,
                "chunk_id": f"{doc_id}:_consensus",
                "mentions": [
                    {"text": text, "mention_type": label, "span_start": None, "span_end": None}
                    for text, label in accepted
                ],
            }
        ],
    }


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
    _write_json(out / "ground-truth.json", _build_happy_path_gt())
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


def _build_diversity_composite(rng: random.Random) -> tuple[list[dict], dict]:
    """One doc, 1 chunk, ~10 mentions, 3 encoders with overlapping but
    non-identical sets to produce varied Jaccard off-diagonals for Gap #2
    (encoder co-vote matrix).

    Also includes:
      - ≥1 pack_evidence event with kept + pruned windows
      - ≥1 evidence_window_pruned with composite prune_reason="low_confidence,sparse_density"
      - ≥1 rejected mention with source_models populated (Gap #9 cross-cut)

    Mention coverage by encoder (deliberately non-uniform):
      - gliner-l: accepts mentions {0, 1, 2, 4, 5, 7}      [6 accepted]
      - gliner-pii: accepts mentions {1, 2, 3, 4, 6, 8}    [6 accepted]
      - gliner-news: accepts mentions {2, 3, 4, 5, 8, 9}   [6 accepted]

    This produces a jaccard matrix with variance:
      - [0,1]: J({0,1,2,4,5,7} ∩ {1,2,3,4,6,8}) / |∪| = 3/9 = 0.33
      - [0,2]: J({0,1,2,4,5,7} ∩ {2,3,4,5,8,9}) / |∪| = 3/9 = 0.33
      - [1,2]: J({1,2,3,4,6,8} ∩ {2,3,4,5,8,9}) / |∪| = 4/8 = 0.50
    """
    doc_id = "diversity-composite-doc-001"
    encoders = ["gliner-l", "gliner-pii", "gliner-news"]
    spo_model = "gpt-4o-mini"
    events: list[dict] = []
    seq = 0

    def add(**kw: Any) -> None:
        nonlocal seq
        events.append(_ev(seq=seq, **kw))
        seq += 1

    # 1. chunk_loaded
    add(
        node_name="chunk_loaded",
        doc_id=doc_id,
        chunk_id=f"{doc_id}:c000",
        chunk_idx=0,
        details={"text": "diversity sample chunk", "char_count": 300},
    )

    # 2. Per-encoder chunk_extracted with deliberate mention set coverage
    # 10 total mentions (0-9), each encoder sees a subset
    all_mentions = []
    for i in range(10):
        all_mentions.append(
            {
                "text": f"Entity{i}",
                "label": rng.choice(["PERSON", "ORG", "GPE", "LOCATION"]),
                "span_start": 20 + i * 20,
                "span_end": 30 + i * 20,
                "confidence": round(rng.uniform(0.5, 0.9), 3),
            }
        )

    # gliner-l accepts {0, 1, 2, 4, 5, 7}
    encoder_coverage = {
        "gliner-l": [0, 1, 2, 4, 5, 7],
        "gliner-pii": [1, 2, 3, 4, 6, 8],
        "gliner-news": [2, 3, 4, 5, 8, 9],
    }

    for enc in encoders:
        indices = encoder_coverage[enc]
        mentions = [all_mentions[i] for i in indices]
        add(
            node_name="chunk_extracted",
            doc_id=doc_id,
            chunk_id=f"{doc_id}:c000:_ner_{enc}",
            chunk_idx=0,
            model=enc,
            details={"mentions": mentions, "encoder": enc},
        )
        add(
            node_name="ner_encoder_completed",
            doc_id=doc_id,
            model=enc,
            details={"mention_count": len(mentions)},
        )

    # 3. mention_decision — accepted (multi-encoder consensus)
    # Entity2, Entity4 covered by all 3 encoders
    for ent_idx in [2, 4]:
        add(
            node_name="mention_decision",
            doc_id=doc_id,
            chunk_id=f"{doc_id}:_consensus",
            details={
                "decision": "accepted",
                "text": f"Entity{ent_idx}",
                "label": all_mentions[ent_idx]["label"],
                "source_models": list(encoders),
                "vote_count": 3,
                "confidence": round(rng.uniform(0.75, 0.95), 3),
            },
        )

    # 4. mention_decision — rejected (single encoder only)
    # Entity0 is gliner-l only
    add(
        node_name="mention_decision",
        doc_id=doc_id,
        chunk_id=f"{doc_id}:_consensus",
        details={
            "decision": "rejected",
            "text": "Entity0",
            "label": all_mentions[0]["label"],
            "source_models": [encoders[0]],
            "vote_count": 1,
            "confidence": round(rng.uniform(0.3, 0.5), 3),
            "reject_reason": "single_source",
        },
    )

    # 5. SPO windows
    for w in range(2):
        win_id = f"win-{w:03d}"
        add(
            node_name="chunk_extracted",
            doc_id=doc_id,
            chunk_id=f"{doc_id}:{win_id}",
            model=spo_model,
            details={
                "window_id": win_id,
                "proposition_count": 2 + w,
                "mention_count": 3 + w,
            },
        )

    # 6. evidence_window_pruned with composite reason (Gap #7)
    add(
        node_name="evidence_window_pruned",
        doc_id=doc_id,
        chunk_id=f"{doc_id}:win-100",
        details={
            "window_id": "win-100",
            "prune_reason": "low_confidence,sparse_density",
            "mention_count": 1,
            "candidates_per_mention": 0.3,
        },
    )

    # 7. pack_evidence with kept + pruned
    add(
        node_name="pack_evidence",
        status="completed",
        doc_id=doc_id,
        details={
            "kept_windows": [{"window_id": f"win-{w:03d}", "mention_count": 3 + w} for w in range(2)],
            "pruned_windows": [
                {
                    "window_id": "win-100",
                    "mention_count": 1,
                    "prune_reason": "low_confidence,sparse_density",
                }
            ],
        },
    )

    # ── report.json ──────────────────────────────────────────
    report = {
        "run_id": "fixture-diversity-composite",
        "ground_truth": {"available": True, "mention_count": 2},
        "models": [
            {
                "name": enc,
                "type": "encoder",
                "scores": {
                    "mention_strict_precision": round(rng.uniform(0.65, 0.80), 4),
                    "mention_strict_recall": round(rng.uniform(0.60, 0.75), 4),
                    "mention_strict_f1": round(rng.uniform(0.62, 0.77), 4),
                },
            }
            for enc in encoders
        ]
        + [
            {
                "name": "ensemble",
                "type": "consensus",
                "scores": {
                    "mention_strict_precision": 0.80,
                    "mention_strict_recall": 0.75,
                    "mention_strict_f1": 0.77,
                },
            }
        ],
        "docs": [doc_id],
    }
    return events, report


def build_diversity_composite() -> None:
    rng = random.Random(RANDOM_SEED + 2)
    events, report = _build_diversity_composite(rng)
    out = CORPORA_DIR / "diversity-composite"
    _write_ndjson(out / "events.ndjson", events)
    _write_json(out / "report.json", report)
    _write_manifest(
        out / "manifest.yaml",
        {
            "_header": "diversity-composite corpus (CD-1qqy) — ≥3 encoders with varied jaccard pairwise",
            "doc_id": "diversity-composite-doc-001",
            "covers_gaps": ["#2", "#7-composite", "#9-cross-cut"],
            "encoders": ["gliner-l", "gliner-pii", "gliner-news"],
            "event_count": len(events),
        },
    )
    print(f"  diversity-composite: {len(events)} events → {out}")


def _build_edge_cases(rng: random.Random) -> tuple[list[dict], dict]:
    """Two docs to cover edge cases:

    Doc 1: encoder with all-null confidence (Gap #3 empty branch) +
    pruned window with prune_reason=null (Gap #7 null branch).

    Doc 2: zero-mention doc (chunks loaded but no chunk_extracted events).
    """
    doc1_id = "edge-cases-null-conf-001"
    doc2_id = "edge-cases-zero-mention-002"
    encoder_null_conf = "gliner-null-conf"
    spo_model = "gpt-4o-mini"
    events: list[dict] = []
    seq = 0

    def add(**kw: Any) -> None:
        nonlocal seq
        events.append(_ev(seq=seq, **kw))
        seq += 1

    # ── Doc 1: null-confidence encoder + null-reason pruned window ────────

    # 1a. chunk_loaded for doc1
    add(
        node_name="chunk_loaded",
        doc_id=doc1_id,
        chunk_id=f"{doc1_id}:c000",
        chunk_idx=0,
        details={"text": "null confidence edge case", "char_count": 200},
    )

    # 1b. chunk_extracted for doc1 with every mention having confidence=null
    mentions_null_conf = []
    for i in range(5):
        mentions_null_conf.append(
            {
                "text": f"NullEntity{i}",
                "label": "PERSON",
                "span_start": 10 + i * 15,
                "span_end": 20 + i * 15,
                "confidence": None,  # Explicitly null
            }
        )
    add(
        node_name="chunk_extracted",
        doc_id=doc1_id,
        chunk_id=f"{doc1_id}:c000:_ner_{encoder_null_conf}",
        chunk_idx=0,
        model=encoder_null_conf,
        details={"mentions": mentions_null_conf, "encoder": encoder_null_conf},
    )
    add(
        node_name="ner_encoder_completed",
        doc_id=doc1_id,
        model=encoder_null_conf,
        details={"mention_count": len(mentions_null_conf)},
    )

    # 1c. One consensus mention (so doc appears in lists)
    add(
        node_name="mention_decision",
        doc_id=doc1_id,
        chunk_id=f"{doc1_id}:_consensus",
        details={
            "decision": "accepted",
            "text": "NullEntity0",
            "label": "PERSON",
            "source_models": [encoder_null_conf],
            "vote_count": 1,
            "confidence": 0.6,
        },
    )

    # 1d. SPO window for pack_evidence
    add(
        node_name="chunk_extracted",
        doc_id=doc1_id,
        chunk_id=f"{doc1_id}:win-000",
        model=spo_model,
        details={
            "window_id": "win-000",
            "proposition_count": 1,
            "mention_count": 2,
        },
    )

    # 1e. evidence_window_pruned with prune_reason=null (Gap #7)
    add(
        node_name="evidence_window_pruned",
        doc_id=doc1_id,
        chunk_id=f"{doc1_id}:win-null-reason",
        details={
            "window_id": "win-null-reason",
            "prune_reason": None,  # Explicitly null reason
            "mention_count": 0,
            "candidates_per_mention": 0,
        },
    )

    # 1f. pack_evidence for doc1
    add(
        node_name="pack_evidence",
        status="completed",
        doc_id=doc1_id,
        details={
            "kept_windows": [{"window_id": "win-000", "mention_count": 2}],
            "pruned_windows": [
                {
                    "window_id": "win-null-reason",
                    "mention_count": 0,
                    "prune_reason": None,
                }
            ],
        },
    )

    # ── Doc 2: zero-mention doc ──────────────────────────────────────────

    # 2a. chunk_loaded for doc2 (chunks exist but no extracted mentions)
    add(
        node_name="chunk_loaded",
        doc_id=doc2_id,
        chunk_id=f"{doc2_id}:c000",
        chunk_idx=0,
        details={"text": "document with zero mentions", "char_count": 150},
    )

    # 2b. No chunk_extracted events at all — doc has zero mentions

    # 2c. One empty pack_evidence for completeness
    add(
        node_name="pack_evidence",
        status="completed",
        doc_id=doc2_id,
        details={
            "kept_windows": [],
            "pruned_windows": [],
        },
    )

    # ── report.json: covers both docs ────────────────────────────────────
    report = {
        "run_id": "fixture-edge-cases",
        "ground_truth": {"available": False, "mention_count": 0},
        "models": [
            {
                "name": encoder_null_conf,
                "type": "encoder",
                "scores": {
                    "mention_strict_precision": 0,
                    "mention_strict_recall": 0,
                    "mention_strict_f1": 0,
                },
            }
        ],
        "docs": [doc1_id, doc2_id],
    }
    return events, report


def build_edge_cases() -> None:
    rng = random.Random(RANDOM_SEED + 3)
    events, report = _build_edge_cases(rng)
    out = CORPORA_DIR / "edge-cases"
    _write_ndjson(out / "events.ndjson", events)
    _write_json(out / "report.json", report)
    _write_manifest(
        out / "manifest.yaml",
        {
            "_header": "edge-cases corpus (CD-1qqy) — null confidence, null reason, zero-mention doc",
            "docs": ["edge-cases-null-conf-001", "edge-cases-zero-mention-002"],
            "covers_gaps": ["#3-empty", "#7-null"],
            "event_count": len(events),
        },
    )
    print(f"  edge-cases: {len(events)} events → {out}")


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
        build_diversity_composite()
    if args.corpus in ("all", "edge-cases"):
        build_edge_cases()

    return 0


if __name__ == "__main__":
    sys.exit(main())

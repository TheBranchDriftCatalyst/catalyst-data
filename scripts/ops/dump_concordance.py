#!/usr/bin/env python3
"""Dump concordance audit data from PostgreSQL to CSV.

Produces 2 files:
  1. concordance_audit.csv — one flat row per entity with all context inline
  2. concordance_summary.csv — aggregate stats for quick review

No IDs, no cross-referencing. Every row tells the full story.

Usage:
    python scripts/ops/dump_concordance.py                     # via kubectl (default)
    python scripts/ops/dump_concordance.py --host localhost     # direct PG connection
    python scripts/ops/dump_concordance.py -o /tmp/audit        # custom output dir
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def query_via_kubectl(sql: str) -> list[dict]:
    python_script = f"""
import psycopg2, json
conn = psycopg2.connect(
    host='postgres-knowledge.catalyst-data.svc.cluster.local',
    port=5432, dbname='knowledge_graph', user='kg', password='kg-homelab'
)
cur = conn.cursor()
cur.execute('''{sql}''')
cols = [d[0] for d in cur.description]
rows = [dict(zip(cols, row)) for row in cur.fetchall()]
print(json.dumps(rows, default=str))
conn.close()
"""
    kg_pod = (
        subprocess.check_output(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                "catalyst-data",
                "-l",
                "app=knowledge-graph",
                "--no-headers",
                "-o",
                "custom-columns=:metadata.name",
            ],
            text=True,
        )
        .strip()
        .split("\n")[0]
    )
    result = subprocess.check_output(
        ["kubectl", "exec", "-n", "catalyst-data", kg_pod, "--", "python3", "-c", python_script],
        text=True,
        stderr=subprocess.DEVNULL,
    )
    return json.loads(result)


def query_via_psycopg(sql: str, host: str, port: int, dbname: str, user: str, password: str) -> list[dict]:
    import psycopg2

    conn = psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password)
    cur = conn.cursor()
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    conn.close()
    return rows


def dump_audit(query_fn, output_dir: Path):
    """Single denormalized CSV — one row per entity, everything inline."""

    # 1. Load all entities (including source_candidate_ids for edge→entity mapping)
    entities = query_fn("""
        SELECT canonical_id, canonical_name, entity_type, aliases,
               mention_count, source_code_locations, source_candidate_ids
        FROM canonical_entities
        ORDER BY mention_count DESC
    """)

    # 2. Load all alignment edges (denormalized — names inline since CD-xxx)
    edges = query_fn("""
        SELECT source_entity_id, target_entity_id, alignment_type, score,
               evidence, source_name, target_name, entity_type,
               source_code_location, target_code_location
        FROM alignment_edges
    """)

    # 3. Build edge lookup keyed by canonical_id

    # Map candidate_ids → canonical entity via source_candidate_ids
    candidate_to_canonical: dict[str, dict] = {}
    for ent in entities:
        for cid in ent.get("source_candidate_ids") or []:
            candidate_to_canonical[cid] = ent

    # entity_edges keyed by CANONICAL_ID (not candidate_id) so the
    # flat-row loop can look up edges by the entity's canonical_id.
    entity_edges: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        src_cand_id, tgt_cand_id = edge["source_entity_id"], edge["target_entity_id"]
        # Use denormalized names from PG columns
        src_name = edge.get("source_name") or ""
        tgt_name = edge.get("target_name") or ""
        if not src_name:
            ce = candidate_to_canonical.get(src_cand_id)
            src_name = ce["canonical_name"] if ce else src_cand_id[:12]
        if not tgt_name:
            ce = candidate_to_canonical.get(tgt_cand_id)
            tgt_name = ce["canonical_name"] if ce else tgt_cand_id[:12]

        evidence = edge.get("evidence", [])
        ev_str = ", ".join(evidence) if isinstance(evidence, list) else str(evidence)
        src_loc = edge.get("source_code_location", "")
        tgt_loc = edge.get("target_code_location", "")

        edge_info = {
            "type": edge["alignment_type"],
            "score": edge["score"],
            "evidence": ev_str,
        }

        # Map candidate_id → canonical_id for keying
        src_canonical = candidate_to_canonical.get(src_cand_id, {}).get("canonical_id", src_cand_id)
        tgt_canonical = candidate_to_canonical.get(tgt_cand_id, {}).get("canonical_id", tgt_cand_id)
        entity_edges[src_canonical].append({**edge_info, "partner": tgt_name, "partner_location": tgt_loc})
        entity_edges[tgt_canonical].append({**edge_info, "partner": src_name, "partner_location": src_loc})

    # 4. Each canonical entity IS already a cluster (union-find ran in the asset).
    # "merged_with" shows the sameAs edge partners for this entity — i.e.,
    # which OTHER entity candidates were considered the same by the aligner.
    # The number of source_candidate_ids tells how many gold-layer candidates
    # were collapsed into this canonical entity.

    # 5. Build flat rows
    rows = []
    for ent in entities:
        eid = ent["canonical_id"]
        aliases = ent.get("aliases") or []
        sources = ent.get("source_code_locations") or []
        candidate_ids = ent.get("source_candidate_ids") or []
        ent_edges = entity_edges.get(eid, [])

        # Separate sameAs and possibleSameAs edges
        sameas_partners = [e for e in ent_edges if e["type"] == "sameAs"]
        possible_partners = [e for e in ent_edges if e["type"] == "possibleSameAs"]

        # Format edge summaries as human-readable strings
        sameas_str = "; ".join(
            f"{e['partner']} ({e['score']:.2f}, {e['evidence']})"
            for e in sorted(sameas_partners, key=lambda x: -x["score"])
        )
        possible_str = "; ".join(
            f"{e['partner']} ({e['score']:.2f}, {e['evidence']})"
            for e in sorted(possible_partners, key=lambda x: -x["score"])
        )

        # Unique sameAs partner names (deduplicated)
        sameas_names = sorted({e["partner"] for e in sameas_partners})
        merged_with = "; ".join(n for n in sameas_names if n != ent["canonical_name"])

        rows.append(
            {
                "entity_name": ent["canonical_name"],
                "entity_type": ent["entity_type"],
                "mention_count": ent["mention_count"],
                "aliases": "; ".join(aliases),
                "alias_count": len(aliases),
                "sources": "; ".join(sources),
                "source_count": len(sources),
                "candidate_count": len(candidate_ids),
                "merged_with": merged_with,
                "sameas_edges": sameas_str,
                "sameas_count": len(sameas_partners),
                "possible_edges": possible_str,
                "possible_count": len(possible_partners),
            }
        )

    # Write audit CSV
    path = output_dir / "concordance_audit.csv"
    fieldnames = [
        "entity_name",
        "entity_type",
        "mention_count",
        "aliases",
        "alias_count",
        "sources",
        "source_count",
        "candidate_count",
        "merged_with",
        "sameas_edges",
        "sameas_count",
        "possible_edges",
        "possible_count",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"  {path.name}: {len(rows)} rows")
    return entities, edges


def dump_summary(entities, edges, output_dir: Path):
    """Summary CSV — one row per stat, scannable at a glance."""
    total = len(entities)
    sameas_edges = [e for e in edges if e.get("alignment_type") == "sameAs"]
    possible_edges = [e for e in edges if e.get("alignment_type") == "possibleSameAs"]
    multi_candidate = [e for e in entities if len(e.get("source_candidate_ids") or []) > 1]
    type_counts = Counter(e.get("entity_type", "?") for e in entities)

    rows = []

    # Overview
    rows.append({"category": "overview", "metric": "total_entities", "value": total})
    rows.append({"category": "overview", "metric": "total_alignment_edges", "value": len(edges)})
    rows.append({"category": "overview", "metric": "sameas_edges", "value": len(sameas_edges)})
    rows.append({"category": "overview", "metric": "possible_sameas_edges", "value": len(possible_edges)})
    rows.append({"category": "overview", "metric": "multi_candidate_entities", "value": len(multi_candidate)})
    rows.append({"category": "overview", "metric": "singletons", "value": total - len(multi_candidate)})
    if total > 0:
        total_mentions = sum(e.get("mention_count", 0) for e in entities)
        rows.append({"category": "overview", "metric": "total_mentions", "value": total_mentions})

    # Type distribution
    for t, c in type_counts.most_common():
        rows.append({"category": "entity_types", "metric": t, "value": c})

    # Score buckets
    score_buckets = Counter()
    for e in edges:
        s = e.get("score", 0)
        if s >= 0.95:
            bucket = "0.95-1.00"
        elif s >= 0.90:
            bucket = "0.90-0.94"
        elif s >= 0.80:
            bucket = "0.80-0.89"
        elif s >= 0.65:
            bucket = "0.65-0.79"
        elif s >= 0.50:
            bucket = "0.50-0.64"
        else:
            bucket = "<0.50"
        score_buckets[bucket] += 1
    for b in sorted(score_buckets):
        rows.append({"category": "score_distribution", "metric": b, "value": score_buckets[b]})

    # Multi-candidate entities (entities formed from 2+ gold-layer candidates)
    for e in sorted(multi_candidate, key=lambda x: -x.get("mention_count", 0)):
        n_cands = len(e.get("source_candidate_ids") or [])
        aliases = e.get("aliases") or []
        rows.append(
            {
                "category": "multi_candidate_entities",
                "metric": f"{e['canonical_name']} ({e.get('entity_type', '?')})",
                "value": f"{e.get('mention_count', 0)} mentions, {n_cands} candidates merged, aliases: {', '.join(aliases[:5])}",
            }
        )

    # Entities with 3+ aliases (review candidates)
    for e in entities:
        aliases = e.get("aliases") or []
        if len(aliases) >= 3:
            rows.append(
                {
                    "category": "review_many_aliases",
                    "metric": f"{e['canonical_name']} ({e.get('entity_type', '?')})",
                    "value": f"{len(aliases)} aliases: {', '.join(aliases[:6])}",
                }
            )

    # Evidence signal frequency across all edges
    signal_counts: Counter[str] = Counter()
    for e in edges:
        ev = e.get("evidence", [])
        if isinstance(ev, list):
            for sig in ev:
                signal_counts[sig] += 1
    for sig, cnt in signal_counts.most_common():
        rows.append({"category": "signal_frequency", "metric": sig, "value": cnt})

    path = output_dir / "concordance_summary.csv"
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["category", "metric", "value"])
        w.writeheader()
        w.writerows(rows)
    print(f"  {path.name}: {len(rows)} rows")


def main():
    parser = argparse.ArgumentParser(description="Dump concordance audit to CSV")
    parser.add_argument("--kubectl", action="store_true", default=True)
    parser.add_argument("--host", help="PostgreSQL host (disables kubectl)")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--dbname", default="knowledge_graph")
    parser.add_argument("--user", default="kg")
    parser.add_argument("--password", default="kg-homelab")
    parser.add_argument("--output", "-o", default=None)
    args = parser.parse_args()

    if args.output:
        output_dir = Path(args.output)
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = Path(f"concordance-audit-{ts}")
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.host:

        def query_fn(sql):
            return query_via_psycopg(sql, args.host, args.port, args.dbname, args.user, args.password)
    else:
        query_fn = query_via_kubectl

    print(f"Dumping concordance audit to {output_dir}/\n")

    entities, edges = dump_audit(query_fn, output_dir)
    dump_summary(entities, edges, output_dir)

    print(f"\nDone. Open {output_dir}/ to review.")
    print("  concordance_audit.csv   — one row per entity, all context inline")
    print("  concordance_summary.csv — stats, clusters, review flags")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Dump concordance audit data from PostgreSQL to CSV.

Produces 2 files:
  1. concordance_audit.csv — one flat row per entity with all context inline
  2. concordance_summary.csv — aggregate stats for quick review

No IDs, no cross-referencing. Every row tells the full story.

Usage:
    python scripts/dump_concordance.py                     # via kubectl (default)
    python scripts/dump_concordance.py --host localhost     # direct PG connection
    python scripts/dump_concordance.py -o /tmp/audit        # custom output dir
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

    # 1. Load all entities
    entities = query_fn("""
        SELECT canonical_id, canonical_name, entity_type, aliases,
               mention_count, source_code_locations
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

    # 3. Build edge lookup: entity_id → list of (partner_name, type, score, evidence)
    entity_by_id = {e["canonical_id"]: e for e in entities}

    # Map candidate_ids → canonical entity via source_candidate_ids
    candidate_to_canonical: dict[str, dict] = {}
    for ent in entities:
        for cid in ent.get("source_candidate_ids") or []:
            candidate_to_canonical[cid] = ent

    entity_edges: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        src_id, tgt_id = edge["source_entity_id"], edge["target_entity_id"]
        # Prefer denormalized names from PG; fall back to candidate→canonical mapping
        src_name = edge.get("source_name") or ""
        tgt_name = edge.get("target_name") or ""
        if not src_name:
            ce = candidate_to_canonical.get(src_id)
            src_name = ce["canonical_name"] if ce else src_id[:12]
        if not tgt_name:
            ce = candidate_to_canonical.get(tgt_id)
            tgt_name = ce["canonical_name"] if ce else tgt_id[:12]

        evidence = edge.get("evidence", [])
        ev_str = ", ".join(evidence) if isinstance(evidence, list) else str(evidence)
        src_loc = edge.get("source_code_location", "")
        tgt_loc = edge.get("target_code_location", "")

        edge_info = {
            "type": edge["alignment_type"],
            "score": edge["score"],
            "evidence": ev_str,
        }
        entity_edges[src_id].append({**edge_info, "partner": tgt_name, "partner_location": tgt_loc})
        entity_edges[tgt_id].append({**edge_info, "partner": src_name, "partner_location": src_loc})

    # 4. Build union-find for cluster grouping
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Resolve entity by canonical_id OR candidate_id
    def resolve_name(eid):
        ent = entity_by_id.get(eid)
        if ent:
            return ent
        ce = candidate_to_canonical.get(eid)
        return ce if ce else None

    for e in entities:
        find(e["canonical_id"])
    for edge in edges:
        if edge["alignment_type"] == "sameAs":
            # Map candidate_ids to canonical_ids if possible
            src_ce = candidate_to_canonical.get(edge["source_entity_id"])
            tgt_ce = candidate_to_canonical.get(edge["target_entity_id"])
            src_key = src_ce["canonical_id"] if src_ce else edge["source_entity_id"]
            tgt_key = tgt_ce["canonical_id"] if tgt_ce else edge["target_entity_id"]
            find(src_key)
            find(tgt_key)
            union(src_key, tgt_key)

    # Group clusters and pick primary (highest mention_count)
    clusters: dict[str, list[str]] = defaultdict(list)
    for eid in parent:
        clusters[find(eid)].append(eid)

    # Map entity_id → (cluster_label, cluster_size, is_primary)
    entity_cluster: dict[str, dict] = {}
    for _root, members in clusters.items():
        members_sorted = sorted(
            members,
            key=lambda mid: entity_by_id.get(mid, {}).get("mention_count", 0),
            reverse=True,
        )
        primary = entity_by_id.get(members_sorted[0], {})
        cluster_label = primary.get("canonical_name", "?")
        cluster_size = len(members)
        member_names = [entity_by_id.get(mid, {}).get("canonical_name", "?") for mid in members_sorted]
        for rank, mid in enumerate(members_sorted, 1):
            entity_cluster[mid] = {
                "cluster_label": cluster_label,
                "cluster_size": cluster_size,
                "is_primary": rank == 1,
                "cluster_members": "; ".join(
                    n for n in member_names if n != entity_by_id.get(mid, {}).get("canonical_name")
                ),
            }

    # 5. Build flat rows
    rows = []
    for ent in entities:
        eid = ent["canonical_id"]
        aliases = ent.get("aliases") or []
        sources = ent.get("source_code_locations") or []
        cluster = entity_cluster.get(eid, {})
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

        rows.append(
            {
                "entity_name": ent["canonical_name"],
                "entity_type": ent["entity_type"],
                "mention_count": ent["mention_count"],
                "aliases": "; ".join(aliases),
                "alias_count": len(aliases),
                "sources": "; ".join(sources),
                "source_count": len(sources),
                "cluster_label": cluster.get("cluster_label", ent["canonical_name"]),
                "cluster_size": cluster.get("cluster_size", 1),
                "is_cluster_primary": cluster.get("is_primary", True),
                "merged_with": cluster.get("cluster_members", ""),
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
        "cluster_label",
        "cluster_size",
        "is_cluster_primary",
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
    return entities, edges, clusters


def dump_summary(entities, edges, clusters, output_dir: Path):
    """Summary CSV — one row per stat, scannable at a glance."""
    total = len(entities)
    sameas_edges = [e for e in edges if e.get("alignment_type") == "sameAs"]
    possible_edges = [e for e in edges if e.get("alignment_type") == "possibleSameAs"]
    multi_clusters = {k: v for k, v in clusters.items() if len(v) > 1}
    singletons = total - sum(len(v) for v in multi_clusters.values())
    type_counts = Counter(e.get("entity_type", "?") for e in entities)
    entity_by_id = {e["canonical_id"]: e for e in entities}

    rows = []

    # Overview
    rows.append({"category": "overview", "metric": "total_entities", "value": total})
    rows.append({"category": "overview", "metric": "total_alignment_edges", "value": len(edges)})
    rows.append({"category": "overview", "metric": "sameas_edges", "value": len(sameas_edges)})
    rows.append({"category": "overview", "metric": "possible_sameas_edges", "value": len(possible_edges)})
    rows.append({"category": "overview", "metric": "merged_clusters", "value": len(multi_clusters)})
    rows.append({"category": "overview", "metric": "singletons", "value": singletons})
    rows.append(
        {
            "category": "overview",
            "metric": "max_cluster_size",
            "value": max((len(v) for v in clusters.values()), default=0),
        }
    )
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

    # Merged clusters detail
    for _root, members in sorted(multi_clusters.items(), key=lambda x: -len(x[1])):
        members_sorted = sorted(members, key=lambda m: entity_by_id.get(m, {}).get("mention_count", 0), reverse=True)
        names = [entity_by_id.get(m, {}).get("canonical_name", "?") for m in members_sorted]
        total_mentions = sum(entity_by_id.get(m, {}).get("mention_count", 0) for m in members)
        rows.append(
            {
                "category": "merged_clusters",
                "metric": f"{names[0]} (+{len(names) - 1} merged)",
                "value": f"{total_mentions} mentions: {', '.join(names)}",
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

    entities, edges, clusters = dump_audit(query_fn, output_dir)
    dump_summary(entities, edges, clusters, output_dir)

    print(f"\nDone. Open {output_dir}/ to review.")
    print("  concordance_audit.csv   — one row per entity, all context inline")
    print("  concordance_summary.csv — stats, clusters, review flags")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""Migrate GT files from chunk-keyed to doc-char-range-keyed format.

Phase 0 of the v3 chunking epic (CD-9wno).

Usage
-----
    # Dry run — print what would change (no writes)
    python scripts/migrate_gt_to_doc_anchored.py --dry-run

    # Migrate all GT files against MinIO (Tilt must be running)
    python scripts/migrate_gt_to_doc_anchored.py

    # Migrate a specific GT file
    python scripts/migrate_gt_to_doc_anchored.py --name active

    # Skip backup (only if you know what you're doing)
    python scripts/migrate_gt_to_doc_anchored.py --no-backup

IMPORTANT
---------
DO NOT run this against live S3 data without first confirming MinIO is
accessible (i.e. ``mc ls minio/dagster/bench/ground-truth/`` lists files)
and that Tilt (or the cluster port-forward) is up.

Originals are backed up to
``s3://dagster/bench/ground-truth/_backup_pre_v3/<name>.json`` before
overwriting.  The script is idempotent: files that already have
``doc_char_start`` on their first chunk entry are skipped.

Silver chunk lookup
-------------------
For each chunk_id in the GT the script looks up the silver chunk at::

    s3://dagster/silver/<code_loc>/<group>/<asset>/<doc_id>/data.jsonl

to pull ``metadata.chunk_char_offset``.  The S3BenchmarkStore client
is used for all reads/writes so the same MinIO credentials apply.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure project root on PYTHONPATH so dagster_io is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "libs" / "dagster-io" / "src"))

from dagster_io.bench.store import S3BenchmarkStore
from dagster_io.s3_client import S3Client

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The silver layer layout for chunks:
#   silver/{code_location}/{group}/media_chunks/{doc_id}/data.jsonl
# We search all known code locations / groups.
SILVER_CHUNK_PATHS = [
    "silver/media_ingest/media/media_chunks/{doc_id}/data.jsonl",
    "silver/congress_data/congress/congress_chunks/{doc_id}/data.jsonl",
    "silver/open_leaks/leaks/leak_chunks/{doc_id}/data.jsonl",
]


def _build_s3_client() -> S3Client:
    return S3Client(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
        bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
    )


# ---------------------------------------------------------------------------
# Silver chunk loader
# ---------------------------------------------------------------------------


def _load_silver_chunks(client: S3Client, doc_id: str) -> list[dict]:
    """Try each known silver path pattern for this doc_id."""
    for pattern in SILVER_CHUNK_PATHS:
        key = pattern.format(doc_id=doc_id)
        try:
            raw = client.get_object(key)
            rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
            if rows:
                return rows
        except Exception:
            continue
    return []


def _build_chunk_offset_map(client: S3Client, doc_ids: set[str]) -> dict[str, dict]:
    """Load silver chunks for all doc_ids and return chunk_id → metadata."""
    offset_map: dict[str, dict] = {}
    for doc_id in sorted(doc_ids):
        chunks = _load_silver_chunks(client, doc_id)
        for chunk in chunks:
            cid = chunk.get("chunk_id") or ""
            if not cid:
                continue
            meta = chunk.get("metadata") or {}
            text = chunk.get("text") or ""
            offset_map[cid] = {
                "chunk_char_offset": meta.get("chunk_char_offset"),
                "chunk_text_len": len(text),
                "chunk_text": text,
                "doc_id": doc_id,
            }
    return offset_map


# ---------------------------------------------------------------------------
# Migration logic (pure function — no I/O)
# ---------------------------------------------------------------------------


def migrate_gt_doc(gt_doc: dict, offset_map: dict[str, dict]) -> tuple[dict, dict]:
    """Migrate a single GT document from chunk-keyed to doc-anchored format.

    Returns ``(migrated_doc, stats)`` where stats contains counts of
    translated / skipped / unchanged entries.
    """
    stats = {"translated": 0, "skipped_no_offset": 0, "skipped_no_chunk": 0, "already_migrated": 0}

    new_chunks = []
    for old_chunk in gt_doc.get("chunks", []):
        # Already migrated — preserve and skip
        if "doc_char_start" in old_chunk and "doc_id" in old_chunk:
            stats["already_migrated"] += 1
            new_chunks.append(old_chunk)
            continue

        cid = old_chunk.get("chunk_id") or old_chunk.get("legacy_chunk_id") or ""
        if not cid:
            stats["skipped_no_chunk"] += 1
            new_chunks.append(old_chunk)
            continue

        chunk_info = offset_map.get(cid)
        if chunk_info is None:
            stats["skipped_no_chunk"] += 1
            # Preserve with legacy_chunk_id but no doc-frame fields
            new_chunks.append(
                {
                    **old_chunk,
                    "legacy_chunk_id": cid,
                    "doc_id": None,
                    "doc_char_start": None,
                    "doc_char_end": None,
                }
            )
            continue

        offset = chunk_info.get("chunk_char_offset")
        if offset is None:
            stats["skipped_no_offset"] += 1
            new_chunks.append(
                {
                    **old_chunk,
                    "legacy_chunk_id": cid,
                    "doc_id": chunk_info.get("doc_id") or cid.rsplit(":chunk-", 1)[0],
                    "doc_char_start": None,
                    "doc_char_end": None,
                }
            )
            continue

        chunk_text = old_chunk.get("text") or chunk_info.get("chunk_text") or ""
        doc_char_start = offset
        doc_char_end = offset + len(chunk_text)
        doc_id = chunk_info.get("doc_id") or cid.rsplit(":chunk-", 1)[0]

        # Translate per-mention spans
        new_mentions = []
        for m in old_chunk.get("mentions", []):
            s, e = m.get("span_start"), m.get("span_end")
            if s is not None and e is not None:
                doc_m_start = offset + s
                doc_m_end = offset + e
            else:
                doc_m_start, doc_m_end = None, None
            new_mentions.append(
                {
                    **{k: v for k, v in m.items() if k not in ("span_start", "span_end")},
                    "doc_char_start": doc_m_start,
                    "doc_char_end": doc_m_end,
                }
            )

        new_chunks.append(
            {
                "doc_id": doc_id,
                "doc_char_start": doc_char_start,
                "doc_char_end": doc_char_end,
                "text_excerpt": chunk_text,
                "legacy_chunk_id": cid,
                "mentions": new_mentions,
                "propositions": old_chunk.get("propositions", []),
                "reviewed": old_chunk.get("reviewed"),
            }
        )
        stats["translated"] += 1

    migrated = {
        **{k: v for k, v in gt_doc.items() if k != "chunks"},
        "chunks": new_chunks,
    }
    return migrated, stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate GT files from chunk-keyed to doc-char-range-keyed format (CD-9wno).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--name", help="Only migrate this GT file (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without writing")
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip writing backup to _backup_pre_v3/ prefix",
    )
    args = parser.parse_args()

    store = S3BenchmarkStore()
    client = store.client

    # Discover GT files to process
    names = store.list_ground_truths()
    if not names:
        print("No GT files found under bench/ground-truth/. Nothing to do.")
        return 0

    if args.name:
        if args.name not in names:
            print(f"ERROR: GT '{args.name}' not found. Available: {', '.join(names)}")
            return 1
        names = [args.name]

    # Filter out backup entries
    names = [n for n in names if not n.startswith("_backup_pre_v3")]

    print(f"GT files to process: {', '.join(names)}")
    print()

    total_translated = 0
    total_skipped = 0
    total_already = 0
    errors: list[str] = []

    for name in names:
        print(f"  [{name}] Loading...")
        gt_doc = store.load_ground_truth(name)
        if gt_doc is None:
            print(f"  [{name}] ERROR: could not load. Skipping.")
            errors.append(f"{name}: load failed")
            continue

        # Detect idempotency: if first chunk has doc_char_start already set
        first_chunk = (gt_doc.get("chunks") or [None])[0]
        if first_chunk and "doc_char_start" in first_chunk and first_chunk.get("doc_char_start") is not None:
            print(f"  [{name}] Already in doc-anchored format. Skipping.")
            total_already += 1
            continue

        # Collect doc_ids referenced in this GT
        doc_ids: set[str] = set()
        for chunk in gt_doc.get("chunks", []):
            cid = chunk.get("chunk_id") or chunk.get("legacy_chunk_id") or ""
            if cid and ":chunk-" in cid:
                doc_ids.add(cid.rsplit(":chunk-", 1)[0])

        print(f"  [{name}] {len(gt_doc.get('chunks', []))} chunks, {len(doc_ids)} doc_ids. Loading silver chunks...")

        offset_map = _build_chunk_offset_map(client, doc_ids)
        print(f"  [{name}] Loaded {len(offset_map)} chunk offsets from silver layer.")

        migrated_doc, stats = migrate_gt_doc(gt_doc, offset_map)

        print(
            f"  [{name}] Migration stats: "
            f"translated={stats['translated']} "
            f"skipped_no_offset={stats['skipped_no_offset']} "
            f"skipped_no_chunk={stats['skipped_no_chunk']} "
            f"already_migrated={stats['already_migrated']}"
        )

        total_translated += stats["translated"]
        total_skipped += stats["skipped_no_offset"] + stats["skipped_no_chunk"]
        total_already += stats["already_migrated"]

        if args.dry_run:
            print(f"  [{name}] DRY RUN — no writes.")
            continue

        # Backup original
        if not args.no_backup:
            backup_key = f"{store.ground_truth_prefix}/_backup_pre_v3/{name}.json"
            try:
                original_bytes = json.dumps(gt_doc, indent=2, default=str).encode("utf-8")
                client.put_object(backup_key, original_bytes)
                print(f"  [{name}] Backed up to {backup_key}")
            except Exception as e:
                print(f"  [{name}] WARNING: backup failed ({e}). Proceeding anyway.")

        # Write migrated GT
        key = store.save_ground_truth(name, migrated_doc)
        print(f"  [{name}] Written to {key}")
        print()

    print("=" * 60)
    print("Migration complete:")
    print(f"  Translated:      {total_translated}")
    print(f"  Skipped:         {total_skipped}")
    print(f"  Already migrated: {total_already}")
    if errors:
        print(f"  Errors:          {len(errors)}")
        for e in errors:
            print(f"    - {e}")
        return 1

    if args.dry_run:
        print("(DRY RUN — no data was written)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

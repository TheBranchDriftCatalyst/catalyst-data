#!/usr/bin/env python3
"""Local-dev seed — populate MinIO with a small representative slice of each domain.

Drives Dagster's own ``materialize(...)`` against the production assets,
constrained by partition count. Not an ML seed: this just exists to give
a fresh ``tilt up`` something real to render in the SPA. The ML
GT-candidate sampler lives at ``scripts/benchmark/sample_gt_candidates.py``.

Usage::

    python scripts/dev/seed_local.py                         # all 3 domains, 5 each
    python scripts/dev/seed_local.py --domain media          # one domain
    python scripts/dev/seed_local.py --domain all --limit 3  # smaller slice
    python scripts/dev/seed_local.py --domain all --regen    # nuke S3 prefix first

Per-domain behavior:

- **media-ingest** — reads ``packages/media-ingest/tests/fixtures/audio_manifest.yaml``,
  picks ``--limit`` doc_ids that have a cached diarization at
  ``.test-output/media-ingest/pipeline-cache/<doc_id>/1_diarization.json``.
  Pre-seeds ``media_segment_merge`` upstream (same pattern as
  ``test_chunks_cpu.py``), then materializes ``media_chunks`` per doc.
- **congress-data** — reads ``bill_manifest.yaml``, picks the first
  ``--limit`` bills, materializes ``[bill_documents, congress_chunks]``
  per bill_id partition. Skips cleanly if ``CONGRESS_API_KEY`` is unset.
- **open-leaks** — env-swaps ``CABLEGATE_CSV_URL`` to the bundled
  ``packages/open-leaks/tests/fixtures/cablegate_sample.csv`` (5 cables)
  and materializes ``[wikileaks_cables, leak_documents, leak_chunks]``.
  No partitions involved; the CSV size IS the limit.

Resources dict per domain mirrors each code-location's ``Definitions(resources={...})``;
``select_io_managers()`` resolves to ``MinioIOManager`` because ``task dev``
sets ``DAGSTER_S3_ENDPOINT_URL`` and the seed inherits that env.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "media-ingest" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "congress-data" / "src"))
sys.path.insert(0, str(ROOT / "packages" / "open-leaks" / "src"))
sys.path.insert(0, str(ROOT / "libs" / "dagster-io" / "src"))

# MinioIOManager reads endpoint_url as a class-attribute default at
# module-import time — so we must set the env vars BEFORE any dagster_io
# import below. We FORCE the localhost endpoint here (not setdefault)
# because this is a dev-only seed: the shell-exported defaults often
# point at the cluster-internal URL (.envrc.cluster sets
# DAGSTER_S3_ENDPOINT_URL=http://minio.minio.svc.cluster.local) which
# can't resolve off-cluster. To target a non-localhost MinIO, set the
# CATALYST_SEED_S3_ENDPOINT env var as an explicit override.
_endpoint_override = os.environ.get("CATALYST_SEED_S3_ENDPOINT")
os.environ["DAGSTER_S3_ENDPOINT_URL"] = _endpoint_override or "http://localhost:9000"
os.environ["DAGSTER_S3_ACCESS_KEY"] = os.environ.get("CATALYST_SEED_S3_ACCESS_KEY", "minio")
os.environ["DAGSTER_S3_SECRET_KEY"] = os.environ.get("CATALYST_SEED_S3_SECRET_KEY", "minio123")
os.environ["DAGSTER_S3_BUCKET"] = os.environ.get("CATALYST_SEED_S3_BUCKET", "dagster")


def _media_fixtures() -> Path:
    return ROOT / "packages" / "media-ingest" / "tests" / "fixtures"


def _congress_fixtures() -> Path:
    return ROOT / "packages" / "congress-data" / "tests" / "fixtures"


def _leaks_fixtures() -> Path:
    return ROOT / "packages" / "open-leaks" / "tests" / "fixtures"


def _print_header(name: str) -> None:
    print(f"\n{'=' * 70}\n  seed:{name}\n{'=' * 70}")


def _maybe_regen(prefix: str) -> None:
    """``--regen`` clears an S3 prefix so seeds are deterministic. Best-effort."""
    try:
        from dagster_io.s3_client import S3Client

        client = S3Client(
            endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
            access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
            secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
            bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
        )
        keys = client.list_all_objects(prefix)
        if keys:
            deleted, _ = client.delete_objects(keys)
            print(f"  --regen: cleared {deleted} keys under s3://{client.bucket}/{prefix}")
    except Exception as exc:
        print(f"  --regen: skipped ({exc})")


# ─────────────────────────────────────────────────────────────────────────────
# media-ingest
# ─────────────────────────────────────────────────────────────────────────────


def _seed_media(limit: int, with_gold: bool, regen: bool) -> dict:
    _print_header("media")
    # FORCE — not setdefault — because when --domain all runs, the previous
    # domain leaves its CODE_LOCATION set and path_builder uses it for the
    # next domain's S3 keys (we'd see e.g. bronze/media_ingest/bill/... for
    # congress assets — exactly the bug we hit).
    os.environ["DAGSTER_CODE_LOCATION"] = "media_ingest"

    import yaml
    from dagster import AssetKey, DagsterInstance, SourceAsset, materialize
    from media_ingest.assets.chunks import media_chunks
    from media_ingest.partitions import media_partitions

    from dagster_io import ChunkingResource, EmbeddingResource, select_io_managers
    from dagster_io.s3_client import S3Client

    if regen:
        _maybe_regen("silver/media_ingest/")
        _maybe_regen("gold/media_ingest/")

    manifest = _media_fixtures() / "audio_manifest.yaml"
    if not manifest.exists():
        print(f"  ⊘ {manifest} missing — nothing to seed")
        return {"materialized": 0, "skipped": 0, "errors": 0}

    videos = (yaml.safe_load(manifest.read_text()) or {}).get("videos", [])

    # Filter to docs whose media_segment_merge has been materialized to S3
    # (run `task bench:fixtures:regen` to populate it via the Dagster
    # transcription + diarization assets — slow on first run, idempotent
    # after). Same lookup the asset would do via its IO manager.
    s3 = S3Client(
        endpoint_url=os.environ["DAGSTER_S3_ENDPOINT_URL"],
        access_key=os.environ["DAGSTER_S3_ACCESS_KEY"],
        secret_key=os.environ["DAGSTER_S3_SECRET_KEY"],
        bucket=os.environ["DAGSTER_S3_BUCKET"],
    )

    def _segment_merge_in_s3(doc_id: str) -> bool:
        key = f"gold/media_ingest/media/media_segment_merge/{doc_id}/_metadata.json"
        return s3.head_object(key) is not None

    available: list[dict] = []
    for entry in videos:
        if _segment_merge_in_s3(entry["doc_id"]):
            available.append(entry)
        if len(available) >= limit:
            break

    skipped = sum(1 for v in videos[: max(limit, len(videos))] if v not in available)
    if not available:
        print(
            "  ⊘ no media_segment_merge in S3 — run `task bench:fixtures:regen` first "
            "(materializes media_transcriptions + media_diarization + media_segment_merge "
            "via Dagster, ~slow on first run)"
        )
        return {"materialized": 0, "skipped": skipped, "errors": 0}

    # Resources mirror media_ingest/__init__.py
    resources = {
        **{
            k: v
            for k, v in select_io_managers(default_local_dir=".test-output/media-ingest").items()
            if k in ("io_manager", "optional_io_manager")
        },
        "chunking": ChunkingResource(prepend_title=False),
        "embedding": EmbeddingResource(),
        "embeddings": EmbeddingResource(),
        "embedding_seed": EmbeddingResource(),
    }
    # EmbeddingResource normally builds its langchain client inside
    # setup_for_execution; outside Dagster's lifecycle (this materialize)
    # we invoke it manually.
    for key in ("embedding", "embeddings", "embedding_seed"):
        resources[key].setup_for_execution(None)

    # Pre-seed media_segment_merge per doc_id, same pattern as
    # test_chunks_cpu.py:62–100. Path matches MinioIOManager's medallion shape.
    media_segment_merge_source = SourceAsset(
        key=AssetKey(["media_segment_merge"]),
        partitions_def=media_partitions,
        metadata={"layer": "gold"},
    )

    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions("media_document", [v["doc_id"] for v in available])

    # Pre-write segment_merge S3 object so MinioIOManager.load_input finds it.
    from dagster_io.s3_client import S3Client

    s3 = S3Client(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
        bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
    )

    # ── media_documents seed ────────────────────────────────────────────────
    # The Media tab in viewer-ui reads silver/media_ingest/media/media_documents/data.jsonl.
    # The transcription/diarization assets look up the doc by `id == partition_key`,
    # so the doc.id must equal the manifest's doc_id (NOT the hashed
    # _make_document_id form). has_audio=True keeps the transcription asset
    # from short-circuiting on its no-audio fast-path.
    from media_ingest.assets.documents import MediaDocument

    fixture_dir = _media_fixtures()
    docs = []
    for entry in available:
        mp4 = fixture_dir / entry["file"]
        if not mp4.exists():
            continue
        doc = MediaDocument(
            id=entry["doc_id"],
            title=entry.get("title") or mp4.stem,
            source_path=str(mp4.resolve()),
            source="metube",
            metadata={
                "extension": mp4.suffix,
                "size_bytes": mp4.stat().st_size,
                "has_audio": True,
                "doc_id": entry["doc_id"],
            },
        )
        docs.append(doc.model_dump(mode="json"))

    docs_payload = "\n".join(json.dumps(d, default=str) for d in docs) + ("\n" if docs else "")
    docs_prefix = "silver/media_ingest/media/media_documents"
    docs_meta = {
        "format": "jsonl",
        "size_bytes": len(docs_payload.encode("utf-8")),
        "row_count": len(docs),
        "code_location": "media_ingest",
        "asset_key": "media_documents",
        "partition": None,
        "layer": "silver",
        "upstream_assets": [],
        "stub": "dev-seed",
    }
    s3.put_object(f"{docs_prefix}/_metadata.json", json.dumps(docs_meta).encode("utf-8"))
    s3.put_object(f"{docs_prefix}/data.jsonl", docs_payload.encode("utf-8"))
    print(f"  ✓ media_documents: {len(docs)} doc rows → s3://{s3.bucket}/{docs_prefix}/data.jsonl")

    materialized = 0
    errors = 0
    for entry in available:
        doc_id = entry["doc_id"]
        try:
            # media_chunks consumes media_segment_merge as upstream — declared
            # as a SourceAsset since the regen step (task bench:fixtures:regen)
            # already wrote it to S3. Dagster's IO manager loads it from there.
            result = materialize(
                [media_segment_merge_source, media_chunks],
                resources=resources,
                partition_key=doc_id,
                instance=instance,
            )
            if result.success:
                materialized += 1
                print(f"  ✓ {doc_id}: media_chunks materialized")
            else:
                errors += 1
                print(f"  ✗ {doc_id}: materialize returned failure")
        except Exception as exc:
            errors += 1
            print(f"  ✗ {doc_id}: {type(exc).__name__}: {exc}")

    if with_gold:
        print("  --with-gold: media gold-layer pass not yet implemented (TODO)")

    return {"materialized": materialized, "skipped": skipped, "errors": errors}


# ─────────────────────────────────────────────────────────────────────────────
# congress-data
# ─────────────────────────────────────────────────────────────────────────────


def _seed_congress(limit: int, with_gold: bool, regen: bool) -> dict:
    _print_header("congress")
    os.environ["DAGSTER_CODE_LOCATION"] = "congress_data"

    if not os.environ.get("CONGRESS_API_KEY"):
        print("  ⊘ skipped: CONGRESS_API_KEY not set (live API call required)")
        return {"materialized": 0, "skipped": limit, "errors": 0}

    import yaml
    from congress_data.assets.bill_tail import (
        bill_actions,
        bill_chunks,
        bill_cosponsors,
        bill_detail,
        bill_document,
        bill_full_text,
        bill_text_versions,
    )
    from dagster import DagsterInstance, materialize

    from dagster_io import ChunkingResource, EmbeddingResource, select_io_managers

    if regen:
        _maybe_regen("silver/congress_data/")
        _maybe_regen("bronze/congress_data/")
        _maybe_regen("gold/congress_data/")

    manifest = _congress_fixtures() / "bill_manifest.yaml"
    if not manifest.exists():
        print(f"  ⊘ {manifest} missing — nothing to seed")
        return {"materialized": 0, "skipped": 0, "errors": 0}

    raw = yaml.safe_load(manifest.read_text()) or {}
    # Prefer seed_subset (hand-curated variety pick); fall back to first N
    # of the broader bills list. Both keys living in the same manifest keeps
    # ownership in one place — the bench corpus and the dev seed share a
    # single source of truth.
    seed_subset = raw.get("seed_subset") or []
    bills = raw.get("bills", []) or []
    if seed_subset:
        selected = seed_subset[:limit]
        print(f"  using seed_subset: {len(selected)} hand-picked variety bills")
    else:
        selected = bills[:limit]
        print(f"  no seed_subset key, using first {len(selected)} of {len(bills)} bills")
    if not selected:
        print("  ⊘ bill_manifest.yaml has no bills")
        return {"materialized": 0, "skipped": 0, "errors": 0}

    resources = {
        **select_io_managers(default_local_dir=".test-output/congress-data"),
        "chunking": ChunkingResource(),
        "embeddings": EmbeddingResource(),
        "embedding_seed": EmbeddingResource(),
    }
    for key in ("embeddings", "embedding_seed"):
        resources[key].setup_for_execution(None)

    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions("congress_bill", selected)

    materialized = 0
    errors = 0
    for bill_id in selected:
        try:
            result = materialize(
                [
                    bill_detail,
                    bill_actions,
                    bill_cosponsors,
                    bill_text_versions,
                    bill_full_text,
                    bill_document,
                    bill_chunks,
                ],
                resources=resources,
                partition_key=bill_id,
                instance=instance,
            )
            if result.success:
                materialized += 1
                print(f"  ✓ {bill_id}: bill_document + bill_chunks materialized")
            else:
                errors += 1
                print(f"  ✗ {bill_id}: materialize returned failure")
        except Exception as exc:
            errors += 1
            print(f"  ✗ {bill_id}: {type(exc).__name__}: {exc}")

    if with_gold:
        print("  --with-gold: congress gold-layer pass not yet implemented (TODO)")

    return {"materialized": materialized, "skipped": len(bills) - len(selected), "errors": errors}


# ─────────────────────────────────────────────────────────────────────────────
# open-leaks
# ─────────────────────────────────────────────────────────────────────────────


def _seed_leaks(limit: int, with_gold: bool, regen: bool) -> dict:
    _print_header("leaks")
    os.environ["DAGSTER_CODE_LOCATION"] = "open_leaks"

    sample = _leaks_fixtures() / "cablegate_sample.csv"
    if not sample.exists():
        print(f"  ⊘ {sample} missing — nothing to seed")
        return {"materialized": 0, "skipped": 0, "errors": 0}

    # _download_file uses httpx and would bomb on a file:// URL, but it
    # *does* honor a pre-cached file at <cache_dir>/cables.csv (size-match
    # short-circuit). Point the cache_dir at a dev-only temp location and
    # pre-copy the sample so the asset reads it without ever touching the
    # network. The CABLEGATE_CSV_URL env var still gets set so the source_url
    # metadata on the resulting cables reflects the seed origin.
    seed_cache = Path(os.environ.get("TEST_OUTPUT_ROOT", str(ROOT / ".test-output"))) / "open-leaks" / "seed-cache"
    seed_cache.mkdir(parents=True, exist_ok=True)
    import shutil

    shutil.copy2(sample, seed_cache / "cables.csv")
    os.environ["OPEN_LEAKS_CACHE_DIR"] = str(seed_cache)
    os.environ["CABLEGATE_CSV_URL"] = sample.absolute().as_uri()

    from dagster import AssetKey, DagsterInstance, SourceAsset, materialize
    from open_leaks.assets.chunks import leak_chunks
    from open_leaks.assets.documents import leak_documents
    from open_leaks.assets.extraction import wikileaks_cables

    from dagster_io import ChunkingResource, EmbeddingResource, select_io_managers

    # leak_documents reads icij_offshore_entities + epstein_court_docs in
    # addition to wikileaks_cables; we only have a sample CSV for cables, so
    # pre-write empty lists for the other two bronze sources at their
    # medallion paths and declare them as SourceAssets so Dagster doesn't try
    # to materialize them (which would pull 1.5GB+ ICIJ zip + live API).
    from dagster_io.s3_client import S3Client

    s3 = S3Client(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
        bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
    )
    icij_offshore_entities_src = SourceAsset(
        key=AssetKey(["icij_offshore_entities"]),
        metadata={"layer": "bronze"},
    )
    epstein_court_docs_src = SourceAsset(
        key=AssetKey(["epstein_court_docs"]),
        metadata={"layer": "bronze"},
    )

    if regen:
        _maybe_regen("silver/open_leaks/")
        _maybe_regen("bronze/open_leaks/")
        _maybe_regen("gold/open_leaks/")

    # Pre-seed empty bronze stubs AFTER the regen sweep so they survive.
    # MinioIOManager.load_input reads _metadata.json first to determine
    # format, then data.<ext>. Both files have to exist or load_input
    # raises NoSuchKey. Group derived from asset name's first
    # underscore-segment (path_builder convention).
    for prefix, asset_name in (
        ("bronze/open_leaks/icij/icij_offshore_entities", "icij_offshore_entities"),
        ("bronze/open_leaks/icij/icij_offshore_relationships", "icij_offshore_relationships"),
        ("bronze/open_leaks/epstein/epstein_court_docs", "epstein_court_docs"),
    ):
        meta = {
            "format": "jsonl",
            "size_bytes": 0,
            "row_count": 0,
            "code_location": "open_leaks",
            "asset_key": asset_name,
            "partition": None,
            "layer": "bronze",
            "upstream_assets": [],
            "stub": "dev-seed",
        }
        s3.put_object(f"{prefix}/_metadata.json", json.dumps(meta).encode("utf-8"))
        s3.put_object(f"{prefix}/data.jsonl", b"")

    resources = {
        **{
            k: v
            for k, v in select_io_managers(default_local_dir=".test-output/open-leaks").items()
            if k == "io_manager"
        },
        "chunking": ChunkingResource(),
        "embeddings": EmbeddingResource(),
        "embedding_seed": EmbeddingResource(),
    }
    for key in ("embeddings", "embedding_seed"):
        resources[key].setup_for_execution(None)

    instance = DagsterInstance.ephemeral()

    try:
        result = materialize(
            [
                wikileaks_cables,
                icij_offshore_entities_src,
                epstein_court_docs_src,
                leak_documents,
                leak_chunks,
            ],
            resources=resources,
            instance=instance,
        )
        if result.success:
            print(f"  ✓ wikileaks_cables + leak_documents + leak_chunks materialized from {sample.name}")
            return {"materialized": 1, "skipped": 0, "errors": 0}
        print("  ✗ materialize returned failure")
        return {"materialized": 0, "skipped": 0, "errors": 1}
    except Exception as exc:
        print(f"  ✗ {type(exc).__name__}: {exc}")
        return {"materialized": 0, "skipped": 0, "errors": 1}
    finally:
        if with_gold:
            print("  --with-gold: leaks gold-layer pass not yet implemented (TODO)")


# ─────────────────────────────────────────────────────────────────────────────
# entry point
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--domain",
        choices=["media", "congress", "leaks", "all"],
        default="all",
        help="Which domain to seed (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max partition count for media + congress (default: 5). Ignored for leaks.",
    )
    parser.add_argument(
        "--with-gold",
        action="store_true",
        help="Also run gold-layer extraction (mentions/assertions). Requires LLM_API_KEY.",
    )
    parser.add_argument(
        "--regen",
        action="store_true",
        help="Clear the domain's S3 prefix before seeding for deterministic re-runs.",
    )
    args = parser.parse_args()

    if args.with_gold and not os.environ.get("LLM_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: --with-gold requires LLM_API_KEY or OPENAI_API_KEY", file=sys.stderr)
        return 2

    summary: dict[str, dict] = {}
    if args.domain in ("media", "all"):
        summary["media"] = _seed_media(args.limit, args.with_gold, args.regen)
    if args.domain in ("congress", "all"):
        summary["congress"] = _seed_congress(args.limit, args.with_gold, args.regen)
    if args.domain in ("leaks", "all"):
        summary["leaks"] = _seed_leaks(args.limit, args.with_gold, args.regen)

    print(f"\n{'═' * 70}")
    parts = []
    for name, s in summary.items():
        m, sk, e = s["materialized"], s["skipped"], s["errors"]
        parts.append(f"{name}: {m} ok · {sk} skipped · {e} errors")
    print("  " + " · ".join(parts))
    print(f"{'═' * 70}\n")

    return 1 if any(s["errors"] for s in summary.values()) else 0


if __name__ == "__main__":
    sys.exit(main())

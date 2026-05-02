#!/usr/bin/env python3
"""Regenerate per-video audio fixtures via Dagster materialize.

Drives ``dagster.materialize`` against the production
``media_transcriptions`` + ``media_diarization`` + ``media_segment_merge``
assets — same code path as prod, output lands in S3 (the local
Tilt-managed MinIO container in dev) at the canonical medallion paths:

    s3://dagster/gold/media_ingest/media/media_transcriptions/<doc_id>/data.json
    s3://dagster/gold/media_ingest/media/media_diarization/<doc_id>/data.json
    s3://dagster/gold/media_ingest/media/media_segment_merge/<doc_id>/data.json

Reads ``packages/media-ingest/tests/fixtures/audio_manifest.yaml`` for the
list of videos. Pre-seeds ``media_documents`` from the manifest + on-disk
mp4 sizes (no NFS scanner required) and declares it as a SourceAsset.

Usage::

    HF_TOKEN=hf_xxx task bench:fixtures:regen
    HF_TOKEN=hf_xxx WHISPER_BACKEND=mlx-whisper task bench:fixtures:regen
    HF_TOKEN=hf_xxx python scripts/fixtures/regen_audio_fixtures.py --force
    HF_TOKEN=hf_xxx python scripts/fixtures/regen_audio_fixtures.py --only demo-video,inside-the-aipac-pipeline

Cache check: re-materializing is unconditional when called via this
script (Dagster's ``materialize`` always re-runs the selected assets).
``--only`` narrows the partition list, ``--skip-diarization`` runs
transcription only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "media-ingest" / "src"))
sys.path.insert(0, str(ROOT / "libs" / "dagster-io" / "src"))

# Force the local MinIO endpoint before any dagster_io import — same
# pattern as scripts/dev/seed_local.py. CATALYST_SEED_S3_ENDPOINT is the
# explicit override for non-default targets.
_endpoint_override = os.environ.get("CATALYST_SEED_S3_ENDPOINT")
os.environ["DAGSTER_S3_ENDPOINT_URL"] = _endpoint_override or "http://localhost:9000"
os.environ["DAGSTER_S3_ACCESS_KEY"] = os.environ.get("CATALYST_SEED_S3_ACCESS_KEY", "minio")
os.environ["DAGSTER_S3_SECRET_KEY"] = os.environ.get("CATALYST_SEED_S3_SECRET_KEY", "minio123")
os.environ["DAGSTER_S3_BUCKET"] = os.environ.get("CATALYST_SEED_S3_BUCKET", "dagster")
os.environ["DAGSTER_CODE_LOCATION"] = "media_ingest"

# Whisper + pyannote model caches default to /data/... (the cluster NFS path);
# redirect to a local writable dir so the assets don't try to mkdir under /data.
_local_models = ROOT / ".test-output" / "media-ingest" / "model-cache"
_local_models.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("WHISPER_MODEL_CACHE", str(_local_models / "whisper"))
os.environ.setdefault("HF_HOME", str(_local_models / "hf"))
# MediaIngestConfig defaults whisper_backend to "openvino" (Intel GPU, k8s prod).
# Dev laptops don't have openvino_genai installed and openvino doesn't have an
# Apple Silicon path. Default to mlx-whisper on Darwin, faster-whisper everywhere
# else — both work CPU-only and don't require the openvino package.
import platform  # noqa: E402

if "WHISPER_BACKEND" not in os.environ:
    os.environ["WHISPER_BACKEND"] = "mlx-whisper" if platform.system() == "Darwin" else "faster-whisper"

import yaml  # noqa: E402

FIXTURE_DIR = ROOT / "packages" / "media-ingest" / "tests" / "fixtures"
MANIFEST = FIXTURE_DIR / "audio_manifest.yaml"


def _human(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60)}s"


def _seed_media_documents_to_s3() -> int:
    """Pre-seed silver/media_ingest/media/media_documents/data.jsonl from
    the manifest + on-disk mp4 paths. Builds MediaDocument rows directly
    instead of going through ``_file_to_document`` because the asset's
    partition lookup matches ``doc.id == partition_key`` — the partition
    key is the manifest's ``doc_id``, not the hashed
    ``_make_document_id`` output. We also need ``metadata.has_audio=True``
    so the transcription asset doesn't short-circuit on the no-audio
    fast-path. Returns count of docs written.
    """
    from media_ingest.assets.documents import MediaDocument

    from dagster_io.s3_client import S3Client

    s3 = S3Client(
        endpoint_url=os.environ["DAGSTER_S3_ENDPOINT_URL"],
        access_key=os.environ["DAGSTER_S3_ACCESS_KEY"],
        secret_key=os.environ["DAGSTER_S3_SECRET_KEY"],
        bucket=os.environ["DAGSTER_S3_BUCKET"],
    )

    videos = (yaml.safe_load(MANIFEST.read_text()) or {}).get("videos", [])
    docs = []
    for entry in videos:
        mp4 = FIXTURE_DIR / entry["file"]
        if not mp4.exists():
            continue
        # Whisper reads source_path directly via ffmpeg, so during regen
        # this MUST be the actual on-disk fixture path. After materialize
        # finishes, we rewrite the same MediaDocument with the canonical
        # /data/metube/<filename> path so the viewer's resolve_media_url()
        # picks it up and the SPA can render thumbnails. See the
        # _rewrite_canonical_paths_to_s3() call at the end of main().
        doc = MediaDocument(
            id=entry["doc_id"],  # match the partition_key exactly
            title=entry.get("title") or mp4.stem,
            source_path=str(mp4.resolve()),
            source="metube",
            metadata={
                "extension": mp4.suffix,
                "size_bytes": mp4.stat().st_size,
                "has_audio": True,
                "has_video": True,  # gates thumbnail_url in /viewer/api/documents
                "doc_id": entry["doc_id"],
                "fixture_filename": mp4.name,  # used by the post-rewrite step
            },
        )
        docs.append(doc.model_dump(mode="json"))

    payload = "\n".join(json.dumps(d, default=str) for d in docs) + ("\n" if docs else "")
    prefix = "silver/media_ingest/media/media_documents"
    meta = {
        "format": "jsonl",
        "size_bytes": len(payload.encode("utf-8")),
        "row_count": len(docs),
        "code_location": "media_ingest",
        "asset_key": "media_documents",
        "partition": None,
        "layer": "silver",
        "upstream_assets": [],
        "stub": "regen-audio",
    }
    s3.put_object(f"{prefix}/_metadata.json", json.dumps(meta).encode("utf-8"))
    s3.put_object(f"{prefix}/data.jsonl", payload.encode("utf-8"))
    return len(docs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, default=MANIFEST, help=f"Path to manifest YAML (default {MANIFEST})")
    parser.add_argument("--only", default=None, help="Comma-separated list of doc_ids to process (default: all)")
    parser.add_argument(
        "--skip-diarization",
        action="store_true",
        help="Only run transcription (useful when HF_TOKEN unavailable)",
    )
    args = parser.parse_args()

    if not args.skip_diarization and not os.environ.get("HF_TOKEN"):
        print("ERROR: HF_TOKEN required for diarization. Set HF_TOKEN or pass --skip-diarization.", file=sys.stderr)
        return 2

    if not args.manifest.exists():
        print(f"ERROR: manifest not found at {args.manifest}", file=sys.stderr)
        return 2

    raw = yaml.safe_load(args.manifest.read_text()) or {}
    videos = raw.get("videos", []) or []
    only = set(args.only.split(",")) if args.only else None
    if only:
        videos = [v for v in videos if v["doc_id"] in only]
        if not videos:
            print("error: --only filter matched 0 entries in manifest", file=sys.stderr)
            return 2
    if not videos:
        print("error: manifest has no videos")
        return 2

    print(f"\n{'─' * 70}")
    print(f"  Regenerating audio fixtures via Dagster materialize for {len(videos)} video(s)")
    print(
        f"  Output: s3://{os.environ['DAGSTER_S3_BUCKET']}/gold/media_ingest/media/media_{{transcriptions,diarization,segment_merge}}/<doc_id>/data.json"
    )
    print(f"  Backend: {os.environ.get('WHISPER_BACKEND', 'faster-whisper')}")
    if args.skip_diarization:
        print("  Skipping diarization (--skip-diarization)")
    print(f"{'─' * 70}\n")

    # Seed media_documents (the silver upstream the gold transcription asset
    # consumes). Same trick the dev seed uses — pre-write it to S3 + declare
    # as a SourceAsset for the materialize call below.
    n_docs = _seed_media_documents_to_s3()
    print(f"  ✓ media_documents pre-seeded: {n_docs} docs → silver/media_ingest/media/media_documents/data.jsonl")

    # Now drive Dagster materialize. Same code path as prod — Whisper +
    # pyannote run via the actual asset bodies; output flows through
    # MinioIOManager to s3://dagster/gold/...
    from dagster import AssetKey, DagsterInstance, SourceAsset, materialize
    from media_ingest.assets.diarization import media_diarization, media_segment_merge
    from media_ingest.assets.transcription import media_transcriptions

    from dagster_io import EmbeddingResource, select_io_managers

    media_documents_source = SourceAsset(
        key=AssetKey(["media_documents"]),
        metadata={"layer": "silver"},
    )

    resources = {
        **{
            k: v
            for k, v in select_io_managers(default_local_dir=".test-output/media-ingest").items()
            if k in ("io_manager", "optional_io_manager")
        },
        # Embeddings aren't actually used by transcription/diarization but the
        # resources dict in media_ingest/__init__.py declares them, so mirror.
        "embedding": EmbeddingResource(),
        "embeddings": EmbeddingResource(),
        "embedding_seed": EmbeddingResource(),
    }
    for key in ("embedding", "embeddings", "embedding_seed"):
        resources[key].setup_for_execution(None)

    # MediaIngestConfig is a Dagster Config (per-asset run_config) — NOT
    # env-driven by default. Build it from env vars here and pass it to
    # materialize() via run_config so the dev seed picks up WHISPER_BACKEND
    # etc. that we forced above.
    backend_default = os.environ["WHISPER_BACKEND"]  # set above
    media_config = {
        "whisper_backend": backend_default,
        "whisper_model": os.environ.get("WHISPER_MODEL", "base"),
        "whisper_device": os.environ.get("WHISPER_DEVICE", "auto"),
        "whisper_compute_type": os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
        "mlx_model_id": os.environ.get("MLX_MODEL_ID", "mlx-community/whisper-base-mlx"),
        "hf_token": os.environ.get("HF_TOKEN", ""),
    }
    print(
        f"  Whisper config: backend={media_config['whisper_backend']} "
        f"model={media_config['whisper_model']} device={media_config['whisper_device']}"
    )

    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions("media_document", [v["doc_id"] for v in videos])

    selection = [media_documents_source, media_transcriptions]
    if not args.skip_diarization:
        selection += [media_diarization, media_segment_merge]

    # The asset op_name is auto-derived from the function name. Same config
    # is reused across transcription/diarization/segment_merge — they all
    # take a MediaIngestConfig parameter.
    run_config = {
        "ops": {
            "media_transcriptions": {"config": media_config},
            "media_diarization": {"config": media_config},
            "media_segment_merge": {"config": media_config},
        }
    }

    overall_start = time.monotonic()
    succeeded = 0
    failed: list[tuple[str, str]] = []

    for entry in videos:
        doc_id = entry["doc_id"]
        title = entry.get("title", doc_id)
        print(f"\n  ── {doc_id}  ({title})")
        try:
            result = materialize(
                selection,
                resources=resources,
                partition_key=doc_id,
                instance=instance,
                run_config=run_config,
            )
            if result.success:
                succeeded += 1
                print(f"  ✓ {doc_id}: {len(selection) - 1} assets materialized")
            else:
                failed.append((doc_id, "materialize returned failure"))
                print(f"  ✗ {doc_id}: materialize returned failure")
        except Exception as exc:
            failed.append((doc_id, f"{type(exc).__name__}: {exc}"))
            print(f"  ✗ {doc_id}: {type(exc).__name__}: {exc}")

    # Post-materialize: rewrite media_documents with canonical /data/metube/
    # paths so the viewer's resolve_media_url + thumbnail flow works. The
    # on-disk paths we used during Whisper aren't recognized by _MEDIA_ROOTS.
    if succeeded:
        _rewrite_canonical_paths_to_s3()
        print("  ✓ media_documents rewritten with canonical /data/metube/ paths")

    total = time.monotonic() - overall_start
    print(f"\n{'─' * 70}")
    print(f"  Done in {_human(total)}: {succeeded} succeeded, {len(failed)} failed")
    if failed:
        for doc_id, msg in failed:
            print(f"    ✗ {doc_id}: {msg}")
    print(f"{'─' * 70}")
    return 1 if failed else 0


def _rewrite_canonical_paths_to_s3() -> None:
    """Read silver/.../media_documents/data.jsonl, swap each doc's
    source_path to /data/metube/<fixture_filename> (recorded in metadata
    during the initial seed), write back. Whisper's already done; the
    viewer needs the canonical layout to resolve_media_url + render
    thumbnails."""
    from dagster_io.s3_client import S3Client

    s3 = S3Client(
        endpoint_url=os.environ["DAGSTER_S3_ENDPOINT_URL"],
        access_key=os.environ["DAGSTER_S3_ACCESS_KEY"],
        secret_key=os.environ["DAGSTER_S3_SECRET_KEY"],
        bucket=os.environ["DAGSTER_S3_BUCKET"],
    )
    prefix = "silver/media_ingest/media/media_documents"
    raw = s3.get_object(f"{prefix}/data.jsonl").decode("utf-8")
    rewritten = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        meta = d.get("metadata") or {}
        fname = meta.get("fixture_filename")
        if fname:
            d["source_path"] = f"/data/metube/{fname}"
        rewritten.append(d)
    payload = "\n".join(json.dumps(d, default=str) for d in rewritten) + "\n"
    s3.put_object(f"{prefix}/data.jsonl", payload.encode("utf-8"))
    # Update _metadata.json size to match
    meta_key = f"{prefix}/_metadata.json"
    md = json.loads(s3.get_object(meta_key))
    md["size_bytes"] = len(payload.encode("utf-8"))
    s3.put_object(meta_key, json.dumps(md).encode("utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())

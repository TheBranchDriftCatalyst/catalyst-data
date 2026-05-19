#!/usr/bin/env python3
"""Write silver/media_ingest/media/media_documents/data.jsonl pointing at
the bundled test videos in ``packages/media-ingest/tests/fixtures/*.mp4``.

This is a documents-only stub for local dev — the segment_merge cache
(transcription + diarization) isn't required. The viewer-api reads the
.jsonl, the Media Explorer SPA lists each row, and clicking a tile loads
the player against the on-disk mp4 (served by the viewer-api's
``/viewer/api/media/<doc_id>`` route using ``CATALYST_MEDIA_ROOT_METUBE``).

Use ``task seed:media --limit 5`` for the full bronze+silver+gold flow
(requires ``task bench:fixtures:regen`` first to populate the
transcription cache). This script is the lighter "just show me the
videos" variant.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs" / "dagster-io" / "src"))

from dagster_io.manifests import load_media_manifest
from dagster_io.s3_client import S3Client


def main() -> int:
    fixture_dir = ROOT / "packages" / "media-ingest" / "tests" / "fixtures"
    manifest = load_media_manifest()
    if not manifest.videos:
        print("error: audio_manifest.yaml is empty", file=sys.stderr)
        return 1

    docs: list[dict] = []
    for entry in manifest.videos:
        mp4 = fixture_dir / entry.file
        if not mp4.exists():
            print(f"  ⊘ {entry.doc_id}: {mp4.name} not on disk — skipping")
            continue
        docs.append(
            {
                "id": entry.doc_id,
                "title": entry.title or mp4.stem,
                # /data/metube/<filename> is the canonical source_path the
                # viewer-api's resolve_media_url() understands. The actual
                # mp4 lives at the fixture path; CATALYST_MEDIA_ROOT_METUBE
                # in the viewer-api env maps the prefix back.
                "source_path": f"/data/metube/{mp4.name}",
                "source": "metube",
                "domain": "media",
                "document_type": "video",
                "metadata": {
                    "extension": mp4.suffix,
                    "size_bytes": mp4.stat().st_size,
                    "has_audio": True,
                    "has_video": True,
                    "doc_id": entry.doc_id,
                },
            }
        )

    if not docs:
        print("error: no videos resolved to on-disk fixtures", file=sys.stderr)
        return 1

    s3 = S3Client.from_env()
    prefix = "silver/media_ingest/media/media_documents"
    payload = "\n".join(json.dumps(d) for d in docs) + "\n"
    meta = {
        "format": "jsonl",
        "size_bytes": len(payload.encode("utf-8")),
        "row_count": len(docs),
        "code_location": "media_ingest",
        "asset_key": "media_documents",
        "partition": None,
        "layer": "silver",
        "upstream_assets": [],
        "stub": "link_media_videos",
    }
    s3.put_object(f"{prefix}/_metadata.json", json.dumps(meta).encode("utf-8"))
    s3.put_object(f"{prefix}/data.jsonl", payload.encode("utf-8"))
    print(f"  ✓ {len(docs)} media documents → s3://{s3.bucket}/{prefix}/data.jsonl")
    for d in docs:
        print(f"    - {d['id']:<60} {d['metadata']['size_bytes'] // 1024 // 1024} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

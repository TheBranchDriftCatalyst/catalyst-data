"""CPU-only ``media_chunks`` materialization test.

For each video in ``packages/media-ingest/tests/fixtures/audio_manifest.yaml``:

  1. Read the cached diarization output from
     ``.test-output/media-ingest/pipeline-cache/<doc_id>/1_diarization.json``
     (populated by ``task bench:fixtures:regen``, which runs the GPU stages
     once and stashes the output).
  2. Run ``_merge_same_speaker_segments`` + ``_build_speaker_text`` (fast CPU)
     and pre-seed the ``media_segment_merge`` upstream at the canonical
     medallion path so ``LocalJsonIOManager.load_input`` finds it.
  3. ``dagster.materialize([media_chunks], partition_key=doc_id, resources={...})``
     against ``LocalJsonIOManager`` → output lands at
     ``.test-output/media-ingest/gold/media_ingest/media/media_chunks/<doc_id>/data.jsonl``.

Skips a doc_id whose diarization cache is missing (re-run ``task bench:fixtures:regen``).

Run via: ``task bench:chunks:regen:media``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml
from dagster import AssetKey, DagsterInstance, SourceAsset, materialize
from media_ingest.assets.chunks import media_chunks
from media_ingest.assets.diarization import _build_speaker_text, _merge_same_speaker_segments
from media_ingest.partitions import media_partitions

from dagster_io import ChunkingResource, EmbeddingResource, LocalJsonIOManager

# media_chunks consumes media_segment_merge as upstream. We pre-seed
# media_segment_merge's medallion path on disk before materializing, so
# Dagster needs a SourceAsset declaration to know the input is loadable
# from the IO manager rather than something it should re-materialize.
media_segment_merge_source = SourceAsset(
    key=AssetKey(["media_segment_merge"]),
    partitions_def=media_partitions,
    metadata={"layer": "gold"},
)

os.environ.setdefault("DAGSTER_CODE_LOCATION", "media_ingest")

REPO_ROOT = Path(__file__).resolve().parents[4]
DOMAIN_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
AUDIO_MANIFEST = DOMAIN_FIXTURE_DIR / "audio_manifest.yaml"
TEST_OUTPUT = REPO_ROOT / ".test-output" / "media-ingest"
DIARIZATION_CACHE = TEST_OUTPUT / "pipeline-cache"


def _load_diarization(doc_id: str) -> dict | None:
    p = DIARIZATION_CACHE / doc_id / "1_diarization.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


@pytest.fixture(scope="session")
def media_chunks_materialized():
    if not AUDIO_MANIFEST.exists():
        pytest.skip(f"audio_manifest.yaml not found at {AUDIO_MANIFEST}")

    videos = (yaml.safe_load(AUDIO_MANIFEST.read_text()) or {}).get("videos", [])
    if not videos:
        pytest.skip("audio_manifest.yaml has no videos")

    io_manager = LocalJsonIOManager(base_dir=str(TEST_OUTPUT))
    chunking = ChunkingResource(prepend_title=False)
    embedding = EmbeddingResource()
    # EmbeddingResource builds its langchain client inside setup_for_execution;
    # outside Dagster's lifecycle (this materialize call) we invoke it manually.
    embedding.setup_for_execution(None)

    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions("media_document", [v["doc_id"] for v in videos])

    seg_merge_root = TEST_OUTPUT / "gold" / "media_ingest" / "media" / "media_segment_merge"
    materialized: dict[str, int] = {}

    for entry in videos:
        doc_id = entry["doc_id"]
        diar = _load_diarization(doc_id)
        if not diar:
            print(f"  ⊘ {doc_id}: no diarization cache; skipping")
            continue

        merged_segments = _merge_same_speaker_segments(diar["segments"], gap_threshold_s=7.0)
        seg_payload = {
            **diar,
            "segments": merged_segments,
            "speaker_text": _build_speaker_text(merged_segments),
        }
        seed_dir = seg_merge_root / doc_id
        seed_dir.mkdir(parents=True, exist_ok=True)
        (seed_dir / "data.json").write_text(json.dumps(seg_payload, default=str))

        result = materialize(
            [media_segment_merge_source, media_chunks],
            resources={
                "io_manager": io_manager,
                "chunking": chunking,
                "embedding": embedding,
                # Reuse the production embedder as the seed embedder for
                # this test — same EmbeddingResource instance is fine
                # since the test isn't probing seed-vs-prod model split.
                "embedding_seed": embedding,
            },
            partition_key=doc_id,
            instance=instance,
        )
        assert result.success, f"materialize failed for {doc_id}"
        out = TEST_OUTPUT / "gold" / "media_ingest" / "media" / "media_chunks" / doc_id / "data.jsonl"
        n = sum(1 for line in out.read_text().splitlines() if line.strip()) if out.exists() else 0
        materialized[doc_id] = n
        print(f"  ✓ {doc_id}: {n} chunks → {out.relative_to(REPO_ROOT)}")

    return materialized


def test_media_chunks_pipeline_for_all_videos(media_chunks_materialized):
    """Every manifest video with a diarization cache produces chunks at the
    canonical medallion path with the production chunker + IO manager.
    """
    assert media_chunks_materialized, "no videos materialized — diarization cache missing for all entries"
    chunks_root = TEST_OUTPUT / "gold" / "media_ingest" / "media" / "media_chunks"
    for doc_id, count in media_chunks_materialized.items():
        out = chunks_root / doc_id / "data.jsonl"
        assert out.exists(), f"missing materialized chunks for {doc_id}"
        assert count > 0, f"empty chunks for {doc_id}"
        rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
        for r in rows:
            assert r["document_id"] == doc_id
            assert r["metadata"].get("primary_speaker"), f"chunk missing primary_speaker: {r['chunk_id']}"
            assert len(r["text"]) <= 5000, f"chunk too large ({len(r['text'])}): {r['chunk_id']}"

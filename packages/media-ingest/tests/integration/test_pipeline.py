"""Integration tests — run real Dagster assets with local filesystem IO.

Each test materializes one or more assets in the real pipeline using
LocalJsonIOManager. Outputs land in tests/pipeline-output/ as readable
JSON/JSONL files so you can inspect intermediate results.

Run the full chain:
    DAGSTER_CODE_LOCATION=media_ingest pytest tests/integration/test_pipeline.py -v -s

Run just discovery (fast, no GPU):
    DAGSTER_CODE_LOCATION=media_ingest pytest tests/integration/test_pipeline.py -v -s -k discovery

Run with custom output dir:
    DAGSTER_CODE_LOCATION=media_ingest pytest tests/integration/test_pipeline.py --output-dir /tmp/my-run

Inspect outputs:
    find tests/pipeline-output -name "*.json" -o -name "*.jsonl" | head -20
    cat tests/pipeline-output/silver/media_ingest/media/media_documents/data.jsonl | jq .
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from dagster import (
    materialize,
)
from media_ingest.assets import (
    media_assertions,
    media_chunks,
    media_diarization,
    media_documents,
    media_files,
    media_mentions,
    media_metadata,
    media_segment_merge,
    media_transcriptions,
)
from media_ingest.config import MediaIngestConfig

# Ensure DAGSTER_CODE_LOCATION is set (required by path_builder)
os.environ.setdefault("DAGSTER_CODE_LOCATION", "media_ingest")


# ── Helpers ────────────────────────────────────────────────────────────────


def _read_output(output_dir: Path, prefix: str) -> dict | list | None:
    """Read a pipeline output from the local filesystem."""
    base = output_dir / prefix
    jsonl = base / "data.jsonl"
    if jsonl.exists():
        with open(jsonl) as f:
            return [json.loads(line) for line in f if line.strip()]
    json_path = base / "data.json"
    if json_path.exists():
        with open(json_path) as f:
            return json.load(f)
    return None


def _get_partition_key(output_dir: Path) -> str | None:
    """Find the first document ID from materialized media_documents."""
    docs = _read_output(output_dir, "silver/media_ingest/media/media_documents")
    if docs and isinstance(docs, list) and docs:
        return docs[0].get("id")
    return None


# ═══════════════════════════════════════════════════════════════════════════
# Discovery chain (CPU only, fast)
# ═══════════════════════════════════════════════════════════════════════════


class TestDiscoveryChain:
    """media_files → media_metadata → media_documents (unpartitioned, CPU)."""

    def test_media_files(self, test_resources, media_dir, output_dir):
        """Scan the local media directory for files."""
        config = MediaIngestConfig(metube_path=str(media_dir), tubesync_path="")
        result = materialize(
            [media_files],
            resources={**test_resources, "config": config},
        )
        assert result.success
        files = result.output_for_node("media_files")
        assert len(files) >= 1, "Should find at least the demo video"
        print(f"\n  Found {len(files)} files: {[f['filename'] for f in files]}")

    def test_media_metadata(self, test_resources, media_dir, output_dir):
        """Run ffprobe on discovered files."""
        config = MediaIngestConfig(metube_path=str(media_dir), tubesync_path="")
        result = materialize(
            [media_files, media_metadata],
            resources={**test_resources, "config": config},
        )
        assert result.success
        metadata = result.output_for_node("media_metadata")
        assert len(metadata) >= 1
        meta = metadata[0].get("metadata", {})
        print(
            f"\n  {metadata[0]['filename']}: {meta.get('duration_seconds', 0):.0f}s, "
            f"codec={meta.get('video_codec')}, {meta.get('width')}x{meta.get('height')}"
        )

    def test_media_documents(self, test_resources, media_dir, output_dir):
        """Create document records with slugified IDs."""
        config = MediaIngestConfig(metube_path=str(media_dir), tubesync_path="")
        result = materialize(
            [media_files, media_metadata, media_documents],
            resources={**test_resources, "config": config},
        )
        assert result.success
        docs = result.output_for_node("media_documents")
        assert len(docs) >= 1
        doc_id = docs[0]["id"]
        assert doc_id.startswith("media-"), f"Expected slugified ID, got: {doc_id}"
        print(f"\n  Document ID: {doc_id}")
        print(f"  Title: {docs[0].get('title')}")


# ═══════════════════════════════════════════════════════════════════════════
# Transcription + Diarization (GPU, slow)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.gpu
@pytest.mark.slow
class TestTranscriptionChain:
    """media_transcriptions → media_diarization → media_segment_merge (partitioned, GPU)."""

    @pytest.fixture(autouse=True)
    def _ensure_documents(self, test_resources, media_dir):
        """Materialize discovery chain first so documents exist."""
        config = MediaIngestConfig(metube_path=str(media_dir), tubesync_path="")
        materialize(
            [media_files, media_metadata, media_documents],
            resources={**test_resources, "config": config},
        )

    def _get_doc_id(self, test_resources, media_dir) -> str:
        config = MediaIngestConfig(metube_path=str(media_dir), tubesync_path="")
        result = materialize(
            [media_files, media_metadata, media_documents],
            resources={**test_resources, "config": config},
        )
        docs = result.output_for_node("media_documents")
        return docs[0]["id"]

    def test_transcription(self, test_resources, media_dir, output_dir):
        """Transcribe the demo video."""
        doc_id = self._get_doc_id(test_resources, media_dir)
        config = MediaIngestConfig(metube_path=str(media_dir), tubesync_path="")
        result = materialize(
            [media_files, media_metadata, media_documents, media_transcriptions],
            resources={**test_resources, "config": config},
            partition_key=doc_id,
        )
        assert result.success
        t = result.output_for_node("media_transcriptions")
        assert t.get("segments"), "Transcription should produce segments"
        assert t.get("text"), "Transcription should produce text"
        print(f"\n  Segments: {len(t['segments'])}, Duration: {t.get('duration_s', 0):.0f}s")
        print(f"  Language: {t.get('language')} ({t.get('language_probability', 0):.0%})")
        print(f"  First 200 chars: {t['text'][:200]}")

    def test_diarization(self, test_resources, media_dir, output_dir):
        """Run speaker diarization on transcribed segments."""
        doc_id = self._get_doc_id(test_resources, media_dir)
        config = MediaIngestConfig(metube_path=str(media_dir), tubesync_path="")
        result = materialize(
            [media_files, media_metadata, media_documents, media_transcriptions, media_diarization],
            resources={**test_resources, "config": config},
            partition_key=doc_id,
        )
        assert result.success
        d = result.output_for_node("media_diarization")
        assert d.get("speakers"), "Should detect at least one speaker"
        print(f"\n  Speakers: {d.get('speakers')}")
        print(f"  Diarization time: {d.get('diarization_time_s', 0):.1f}s")
        print(f"  Device: {d.get('diarization_device')}")

    def test_segment_merge(self, test_resources, media_dir, output_dir):
        """Merge same-speaker segments."""
        doc_id = self._get_doc_id(test_resources, media_dir)
        config = MediaIngestConfig(metube_path=str(media_dir), tubesync_path="")
        result = materialize(
            [
                media_files,
                media_metadata,
                media_documents,
                media_transcriptions,
                media_diarization,
                media_segment_merge,
            ],
            resources={**test_resources, "config": config},
            partition_key=doc_id,
        )
        assert result.success
        m = result.output_for_node("media_segment_merge")
        assert m.get("segments"), "Should produce merged segments"
        assert m.get("speaker_text"), "Should produce speaker-attributed text"
        print(f"\n  Merged segments: {len(m['segments'])}")
        print(f"  Speaker text length: {len(m.get('speaker_text', ''))} chars")


# ═══════════════════════════════════════════════════════════════════════════
# Chunking + Extraction (CPU + LLM)
# ═══════════════════════════════════════════════════════════════════════════


class TestChunkingChain:
    """media_chunks → media_mentions → media_assertions (CPU chunking, LLM extraction)."""

    def test_chunks(self, test_resources, media_dir, output_dir):
        """Chunk merged segments using speaker-aware strategy."""
        doc_id = _get_partition_key(output_dir)
        if not doc_id:
            pytest.skip("No documents materialized yet — run discovery tests first")

        config = MediaIngestConfig(metube_path=str(media_dir), tubesync_path="")
        result = materialize(
            [
                media_files,
                media_metadata,
                media_documents,
                media_transcriptions,
                media_diarization,
                media_segment_merge,
                media_chunks,
            ],
            resources={**test_resources, "config": config},
            partition_key=doc_id,
        )
        assert result.success
        chunks = result.output_for_node("media_chunks")
        assert len(chunks) >= 1, "Should produce at least one chunk"

        strategies = {}
        for c in chunks:
            s = c.metadata.get("strategy", "unknown") if hasattr(c, "metadata") else "unknown"
            strategies[s] = strategies.get(s, 0) + 1
        print(f"\n  Chunks: {len(chunks)}")
        print(f"  Strategies: {strategies}")

    @pytest.mark.llm
    def test_mentions(self, test_resources, media_dir, output_dir):
        """Extract entity mentions via LLM."""
        if "llm" not in test_resources:
            pytest.skip("No LLM_API_KEY — skipping LLM extraction")
        doc_id = _get_partition_key(output_dir)
        if not doc_id:
            pytest.skip("No documents materialized yet")

        config = MediaIngestConfig(metube_path=str(media_dir), tubesync_path="")
        result = materialize(
            [
                media_files,
                media_metadata,
                media_documents,
                media_transcriptions,
                media_diarization,
                media_segment_merge,
                media_chunks,
                media_mentions,
            ],
            resources={**test_resources, "config": config},
            partition_key=doc_id,
        )
        assert result.success
        mentions = result.output_for_node("media_mentions")
        assert len(mentions) >= 1
        types = {}
        for m in mentions:
            t = m.mention_type if hasattr(m, "mention_type") else m.get("mention_type", "?")
            types[t] = types.get(t, 0) + 1
        print(f"\n  Mentions: {len(mentions)}")
        print(f"  Types: {types}")

    @pytest.mark.llm
    def test_assertions(self, test_resources, media_dir, output_dir):
        """Extract qualified assertions via LLM."""
        if "llm" not in test_resources:
            pytest.skip("No LLM_API_KEY — skipping LLM extraction")
        doc_id = _get_partition_key(output_dir)
        if not doc_id:
            pytest.skip("No documents materialized yet")

        config = MediaIngestConfig(metube_path=str(media_dir), tubesync_path="")
        result = materialize(
            [
                media_files,
                media_metadata,
                media_documents,
                media_transcriptions,
                media_diarization,
                media_segment_merge,
                media_chunks,
                media_assertions,
            ],
            resources={**test_resources, "config": config},
            partition_key=doc_id,
        )
        assert result.success
        assertions = result.output_for_node("media_assertions")
        print(f"\n  Assertions: {len(assertions)}")
        negated = sum(1 for a in assertions if getattr(a, "negated", False))
        hedged = sum(1 for a in assertions if getattr(a, "hedged", False))
        print(f"  Negated: {negated}, Hedged: {hedged}")

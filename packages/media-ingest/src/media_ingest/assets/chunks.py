"""Gold: Multi-speaker windowed chunking + semantic refinement.

Thin Dagster asset wrapping ``ChunkingResource.chunk_with_semantic_refinement``
in ``dagster_io.chunking``. The chunker:

1. Groups consecutive speaker turns into ~chunk_size windows with inline
   ``[SPEAKER_X]`` tags so the LLM sees who said what.
2. Embeds each window via ``EmbeddingResource``.
3. Merges adjacent windows whose cosine similarity is above the 75th-percentile
   threshold (collapses topically-continuous neighbors into longer windows).

Production AND benchmark use this same entry point so chunking is in lockstep
across both. See CHUNKING.md for tuning. When ``EmbeddingResource`` isn't
available (e.g. unit-test envs), refinement is skipped automatically.
"""

from collections import Counter
from typing import Any

from dagster import AssetExecutionContext, Output, asset

from dagster_io import ChunkingResource, EmbeddingResource, TextChunk, attach_seeds_batch
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from media_ingest.partitions import media_partitions

logger = get_logger(__name__)
tracer = get_tracer(__name__)

CHUNKS_K8S_CONFIG = {
    "dagster-k8s/config": {
        "container_config": {
            "resources": {"requests": {"cpu": "250m", "memory": "1Gi"}, "limits": {"cpu": "1", "memory": "2Gi"}}
        }
    }
}


@asset(
    group_name="media_ingest",
    description="Multi-speaker windowed chunking with semantic refinement (merges topically-continuous neighbors).",
    compute_kind="python",
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    op_tags=CHUNKS_K8S_CONFIG,
)
def media_chunks(
    context: AssetExecutionContext,
    chunking: ChunkingResource,
    embedding: EmbeddingResource,
    embedding_seed: EmbeddingResource,
    media_segment_merge: dict[str, Any],
) -> Output[list[TextChunk]]:
    partition_key = context.partition_key
    with trace_operation(
        "media_chunks",
        tracer,
        {"code_location": "media_ingest", "layer": "gold", "partition_key": partition_key},
    ):
        t = media_segment_merge
        segments = t.get("segments", [])
        doc_id = t.get("document_id", partition_key)
        title = t.get("title", "")

        if not segments:
            text = t.get("text", "")
            if not text:
                context.log.info(f"No segments or text for partition={partition_key}")
                return Output([], metadata={"document_id": doc_id, "chunk_count": 0, "skipped": True})
            chunks = chunking.chunk_document(
                doc_id,
                title,
                text,
                chunk_overlap=0,
                metadata={"source": "media_ingest", "domain": "media_ingest"},
            )
        else:
            # Hybrid: multi-speaker windowing + semantic refinement. Same code
            # path the benchmark fixtures regenerate against, so prod and
            # benchmark drift is structurally impossible.
            chunks = chunking.chunk_with_semantic_refinement(
                segments,
                document_id=doc_id,
                title=title,
                embedder=embedding,  # has .embed(texts) -> list[list[float]]
                metadata={
                    "source": "media_ingest",
                    "domain": "media_ingest",
                    "language": t.get("language", "unknown"),
                    "speaker_count": t.get("speaker_count", 0),
                },
            )

        # Attach SemanticChunkingSeed to every emitted chunk so the GT
        # candidate sampler can read precomputed embeddings instead of
        # re-embedding the whole corpus on every run. Travels with the
        # chunk through silver/gold via TextChunk.metadata.
        attach_seeds_batch(chunks, embedding_seed, domain="media_ingest")

        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_chunks", layer="gold").inc(
            len(chunks)
        )

        strategies = Counter(c.metadata.get("strategy") for c in chunks)
        avg_len = sum(len(c.text) for c in chunks) / max(len(chunks), 1)
        context.log.info(
            f"Chunked '{title}': {len(segments)} turns → {len(chunks)} chunks "
            f"(strategies={dict(strategies)}, avg={avg_len:.0f} chars)"
        )

        return Output(
            chunks,
            metadata={
                "document_id": doc_id,
                "chunk_count": len(chunks),
                "input_segments": len(segments),
                **{f"strategy_{k}": v for k, v in strategies.items()},
            },
        )

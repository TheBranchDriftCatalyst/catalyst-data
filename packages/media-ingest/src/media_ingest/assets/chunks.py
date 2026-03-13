"""Silver: Text chunking for downstream embedding stage.

Uses 800/150 chunk sizes optimized for speech transcriptions — shorter chunks
improve retrieval quality for conversational audio content.
"""

from typing import Any

from dagster import AssetExecutionContext, Output, asset
from dagster_io import ChunkingResource, TextChunk

# Speech transcriptions benefit from smaller chunks since spoken language
# is less information-dense than written text.
TRANSCRIPTION_CHUNK_SIZE = 800
TRANSCRIPTION_CHUNK_OVERLAP = 150


@asset(
    group_name="media_ingest",
    description="Chunk media transcriptions for embedding",
    compute_kind="python",
    metadata={"layer": "silver"},
)
def media_chunks(
    context: AssetExecutionContext,
    chunking: ChunkingResource,
    media_transcriptions: list[dict[str, Any]],
) -> Output[list[TextChunk]]:
    all_chunks: list[TextChunk] = []
    skipped = 0

    for t in media_transcriptions:
        text = t.get("text", "")
        if not text:
            skipped += 1
            continue

        chunks = chunking.chunk_document(
            document_id=t["document_id"],
            title=t.get("title", ""),
            content=text,
            metadata={
                "source": "media_ingest",
                "language": t.get("language", "unknown"),
            },
            chunk_size=TRANSCRIPTION_CHUNK_SIZE,
            chunk_overlap=TRANSCRIPTION_CHUNK_OVERLAP,
        )
        all_chunks.extend(chunks)

    context.log.info(
        f"Chunked {len(media_transcriptions) - skipped} transcriptions into {len(all_chunks)} chunks "
        f"(skipped {skipped} empty, size={TRANSCRIPTION_CHUNK_SIZE}, overlap={TRANSCRIPTION_CHUNK_OVERLAP})"
    )
    return Output(
        all_chunks,
        metadata={
            "transcription_count": len(media_transcriptions),
            "skipped": skipped,
            "chunk_count": len(all_chunks),
            "chunk_size": TRANSCRIPTION_CHUNK_SIZE,
            "chunk_overlap": TRANSCRIPTION_CHUNK_OVERLAP,
        },
    )

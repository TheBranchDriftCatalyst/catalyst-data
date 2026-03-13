"""Gold: Vector embeddings for leak document chunks."""

from typing import Any

from dagster import AssetExecutionContext, Output, asset
from dagster_io import EmbeddingResource, TextChunk


@asset(
    group_name="leaks",
    description="Generate vector embeddings for leak document chunks",
    compute_kind="ml",
    metadata={"layer": "gold"},
    op_tags={
        "dagster-k8s/config": {
            "container_config": {
                "resources": {
                    "requests": {"cpu": "1", "memory": "4Gi"},
                    "limits": {"cpu": "4", "memory": "8Gi"},
                }
            }
        }
    },
)
def leak_embeddings(
    context: AssetExecutionContext,
    embeddings: EmbeddingResource,
    leak_chunks: list[TextChunk],
) -> Output[list[dict[str, Any]]]:
    texts = [chunk.text for chunk in leak_chunks]

    context.log.info(f"Embedding {len(texts)} chunks with model={embeddings.model}")
    vectors = embeddings.embed(texts)

    results = [
        {
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "embedding": vec,
            "model": embeddings.model,
            "dimensions": len(vec),
        }
        for chunk, vec in zip(leak_chunks, vectors)
    ]

    context.log.info(f"Generated {len(results)} embeddings ({len(vectors[0])}d)")
    return Output(
        results,
        metadata={
            "embedding_count": len(results),
            "model": embeddings.model,
            "dimensions": len(vectors[0]) if vectors else 0,
        },
    )

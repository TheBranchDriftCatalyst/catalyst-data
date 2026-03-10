"""Gold: Vector embeddings for leak documents."""

from typing import Any

from dagster import AssetExecutionContext, Output, asset

from open_leaks.core.document import Document


@asset(
    group_name="leaks",
    description="Generate vector embeddings for leak documents",
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
    leak_documents: list[Document],
) -> Output[list[dict[str, Any]]]:
    raise NotImplementedError(
        "Embedding generation requires sentence-transformers or OpenAI API — "
        "configure via EMBEDDING_PROVIDER and EMBEDDING_MODEL env vars. "
        "Uses EmbeddingClient from open_leaks.llm."
    )

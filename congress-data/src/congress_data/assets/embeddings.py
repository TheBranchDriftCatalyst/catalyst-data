"""Stage 4: Vector embeddings for documents.

Stubbed — requires sentence-transformers or OpenAI embeddings API.
"""

from typing import Any

from dagster import AssetExecutionContext, Output, asset

from congress_data.core.document import Document

# Versioned embedding configs for reproducibility
EMBEDDING_CONFIGS = {
    "sentence-transformers-v1": {
        "model": "all-MiniLM-L6-v2",
        "dimensions": 384,
        "provider": "sentence-transformers",
    },
    "openai-ada-v2": {
        "model": "text-embedding-ada-002",
        "dimensions": 1536,
        "provider": "openai",
    },
}


@asset(
    group_name="congress",
    description="Generate vector embeddings for Congress documents (requires sentence-transformers)",
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
def congress_embeddings(
    context: AssetExecutionContext,
    congress_documents: list[Document],
) -> Output[list[dict[str, Any]]]:
    raise NotImplementedError(
        "Embedding generation requires sentence-transformers or OpenAI API — "
        "install sentence-transformers and set EMBEDDING_CONFIG env var"
    )

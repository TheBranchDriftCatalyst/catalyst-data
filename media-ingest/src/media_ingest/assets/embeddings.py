"""Stage 5: Vector embeddings for transcriptions.

Stubbed — requires sentence-transformers or embedding API.
"""

from typing import Any

from dagster import AssetExecutionContext, Output, asset


@asset(
    group_name="media_ingest",
    description="Generate vector embeddings for transcriptions (requires sentence-transformers)",
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
def media_embeddings(
    context: AssetExecutionContext,
    media_transcriptions: list[dict[str, Any]],
) -> Output[list[dict[str, Any]]]:
    raise NotImplementedError(
        "Embedding generation requires sentence-transformers or OpenAI API — "
        "install sentence-transformers and configure embedding model"
    )

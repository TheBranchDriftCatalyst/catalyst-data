"""Stage 3: Named Entity Recognition (NER) extraction from documents.

Stubbed — requires spaCy or Ollama. Set OLLAMA_URL env var when available.
"""

from typing import Any

from dagster import AssetExecutionContext, Output, asset

from congress_data.core.document import Document


@asset(
    group_name="congress",
    description="Extract named entities from Congress documents (requires spaCy or Ollama)",
    compute_kind="llm",
    metadata={"layer": "silver"},
    op_tags={
        "dagster-k8s/config": {
            "container_config": {
                "resources": {
                    "requests": {"cpu": "500m", "memory": "2Gi"},
                    "limits": {"cpu": "2", "memory": "4Gi"},
                }
            }
        }
    },
)
def congress_entities(
    context: AssetExecutionContext,
    congress_documents: list[Document],
) -> Output[list[dict[str, Any]]]:
    raise NotImplementedError(
        "NER extraction requires spaCy or Ollama — configure via OLLAMA_URL env var"
    )

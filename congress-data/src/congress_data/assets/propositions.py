"""Stage 5: Subject-Predicate-Object proposition extraction.

Stubbed — requires LLM (Ollama or OpenAI) for S-P-O triple extraction.
"""

from typing import Any

from dagster import AssetExecutionContext, Output, asset

from congress_data.core.document import Document


@asset(
    group_name="congress",
    description="Extract S-P-O propositions from Congress documents (requires LLM)",
    compute_kind="llm",
    metadata={"layer": "gold"},
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
def congress_propositions(
    context: AssetExecutionContext,
    congress_documents: list[Document],
) -> Output[list[dict[str, Any]]]:
    raise NotImplementedError(
        "Proposition extraction requires an LLM backend — "
        "configure via OLLAMA_URL or OPENAI_API_KEY env var"
    )

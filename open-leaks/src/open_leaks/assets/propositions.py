"""Gold: Subject-Predicate-Object proposition extraction via LLM."""

from typing import Any

from dagster import AssetExecutionContext, Output, asset

from open_leaks.core.document import Document


@asset(
    group_name="leaks",
    description="Extract S-P-O propositions from leak documents via LLM",
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
def leak_propositions(
    context: AssetExecutionContext,
    leak_documents: list[Document],
) -> Output[list[dict[str, Any]]]:
    raise NotImplementedError(
        "Proposition extraction requires an LLM backend — "
        "configure via LLM_PROVIDER, OPENAI_API_KEY, or OLLAMA_URL env vars. "
        "Uses LLMClient from open_leaks.llm for S-P-O triple extraction."
    )

"""Silver: Named Entity Recognition via LLM."""

from typing import Any

from dagster import AssetExecutionContext, Output, asset

from open_leaks.core.document import Document


@asset(
    group_name="leaks",
    description="Extract named entities from leak documents via LLM",
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
def leak_entities(
    context: AssetExecutionContext,
    leak_documents: list[Document],
) -> Output[list[dict[str, Any]]]:
    raise NotImplementedError(
        "NER extraction requires LLM backend — "
        "configure via LLM_PROVIDER, OPENAI_API_KEY, or OLLAMA_URL env vars. "
        "Uses LLMClient from open_leaks.llm for OpenAI/Ollama extraction."
    )

"""Open-source leaked documents pipeline — Dagster code location."""

from dagster_io.logging import configure_logging
from dagster_io.metrics import start_metrics_server
from dagster_io.observability import configure_tracing

configure_logging()
configure_tracing(service_name="catalyst-data.open_leaks")
start_metrics_server()

from dagster import Definitions
from dagster_k8s import k8s_job_executor
from dagster_io import ChunkingResource, EmbeddingResource, LLMResource, MinioIOManager

# Forward DAGSTER_CODE_LOCATION from the code-server to step pods — required
# by dagster_io.path_builder for S3 path construction.
_k8s_executor = k8s_job_executor.configured(
    {"env_vars": ["DAGSTER_CODE_LOCATION"]},
    name="k8s_job_executor_with_code_location",
)

from open_leaks.assets import (
    epstein_court_docs,
    icij_offshore_entities,
    icij_offshore_relationships,
    leak_assertions,
    leak_chunks,
    leak_documents,
    leak_embeddings,
    leak_entities,
    leak_entity_candidates,
    leak_graph,
    leak_mentions,
    leak_propositions,
    wikileaks_cables,
)

defs = Definitions(
    assets=[
        # Bronze
        wikileaks_cables,
        icij_offshore_entities,
        icij_offshore_relationships,
        epstein_court_docs,
        # Silver
        leak_documents,
        leak_chunks,
        # Gold (legacy — backward compat)
        leak_entities,
        leak_propositions,
        # Gold (EDC)
        leak_mentions,
        leak_entity_candidates,
        leak_assertions,
        # Gold (unchanged)
        leak_embeddings,
        leak_graph,
    ],
    executor=_k8s_executor,
    resources={
        "io_manager": MinioIOManager(),
        "chunking": ChunkingResource(),
        "llm": LLMResource(),
        "embeddings": EmbeddingResource(),
    },
)

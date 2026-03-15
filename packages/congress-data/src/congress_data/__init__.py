"""Congress.gov data pipeline — Dagster code location."""

from dagster_io.logging import configure_logging
from dagster_io.metrics import start_metrics_server
from dagster_io.observability import configure_tracing

configure_logging()
configure_tracing(service_name="catalyst-data.congress_data")
start_metrics_server()

from dagster import Definitions
from dagster_io import ChunkingResource, EmbeddingResource, LLMResource, MinioIOManager

from congress_data.assets import (
    congress_assertions,
    congress_bills,
    congress_chunks,
    congress_committees,
    congress_documents,
    congress_embeddings,
    congress_entities,
    congress_entity_candidates,
    congress_graph,
    congress_members,
    congress_mentions,
    congress_propositions,
)

defs = Definitions(
    assets=[
        # Bronze
        congress_bills,
        congress_members,
        congress_committees,
        # Silver
        congress_documents,
        congress_chunks,
        # Gold (legacy — backward compat)
        congress_entities,
        congress_propositions,
        # Gold (EDC)
        congress_mentions,
        congress_entity_candidates,
        congress_assertions,
        # Gold (unchanged)
        congress_embeddings,
        congress_graph,
    ],
    resources={
        "io_manager": MinioIOManager(),
        "chunking": ChunkingResource(),
        "llm": LLMResource(),
        "embeddings": EmbeddingResource(),
    },
)

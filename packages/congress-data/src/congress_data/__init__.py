"""Congress.gov data pipeline — Dagster code location."""

from dagster import Definitions
from dagster_io import ChunkingResource, EmbeddingResource, LLMResource, MinioIOManager

from congress_data.assets import (
    congress_bills,
    congress_chunks,
    congress_committees,
    congress_documents,
    congress_embeddings,
    congress_entities,
    congress_graph,
    congress_members,
    congress_propositions,
)

defs = Definitions(
    assets=[
        congress_bills,
        congress_members,
        congress_committees,
        congress_documents,
        congress_chunks,
        congress_entities,
        congress_embeddings,
        congress_propositions,
        congress_graph,
    ],
    resources={
        "io_manager": MinioIOManager(),
        "chunking": ChunkingResource(),
        "llm": LLMResource(),
        "embeddings": EmbeddingResource(),
    },
)

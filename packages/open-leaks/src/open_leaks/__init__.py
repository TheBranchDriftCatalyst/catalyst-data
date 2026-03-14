"""Open-source leaked documents pipeline — Dagster code location."""

from dagster import Definitions
from dagster_io import ChunkingResource, EmbeddingResource, LLMResource, MinioIOManager

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
    resources={
        "io_manager": MinioIOManager(),
        "chunking": ChunkingResource(),
        "llm": LLMResource(),
        "embeddings": EmbeddingResource(),
    },
)

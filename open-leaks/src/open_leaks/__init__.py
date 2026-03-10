"""Open-source leaked documents pipeline — Dagster code location."""

from dagster import Definitions
from dagster_io import MinioIOManager

from open_leaks.assets import (
    epstein_court_docs,
    icij_offshore_entities,
    icij_offshore_relationships,
    leak_documents,
    leak_embeddings,
    leak_entities,
    leak_graph,
    leak_propositions,
    wikileaks_cables,
)

defs = Definitions(
    assets=[
        wikileaks_cables,
        icij_offshore_entities,
        icij_offshore_relationships,
        epstein_court_docs,
        leak_documents,
        leak_entities,
        leak_embeddings,
        leak_propositions,
        leak_graph,
    ],
    resources={"io_manager": MinioIOManager()},
)

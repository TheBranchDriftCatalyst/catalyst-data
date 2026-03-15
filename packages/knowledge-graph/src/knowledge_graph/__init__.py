"""Knowledge graph platinum layer — Dagster code location.

Cross-source entity resolution, alignment edges, and assertion graph.
Reads gold-layer EntityCandidates and Assertions from congress_data and open_leaks,
produces CanonicalEntities and writes to PostgreSQL+pgvector and Neo4j.
"""

from dagster_io.logging import configure_logging
from dagster_io.metrics import start_metrics_server
from dagster_io.observability import configure_tracing

configure_logging()
configure_tracing(service_name="catalyst-data.knowledge_graph")
start_metrics_server()

from dagster import Definitions, SourceAsset
from dagster_io import MinioIOManager

from knowledge_graph.resources import GraphDBResource

# Import assets AFTER SourceAsset definitions to avoid circular issues
# The assets module does not import from __init__

# Source assets from other code locations (gold layer inputs)
# Keys must match the actual asset keys in their respective code locations.
# Metadata provides source_code_location and layer so the IO manager reads
# from the correct S3 path (the producing code location's prefix, not ours).
_congress_entity_candidates = SourceAsset(
    key="congress_entity_candidates",
    description="Entity candidates from congress_data code location",
    metadata={"layer": "gold", "source_code_location": "congress_data"},
)
_leak_entity_candidates = SourceAsset(
    key="leak_entity_candidates",
    description="Entity candidates from open_leaks code location",
    metadata={"layer": "gold", "source_code_location": "open_leaks"},
)
_congress_assertions = SourceAsset(
    key="congress_assertions",
    description="Assertions from congress_data code location",
    metadata={"layer": "gold", "source_code_location": "congress_data"},
)
_leak_assertions = SourceAsset(
    key="leak_assertions",
    description="Assertions from open_leaks code location",
    metadata={"layer": "gold", "source_code_location": "open_leaks"},
)

# Import platinum layer assets
from knowledge_graph.assets import assertion_graph, canonical_entities, entity_alignments  # noqa: E402

defs = Definitions(
    assets=[
        # Source assets (from other code locations)
        _congress_entity_candidates,
        _leak_entity_candidates,
        _congress_assertions,
        _leak_assertions,
        # Platinum layer assets
        canonical_entities,
        entity_alignments,
        assertion_graph,
    ],
    resources={
        "io_manager": MinioIOManager(),
        "graph_db": GraphDBResource(),
    },
)

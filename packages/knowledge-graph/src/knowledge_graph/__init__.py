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

from dagster import Definitions, DynamicPartitionsDefinition, SourceAsset

from dagster_io import (
    MinioIOManager,
    OptionalMinioIOManager,
    make_k8s_executor,
    make_run_status_sensor,
)
from knowledge_graph.resources import GraphDBResource

_k8s_executor = make_k8s_executor("knowledge_graph")
_run_status_sensors = make_run_status_sensor("knowledge_graph")

# Import assets AFTER SourceAsset definitions to avoid circular issues
# The assets module does not import from __init__

# media_ingest uses a dynamic partition set keyed by document_id. We declare
# the same DynamicPartitionsDefinition here so this code location knows the
# media_* sources are partitioned and fans in all partitions when loading.
# Dagster identifies dynamic partitions by name, so "media_document" here
# references the same partition set media_ingest writes to.
_media_partitions = DynamicPartitionsDefinition(name="media_document")

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
_media_entity_candidates = SourceAsset(
    key="media_entity_candidates",
    description="Entity candidates from media_ingest code location",
    metadata={"layer": "gold", "source_code_location": "media_ingest"},
    partitions_def=_media_partitions,
)
_media_assertions = SourceAsset(
    key="media_assertions",
    description="Assertions from media_ingest code location",
    metadata={"layer": "gold", "source_code_location": "media_ingest"},
    partitions_def=_media_partitions,
)

# Import platinum layer assets
from knowledge_graph.assets import (  # noqa: E402
    assertion_graph,
    canonical_entities,
    entity_alignments,
)

defs = Definitions(
    assets=[
        # Source assets (from other code locations)
        _congress_entity_candidates,
        _leak_entity_candidates,
        _media_entity_candidates,
        _congress_assertions,
        _leak_assertions,
        _media_assertions,
        # Platinum layer assets
        canonical_entities,
        entity_alignments,
        assertion_graph,
    ],
    sensors=[
        *_run_status_sensors,
    ],
    executor=_k8s_executor,
    resources={
        "io_manager": MinioIOManager(),
        # Used via AssetIn(input_manager_key="optional_io_manager") for
        # cross-source inputs that may not yet be materialized (e.g. the
        # congress/leak entity_candidates and assertions inputs to the
        # platinum assets). Returns None on NoSuchKey instead of raising.
        "optional_io_manager": OptionalMinioIOManager(),
        "graph_db": GraphDBResource(),
    },
)

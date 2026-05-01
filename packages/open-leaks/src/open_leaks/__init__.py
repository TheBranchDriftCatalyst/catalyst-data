"""Open-source leaked documents pipeline — Dagster code location."""

from dagster_io.logging import configure_logging
from dagster_io.metrics import start_metrics_server
from dagster_io.observability import configure_tracing

configure_logging()
configure_tracing(service_name="catalyst-data.open_leaks")
start_metrics_server()

from dagster import Definitions

from dagster_io import (
    ChunkingResource,
    EmbeddingResource,
    LLMResource,
    make_run_status_sensor,
    select_io_managers,
)
from dagster_io.executor import make_in_process_executor

_executor = make_in_process_executor("open_leaks")
_run_status_sensors = make_run_status_sensor("open_leaks")

from open_leaks.assets import (
    epstein_court_docs,
    icij_offshore_entities,
    icij_offshore_relationships,
    leak_chunks,
    leak_documents,
    leak_entity_candidates,
    leak_gold_assets,
    leak_graph,
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
        # Gold (factory-generated: leak_mentions, leak_assertions, leak_embeddings)
        *leak_gold_assets,
        leak_entity_candidates,
        leak_graph,
    ],
    sensors=[
        *_run_status_sensors,
    ],
    executor=_executor,
    resources={
        **{
            k: v
            for k, v in select_io_managers(default_local_dir=".test-output/open-leaks").items()
            if k == "io_manager"
        },
        "chunking": ChunkingResource(),
        "llm": LLMResource(),
        "embeddings": EmbeddingResource(),
    },
)

"""Congress.gov data pipeline — Dagster code location.

Re-architected: unpartitioned HEAD (discovery + manifests) →
sensors → partitioned TAIL (per-bill, per-member).

Pattern mirrors media_ingest with two partition sets:
  congress_bill  (key = {congress}-{bill_type}-{number})
  congress_member (key = bioguide_id)
"""

from dagster_io.logging import configure_logging
from dagster_io.metrics import start_metrics_server
from dagster_io.observability import configure_tracing

configure_logging()
configure_tracing(service_name="catalyst-data.congress_data")
start_metrics_server()

from dagster import Definitions

from dagster_io import (
    ChunkingResource,
    EmbeddingResource,
    make_run_status_sensor,
    select_io_managers,
)
from dagster_io.executor import make_in_process_executor

_executor = make_in_process_executor("congress_data")
_run_status_sensors = make_run_status_sensor("congress_data")

# ── Head assets (unpartitioned) ──────────────────────────────────────────────

# ── Bill tail assets (partitioned on congress_bill — bronze/silver) ──────────
from congress_data.assets.bill_tail import (
    bill_actions,
    bill_amendments,
    bill_chunks,
    bill_cosponsors,
    bill_detail,
    bill_document,
    bill_full_text,
    bill_text_versions,
)

# ── Gold extraction assets (factory-generated) ──────────────────────────────
from congress_data.assets.gold import bill_gold_assets, member_gold_assets
from congress_data.assets.head import (
    bills_list_incremental,
    bills_manifest,
    members_list_incremental,
    members_manifest,
)

# ── Member tail assets (partitioned on congress_member — bronze/silver) ──────
from congress_data.assets.member_tail import (
    member_chunks,
    member_committee_assignments,
    member_cosponsored,
    member_detail,
    member_document,
    member_sponsored,
)

# ── Schedules ────────────────────────────────────────────────────────────────
from congress_data.schedules import (
    bills_discovery_job,
    bills_discovery_schedule,
    members_discovery_job,
    members_discovery_schedule,
)

# ── Sensors ──────────────────────────────────────────────────────────────────
from congress_data.sensors import congress_bill_sensor, congress_member_sensor

defs = Definitions(
    assets=[
        # HEAD (unpartitioned — discovery + manifests)
        bills_list_incremental,
        bills_manifest,
        members_list_incremental,
        members_manifest,
        # TAIL per-bill (partitioned on congress_bill)
        bill_detail,
        bill_actions,
        bill_cosponsors,
        bill_text_versions,
        bill_amendments,
        bill_full_text,
        bill_document,
        bill_chunks,
        *bill_gold_assets,
        # TAIL per-member (partitioned on congress_member)
        member_detail,
        member_committee_assignments,
        member_sponsored,
        member_cosponsored,
        member_document,
        member_chunks,
        *member_gold_assets,
    ],
    sensors=[
        congress_bill_sensor,
        congress_member_sensor,
        *_run_status_sensors,
    ],
    jobs=[
        bills_discovery_job,
        members_discovery_job,
    ],
    schedules=[
        bills_discovery_schedule,
        members_discovery_schedule,
    ],
    executor=_executor,
    resources={
        # IO backend: MinIO in prod, Local* when DAGSTER_IO_BACKEND=local.
        **select_io_managers(default_local_dir=".test-output/congress-data"),
        "chunking": ChunkingResource(),
        "embeddings": EmbeddingResource(),
    },
)

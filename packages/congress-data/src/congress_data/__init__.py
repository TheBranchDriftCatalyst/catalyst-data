"""Congress.gov data pipeline — Dagster code location.

Re-architected: unpartitioned HEAD (discovery + manifests) →
sensors → partitioned TAIL (per-bill, per-member).

Pattern mirrors media_ingest with two partition sets:
  congress_bill  (key = {congress}-{bill_type}-{number})
  congress_member (key = bioguide_id)
"""

import os

# Self-identify so dagster_io.path_builder._code_location_from_context
# resolves correctly. setdefault — explicit env wins (k8s deployments
# set DAGSTER_CODE_LOCATION via run_k8s_config). For `task dev` on a
# host, each ``-m <location>`` subprocess inherits this from its own
# __init__ load so multi-location dev works without per-shell setup.
os.environ.setdefault("DAGSTER_CODE_LOCATION", "congress_data")

from dagster_io.logging import configure_logging
from dagster_io.metrics import start_metrics_server
from dagster_io.observability import configure_tracing

configure_logging()
configure_tracing(service_name="catalyst-data.congress_data")
start_metrics_server()

from dagster import Definitions, EnvVar

from dagster_io import (
    ChunkingResource,
    EmbeddingResource,
    make_run_status_sensor,
    select_io_managers,
)
from dagster_io.executor import make_in_process_executor
from dagster_io.llm import LLMResource

_executor = make_in_process_executor("congress_data")
_run_status_sensors = make_run_status_sensor("congress_data")

# ── Head assets (unpartitioned) ──────────────────────────────────────────────

# ── Bill tail assets (partitioned on congress_bill — bronze/silver) ──────────
# ── LLM-synthesised legal claims (LegalRuleML-style) ────────────────────────
from congress_data.assets.bill_claims import bill_claims
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

# ── Structured-projection assets (Cosponsor/PublicLaw → Assertion) ─────────
from congress_data.assets.structured_assertions import congress_structured_assertions

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
        congress_structured_assertions,
        bill_claims,
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
        "chunking": ChunkingResource(
            chunk_size=EnvVar.int("CHUNK_SIZE"),
            chunk_overlap=EnvVar.int("CHUNK_OVERLAP"),
        ),
        # LLM resource for the bill_claims synthesis asset. Picks up
        # LLM_MODEL / LLM_BASE_URL / LLM_API_KEY from the dev ConfigMap
        # + secrets — same wiring shape that media-ingest + open-leaks use.
        "llm": LLMResource(
            base_url=EnvVar("LLM_BASE_URL"),
            api_key=EnvVar("LLM_API_KEY"),
            model=EnvVar("LLM_MODEL"),
        ),
        "embeddings": EmbeddingResource(
            provider=EnvVar("EMBEDDING_PROVIDER"),
            base_url=EnvVar("EMBEDDING_BASE_URL"),
            api_key=EnvVar("EMBEDDING_API_KEY"),
            model=EnvVar("EMBEDDING_MODEL"),
        ),
        # Seed embedder for SemanticChunkingSeed (CD-wnu5 picks the long-term
        # default; today it tracks production at text-embedding-3-small).
        "embedding_seed": EmbeddingResource(
            provider=EnvVar("EMBEDDING_PROVIDER"),
            base_url=EnvVar("EMBEDDING_BASE_URL"),
            api_key=EnvVar("EMBEDDING_API_KEY"),
            model=EnvVar("EMBEDDING_MODEL"),
        ),
    },
)

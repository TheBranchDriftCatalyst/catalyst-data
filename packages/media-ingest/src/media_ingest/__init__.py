"""Media file processing pipeline — Dagster code location."""

from dagster_io.logging import configure_logging
from dagster_io.metrics import start_metrics_server
from dagster_io.observability import configure_tracing

configure_logging()
configure_tracing(service_name="catalyst-data.media_ingest")
start_metrics_server()

# ── Media Viewer (FastAPI) — runs as a daemon thread on port 8080 ────────────
# Only start the viewer in the code-server pod, NOT in ephemeral run/step pods.
# The viewer's uvicorn event loop can deadlock dagster's k8s_job_executor.
import os

if os.environ.get("DAGSTER_IS_CODE_SERVER", "").lower() in ("1", "true"):
    import threading

    def _start_viewer() -> None:
        import uvicorn

        from media_ingest.viewer.app import create_viewer_app

        uvicorn.run(create_viewer_app(), host="0.0.0.0", port=8080, log_level="warning")

    threading.Thread(target=_start_viewer, daemon=True, name="media-viewer").start()

from dagster import Definitions, EnvVar

from dagster_io import (
    ChunkingResource,
    EmbeddingResource,
    LLMResource,
    make_run_status_sensor,
    select_io_managers,
)
from dagster_io.executor import make_k8s_executor

_k8s_executor = make_k8s_executor("media_ingest")
_run_status_sensors = make_run_status_sensor("media_ingest")

from media_ingest.assets import (
    bench_overrides_snapshot,
    dpo_dataset,
    media_chunks,
    media_diarization,
    media_documents,
    media_entity_candidates,
    media_files,
    media_gold_assets,
    media_metadata,
    media_segment_merge,
    media_speaker_embeddings,
    media_speaker_profiles,
    media_transcode,
    media_transcriptions,
    mention_proposition_artifacts,
    sft_dataset,
)
from media_ingest.schedules import media_discovery_job, media_discovery_schedule
from media_ingest.sensors import media_document_sensor

defs = Definitions(
    assets=[
        # Unpartitioned: discovery + transcode
        media_files,
        media_metadata,
        media_transcode,
        media_documents,
        # Partitioned per-document
        media_transcriptions,
        media_diarization,
        media_segment_merge,
        media_chunks,
        *media_gold_assets,
        media_entity_candidates,
        # Audit events (cold-path persist_artifacts emission after mentions + assertions materialize)
        mention_proposition_artifacts,
        # Speaker identity (CD-34j.1)
        media_speaker_embeddings,
        media_speaker_profiles,
        # Bench/training plumbing — postgres → S3 snapshot for deterministic
        # training-dataset materialization (Phase 2/3).
        bench_overrides_snapshot,
        # Training-dataset emitters (Phase 3) — write SFT/DPO JSONL to
        # s3://<bucket>/bench/training/{sft,dpo}/<domain>/data.jsonl.
        sft_dataset,
        dpo_dataset,
    ],
    sensors=[
        media_document_sensor,
        *_run_status_sensors,
    ],
    jobs=[
        media_discovery_job,
    ],
    schedules=[
        media_discovery_schedule,
    ],
    executor=_k8s_executor,
    resources={
        # IO backend: MinIO in prod, LocalJsonIOManager when DAGSTER_IO_BACKEND=local
        # (mirrors the integration test layout — same medallion paths on disk).
        **{
            k: v
            for k, v in select_io_managers(default_local_dir=".test-output/media-ingest").items()
            if k in ("io_manager", "optional_io_manager")
        },
        "chunking": ChunkingResource(
            chunk_size=EnvVar.int("CHUNK_SIZE"),
            chunk_overlap=EnvVar.int("CHUNK_OVERLAP"),
        ),
        "llm": LLMResource(
            base_url=EnvVar("LLM_BASE_URL"),
            api_key=EnvVar("LLM_API_KEY"),
            model=EnvVar("LLM_MODEL"),
        ),
        # Both keys point at the same resource: media_chunks asset signature uses
        # ``embedding`` (singular); other media assets use ``embeddings`` (plural).
        "embedding": EmbeddingResource(
            provider=EnvVar("EMBEDDING_PROVIDER"),
            base_url=EnvVar("EMBEDDING_BASE_URL"),
            api_key=EnvVar("EMBEDDING_API_KEY"),
            model=EnvVar("EMBEDDING_MODEL"),
        ),
        "embeddings": EmbeddingResource(
            provider=EnvVar("EMBEDDING_PROVIDER"),
            base_url=EnvVar("EMBEDDING_BASE_URL"),
            api_key=EnvVar("EMBEDDING_API_KEY"),
            model=EnvVar("EMBEDDING_MODEL"),
        ),
        # Seed embedder for SemanticChunkingSeed — separate registration so the
        # model can diverge from production without affecting downstream
        # similarity quality (CD-wnu5 picks the long-term default; today it
        # tracks production at text-embedding-3-small).
        "embedding_seed": EmbeddingResource(
            provider=EnvVar("EMBEDDING_PROVIDER"),
            base_url=EnvVar("EMBEDDING_BASE_URL"),
            api_key=EnvVar("EMBEDDING_API_KEY"),
            model=EnvVar("EMBEDDING_MODEL"),
        ),
    },
)

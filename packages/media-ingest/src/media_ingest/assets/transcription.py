"""Stage 4: Transcribe audio from media files using Whisper."""

import time
from typing import Any

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from media_ingest.assets.discovery import NFS_VOLUMES_CONFIG
from media_ingest.assets.documents import MediaDocument
from media_ingest.config import MediaIngestConfig

logger = get_logger(__name__)

WHISPER_K8S_CONFIG = {
    **NFS_VOLUMES_CONFIG,
    "dagster-k8s/config": {
        **NFS_VOLUMES_CONFIG["dagster-k8s/config"],
        "container_config": {
            **NFS_VOLUMES_CONFIG["dagster-k8s/config"]["container_config"],
            "resources": {
                "requests": {"cpu": "250m", "memory": "4Gi"},
                "limits": {"cpu": "1", "memory": "8Gi"},
            },
        },
    },
}


@asset(
    group_name="media_ingest",
    description="Transcribe audio tracks using OpenAI Whisper",
    compute_kind="ml",
    metadata={"layer": "gold"},
    op_tags=WHISPER_K8S_CONFIG,
)
def media_transcriptions(
    context: AssetExecutionContext,
    config: MediaIngestConfig,
    media_documents: list[MediaDocument],
) -> Output[list[dict[str, Any]]]:
    import whisper

    audio_docs = [d for d in media_documents if d.metadata.get("has_audio")]
    logger.info("Starting media_transcriptions: %d audio files (model=%s)", len(audio_docs), config.whisper_model)
    context.log.info(f"Loading whisper model '{config.whisper_model}'")
    model = whisper.load_model(config.whisper_model)

    results: list[dict[str, Any]] = []
    errors = 0

    for doc in audio_docs:
        context.log.info(f"Transcribing: {doc.title}")
        logger.info("Transcribing file=%s id=%s", doc.title, doc.id)
        start = time.monotonic()
        try:
            result = model.transcribe(doc.source_path)
            duration = time.monotonic() - start
            logger.info("Transcription complete file=%s duration=%.1fs language=%s segments=%d", doc.title, duration, result.get("language", "unknown"), len(result.get("segments", [])))
            results.append({
                "document_id": doc.id,
                "title": doc.title,
                "text": result["text"],
                "language": result.get("language", "unknown"),
                "segments": len(result.get("segments", [])),
            })
        except Exception as e:
            duration = time.monotonic() - start
            context.log.warning(f"Whisper failed for {doc.title}: {e}")
            logger.error("Transcription failed file=%s duration=%.1fs error=%s", doc.title, duration, str(e))
            results.append({
                "document_id": doc.id,
                "title": doc.title,
                "text": "",
                "language": "unknown",
                "error": str(e),
            })
            errors += 1

    ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_transcriptions", layer="gold").inc(len(results))
    logger.info("media_transcriptions complete: %d transcribed (%d errors)", len(results), errors)
    context.log.info(f"Transcribed {len(results)} files ({errors} errors)")

    return Output(
        results,
        metadata={
            "total_transcribed": len(results),
            "errors": errors,
            "languages": MetadataValue.json(
                list({r["language"] for r in results if r.get("language") != "unknown"})
            ),
        },
    )

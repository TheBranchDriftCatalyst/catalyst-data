"""Stage 4: Transcribe audio from media files using Whisper."""

from typing import Any

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from media_ingest.assets.discovery import NFS_VOLUMES_CONFIG
from media_ingest.assets.documents import MediaDocument
from media_ingest.config import MediaIngestConfig

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
    context.log.info(f"Loading whisper model '{config.whisper_model}'")
    model = whisper.load_model(config.whisper_model)

    results: list[dict[str, Any]] = []
    errors = 0

    for doc in audio_docs:
        context.log.info(f"Transcribing: {doc.title}")
        try:
            result = model.transcribe(doc.source_path)
            results.append({
                "document_id": doc.id,
                "title": doc.title,
                "text": result["text"],
                "language": result.get("language", "unknown"),
                "segments": len(result.get("segments", [])),
            })
        except Exception as e:
            context.log.warning(f"Whisper failed for {doc.title}: {e}")
            results.append({
                "document_id": doc.id,
                "title": doc.title,
                "text": "",
                "language": "unknown",
                "error": str(e),
            })
            errors += 1

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

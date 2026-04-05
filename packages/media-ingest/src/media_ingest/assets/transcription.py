"""Stage 4a: Transcribe audio (speech-to-text only, no diarization).

Supports two backends:
- faster-whisper (CTranslate2): CPU-optimized
- openvino (OpenVINO GenAI WhisperPipeline): Intel GPU accelerated

Partitioned by document_id — each run transcribes a single media file.
Diarization is a separate downstream asset (media_diarization).
"""

import os
import subprocess
import tempfile
import time
from typing import Any

from dagster import AssetExecutionContext, AssetIn, MetadataValue, Output, asset

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED
from dagster_io.observability import get_tracer, trace_operation
from media_ingest.assets.discovery import NFS_VOLUMES_CONFIG
from media_ingest.assets.documents import MediaDocument
from media_ingest.config import MediaIngestConfig
from media_ingest.partitions import media_partitions

logger = get_logger(__name__)
tracer = get_tracer(__name__)

WHISPER_MODEL_CACHE = "/data/whisper-models"

# GPU + NFS volumes for transcription step
WHISPER_K8S_CONFIG = {
    **NFS_VOLUMES_CONFIG,
    "dagster-k8s/config": {
        **NFS_VOLUMES_CONFIG["dagster-k8s/config"],
        "container_config": {
            **NFS_VOLUMES_CONFIG["dagster-k8s/config"]["container_config"],
            "resources": {
                "requests": {"cpu": "1", "memory": "8Gi", "gpu.intel.com/i915": "1"},
                "limits": {"cpu": "2", "memory": "16Gi", "gpu.intel.com/i915": "1"},
            },
        },
    },
}


def extract_audio_to_wav(audio_path: str) -> str:
    """Extract audio from any media container to a temp WAV file using ffmpeg."""
    wav_path = tempfile.mktemp(suffix=".wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", audio_path, "-vn",
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_path],
        capture_output=True, check=True, timeout=300,
    )
    return wav_path


# ── Backend: faster-whisper ──────────────────────────────────────────────────


def _load_faster_whisper(config: MediaIngestConfig):
    from faster_whisper import WhisperModel
    return WhisperModel(
        config.whisper_model,
        device=config.whisper_device,
        compute_type=config.whisper_compute_type,
        download_root=WHISPER_MODEL_CACHE,
    )


def _transcribe_faster_whisper(model, audio_path: str) -> dict:
    segments, info = model.transcribe(audio_path, word_timestamps=True)
    segments_list = []
    for s in segments:
        seg = {"start": s.start, "end": s.end, "text": s.text.strip()}
        if s.words:
            seg["words"] = [
                {"start": w.start, "end": w.end, "word": w.word, "probability": w.probability}
                for w in s.words
            ]
        segments_list.append(seg)
    return {
        "segments": segments_list,
        "language": info.language,
        "language_probability": info.language_probability,
        "duration_s": info.duration,
    }


# ── Backend: OpenVINO GenAI ──────────────────────────────────────────────────


def _load_openvino(config: MediaIngestConfig):
    from huggingface_hub import snapshot_download
    model_dir = os.path.join(WHISPER_MODEL_CACHE, config.openvino_model_id.replace("/", "--"))
    if not os.path.isdir(model_dir):
        logger.info("Downloading OpenVINO model %s to %s", config.openvino_model_id, model_dir)
        snapshot_download(config.openvino_model_id, local_dir=model_dir)

    import openvino_genai
    device = config.openvino_device
    try:
        return openvino_genai.WhisperPipeline(model_dir, device)
    except RuntimeError as e:
        if "GPU" in device and "Context was not initialized" in str(e):
            logger.warning("GPU not available, falling back to CPU: %s", e)
            return openvino_genai.WhisperPipeline(model_dir, "CPU")
        raise


def _transcribe_openvino(pipe, audio_path: str) -> dict:
    import soundfile as sf

    wav_path = extract_audio_to_wav(audio_path)
    try:
        raw_speech, sr = sf.read(wav_path, dtype="float32")
        if raw_speech.ndim > 1:
            raw_speech = raw_speech.mean(axis=1)
        duration_s = len(raw_speech) / sr
    finally:
        os.unlink(wav_path)

    result = pipe.generate(
        raw_speech.tolist(),
        max_new_tokens=448,
        return_timestamps=True,
    )

    segments_list = []
    if hasattr(result, "chunks") and result.chunks:
        for chunk in result.chunks:
            segments_list.append({
                "start": chunk.start_ts,
                "end": chunk.end_ts,
                "text": chunk.text.strip(),
            })
    else:
        segments_list.append({
            "start": 0.0,
            "end": duration_s,
            "text": str(result).strip(),
        })

    language = "en"
    language_probability = 0.0
    if hasattr(result, "chunks") and result.chunks:
        for chunk in result.chunks:
            if hasattr(chunk, "language"):
                language = chunk.language
                break

    return {
        "segments": segments_list,
        "language": language,
        "language_probability": language_probability,
        "duration_s": duration_s,
    }


# ── Dagster asset ────────────────────────────────────────────────────────────


@asset(
    group_name="media_ingest",
    description="Transcribe audio to text (no diarization). One partition = one media file.",
    compute_kind="ml",
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    ins={"media_documents": AssetIn(partition_mapping=None)},
    op_tags=WHISPER_K8S_CONFIG,
)
def media_transcriptions(
    context: AssetExecutionContext,
    config: MediaIngestConfig,
    media_documents: list[MediaDocument],
) -> Output[dict[str, Any]]:
    partition_key = context.partition_key
    with trace_operation("media_transcriptions", tracer, {"code_location": "media_ingest", "layer": "gold", "partition_key": partition_key}):
        doc = next((d for d in media_documents if d.id == partition_key), None)
        if doc is None:
            raise ValueError(f"Document '{partition_key}' not found in media_documents")

        if not doc.metadata.get("has_audio"):
            return Output(
                {"document_id": doc.id, "title": doc.title, "text": "", "language": "unknown", "error": "no_audio"},
                metadata={"skipped": True, "reason": "no_audio"},
            )

        backend = config.whisper_backend
        start = time.monotonic()

        if backend == "openvino":
            context.log.info(f"Loading OpenVINO model '{config.openvino_model_id}' (device={config.openvino_device})")
            model = _load_openvino(config)
            model_label = f"openvino:{config.openvino_model_id}:{config.openvino_device}"
        else:
            context.log.info(f"Loading faster-whisper '{config.whisper_model}' (compute={config.whisper_compute_type})")
            model = _load_faster_whisper(config)
            model_label = f"faster-whisper:{config.whisper_model}:{config.whisper_compute_type}"

        context.log.info(f"Transcribing: {doc.title}")

        try:
            if backend == "openvino":
                result = _transcribe_openvino(model, doc.source_path)
            else:
                result = _transcribe_faster_whisper(model, doc.source_path)

            duration = time.monotonic() - start
            full_text = " ".join(s["text"] for s in result["segments"])

            output = {
                "document_id": doc.id,
                "title": doc.title,
                "text": full_text,
                "language": result["language"],
                "language_probability": result.get("language_probability", 0.0),
                "segments": result["segments"],
                "segment_count": len(result["segments"]),
                "duration_s": result["duration_s"],
                "transcription_time_s": round(duration, 1),
                "source_path": doc.source_path,
            }
        except Exception as e:
            context.log.warning(f"Transcription failed for {doc.title}: {e}")
            logger.error("Transcription failed file=%s error=%s", doc.title, str(e))
            output = {"document_id": doc.id, "title": doc.title, "text": "", "language": "unknown", "error": str(e)}

        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_transcriptions", layer="gold").inc(1)

        return Output(
            output,
            metadata={
                "document_id": doc.id,
                "title": doc.title,
                "backend": backend,
                "model": model_label,
                "segment_count": output.get("segment_count", 0),
                "language": output.get("language", "unknown"),
                "duration_s": MetadataValue.float(output.get("duration_s", 0.0)),
                "transcription_time_s": MetadataValue.float(output.get("transcription_time_s", 0.0)),
                "error": output.get("error"),
            },
        )

"""Stage 4: Transcribe audio with speaker diarization.

Supports two backends:
- faster-whisper (CTranslate2): CPU-optimized, default
- openvino (OpenVINO GenAI WhisperPipeline): Intel GPU accelerated, 10-50x realtime

Partitioned by document_id — each run transcribes a single media file.
"""

import os
import time
from typing import Any

from dagster import AssetExecutionContext, MetadataValue, Output, asset

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

# K8s config: resources + HF credentials for pyannote diarization.
# GPU requests added dynamically when openvino backend is selected.
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
            "env_from": [
                {"secret_ref": {"name": "hf-credentials"}},
            ],
        },
    },
}


# ── Backend: faster-whisper (CTranslate2, CPU) ──────────────────────────────


def _load_faster_whisper(config: MediaIngestConfig):
    """Load faster-whisper model."""
    from faster_whisper import WhisperModel

    return WhisperModel(
        config.whisper_model,
        device=config.whisper_device,
        compute_type=config.whisper_compute_type,
        download_root=WHISPER_MODEL_CACHE,
    )


def _transcribe_faster_whisper(model, audio_path: str, word_timestamps: bool) -> dict:
    """Transcribe with faster-whisper, return normalized result."""
    segments, info = model.transcribe(audio_path, word_timestamps=word_timestamps)
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


# ── Backend: OpenVINO GenAI (Intel GPU / CPU) ───────────────────────────────


def _load_openvino(config: MediaIngestConfig):
    """Download and load OpenVINO whisper model."""
    from huggingface_hub import snapshot_download

    model_dir = os.path.join(WHISPER_MODEL_CACHE, config.openvino_model_id.replace("/", "--"))
    if not os.path.isdir(model_dir):
        logger.info("Downloading OpenVINO model %s to %s", config.openvino_model_id, model_dir)
        snapshot_download(config.openvino_model_id, local_dir=model_dir)

    import openvino_genai
    return openvino_genai.WhisperPipeline(model_dir, config.openvino_device)


def _transcribe_openvino(pipe, audio_path: str, word_timestamps: bool) -> dict:
    """Transcribe with OpenVINO GenAI WhisperPipeline, return normalized result."""
    import librosa

    raw_speech, sr = librosa.load(audio_path, sr=16000)
    duration_s = len(raw_speech) / sr

    result = pipe.generate(
        raw_speech.tolist(),
        max_new_tokens=448,
        return_timestamps=True,
    )

    # Parse OpenVINO chunks into our segment format
    segments_list = []
    if hasattr(result, "chunks") and result.chunks:
        for chunk in result.chunks:
            seg = {
                "start": chunk.start_ts,
                "end": chunk.end_ts,
                "text": chunk.text.strip(),
            }
            segments_list.append(seg)
    else:
        # Fallback: single segment with full text
        segments_list.append({
            "start": 0.0,
            "end": duration_s,
            "text": str(result).strip(),
        })

    # OpenVINO doesn't expose language detection; detect from text
    language = "en"
    language_probability = 0.0
    if hasattr(result, "chunks") and result.chunks:
        # Try to detect from first chunk if available
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


# ── Speaker diarization (shared across backends) ────────────────────────────


def _assign_speakers(
    whisper_segments: list[dict],
    diarization,
) -> list[dict]:
    """Align whisper segment timestamps with pyannote speaker turns."""
    speaker_turns = list(diarization.itertracks(yield_label=True))

    for seg in whisper_segments:
        if seg.get("words"):
            for word in seg["words"]:
                mid = (word["start"] + word["end"]) / 2
                word["speaker"] = _find_speaker_at(speaker_turns, mid)
            speakers = [w["speaker"] for w in seg["words"] if w["speaker"]]
            if speakers:
                seg["speaker"] = max(set(speakers), key=speakers.count)
            else:
                seg["speaker"] = None
        else:
            mid = (seg["start"] + seg["end"]) / 2
            seg["speaker"] = _find_speaker_at(speaker_turns, mid)

    return whisper_segments


def _find_speaker_at(speaker_turns: list, timestamp: float) -> str | None:
    """Find which speaker is active at a given timestamp."""
    for turn, _, speaker in speaker_turns:
        if turn.start <= timestamp <= turn.end:
            return speaker
    return None


def _patch_pyannote_auth():
    """Monkey-patch huggingface_hub for pyannote 3.4 compat with hf_hub >=1.0."""
    import functools
    import sys

    import huggingface_hub
    import huggingface_hub.file_download as _fd

    def _wrap(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if "use_auth_token" in kwargs:
                kwargs.setdefault("token", kwargs.pop("use_auth_token"))
            return fn(*args, **kwargs)
        return wrapper

    for target in ("hf_hub_download", "cached_download"):
        for mod in (huggingface_hub, _fd):
            orig = getattr(mod, target, None)
            if orig and not getattr(orig, "_patched", False):
                patched = _wrap(orig)
                patched._patched = True
                setattr(mod, target, patched)

    for name, mod in sys.modules.items():
        if "pyannote" in name and mod is not None:
            for attr in ("hf_hub_download", "cached_download"):
                fn = getattr(mod, attr, None)
                if fn and not getattr(fn, "_patched", False):
                    setattr(mod, attr, _wrap(fn))


def _run_diarization(audio_path: str, hf_token: str, cache_dir: str):
    """Run pyannote speaker diarization pipeline."""
    import torch
    _patch_pyannote_auth()
    _orig_load = torch.load

    def _patched_load(*a, **kw):
        kw["weights_only"] = False
        return _orig_load(*a, **kw)

    torch.load = _patched_load
    from pyannote.audio import Pipeline

    os.environ["HF_TOKEN"] = hf_token
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=hf_token,
        cache_dir=cache_dir,
    )
    if pipeline is None:
        raise RuntimeError(
            "Failed to load pyannote pipeline — accept license at "
            "https://hf.co/pyannote/speaker-diarization-3.1"
        )
    return pipeline(audio_path)


# ── Per-file transcription (backend-agnostic) ───────────────────────────────


def _transcribe_file(
    model,
    doc: MediaDocument,
    config: MediaIngestConfig,
    context: AssetExecutionContext,
) -> dict[str, Any]:
    """Transcribe a single file with the configured backend + optional diarization."""
    start = time.monotonic()

    # Dispatch to backend
    if config.whisper_backend == "openvino":
        result = _transcribe_openvino(model, doc.source_path, config.enable_diarization)
    else:
        result = _transcribe_faster_whisper(model, doc.source_path, config.enable_diarization)

    segments_list = result["segments"]

    # Run diarization and align speakers
    hf_token = config.hf_token or os.environ.get("HF_TOKEN", "")
    if config.enable_diarization and hf_token:
        context.log.info(f"Running speaker diarization for: {doc.title}")
        diarization = _run_diarization(
            doc.source_path, hf_token, WHISPER_MODEL_CACHE,
        )
        segments_list = _assign_speakers(segments_list, diarization)
        unique_speakers = {s.get("speaker") for s in segments_list if s.get("speaker")}
    else:
        unique_speakers = set()
        if config.enable_diarization and not hf_token:
            context.log.warning(
                "Diarization enabled but no hf_token configured — skipping speaker ID"
            )

    duration = time.monotonic() - start
    full_text = " ".join(s["text"] for s in segments_list)

    # Build speaker-attributed transcript
    speaker_text = ""
    if any(s.get("speaker") for s in segments_list):
        current_speaker = None
        for s in segments_list:
            spk = s.get("speaker", "UNKNOWN")
            if spk != current_speaker:
                current_speaker = spk
                speaker_text += f"\n[{current_speaker}]: "
            speaker_text += s["text"] + " "
        speaker_text = speaker_text.strip()

    logger.info(
        "Transcription complete file=%s duration=%.1fs language=%s segments=%d speakers=%d backend=%s",
        doc.title, duration, result["language"],
        len(segments_list), len(unique_speakers), config.whisper_backend,
    )

    return {
        "document_id": doc.id,
        "title": doc.title,
        "text": full_text,
        "speaker_text": speaker_text or None,
        "language": result["language"],
        "language_probability": result.get("language_probability", 0.0),
        "segments": segments_list,
        "segment_count": len(segments_list),
        "speaker_count": len(unique_speakers),
        "speakers": sorted(unique_speakers) if unique_speakers else [],
        "duration_s": result["duration_s"],
        "transcription_time_s": round(duration, 1),
    }


# ── Dagster asset ────────────────────────────────────────────────────────────


@asset(
    group_name="media_ingest",
    description="Transcribe audio with speaker diarization (faster-whisper or OpenVINO). One partition = one media file.",
    compute_kind="ml",
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    op_tags=WHISPER_K8S_CONFIG,
)
def media_transcriptions(
    context: AssetExecutionContext,
    config: MediaIngestConfig,
    media_documents: list[MediaDocument],
) -> Output[dict[str, Any]]:
    partition_key = context.partition_key
    with trace_operation("media_transcriptions", tracer, {"code_location": "media_ingest", "layer": "gold", "partition_key": partition_key}):
        # Find the document matching this partition key
        doc = None
        for d in media_documents:
            if d.id == partition_key:
                doc = d
                break

        if doc is None:
            raise ValueError(
                f"Document with id '{partition_key}' not found in media_documents. "
                f"Available ids: {[d.id for d in media_documents[:10]]}"
            )

        if not doc.metadata.get("has_audio"):
            context.log.warning(f"Document '{doc.title}' has no audio — returning empty transcription")
            return Output(
                {
                    "document_id": doc.id,
                    "title": doc.title,
                    "text": "",
                    "language": "unknown",
                    "error": "no_audio",
                },
                metadata={"skipped": True, "reason": "no_audio"},
            )

        backend = config.whisper_backend
        logger.info(
            "Starting media_transcriptions for partition=%s (backend=%s, model=%s, diarize=%s)",
            partition_key, backend, config.whisper_model, config.enable_diarization,
        )

        # Load model based on backend
        if backend == "openvino":
            context.log.info(
                f"Loading OpenVINO model '{config.openvino_model_id}' "
                f"(device={config.openvino_device}, cache={WHISPER_MODEL_CACHE})"
            )
            model = _load_openvino(config)
            model_label = f"openvino:{config.openvino_model_id}:{config.openvino_device}"
        else:
            context.log.info(
                f"Loading faster-whisper model '{config.whisper_model}' "
                f"(device={config.whisper_device}, compute_type={config.whisper_compute_type})"
            )
            model = _load_faster_whisper(config)
            model_label = f"faster-whisper:{config.whisper_model}:{config.whisper_compute_type}"

        context.log.info(f"Transcribing: {doc.title}")
        logger.info("Transcribing file=%s id=%s backend=%s", doc.title, doc.id, backend)

        try:
            result = _transcribe_file(model, doc, config, context)
        except Exception as e:
            context.log.warning(f"Transcription failed for {doc.title}: {e}")
            logger.error("Transcription failed file=%s error=%s", doc.title, str(e))
            result = {
                "document_id": doc.id,
                "title": doc.title,
                "text": "",
                "language": "unknown",
                "error": str(e),
            }

        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_transcriptions", layer="gold").inc(1)
        logger.info("media_transcriptions complete for partition=%s", partition_key)
        context.log.info(
            f"Transcribed '{doc.title}' — "
            f"{result.get('segment_count', 0)} segments, "
            f"{result.get('speaker_count', 0)} speakers"
        )

        return Output(
            result,
            metadata={
                "document_id": doc.id,
                "title": doc.title,
                "backend": backend,
                "model": model_label,
                "diarization_enabled": config.enable_diarization,
                "segment_count": result.get("segment_count", 0),
                "speaker_count": result.get("speaker_count", 0),
                "language": result.get("language", "unknown"),
                "duration_s": MetadataValue.float(result.get("duration_s", 0.0)),
                "transcription_time_s": MetadataValue.float(result.get("transcription_time_s", 0.0)),
                "error": result.get("error"),
            },
        )

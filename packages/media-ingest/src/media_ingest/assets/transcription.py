"""Stage 4: Transcribe audio with speaker diarization using faster-whisper + pyannote."""

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

logger = get_logger(__name__)
tracer = get_tracer(__name__)

WHISPER_MODEL_CACHE = "/data/whisper-models"

# Whisper K8s config: heavier resources + HF credentials for pyannote diarization.
# Volumes (NFS media, whisper-models) are inherited from dagster-instance.yaml.
WHISPER_K8S_CONFIG = {
    **NFS_VOLUMES_CONFIG,
    "dagster-k8s/config": {
        **NFS_VOLUMES_CONFIG["dagster-k8s/config"],
        "container_config": {
            **NFS_VOLUMES_CONFIG["dagster-k8s/config"]["container_config"],
            "resources": {
                "requests": {"cpu": "1", "memory": "8Gi"},
                "limits": {"cpu": "2", "memory": "16Gi"},
            },
            "env_from": [
                {"secret_ref": {"name": "hf-credentials"}},
            ],
        },
    },
}


def _assign_speakers(
    whisper_segments: list[dict],
    diarization,
) -> list[dict]:
    """Align whisper word-level timestamps with pyannote speaker turns."""
    speaker_turns = list(diarization.itertracks(yield_label=True))

    for seg in whisper_segments:
        if seg.get("words"):
            for word in seg["words"]:
                mid = (word["start"] + word["end"]) / 2
                word["speaker"] = _find_speaker_at(speaker_turns, mid)
            # Majority vote for segment-level speaker
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
    """Monkey-patch huggingface_hub to accept deprecated use_auth_token kwarg.

    pyannote.audio 3.4 passes use_auth_token= to hf_hub_download() in
    multiple modules, but huggingface_hub >=1.0 removed that parameter.
    Patch at the module level so every internal import picks it up.
    """
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
    # torch 2.6+ defaults weights_only=True but pyannote checkpoints
    # contain custom types. lightning_fabric passes weights_only=True
    # explicitly, so we must force it off for these trusted HF models.
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
            "Failed to load pyannote pipeline — ensure the HF token has access "
            "to pyannote/speaker-diarization-3.1 (accept license at "
            "https://hf.co/pyannote/speaker-diarization-3.1)"
        )
    return pipeline(audio_path)


def _transcribe_file(
    model,
    doc: MediaDocument,
    config: MediaIngestConfig,
    context: AssetExecutionContext,
) -> dict[str, Any]:
    """Transcribe a single file with optional diarization."""
    start = time.monotonic()

    segments, info = model.transcribe(
        doc.source_path,
        word_timestamps=config.enable_diarization,
    )
    segments_list = []
    for s in segments:
        seg = {
            "start": s.start,
            "end": s.end,
            "text": s.text.strip(),
        }
        if s.words:
            seg["words"] = [
                {"start": w.start, "end": w.end, "word": w.word, "probability": w.probability}
                for w in s.words
            ]
        segments_list.append(seg)

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
        "Transcription complete file=%s duration=%.1fs language=%s prob=%.2f segments=%d speakers=%d",
        doc.title, duration, info.language, info.language_probability,
        len(segments_list), len(unique_speakers),
    )

    return {
        "document_id": doc.id,
        "title": doc.title,
        "text": full_text,
        "speaker_text": speaker_text or None,
        "language": info.language,
        "language_probability": info.language_probability,
        "segments": segments_list,
        "segment_count": len(segments_list),
        "speaker_count": len(unique_speakers),
        "speakers": sorted(unique_speakers) if unique_speakers else [],
        "duration_s": info.duration,
        "transcription_time_s": round(duration, 1),
    }


@asset(
    group_name="media_ingest",
    description="Transcribe audio with speaker diarization (faster-whisper + pyannote)",
    compute_kind="ml",
    metadata={"layer": "gold"},
    op_tags=WHISPER_K8S_CONFIG,
)
def media_transcriptions(
    context: AssetExecutionContext,
    config: MediaIngestConfig,
    media_documents: list[MediaDocument],
) -> Output[list[dict[str, Any]]]:
    with trace_operation("media_transcriptions", tracer, {"code_location": "media_ingest", "layer": "gold", "record_count": len(media_documents), "whisper_model": config.whisper_model}):
        from faster_whisper import WhisperModel

        audio_docs = [d for d in media_documents if d.metadata.get("has_audio")]
        logger.info(
            "Starting media_transcriptions: %d audio files (model=%s, device=%s, compute=%s, diarize=%s)",
            len(audio_docs), config.whisper_model, config.whisper_device,
            config.whisper_compute_type, config.enable_diarization,
        )

        context.log.info(
            f"Loading faster-whisper model '{config.whisper_model}' "
            f"(device={config.whisper_device}, compute_type={config.whisper_compute_type}, "
            f"cache={WHISPER_MODEL_CACHE}, diarization={config.enable_diarization})"
        )
        model = WhisperModel(
            config.whisper_model,
            device=config.whisper_device,
            compute_type=config.whisper_compute_type,
            download_root=WHISPER_MODEL_CACHE,
        )

        results: list[dict[str, Any]] = []
        errors = 0

        for doc in audio_docs:
            context.log.info(f"Transcribing: {doc.title}")
            logger.info("Transcribing file=%s id=%s", doc.title, doc.id)
            try:
                result = _transcribe_file(model, doc, config, context)
                results.append(result)
            except Exception as e:
                context.log.warning(f"Whisper failed for {doc.title}: {e}")
                logger.error("Transcription failed file=%s error=%s", doc.title, str(e))
                results.append({
                    "document_id": doc.id,
                    "title": doc.title,
                    "text": "",
                    "language": "unknown",
                    "error": str(e),
                })
                errors += 1

        total_speakers = sum(r.get("speaker_count", 0) for r in results)
        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_transcriptions", layer="gold").inc(len(results))
        logger.info("media_transcriptions complete: %d transcribed (%d errors)", len(results), errors)
        context.log.info(f"Transcribed {len(results)} files ({errors} errors, {total_speakers} total speakers detected)")

        return Output(
            results,
            metadata={
                "total_transcribed": len(results),
                "errors": errors,
                "model": config.whisper_model,
                "device": config.whisper_device,
                "compute_type": config.whisper_compute_type,
                "diarization_enabled": config.enable_diarization,
                "total_speakers_detected": total_speakers,
                "languages": MetadataValue.json(
                    list({r["language"] for r in results if r.get("language") != "unknown"})
                ),
            },
        )

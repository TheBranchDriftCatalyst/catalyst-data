"""Stage 4b: Speaker diarization — identify who spoke when.

Runs pyannote speaker-diarization-3.1 on the source audio file,
then aligns speaker turns with transcription segments from media_transcriptions.

Partitioned by document_id. Depends on media_transcriptions.
Produces the final speaker-attributed transcript.
"""

import os
import time
from typing import Any

from dagster import AssetExecutionContext, MetadataValue, Output, asset

from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED, DIARIZATION_DURATION
from dagster_io.observability import get_tracer, trace_operation
from media_ingest.assets.discovery import NFS_VOLUMES_CONFIG
from media_ingest.assets.transcription import extract_audio_to_wav
from media_ingest.config import MediaIngestConfig
from media_ingest.partitions import media_partitions

logger = get_logger(__name__)
tracer = get_tracer(__name__)

WHISPER_MODEL_CACHE = "/data/whisper-models"

# Diarization is CPU-bound (pyannote uses torch on CPU), needs HF token
DIARIZATION_K8S_CONFIG = {
    **NFS_VOLUMES_CONFIG,
    "dagster-k8s/config": {
        **NFS_VOLUMES_CONFIG["dagster-k8s/config"],
        "container_config": {
            **NFS_VOLUMES_CONFIG["dagster-k8s/config"]["container_config"],
            "resources": {
                "requests": {"cpu": "2", "memory": "8Gi"},
                "limits": {"cpu": "4", "memory": "16Gi"},
            },
            "env_from": [
                {"secret_ref": {"name": "hf-credentials"}},
            ],
        },
    },
}


# ── pyannote compat patches ──────────────────────────────────────────────────


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
            "Failed to load pyannote pipeline — accept license at https://hf.co/pyannote/speaker-diarization-3.1"
        )
    # pyannote can't read MP4/MKV — extract audio first
    wav_path = extract_audio_to_wav(audio_path)
    try:
        return pipeline(wav_path)
    finally:
        if os.path.exists(wav_path):
            os.unlink(wav_path)


# ── Speaker alignment ────────────────────────────────────────────────────────


def _find_speaker_at(speaker_turns: list, timestamp: float) -> str | None:
    for turn, _, speaker in speaker_turns:
        if turn.start <= timestamp <= turn.end:
            return speaker
    return None


def _assign_speakers(segments: list[dict], diarization) -> list[dict]:
    """Align transcription segments with pyannote speaker turns."""
    speaker_turns = list(diarization.itertracks(yield_label=True))

    for seg in segments:
        if seg.get("words"):
            for word in seg["words"]:
                mid = (word["start"] + word["end"]) / 2
                word["speaker"] = _find_speaker_at(speaker_turns, mid)
            speakers = [w["speaker"] for w in seg["words"] if w["speaker"]]
            seg["speaker"] = max(set(speakers), key=speakers.count) if speakers else None
        else:
            mid = (seg["start"] + seg["end"]) / 2
            seg["speaker"] = _find_speaker_at(speaker_turns, mid)

    return segments


def _build_speaker_text(segments: list[dict]) -> str:
    """Build speaker-attributed transcript from segments."""
    speaker_text = ""
    current_speaker = None
    for s in segments:
        spk = s.get("speaker", "UNKNOWN")
        if spk != current_speaker:
            current_speaker = spk
            speaker_text += f"\n[{current_speaker}]: "
        speaker_text += s["text"] + " "
    return speaker_text.strip()


# ── Dagster asset ────────────────────────────────────────────────────────────


@asset(
    group_name="media_ingest",
    description="Speaker diarization — identify who spoke when. Aligns with transcription segments.",
    compute_kind="ml",
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    op_tags=DIARIZATION_K8S_CONFIG,
)
def media_diarization(
    context: AssetExecutionContext,
    config: MediaIngestConfig,
    media_transcriptions: dict[str, Any],
) -> Output[dict[str, Any]]:
    partition_key = context.partition_key
    with trace_operation(
        "media_diarization",
        tracer,
        {
            "code_location": "media_ingest",
            "layer": "gold",
            "partition_key": partition_key,
        },
    ):
        t = media_transcriptions

        # Skip if transcription failed or no text
        if t.get("error") or not t.get("text"):
            context.log.info(f"Skipping diarization for partition={partition_key} — no transcription")
            return Output(
                {**t, "speaker_text": None, "speaker_count": 0, "speakers": []},
                metadata={
                    "document_id": partition_key,
                    "skipped": True,
                    "reason": t.get("error", "empty_text"),
                },
            )

        source_path = t.get("source_path", "")
        if not source_path or not os.path.exists(source_path):
            context.log.warning(f"Source audio not found: {source_path}")
            return Output(
                {**t, "speaker_text": None, "speaker_count": 0, "speakers": []},
                metadata={
                    "document_id": partition_key,
                    "skipped": True,
                    "reason": "source_not_found",
                },
            )

        hf_token = config.hf_token or os.environ.get("HF_TOKEN", "")
        if not hf_token:
            context.log.warning("No HF_TOKEN — skipping diarization")
            return Output(
                {**t, "speaker_text": None, "speaker_count": 0, "speakers": []},
                metadata={
                    "document_id": partition_key,
                    "skipped": True,
                    "reason": "no_hf_token",
                },
            )

        context.log.info(f"Running speaker diarization for: {t.get('title', partition_key)}")
        start = time.monotonic()

        try:
            diarization = _run_diarization(source_path, hf_token, WHISPER_MODEL_CACHE)
            segments = _assign_speakers(t["segments"], diarization)
            unique_speakers = {s.get("speaker") for s in segments if s.get("speaker")}
            speaker_text = _build_speaker_text(segments) if unique_speakers else None
            diarization_time = round(time.monotonic() - start, 1)

            # Record diarization duration metric
            DIARIZATION_DURATION.observe(diarization_time)

            output = {
                **t,
                "segments": segments,
                "speaker_text": speaker_text,
                "speaker_count": len(unique_speakers),
                "speakers": sorted(unique_speakers) if unique_speakers else [],
                "diarization_time_s": diarization_time,
            }

            context.log.info(f"Diarization complete: {len(unique_speakers)} speakers detected in {diarization_time}s")
        except Exception as e:
            context.log.warning(f"Diarization failed: {e}")
            logger.error("Diarization failed partition=%s error=%s", partition_key, str(e))
            output = {
                **t,
                "speaker_text": None,
                "speaker_count": 0,
                "speakers": [],
                "diarization_error": str(e),
            }

        ASSET_RECORDS_PROCESSED.labels(code_location="media_ingest", asset_key="media_diarization", layer="gold").inc(1)

        return Output(
            output,
            metadata={
                "document_id": partition_key,
                "speaker_count": output.get("speaker_count", 0),
                "speakers": MetadataValue.json(output.get("speakers", [])),
                "diarization_time_s": MetadataValue.float(output.get("diarization_time_s", 0.0)),
                "error": output.get("diarization_error"),
            },
        )

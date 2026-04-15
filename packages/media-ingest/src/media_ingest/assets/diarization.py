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
from dagster_io.metrics import (
    ASSET_RECORDS_PROCESSED,
    ASSET_SOFT_FAILURES,
    DIARIZATION_DURATION,
    DIARIZATION_REALTIME_FACTOR,
)
from dagster_io.observability import get_tracer, trace_operation
from media_ingest.assets.discovery import NFS_VOLUMES_CONFIG
from media_ingest.assets.transcription import extract_audio_to_wav
from media_ingest.config import MediaIngestConfig
from media_ingest.partitions import media_partitions

logger = get_logger(__name__)
tracer = get_tracer(__name__)

WHISPER_MODEL_CACHE = "/data/whisper-models"

# Diarization runs on Intel XPU (GPU) when available, needs HF token
DIARIZATION_K8S_CONFIG = {
    **NFS_VOLUMES_CONFIG,
    "dagster-k8s/config": {
        **NFS_VOLUMES_CONFIG["dagster-k8s/config"],
        "container_config": {
            **NFS_VOLUMES_CONFIG["dagster-k8s/config"]["container_config"],
            "resources": {
                "requests": {"cpu": "2", "memory": "8Gi", "gpu.intel.com/i915": "1"},
                "limits": {"cpu": "4", "memory": "16Gi", "gpu.intel.com/i915": "1"},
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


def _run_diarization(audio_path: str, hf_token: str, cache_dir: str) -> tuple:
    """Run pyannote speaker diarization pipeline.

    Returns (diarization_output, resolved_device) where resolved_device
    is 'xpu', 'cuda', or 'cpu' depending on what was actually available.
    """
    import torch

    _patch_pyannote_auth()
    _orig_load = torch.load

    def _patched_load(*a, **kw):
        kw["weights_only"] = False
        return _orig_load(*a, **kw)

    torch.load = _patched_load
    from pyannote.audio import Pipeline

    from dagster_io.model_cache import cached_model_path

    local_cache = cached_model_path(cache_dir)
    os.environ["HF_TOKEN"] = hf_token
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=hf_token,
        cache_dir=local_cache,
    )
    if pipeline is None:
        raise RuntimeError(
            "Failed to load pyannote pipeline — accept license at https://hf.co/pyannote/speaker-diarization-3.1"
        )

    # Try XPU (Intel GPU) first, fall back to CUDA then CPU
    resolved_device = "cpu"
    if hasattr(torch, "xpu") and torch.xpu.is_available():
        try:
            pipeline.to(torch.device("xpu"))
            resolved_device = "xpu"
            logger.info("pyannote pipeline moved to XPU (Intel GPU)")
        except RuntimeError as e:
            logger.warning("XPU not available, falling back to CPU: %s", e)
    elif torch.cuda.is_available():
        try:
            pipeline.to(torch.device("cuda"))
            resolved_device = "cuda"
            logger.info("pyannote pipeline moved to CUDA")
        except RuntimeError as e:
            logger.warning("CUDA not available, falling back to CPU: %s", e)
    else:
        logger.warning(
            "No GPU available (torch.xpu=%s, torch.cuda=%s), running diarization "
            "on CPU — this will be slow for long audio. Install intel-extension-for-pytorch "
            "in Dockerfile.gpu to enable XPU acceleration (see CD-844).",
            hasattr(torch, "xpu"),
            torch.cuda.is_available(),
        )

    # pyannote can't read MP4/MKV — extract audio first
    wav_path = extract_audio_to_wav(audio_path)
    try:
        result = pipeline(wav_path)
        return result, resolved_device
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


def _merge_same_speaker_segments(
    segments: list[dict],
    gap_threshold_s: float = 1.5,
    min_merge_length: int = 0,
) -> list[dict]:
    """Merge consecutive segments from the same speaker with small gaps.

    Whisper produces very short segments (2-4 words each) aligned to pauses.
    After diarization assigns speakers, consecutive same-speaker segments with
    small gaps should be collapsed into natural sentence-length units for
    better downstream processing (chunking, viewer display, LLM extraction).

    Args:
        segments: Speaker-annotated segment dicts.
        gap_threshold_s: Max silence gap (seconds) between segments to merge.
        min_merge_length: Unused — reserved for future minimum-char filtering.

    Returns:
        New list of merged segment dicts.
    """
    if not segments:
        return []

    merged: list[dict] = []
    current = {**segments[0]}
    if current.get("words"):
        current["words"] = list(current["words"])

    for next_seg in segments[1:]:
        same_speaker = (current.get("speaker") or None) == (next_seg.get("speaker") or None)
        gap = next_seg.get("start", 0) - current.get("end", 0)

        if same_speaker and 0 <= gap <= gap_threshold_s:
            # Extend current segment
            current["end"] = next_seg["end"]
            # Concatenate text — add space if current doesn't end with whitespace
            cur_text = current.get("text", "")
            nxt_text = next_seg.get("text", "")
            if cur_text and not cur_text.endswith(" "):
                current["text"] = cur_text + " " + nxt_text
            else:
                current["text"] = cur_text + nxt_text

            # Merge word arrays
            if current.get("words") is not None and next_seg.get("words"):
                current["words"].extend(next_seg["words"])
            elif next_seg.get("words"):
                current["words"] = list(next_seg["words"])
        else:
            merged.append(current)
            current = {**next_seg}
            if current.get("words"):
                current["words"] = list(current["words"])

    merged.append(current)
    return merged


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
            diarization, resolved_device = _run_diarization(source_path, hf_token, WHISPER_MODEL_CACHE)
            segments = _assign_speakers(t["segments"], diarization)
            pre_merge = len(segments)
            segments = _merge_same_speaker_segments(segments, gap_threshold_s=1.5)
            context.log.info(f"Merged {pre_merge} segments → {len(segments)} ({pre_merge - len(segments)} collapsed)")
            unique_speakers = {s.get("speaker") for s in segments if s.get("speaker")}
            speaker_text = _build_speaker_text(segments) if unique_speakers else None
            diarization_time = round(time.monotonic() - start, 1)

            # Record diarization duration + realtime factor metrics
            DIARIZATION_DURATION.observe(diarization_time)
            audio_duration = t.get("duration_s", 0)
            if diarization_time > 0 and audio_duration > 0:
                rtf = audio_duration / diarization_time
                DIARIZATION_REALTIME_FACTOR.labels(device=resolved_device).observe(rtf)

            context.log.info(f"Diarization complete on device={resolved_device}")

            output = {
                **t,
                "segments": segments,
                "speaker_text": speaker_text,
                "speaker_count": len(unique_speakers),
                "speakers": sorted(unique_speakers) if unique_speakers else [],
                "diarization_time_s": diarization_time,
                "diarization_device": resolved_device,
            }

            context.log.info(f"Diarization complete: {len(unique_speakers)} speakers detected in {diarization_time}s")
        except Exception as e:
            context.log.error(f"Diarization SOFT FAILURE: {e}")
            logger.error("Diarization failed partition=%s error=%s", partition_key, str(e))
            ASSET_SOFT_FAILURES.labels(
                code_location="media_ingest",
                asset_key="media_diarization",
                reason=type(e).__name__,
            ).inc()
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
                "diarization_device": output.get("diarization_device", "cpu"),
                "error": output.get("diarization_error"),
            },
        )

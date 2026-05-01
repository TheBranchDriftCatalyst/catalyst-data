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

WHISPER_MODEL_CACHE = os.environ.get("WHISPER_MODEL_CACHE", "/data/whisper-models")

# Diarization runs on Intel XPU (GPU) when available, needs HF token
DIARIZATION_K8S_CONFIG = {
    **NFS_VOLUMES_CONFIG,
    "dagster-k8s/config": {
        **NFS_VOLUMES_CONFIG["dagster-k8s/config"],
        "container_config": {
            **NFS_VOLUMES_CONFIG["dagster-k8s/config"]["container_config"],
            "resources": {
                "requests": {"cpu": "2", "memory": "4Gi", "gpu.intel.com/i915": "1"},
                "limits": {"cpu": "4", "memory": "12Gi", "gpu.intel.com/i915": "1"},
            },
            "env_from": [
                {"secret_ref": {"name": "hf-credentials"}},
            ],
        },
    },
}


def _run_diarization(audio_path: str, hf_token: str, cache_dir: str) -> tuple:
    """Run pyannote speaker diarization pipeline (pyannote.audio 4.x).

    Returns (annotation, resolved_device) where annotation is a pyannote
    Annotation object with itertracks().
    """
    import torch
    from pyannote.audio import Pipeline

    from dagster_io.model_cache import cached_model_path

    local_cache = cached_model_path(cache_dir)
    os.environ["HF_TOKEN"] = hf_token
    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        token=hf_token,
        cache_dir=local_cache,
    )
    if pipeline is None:
        raise RuntimeError(
            "Failed to load pyannote pipeline — accept license at https://hf.co/pyannote/speaker-diarization-3.1"
        )

    # Try best available accelerator: XPU → CUDA → MPS → CPU
    resolved_device = "cpu"
    for device_name, check in [
        ("xpu", lambda: hasattr(torch, "xpu") and torch.xpu.is_available()),
        ("cuda", lambda: torch.cuda.is_available()),
        ("mps", lambda: hasattr(torch.backends, "mps") and torch.backends.mps.is_available()),
    ]:
        if check():
            try:
                pipeline.to(torch.device(device_name))
                resolved_device = device_name
                logger.info("pyannote pipeline moved to %s", device_name)
                break
            except RuntimeError as e:
                logger.warning("%s not usable, trying next: %s", device_name, e)

    if resolved_device == "cpu":
        logger.warning("Running diarization on CPU — this will be slow for long audio")

    # pyannote can't read MP4/MKV — extract audio first
    wav_path = extract_audio_to_wav(audio_path)
    try:
        # Load WAV via soundfile and pass as pre-loaded waveform dict.
        # This bypasses torchcodec entirely (which has fragile FFmpeg/libpython
        # deps and incomplete XPU support). pyannote 4.x accepts either a
        # file path (→ uses torchcodec) or a {waveform, sample_rate} dict.
        import soundfile as sf

        waveform, sample_rate = sf.read(wav_path, dtype="float32", always_2d=True)
        # soundfile shape is (time, channel); pyannote wants (channel, time)
        waveform_tensor = torch.from_numpy(waveform.T)
        result = pipeline({"waveform": waveform_tensor, "sample_rate": sample_rate})
        # pyannote 4.x returns DiarizeOutput; extract the Annotation
        annotation = getattr(result, "speaker_diarization", result)
        return annotation, resolved_device
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
            # Concatenate text — always ensure exactly one space between segments
            cur_text = current.get("text", "").rstrip()
            nxt_text = next_seg.get("text", "").lstrip()
            current["text"] = f"{cur_text} {nxt_text}" if cur_text else nxt_text

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

        context.log.info(
            f"Running speaker diarization for: {t.get('title', partition_key)}, "
            f"segment_count={len(t.get('segments', []))}, duration={t.get('duration_s', 0):.0f}s"
        )
        start = time.monotonic()

        try:
            diarization, resolved_device = _run_diarization(source_path, hf_token, WHISPER_MODEL_CACHE)
            segments = _assign_speakers(t["segments"], diarization)
            # Raw segments with speaker labels — merging is done by media_segment_merge
            unique_speakers = {s.get("speaker") for s in segments if s.get("speaker")}
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
                "speaker_text": None,  # built by media_segment_merge
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
                "segment_count": len(output.get("segments", [])),
                "speaker_count": output.get("speaker_count", 0),
                "speakers": MetadataValue.json(output.get("speakers", [])),
                "diarization_time_s": MetadataValue.float(output.get("diarization_time_s", 0.0)),
                "diarization_device": output.get("diarization_device", "cpu"),
                "error": output.get("diarization_error"),
            },
        )


# ── Segment merge asset (CPU, instant) ──────────────────────────────────


MERGE_GAP_THRESHOLD_S = float(os.environ.get("SPEAKER_MERGE_GAP_S", "7.0"))


@asset(
    group_name="media_ingest",
    description="Merge consecutive same-speaker segments into natural turns. CPU-only, instant. Re-run to tweak gap threshold without re-running diarization.",
    compute_kind="python",
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
)
def media_segment_merge(
    context: AssetExecutionContext,
    media_diarization: dict[str, Any],
) -> Output[dict[str, Any]]:
    partition_key = context.partition_key
    t = media_diarization

    if t.get("diarization_error") or not t.get("segments"):
        return Output(
            t,
            metadata={"document_id": partition_key, "skipped": True},
        )

    segments = t["segments"]
    pre_merge = len(segments)
    merged = _merge_same_speaker_segments(segments, gap_threshold_s=MERGE_GAP_THRESHOLD_S)
    speaker_text = _build_speaker_text(merged)

    output = {
        **t,
        "segments": merged,
        "speaker_text": speaker_text,
    }

    context.log.info(
        f"Segment merge: {pre_merge} → {len(merged)} segments "
        f"({pre_merge - len(merged)} collapsed, gap_threshold={MERGE_GAP_THRESHOLD_S}s)"
    )

    return Output(
        output,
        metadata={
            "document_id": partition_key,
            "pre_merge_segments": pre_merge,
            "post_merge_segments": len(merged),
            "collapsed": pre_merge - len(merged),
            "gap_threshold_s": MetadataValue.float(MERGE_GAP_THRESHOLD_S),
        },
    )

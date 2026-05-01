"""Stage 4a: Transcribe audio (speech-to-text only, no diarization).

Supports three backends:
- faster-whisper (CTranslate2): CPU-optimized, runs anywhere
- openvino (OpenVINO GenAI WhisperPipeline): Intel GPU accelerated (k8s prod)
- mlx-whisper: Apple Silicon Metal accelerated (mac-node, dev boxes)

Partitioned by document_id — each run transcribes a single media file.
Diarization is a separate downstream asset (media_diarization).
"""

import os
import re
import subprocess
import tempfile
import time
from typing import Any

from dagster import AssetExecutionContext, AssetIn, MetadataValue, Output, asset

from dagster_io.logging import get_logger
from dagster_io.metrics import (
    ASSET_RECORDS_PROCESSED,
    ASSET_SOFT_FAILURES,
    MODEL_LOAD_DURATION,
    TRANSCRIPTION_DURATION,
    TRANSCRIPTION_REALTIME_FACTOR,
)
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
                "requests": {"cpu": "1", "memory": "4Gi", "gpu.intel.com/i915": "1"},
                "limits": {"cpu": "4", "memory": "12Gi", "gpu.intel.com/i915": "1"},
            },
        },
    },
}


def _normalize_text(text: str) -> str:
    """Post-process transcription text: fix ALL CAPS, missing spaces, punctuation.

    Whisper/OpenVINO sometimes outputs uppercase for emphatic speech or
    on-screen text (CNN chyrons), and drops spaces between words at chunk
    boundaries.
    """
    if not text or len(text) < 2:
        return text

    # Fix missing space after sentence-ending punctuation followed by a letter
    text = re.sub(r"([.!?])([A-Za-z])", r"\1 \2", text)
    # Fix missing space after comma followed by a letter (not in numbers)
    text = re.sub(r",([A-Za-z])", r", \1", text)
    # Split camelCase-like joins: "NickFuentes" → "Nick Fuentes"
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)

    # Detect ALL CAPS blocks (>70% uppercase, >30 alpha chars) and sentence-case them
    alpha_chars = [c for c in text if c.isalpha()]
    if len(alpha_chars) > 30:
        upper_ratio = sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars)
        if upper_ratio > 0.7:
            # Split on sentence boundaries, capitalize first letter of each
            sentences = re.split(r"(?<=[.!?])\s+", text)
            text = " ".join(s[0].upper() + s[1:].lower() if len(s) > 1 else s for s in sentences if s)

    return text.strip()


def extract_audio_to_wav(audio_path: str) -> str:
    """Extract audio from any media container to a temp WAV file using ffmpeg."""
    wav_path = tempfile.mktemp(suffix=".wav")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            audio_path,
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            wav_path,
        ],
        capture_output=True,
        check=True,
        timeout=300,
    )
    return wav_path


# ── Audio chunking for long files ──────────────────────────────────────────

# Maximum chunk duration in seconds. 30 minutes keeps peak memory under ~4GB
# per chunk (16kHz mono float32 = ~115MB raw + model overhead).
_CHUNK_DURATION_S = 1800
# Overlap between chunks to avoid cutting words at boundaries.
# Segments in the overlap zone are deduplicated by timestamp.
_CHUNK_OVERLAP_S = 5


def _get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return float(result.stdout.strip())


def _split_audio_chunks(
    audio_path: str, chunk_duration: int = _CHUNK_DURATION_S, overlap: int = _CHUNK_OVERLAP_S
) -> list[tuple[str, float]]:
    """Split audio into fixed-duration WAV chunks via ffmpeg.

    Returns list of (chunk_wav_path, start_offset_seconds).
    Each chunk overlaps the next by `overlap` seconds to avoid
    cutting words at boundaries.
    """
    total_duration = _get_audio_duration(audio_path)
    if total_duration <= chunk_duration + overlap:
        # Short enough — no splitting needed
        wav = extract_audio_to_wav(audio_path)
        return [(wav, 0.0)]

    chunks = []
    offset = 0.0
    while offset < total_duration:
        chunk_path = tempfile.mktemp(suffix=f"_chunk{len(chunks)}.wav")
        # Include overlap at the start (except first chunk)
        actual_start = max(0, offset - overlap) if offset > 0 else 0
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(actual_start),
                "-i",
                audio_path,
                "-t",
                str(chunk_duration + overlap),
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                chunk_path,
            ],
            capture_output=True,
            check=True,
            timeout=300,
        )
        chunks.append((chunk_path, actual_start))
        offset += chunk_duration

    logger.info(
        "Split %.0fs audio into %d chunks of %ds (+%ds overlap)",
        total_duration,
        len(chunks),
        chunk_duration,
        overlap,
    )
    return chunks


def _merge_chunked_segments(all_chunk_results: list[tuple[dict, float]]) -> dict:
    """Merge transcription results from multiple chunks into a single result.

    Deduplicates segments in the overlap zone by keeping the segment from
    whichever chunk it falls more centrally in.
    """
    merged_segments = []
    seen_end = 0.0

    for result, offset in all_chunk_results:
        for seg in result.get("segments", []):
            # Adjust timestamps by chunk offset
            adj_start = seg["start"] + offset
            adj_end = seg["end"] + offset

            # Skip segments that overlap with already-merged content
            if adj_start < seen_end - 0.5:
                continue

            adj_seg = {**seg, "start": adj_start, "end": adj_end}
            if seg.get("words"):
                adj_seg["words"] = [{**w, "start": w["start"] + offset, "end": w["end"] + offset} for w in seg["words"]]
            merged_segments.append(adj_seg)
            seen_end = adj_end

    # Use metadata from first chunk
    first_result = all_chunk_results[0][0] if all_chunk_results else {}
    total_duration = sum(r.get("duration_s", 0) for r, _ in all_chunk_results)
    # Actual duration is from last segment end, not sum of chunks (overlaps)
    if merged_segments:
        total_duration = merged_segments[-1]["end"]

    return {
        "segments": merged_segments,
        "language": first_result.get("language", "en"),
        "language_probability": first_result.get("language_probability", 0.0),
        "duration_s": total_duration,
    }


# ── Backend: faster-whisper ──────────────────────────────────────────────────


def _load_faster_whisper(config: MediaIngestConfig):
    from faster_whisper import WhisperModel

    from dagster_io.model_cache import cached_model_path

    cache_dir = cached_model_path(WHISPER_MODEL_CACHE)
    return WhisperModel(
        config.whisper_model,
        device=config.whisper_device,
        compute_type=config.whisper_compute_type,
        download_root=cache_dir,
    )


def _transcribe_faster_whisper(model, audio_path: str) -> dict:
    segments, info = model.transcribe(audio_path, word_timestamps=True)
    segments_list = []
    for s in segments:
        seg = {"start": s.start, "end": s.end, "text": _normalize_text(s.text.strip())}
        if s.words:
            seg["words"] = [
                {
                    "start": w.start,
                    "end": w.end,
                    "word": w.word,
                    "probability": w.probability,
                }
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


def _load_openvino(config: MediaIngestConfig) -> tuple[Any, str]:
    """Load the OpenVINO Whisper pipeline.

    Returns ``(pipeline, resolved_device)`` where ``resolved_device`` is the
    device the pipeline is actually running on, NOT the requested one. If the
    GPU init fails we fall back to CPU transparently and the returned
    ``resolved_device`` reflects that — this is used downstream to label the
    ``TRANSCRIPTION_REALTIME_FACTOR`` histogram so Grafana can distinguish
    GPU throughput from silent-CPU-fallback throughput.
    """
    from huggingface_hub import snapshot_download

    from dagster_io.model_cache import cached_model_path

    model_name = config.openvino_model_id.replace("/", "--")
    nfs_model_dir = os.path.join(WHISPER_MODEL_CACHE, model_name)
    if not os.path.isdir(nfs_model_dir):
        logger.info("Downloading OpenVINO model %s to %s", config.openvino_model_id, nfs_model_dir)
        snapshot_download(config.openvino_model_id, local_dir=nfs_model_dir)

    # Use node-local cache if available (avoids NFS read on subsequent runs)
    model_dir = cached_model_path(nfs_model_dir)

    import openvino_genai

    requested_device = config.openvino_device
    try:
        pipeline = openvino_genai.WhisperPipeline(model_dir, requested_device)
        return pipeline, requested_device
    except RuntimeError as e:
        if "GPU" in requested_device and "Context was not initialized" in str(e):
            logger.warning("GPU not available, falling back to CPU: %s", e)
            return openvino_genai.WhisperPipeline(model_dir, "CPU"), "CPU"
        raise


def _estimate_word_timestamps(text: str, seg_start: float, seg_end: float) -> list[dict]:
    """Estimate word-level timestamps by distributing segment time proportionally.

    Splits the segment text into whitespace-delimited words and assigns each word
    a time span proportional to its character length (plus a space). This gives
    approximate timing sufficient for karaoke-style word highlighting in the UI.

    Used as a fallback when the transcription backend (e.g. OpenVINO) does not
    produce native word-level timestamps.
    """
    raw_words = text.split()
    if not raw_words:
        return []

    # Weight each word by character count (+ 1 for the trailing space).
    weights = [len(w) + 1 for w in raw_words]
    total_weight = sum(weights)
    duration = seg_end - seg_start

    words = []
    cursor = seg_start
    for i, (w, weight) in enumerate(zip(raw_words, weights, strict=True)):
        word_duration = (weight / total_weight) * duration
        # Prefix with space to match faster-whisper convention (all words
        # after the first carry a leading space so inline rendering works).
        display_word = w if i == 0 else f" {w}"
        words.append(
            {
                "start": round(cursor, 3),
                "end": round(cursor + word_duration, 3),
                "word": display_word,
                "probability": 0.0,  # no confidence score available
            }
        )
        cursor += word_duration

    return words


def _transcribe_openvino_chunk(pipe, wav_path: str) -> dict:
    """Transcribe a single WAV chunk with OpenVINO."""
    import soundfile as sf

    raw_speech, sr = sf.read(wav_path, dtype="float32")
    if raw_speech.ndim > 1:
        raw_speech = raw_speech.mean(axis=1)
    duration_s = len(raw_speech) / sr

    result = pipe.generate(
        raw_speech.tolist(),
        max_new_tokens=448,
        return_timestamps=True,
    )

    segments_list = []
    if hasattr(result, "chunks") and result.chunks:
        for chunk in result.chunks:
            text = _normalize_text(chunk.text.strip())
            seg = {
                "start": chunk.start_ts,
                "end": chunk.end_ts,
                "text": text,
            }
            words = _estimate_word_timestamps(text, chunk.start_ts, chunk.end_ts)
            if words:
                seg["words"] = words
            segments_list.append(seg)
    else:
        text = _normalize_text(str(result).strip())
        seg = {"start": 0.0, "end": duration_s, "text": text}
        words = _estimate_word_timestamps(text, 0.0, duration_s)
        if words:
            seg["words"] = words
        segments_list.append(seg)

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


def _transcribe_openvino(pipe, audio_path: str) -> dict:
    """Transcribe audio with OpenVINO, chunking long files to bound memory."""
    chunks = _split_audio_chunks(audio_path)
    try:
        if len(chunks) == 1:
            # Short file — single chunk, no merging needed
            wav_path, offset = chunks[0]
            return _transcribe_openvino_chunk(pipe, wav_path)

        # Long file — transcribe each chunk and merge
        chunk_results = []
        for i, (wav_path, offset) in enumerate(chunks):
            logger.info("Transcribing chunk %d/%d (offset=%.0fs)", i + 1, len(chunks), offset)
            result = _transcribe_openvino_chunk(pipe, wav_path)
            chunk_results.append((result, offset))

        return _merge_chunked_segments(chunk_results)
    finally:
        for wav_path, _ in chunks:
            if os.path.exists(wav_path):
                os.unlink(wav_path)


# ── Backend: mlx-whisper (Apple Silicon Metal) ───────────────────────────────


def _load_mlx_whisper(config: MediaIngestConfig) -> tuple[str, str]:
    """Verify mlx-whisper is importable and return (model_id, resolved_device).

    MLX runs on Apple Silicon GPU (Metal/MPS) via Apple's MLX framework. There
    is no CPU fallback — failing import or non-arm64 platform means this
    backend isn't usable. Caller should select a different backend in that case.

    Returns the HF model id as the "handle" since mlx-whisper does lazy loading
    inside ``transcribe()`` — there's no separate model object.
    """
    try:
        import mlx_whisper  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "mlx-whisper not installed. Install the optional extra: pip install 'media-ingest[mlx]'"
        ) from e
    return config.mlx_model_id, "mps"


def _transcribe_mlx_whisper(model_id: str, audio_path: str) -> dict:
    """Transcribe with mlx-whisper, returning the canonical schema.

    mlx-whisper returns segments with native word-level timestamps when
    ``word_timestamps=True``. It does not report ``language_probability`` (only
    the detected language code), so that field is set to 0.0 here — the
    fidelity validator surfaces this as a warning when downstream code expects
    it.
    """
    import mlx_whisper

    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=model_id,
        word_timestamps=True,
    )

    segments_list = []
    for s in result.get("segments", []):
        seg = {
            "start": s["start"],
            "end": s["end"],
            "text": _normalize_text(s["text"].strip()),
        }
        if s.get("words"):
            seg["words"] = [
                {
                    "start": w["start"],
                    "end": w["end"],
                    "word": w["word"],
                    "probability": w.get("probability", 0.0),
                }
                for w in s["words"]
            ]
        segments_list.append(seg)

    duration_s = segments_list[-1]["end"] if segments_list else 0.0

    return {
        "segments": segments_list,
        "language": result.get("language", "en"),
        "language_probability": 0.0,  # not exposed by mlx-whisper
        "duration_s": duration_s,
    }


# ── Backend dispatcher ───────────────────────────────────────────────────────


def _select_backend(config: MediaIngestConfig):
    """Resolve the configured backend into a (handle, device, label, transcribe_fn).

    Single source of truth used by both the Dagster asset and the integration
    test fixture so the test exercises the same code path that production runs
    on the same hardware.

    Returns:
        model_handle: backend-specific model object (or model id string)
        resolved_device: actual device ("CPU" / "GPU" / "mps" / "cuda" / etc.)
        model_label: human-readable identifier for metrics + logs
        transcribe_fn: callable taking (handle, audio_path) -> dict
    """
    backend = config.whisper_backend
    if backend == "mlx-whisper":
        handle, device = _load_mlx_whisper(config)
        label = f"mlx-whisper:{config.mlx_model_id}:{device}"
        return handle, device, label, _transcribe_mlx_whisper
    if backend == "openvino":
        handle, device = _load_openvino(config)
        label = f"openvino:{config.openvino_model_id}:{device}"
        return handle, device, label, _transcribe_openvino
    handle = _load_faster_whisper(config)
    label = f"faster-whisper:{config.whisper_model}:{config.whisper_compute_type}"
    return handle, config.whisper_device, label, _transcribe_faster_whisper


# ── Fidelity validation ──────────────────────────────────────────────────────


def _validate_transcription_fidelity(result: dict, backend: str, model_label: str) -> list[str]:
    """Inspect a transcription result for issues that would break downstream stages.

    Returns a list of human-readable warnings (empty if all good). The asset
    logs these via context.log.warning so backend misconfigurations surface in
    the Dagster UI; tests print them so devs see them locally before relying
    on bad cached fixtures.

    Specific things checked:
    - Segments produced at all (silent audio / backend hard-failure).
    - Word-level timestamps on >=50% of segments — required for accurate
      pyannote speaker assignment in the downstream merge step. Without them,
      diarization aligns by segment-level timing only, which loses precision
      at speaker turn boundaries (especially short interjections).
    - language_probability sanity for backends that report it (faster-whisper).
    - Segment count vs duration sanity (long audio collapsed to 1-2 segments
      usually means the timestamp pipeline is broken).
    """
    warnings: list[str] = []

    segments = result.get("segments", [])
    if not segments:
        warnings.append(
            f"[{backend}] No segments produced — likely silent audio or backend hard-failure (model={model_label})."
        )
        return warnings

    n_with_words = sum(1 for s in segments if s.get("words"))
    word_ratio = n_with_words / len(segments)
    if word_ratio < 0.5:
        warnings.append(
            f"[{backend}] Only {word_ratio:.0%} of segments have word timestamps "
            f"({n_with_words}/{len(segments)}). Diarization speaker assignment will be lossy. "
            f"Verify the backend produces word_timestamps=True (model={model_label})."
        )

    lang_prob = result.get("language_probability", 0.0)
    if backend == "faster-whisper" and lang_prob <= 0:
        warnings.append(
            f"[{backend}] language_probability=0 — faster-whisper should report this; "
            f"check model load (model={model_label})."
        )

    duration_s = result.get("duration_s", 0)
    if duration_s > 60 and len(segments) <= 2:
        warnings.append(
            f"[{backend}] Only {len(segments)} segment(s) for {duration_s:.0f}s of audio — "
            f"likely a chunking or timestamp issue (model={model_label})."
        )

    return warnings


# ── Dagster asset ────────────────────────────────────────────────────────────


@asset(
    group_name="media_ingest",
    description="Transcribe audio to text (no diarization). One partition = one media file.",
    compute_kind="ml",
    metadata={"layer": "gold"},
    partitions_def=media_partitions,
    # media_documents is unpartitioned → Dagster loads the full list into each
    # partition run, then we filter by partition_key. O(N_docs) per run but
    # negligible at current scale (<100 docs). If the corpus grows past ~1000,
    # switch to reading the single document from S3 directly in the asset body.
    ins={"media_documents": AssetIn(partition_mapping=None)},
    op_tags=WHISPER_K8S_CONFIG,
)
def media_transcriptions(
    context: AssetExecutionContext,
    config: MediaIngestConfig,
    media_documents: list[MediaDocument],
) -> Output[dict[str, Any]]:
    if not context.has_partition_key:
        raise RuntimeError(
            "media_transcriptions must be materialized with a partition key "
            "(one partition = one media file). Select a partition in the Dagster UI."
        )
    partition_key = context.partition_key
    with trace_operation(
        "media_transcriptions",
        tracer,
        {
            "code_location": "media_ingest",
            "layer": "gold",
            "partition_key": partition_key,
        },
    ):
        context.log.info(f"Starting media_transcriptions for partition={partition_key}")
        context.log.info(f"Searching {len(media_documents)} documents for partition={partition_key}")
        doc = next((d for d in media_documents if d.id == partition_key), None)
        if doc is None:
            context.log.error(f"Document '{partition_key}' not found in {len(media_documents)} media_documents")
            raise ValueError(f"Document '{partition_key}' not found in media_documents")

        context.log.info(f"Found document: title='{doc.title}', source={doc.source}")
        if not doc.metadata.get("has_audio"):
            context.log.warning(f"Document '{doc.title}' has no audio — skipping transcription")
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
        start = time.monotonic()

        # `resolved_device` and `model_name` are passed as labels to the RTF
        # histogram so Grafana can distinguish GPU throughput from silent-CPU
        # fallback throughput (see _load_openvino docstring for context).
        context.log.info(f"Loading whisper backend '{backend}'")
        model_load_start = time.monotonic()
        model, resolved_device, model_label, transcribe_fn = _select_backend(config)
        MODEL_LOAD_DURATION.labels(model_type=backend).observe(time.monotonic() - model_load_start)
        model_name = (
            config.openvino_model_id
            if backend == "openvino"
            else config.mlx_model_id
            if backend == "mlx-whisper"
            else config.whisper_model
        )
        if backend == "openvino" and resolved_device != config.openvino_device:
            context.log.warning(
                f"OpenVINO device fallback: requested={config.openvino_device} resolved={resolved_device}"
            )

        context.log.info(f"Transcribing: {doc.title} ({model_label})")

        try:
            transcribe_start = time.monotonic()
            result = transcribe_fn(model, doc.source_path)

            for w in _validate_transcription_fidelity(result, backend, model_label):
                context.log.warning(w)

            duration = time.monotonic() - start
            transcribe_time = time.monotonic() - transcribe_start
            full_text = " ".join(s["text"] for s in result["segments"])
            context.log.info(
                f"Transcription complete: {len(result['segments'])} segments, "
                f"language={result.get('language', 'unknown')}, "
                f"duration={result.get('duration_s', 0):.0f}s audio in {transcribe_time:.1f}s"
            )

            # Record transcription duration metric
            TRANSCRIPTION_DURATION.labels(backend=backend, model=config.whisper_model).observe(transcribe_time)

            # Record real-time factor (audio_duration / transcription_time).
            # Labels include `resolved_device` (not requested) so GPU vs
            # silent-CPU-fallback runs show up as distinct series in Grafana.
            audio_duration = result.get("duration_s", 0)
            if transcribe_time > 0 and audio_duration > 0:
                rtf = audio_duration / transcribe_time
                TRANSCRIPTION_REALTIME_FACTOR.labels(
                    backend=backend,
                    device=resolved_device,
                    model=model_name,
                ).observe(rtf)

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
            context.log.error(f"Transcription SOFT FAILURE for {doc.title}: {e}")
            logger.error("Transcription failed file=%s error=%s", doc.title, str(e))
            ASSET_SOFT_FAILURES.labels(
                code_location="media_ingest",
                asset_key="media_transcriptions",
                reason=type(e).__name__,
            ).inc()
            output = {
                "document_id": doc.id,
                "title": doc.title,
                "text": "",
                "language": "unknown",
                "error": str(e),
            }

        ASSET_RECORDS_PROCESSED.labels(
            code_location="media_ingest", asset_key="media_transcriptions", layer="gold"
        ).inc(1)

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

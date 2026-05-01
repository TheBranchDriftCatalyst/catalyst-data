"""Media-ingest pipeline integration tests against the bundled demo video.

Each stage runs the production code path. Outputs that are expensive to
regenerate (Whisper transcription, pyannote diarization) are cached
per-doc-id under ``.test-output/media-ingest/pipeline-cache/<doc_id>/``;
the cache is reused on subsequent runs.

Stage 4 (chunks materialization) lives in ``test_chunks_cpu.py`` and runs
``dagster.materialize`` against ``LocalJsonIOManager``. Cross-domain
extraction tests live in ``tests/test_extraction_e2e.py`` and consume the
medallion tree.

Run:
    DAGSTER_CODE_LOCATION=media_ingest pytest packages/media-ingest/tests/integration/test_pipeline_integration.py -v -s
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from tests.shared.store import BenchmarkStore

DEMO_DOC_ID = "demo-video"  # matches audio_manifest.yaml
DOMAIN_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
DEMO_VIDEO = DOMAIN_FIXTURE_DIR / "demo_video.mp4"
REPO_ROOT = Path(__file__).resolve().parents[4]
LOCAL_MODEL_CACHE = str(REPO_ROOT / ".test-output" / "media-ingest" / "model_cache")

pytestmark = pytest.mark.skipif(not DEMO_VIDEO.exists(), reason=f"demo_video.mp4 not found at {DEMO_VIDEO}")

# Override model cache to local dir (not /data/whisper-models which is NFS in k8s)
os.environ.setdefault("MODEL_CACHE_DIR", LOCAL_MODEL_CACHE)
os.environ.setdefault("DAGSTER_CODE_LOCATION", "media_ingest")

_store = BenchmarkStore()


# ── Step 1: Transcription ────────────────────────────────────────────────


@pytest.fixture(scope="session")
def transcription_result():
    """Transcribe via the production backend dispatcher (mlx/openvino/faster-whisper).

    Env overrides: ``WHISPER_BACKEND``, ``WHISPER_MODEL``, ``WHISPER_DEVICE``,
    ``WHISPER_COMPUTE_TYPE``, ``MLX_MODEL_ID``.
    """
    cached = _store.load_pipeline_artifact("transcription", doc_id=DEMO_DOC_ID) or _store.load_fixture("transcription")
    if cached:
        print(
            f"\n  [prewarmup] Transcription cache HIT "
            f"({len(cached.get('segments', []))} segments, backend={cached.get('backend', '?')})",
            flush=True,
        )
        return cached

    from media_ingest.assets.transcription import _select_backend, _validate_transcription_fidelity
    from media_ingest.config import MediaIngestConfig

    config = MediaIngestConfig(
        whisper_backend=os.environ.get("WHISPER_BACKEND", "faster-whisper"),
        whisper_model=os.environ.get("WHISPER_MODEL", "base"),
        whisper_device=os.environ.get("WHISPER_DEVICE", "cpu"),
        whisper_compute_type=os.environ.get("WHISPER_COMPUTE_TYPE", "int8"),
        mlx_model_id=os.environ.get("MLX_MODEL_ID", "mlx-community/whisper-base-mlx"),
    )

    sys.stdout.write("\n" + "=" * 70 + "\n")
    sys.stdout.write("  [prewarmup] AUDIO PIPELINE — STAGE 1/2: TRANSCRIPTION\n")
    sys.stdout.write(f"  Backend: {config.whisper_backend}\n")
    sys.stdout.write(f"  Audio:   {DEMO_VIDEO.name}\n")
    sys.stdout.write("  (cold run; this can take 30s–3min depending on backend/device)\n")
    sys.stdout.write("=" * 70 + "\n")
    sys.stdout.flush()

    print(f"  Loading {config.whisper_backend} via prod dispatcher...", flush=True)
    model, resolved_device, model_label, transcribe_fn = _select_backend(config)

    print(f"  Transcribing {DEMO_VIDEO.name} ({model_label} on {resolved_device})...", flush=True)
    start = time.monotonic()
    result = transcribe_fn(model, str(DEMO_VIDEO))
    duration = time.monotonic() - start

    for w in _validate_transcription_fidelity(result, config.whisper_backend, model_label):
        print(f"\n  ⚠️  {w}", flush=True)

    output = {
        "document_id": "test-demo-video",
        "title": "Demo Video",
        "text": " ".join(s["text"] for s in result["segments"]),
        "language": result["language"],
        "language_probability": result["language_probability"],
        "duration_s": result["duration_s"],
        "segments": result["segments"],
        "segment_count": len(result["segments"]),
        "source_path": str(DEMO_VIDEO),
        "backend": config.whisper_backend,
        "model_label": model_label,
        "resolved_device": resolved_device,
    }

    print(f"  Transcription complete: {len(result['segments'])} segments in {duration:.1f}s")
    _store.save_fixture("transcription", output)
    return output


def test_transcription_produces_segments(transcription_result):
    assert len(transcription_result["segments"]) > 0
    assert transcription_result["duration_s"] > 0


def test_transcription_segments_have_timestamps(transcription_result):
    for seg in transcription_result["segments"]:
        assert seg["end"] > seg["start"]


def test_transcription_segments_have_words(transcription_result):
    with_words = sum(1 for s in transcription_result["segments"] if s.get("words"))
    total = len(transcription_result["segments"])
    assert with_words / total > 0.8


def test_transcription_words_have_timestamps(transcription_result):
    for seg in transcription_result["segments"]:
        for w in seg.get("words", []):
            assert "start" in w and "end" in w and "word" in w


# ── Step 2: Diarization ──────────────────────────────────────────────────


@pytest.fixture(scope="session")
def diarization_result(transcription_result):
    """Run pyannote diarization + speaker assignment (real pipeline code)."""
    cached = _store.load_pipeline_artifact("diarization", doc_id=DEMO_DOC_ID) or _store.load_fixture("diarization")
    if cached:
        print(
            f"\n  [prewarmup] Diarization cache HIT "
            f"({cached.get('speaker_count', '?')} speakers on {cached.get('diarization_device', '?')})",
            flush=True,
        )
        return cached

    hf_token = os.environ.get("HF_TOKEN", "")
    if not hf_token:
        pytest.skip("HF_TOKEN not set — required for pyannote diarization")

    from media_ingest.assets.diarization import _assign_speakers, _run_diarization

    local_cache = str(_store.pipeline_cache_dir / "model_cache")
    os.makedirs(local_cache, exist_ok=True)

    sys.stdout.write("\n" + "=" * 70 + "\n")
    sys.stdout.write("  [prewarmup] AUDIO PIPELINE — STAGE 2/2: DIARIZATION\n")
    sys.stdout.write("  Backend: pyannote.audio (auto cuda → mps → cpu)\n")
    sys.stdout.write("=" * 70 + "\n")
    sys.stdout.flush()

    print("  Running pyannote diarization (actual pipeline code)...", flush=True)
    start = time.monotonic()

    diarization, device = _run_diarization(str(DEMO_VIDEO), hf_token, local_cache)
    segments = _assign_speakers(transcription_result["segments"], diarization)
    unique_speakers = {s.get("speaker") for s in segments if s.get("speaker")}

    output = {
        **transcription_result,
        "segments": segments,
        "speaker_count": len(unique_speakers),
        "speakers": sorted(unique_speakers) if unique_speakers else [],
        "speaker_text": None,
        "diarization_time_s": round(time.monotonic() - start, 1),
        "diarization_device": device,
    }

    print(f"  Diarization complete: {len(unique_speakers)} speakers on {device} in {output['diarization_time_s']}s")
    _store.save_fixture("diarization", output)
    return output


def test_diarization_finds_speakers(diarization_result):
    assert diarization_result["speaker_count"] >= 1


def test_diarization_segments_have_speaker(diarization_result):
    segs = diarization_result["segments"]
    with_speaker = sum(1 for s in segs if s.get("speaker"))
    assert with_speaker / len(segs) > 0.7


def test_diarization_preserves_words(diarization_result):
    with_words = sum(1 for s in diarization_result["segments"] if s.get("words"))
    assert with_words > 0


# ── Step 3: Segment Merge ────────────────────────────────────────────────


@pytest.fixture(scope="session")
def segment_merge_result(diarization_result):
    """Run ``_merge_same_speaker_segments`` + ``_build_speaker_text`` — fast CPU pass."""
    from media_ingest.assets.diarization import _build_speaker_text, _merge_same_speaker_segments

    pre_merge = len(diarization_result["segments"])
    merged = _merge_same_speaker_segments(diarization_result["segments"], gap_threshold_s=7.0)
    speaker_text = _build_speaker_text(merged)

    output = {
        **diarization_result,
        "segments": merged,
        "speaker_text": speaker_text,
        "pre_merge_segments": pre_merge,
        "post_merge_segments": len(merged),
    }

    print(f"\n  Segment merge: {pre_merge} → {len(merged)} segments")
    return output


def test_merge_reduces_segments(segment_merge_result):
    assert segment_merge_result["post_merge_segments"] < segment_merge_result["pre_merge_segments"]


def test_merge_produces_speaker_text(segment_merge_result):
    assert "[SPEAKER_" in segment_merge_result["speaker_text"]


def test_merge_preserves_words(segment_merge_result):
    with_words = sum(1 for s in segment_merge_result["segments"] if s.get("words"))
    assert with_words > 0

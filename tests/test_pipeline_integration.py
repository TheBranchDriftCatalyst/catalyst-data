"""Integration tests against a real demo video.

Each test runs ACTUAL pipeline code against tests/demo_video.mp4 and saves
the output to tests/fixtures/ for downstream tests to consume. Tests are
ordered — transcription first, then diarization, then merge, etc.

Run with: pytest tests/test_pipeline_integration.py -v -s
(use -s to see progress logs since transcription/diarization take time)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

DEMO_VIDEO = Path(__file__).parent / "demo_video.mp4"
FIXTURES = Path(__file__).parent / "fixtures"
LOCAL_MODEL_CACHE = str(Path(__file__).parent / "fixtures" / "model_cache")

pytestmark = pytest.mark.skipif(not DEMO_VIDEO.exists(), reason="demo_video.mp4 not found")

# Override model cache to local dir (not /data/whisper-models which is NFS in k8s)
os.environ.setdefault("MODEL_CACHE_DIR", LOCAL_MODEL_CACHE)


def _load_fixture(name: str) -> dict | list | None:
    f = FIXTURES / f"{name}.json"
    return json.loads(f.read_text()) if f.exists() else None


def _save_fixture(name: str, data):
    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / f"{name}.json").write_text(json.dumps(data, indent=2, default=str))


# ── Step 1: Transcription ────────────────────────────────────────────────


@pytest.fixture(scope="session")
def transcription_result():
    """Transcribe using our actual _transcribe_faster_whisper pipeline code."""
    cached = _load_fixture("transcription")
    if cached:
        return cached

    from media_ingest.assets.transcription import _transcribe_faster_whisper
    from media_ingest.config import MediaIngestConfig

    config = MediaIngestConfig(whisper_model="base", whisper_device="cpu", whisper_compute_type="int8")

    print("\n  Loading faster-whisper model (base)...")
    from faster_whisper import WhisperModel

    model = WhisperModel(config.whisper_model, device=config.whisper_device, compute_type=config.whisper_compute_type)

    print(f"  Transcribing {DEMO_VIDEO.name}...")
    start = time.monotonic()
    result = _transcribe_faster_whisper(model, str(DEMO_VIDEO))
    duration = time.monotonic() - start

    # Wrap in the same dict shape the asset produces
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
    }

    print(f"  Transcription complete: {len(result['segments'])} segments in {duration:.1f}s")
    _save_fixture("transcription", output)
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
    """Run our actual _run_diarization + _assign_speakers pipeline code."""
    cached = _load_fixture("diarization")
    if cached:
        return cached

    hf_token = os.environ.get("HF_TOKEN", "")
    if not hf_token:
        pytest.skip("HF_TOKEN not set — required for pyannote diarization")

    from media_ingest.assets.diarization import _assign_speakers, _run_diarization

    # Use local cache dir instead of /data/whisper-models (NFS in k8s)
    local_cache = str(FIXTURES / "model_cache")
    os.makedirs(local_cache, exist_ok=True)

    print("\n  Running pyannote diarization (actual pipeline code)...")
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
    _save_fixture("diarization", output)
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
    """Run our actual _merge_same_speaker_segments + _build_speaker_text."""
    cached = _load_fixture("segment_merge")
    if cached:
        return cached

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
    _save_fixture("segment_merge", output)
    return output


def test_merge_reduces_segments(segment_merge_result):
    assert segment_merge_result["post_merge_segments"] < segment_merge_result["pre_merge_segments"]


def test_merge_produces_speaker_text(segment_merge_result):
    assert "[SPEAKER_" in segment_merge_result["speaker_text"]


def test_merge_preserves_words(segment_merge_result):
    with_words = sum(1 for s in segment_merge_result["segments"] if s.get("words"))
    assert with_words > 0


# ── Step 4: Speaker-Aware Chunking ───────────────────────────────────────


@pytest.fixture(scope="session")
def chunks_result(segment_merge_result):
    """Run our actual _speaker_turn_chunks."""
    cached = _load_fixture("chunks")
    if cached:
        from dagster_io import TextChunk

        return [TextChunk(**c) for c in cached]

    from media_ingest.assets.chunks import _speaker_turn_chunks

    from dagster_io import ChunkingResource

    chunks = _speaker_turn_chunks(
        segment_merge_result["segments"],
        segment_merge_result["document_id"],
        segment_merge_result["title"],
        ChunkingResource(),
        {"source": "media_ingest", "language": segment_merge_result.get("language", "unknown")},
    )

    print(f"\n  Chunking: {len(segment_merge_result['segments'])} turns → {len(chunks)} chunks")
    _save_fixture("chunks", [c.model_dump() for c in chunks])
    return chunks


def test_chunks_produced(chunks_result):
    assert len(chunks_result) > 0


def test_chunks_have_speaker(chunks_result):
    for c in chunks_result:
        assert "speaker" in c.metadata


def test_chunks_have_timestamps(chunks_result):
    for c in chunks_result:
        assert c.metadata["end_s"] >= c.metadata["start_s"]


def test_chunks_have_strategy(chunks_result):
    for c in chunks_result:
        assert c.metadata["strategy"] in ("speaker_turn", "speech_pause_split")


def test_split_chunks_have_precise_timestamps(chunks_result):
    split = [c for c in chunks_result if c.metadata["strategy"] == "speech_pause_split"]
    if len(split) > 1:
        starts = {c.metadata["start_s"] for c in split}
        assert len(starts) > 1, "All split chunks have the same start_s"


# ── Step 5: Validated Extraction (Mentions + Assertions) ────────────────


def _llm_model() -> str:
    return os.environ.get("LLM_MODEL", "gpt-4o-mini")


def _needs_llm():
    base_url = os.environ.get("LLM_BASE_URL", "")
    is_local = "localhost" in base_url or "127.0.0.1" in base_url or "192.168" in base_url
    if not is_local and not (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        pytest.skip("LLM_API_KEY / OPENAI_API_KEY not set — required for non-local extraction")


@pytest.fixture(scope="session")
def extraction_result(chunks_result):
    """Run validated extraction via the actual dagster_io.extraction pipeline.

    Uses extract_validated() — the same code path as production Dagster assets.
    Runs on a benchmark subset (4 chunks) for fast iteration. Fixtures are
    saved per-model so you can compare results across models:
        LLM_MODEL=gpt-4o-mini pytest ... -k extraction
        LLM_MODEL=gpt-4o      pytest ... -k extraction
    """
    model = _llm_model()
    cached = _load_fixture(f"extraction_{model}")
    if cached:
        return cached

    _needs_llm()

    # Use shared prompts dir (has extraction + repair prompts for the full cycle).
    shared_prompts = Path(__file__).resolve().parents[0] / ".." / "k8s" / "shared" / "prompts"
    if shared_prompts.exists():
        os.environ.setdefault("PROMPT_REGISTRY_DIR", str(shared_prompts.resolve()))

    # Use benchmark subset (4 representative chunks) instead of all 15.
    # Full set takes too long with local models (~30-60s per chunk).
    benchmark_chunks_path = FIXTURES / "benchmark_chunks.json"
    if benchmark_chunks_path.exists():
        from dagster_io import TextChunk

        benchmark_data = json.loads(benchmark_chunks_path.read_text())
        eval_chunks = [TextChunk(**c) for c in benchmark_data]
        print(f"\n  Using benchmark subset: {len(eval_chunks)} chunks (from benchmark_chunks.json)")
    else:
        eval_chunks = chunks_result
        print(f"\n  Using full chunk set: {len(eval_chunks)} chunks")

    from dagster_io.extraction import extract_validated

    print(f"  Running validated extraction (model={model}, concurrency=1)...")
    start = time.monotonic()
    mentions, assertions = extract_validated(
        eval_chunks,
        code_location="media_ingest",
        max_concurrency=1,  # sequential — safe for local model RAM
    )
    duration = time.monotonic() - start

    # Grab pipeline stats (retries, errors) from extract_validated
    pipeline_stats = getattr(extract_validated, "last_stats", {})

    # Compute performance stats
    total_input_chars = sum(len(c.text) for c in chunks_result)
    est_input_tokens = total_input_chars // 4  # ~4 chars per token
    est_output_tokens = (len(mentions) + len(assertions)) * 50
    est_total_tokens = est_input_tokens + est_output_tokens
    tokens_per_sec = est_total_tokens / duration if duration > 0 else 0

    output = {
        "model": model,
        "base_url": os.environ.get("LLM_BASE_URL", ""),
        "structured_method": os.environ.get("LLM_STRUCTURED_METHOD", "function_calling"),
        "mentions": [m.model_dump(mode="json") for m in mentions],
        "assertions": [a.model_dump(mode="json") for a in assertions],
        "stats": {
            "chunk_count": len(chunks_result),
            "duration_s": round(duration, 1),
            "total_input_chars": total_input_chars,
            "est_total_tokens": est_total_tokens,
            "tokens_per_sec": round(tokens_per_sec, 1),
            "mention_count": len(mentions),
            "assertion_count": len(assertions),
            "mention_retries": pipeline_stats.get("mention_retries", 0),
            "proposition_retries": pipeline_stats.get("proposition_retries", 0),
            "errors": pipeline_stats.get("errors", 0),
        },
    }

    print(f"  Extraction complete: {len(mentions)} mentions, {len(assertions)} assertions in {duration:.1f}s")
    print(f"  Estimated {est_total_tokens:,} tokens, {tokens_per_sec:.1f} tok/s")
    print(
        f"  Retries: {pipeline_stats.get('mention_retries', 0)} mention, "
        f"{pipeline_stats.get('proposition_retries', 0)} proposition, "
        f"{pipeline_stats.get('errors', 0)} errors"
    )
    _save_fixture(f"extraction_{model}", output)
    return output


@pytest.mark.llm
def test_extraction_produces_mentions(extraction_result):
    assert len(extraction_result["mentions"]) > 0, "Should extract at least one mention"


@pytest.mark.llm
def test_extraction_produces_assertions(extraction_result):
    assert len(extraction_result["assertions"]) > 0, "Should extract at least one assertion"


@pytest.mark.llm
def test_mentions_have_valid_types(extraction_result):
    from catalyst_contracts_core.enums import MentionType

    valid_types = {t.value for t in MentionType}
    for m in extraction_result["mentions"]:
        assert m["mention_type"] in valid_types, f"Invalid type: {m['mention_type']}"


@pytest.mark.llm
def test_mentions_have_valid_spans(extraction_result, chunks_result):
    """Verify span offsets actually match the source text."""
    chunk_texts = {c.chunk_id: c.text for c in chunks_result}
    checked = 0
    for m in extraction_result["mentions"]:
        s, e = m.get("span_start"), m.get("span_end")
        chunk_id = m.get("chunk_id", "")
        if s is not None and e is not None and chunk_id in chunk_texts:
            source = chunk_texts[chunk_id]
            if 0 <= s < e <= len(source):
                assert source[s:e] == m["text"], f"Span mismatch: '{m['text']}' != source[{s}:{e}]='{source[s:e]}'"
                checked += 1
    print(f"\n  Verified {checked} mention spans against source text")


@pytest.mark.llm
def test_assertions_have_valid_structure(extraction_result):
    for a in extraction_result["assertions"]:
        subject = a.get("subject_text") or a.get("subject", "")
        predicate = a.get("predicate", "")
        obj = a.get("object_text") or a.get("object", "")
        assert subject, f"Assertion missing subject: {a}"
        assert predicate, f"Assertion missing predicate: {a}"
        assert obj, f"Assertion missing object: {a}"


@pytest.mark.llm
def test_extraction_type_distribution(extraction_result):
    """Print mention type distribution for inspection."""
    types: dict[str, int] = {}
    for m in extraction_result["mentions"]:
        t = m.get("mention_type", "?")
        types[t] = types.get(t, 0) + 1
    print(f"\n  Mention type distribution (model={extraction_result['model']}):")
    for t, c in sorted(types.items(), key=lambda x: -x[1]):
        print(f"    {t}: {c}")
    print(f"  Total: {sum(types.values())} mentions, {len(extraction_result['assertions'])} assertions")

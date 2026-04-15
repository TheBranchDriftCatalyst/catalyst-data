"""Tests for speaker-aware chunking with speech pause splitting."""

from media_ingest.assets.chunks import MAX_CHUNK_CHARS, _speaker_turn_chunks, _split_on_pauses

from dagster_io import ChunkingResource


def _seg(text: str, speaker: str = "SPEAKER_01", start: float = 0, end: float = 10, words: list | None = None) -> dict:
    return {"text": text, "speaker": speaker, "start": start, "end": end, "words": words or []}


def test_short_turns_kept_whole():
    segments = [
        _seg("Hello, welcome to the show.", "SPEAKER_01", 0, 5),
        _seg("Thanks for having me.", "SPEAKER_02", 5, 8),
    ]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})
    assert len(chunks) == 2
    assert chunks[0].metadata["strategy"] == "speaker_turn"


def test_title_prepended():
    segments = [_seg("Some text.", "SPEAKER_01")]
    chunks = _speaker_turn_chunks(segments, "doc-1", "My Video", ChunkingResource(), {})
    assert chunks[0].text.startswith("My Video\n\n")


def test_empty_segments():
    assert _speaker_turn_chunks([], "doc-1", "", ChunkingResource(), {}) == []


def test_speaker_metadata_preserved():
    segments = [_seg("Hello.", "SPEAKER_02", 10.5, 12.3)]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})
    assert chunks[0].metadata["speaker"] == "SPEAKER_02"
    assert chunks[0].metadata["start_s"] == 10.5
    assert chunks[0].metadata["end_s"] == 12.3


def test_total_chunks_backfilled():
    segments = [_seg("One.", "S1", 0, 5), _seg("Two.", "S2", 5, 10)]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})
    assert all(c.total_chunks == 2 for c in chunks)


# ── Speech pause splitting ───────────────────────────────────────────


def test_split_on_pauses_basic():
    """Words with a 1s+ gap get split into separate segments."""
    words = [
        {"word": " Hello", "start": 0.0, "end": 0.5},
        {"word": " world.", "start": 0.5, "end": 1.0},
        # 2 second pause here
        {"word": " Goodbye", "start": 3.0, "end": 3.5},
        {"word": " now.", "start": 3.5, "end": 4.0},
    ]
    subs = _split_on_pauses(words, "Hello world. Goodbye now.", pause_threshold=1.0)
    assert len(subs) == 2
    assert subs[0]["end"] == 1.0
    assert subs[1]["start"] == 3.0


def test_split_on_pauses_no_pauses():
    """No pauses above threshold returns single segment."""
    words = [
        {"word": " Hello", "start": 0.0, "end": 0.3},
        {"word": " world.", "start": 0.3, "end": 0.6},
    ]
    subs = _split_on_pauses(words, "Hello world.", pause_threshold=1.0)
    assert len(subs) == 1


def test_split_on_pauses_empty_words():
    subs = _split_on_pauses([], "some text")
    assert len(subs) == 1
    assert subs[0]["text"] == "some text"


def test_oversized_turn_splits_at_pauses():
    """Long monologue with speech pauses splits at pause points."""
    words = []
    for i in range(300):
        # Add a 1.5s pause every 60 words
        gap = 1.5 if i > 0 and i % 60 == 0 else 0.02
        start = words[-1]["end"] + gap if words else 0.0
        words.append({"word": f" word{i}", "start": start, "end": start + 0.3})

    long_text = " ".join(f"word{i}" for i in range(300))
    assert len(long_text) > MAX_CHUNK_CHARS

    segments = [_seg(long_text, "SPEAKER_01", 0, words[-1]["end"], words=words)]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})

    # Should split at the 3 pause points (after word 50, 100, 150) = 4 chunks
    pause_chunks = [c for c in chunks if c.metadata["strategy"] == "speech_pause_split"]
    assert len(pause_chunks) >= 3

    # Timestamps should be sequential
    for i in range(1, len(chunks)):
        assert chunks[i].metadata["start_s"] >= chunks[i - 1].metadata["start_s"]


def test_pause_split_timestamps_are_precise():
    """Each pause-split chunk has exact word-level start/end timestamps."""
    words = [
        {"word": " First", "start": 10.0, "end": 10.5},
        {"word": " sentence.", "start": 10.5, "end": 11.0},
        # 2s pause
        {"word": " Second", "start": 13.0, "end": 13.5},
        {"word": " sentence.", "start": 13.5, "end": 14.0},
    ]
    subs = _split_on_pauses(words, "First sentence. Second sentence.", pause_threshold=1.0)
    assert subs[0]["start"] == 10.0
    assert subs[0]["end"] == 11.0
    assert subs[1]["start"] == 13.0
    assert subs[1]["end"] == 14.0

"""Tests for speaker-aware chunking."""

from media_ingest.assets.chunks import MAX_CHUNK_CHARS, _resolve_sub_chunk_timestamps, _speaker_turn_chunks

from dagster_io import ChunkingResource


def _seg(text: str, speaker: str = "SPEAKER_01", start: float = 0, end: float = 10, words: list | None = None) -> dict:
    return {"text": text, "speaker": speaker, "start": start, "end": end, "words": words or []}


def test_short_turns_kept_whole():
    """Speaker turns under MAX_CHUNK_CHARS become single chunks."""
    segments = [
        _seg("Hello, welcome to the show.", "SPEAKER_01", 0, 5),
        _seg("Thanks for having me.", "SPEAKER_02", 5, 8),
    ]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})
    assert len(chunks) == 2
    assert "[SPEAKER_01]" in chunks[0].text
    assert "[SPEAKER_02]" in chunks[1].text
    assert chunks[0].metadata["strategy"] == "speaker_turn"


def test_oversized_turn_gets_split():
    """Turn over MAX_CHUNK_CHARS is split into sub-chunks."""
    long_text = "This is a very long monologue. " * 200  # ~6000 chars
    assert len(long_text) > MAX_CHUNK_CHARS
    segments = [_seg(long_text, "SPEAKER_01")]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})
    assert len(chunks) > 1
    assert all(c.metadata["strategy"] == "speaker_turn_split" for c in chunks)


def test_mixed_turns():
    """Mix of short and long turns produces correct strategies."""
    segments = [
        _seg("Short turn.", "SPEAKER_01", 0, 2),
        _seg("Another long monologue. " * 200, "SPEAKER_02", 2, 60),
        _seg("Quick reply.", "SPEAKER_01", 60, 62),
    ]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})
    strategies = [c.metadata["strategy"] for c in chunks]
    assert strategies[0] == "speaker_turn"
    assert "speaker_turn_split" in strategies  # middle long turn was split
    assert strategies[-1] == "speaker_turn"


def test_title_prepended():
    """Title is prepended to each chunk."""
    segments = [_seg("Some text.", "SPEAKER_01")]
    chunks = _speaker_turn_chunks(segments, "doc-1", "My Video Title", ChunkingResource(), {})
    assert chunks[0].text.startswith("My Video Title\n\n")


def test_empty_segments():
    """Empty segment list returns empty chunks."""
    chunks = _speaker_turn_chunks([], "doc-1", "", ChunkingResource(), {})
    assert chunks == []


def test_speaker_metadata_preserved():
    """Each chunk has speaker label and timestamps in metadata."""
    segments = [_seg("Hello there.", "SPEAKER_02", 10.5, 12.3)]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})
    assert chunks[0].metadata["speaker"] == "SPEAKER_02"
    assert chunks[0].metadata["start_s"] == 10.5
    assert chunks[0].metadata["end_s"] == 12.3


def test_total_chunks_backfilled():
    """total_chunks is set correctly on all chunks."""
    segments = [
        _seg("Turn one.", "S1", 0, 5),
        _seg("Turn two.", "S2", 5, 10),
        _seg("Turn three.", "S1", 10, 15),
    ]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})
    assert all(c.total_chunks == 3 for c in chunks)


def test_sub_chunk_word_level_timestamps():
    """Split sub-chunks get precise timestamps from word-level data."""
    words = [
        {"word": "First", "start": 0.0, "end": 0.5},
        {"word": "sentence", "start": 0.5, "end": 1.0},
        {"word": "here.", "start": 1.0, "end": 1.5},
        {"word": "Second", "start": 2.0, "end": 2.5},
        {"word": "sentence", "start": 2.5, "end": 3.0},
        {"word": "there.", "start": 3.0, "end": 3.5},
    ]
    start, end = _resolve_sub_chunk_timestamps("First sentence here.", words, 0.0, 10.0)
    assert start == 0.0
    assert end == 1.5


def test_sub_chunk_timestamps_with_speaker_prefix():
    """Speaker prefix is stripped before matching words."""
    words = [
        {"word": "Hello", "start": 5.0, "end": 5.5},
        {"word": "world.", "start": 5.5, "end": 6.0},
    ]
    start, end = _resolve_sub_chunk_timestamps("[SPEAKER_01] Hello world.", words, 0.0, 100.0)
    assert start == 5.0
    assert end == 6.0


def test_sub_chunk_timestamps_fallback():
    """Falls back to segment boundaries when no words available."""
    start, end = _resolve_sub_chunk_timestamps("some text", [], 10.0, 20.0)
    assert start == 10.0
    assert end == 20.0


def test_oversized_turn_has_word_timestamps():
    """Split monologue sub-chunks carry word-level start_s/end_s."""
    # Build a long turn with word timestamps
    word_list = []
    text_parts = []
    for i in range(200):
        w = f"word{i}"
        text_parts.append(w)
        word_list.append({"word": w, "start": float(i), "end": float(i) + 0.5})
    long_text = " ".join(text_parts)

    segments = [_seg(long_text, "SPEAKER_01", 0, 200, words=word_list)]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})

    # First chunk should start near 0
    assert chunks[0].metadata["start_s"] < 5.0
    # Last chunk should end near 200
    assert chunks[-1].metadata["end_s"] > 100.0
    # Each sub-chunk has its own timestamp range, not the parent's full range
    if len(chunks) > 1:
        assert chunks[0].metadata["end_s"] < chunks[-1].metadata["end_s"]

"""Tests for speaker-aware chunking."""

from media_ingest.assets.chunks import MAX_CHUNK_CHARS, _speaker_turn_chunks

from dagster_io import ChunkingResource


def _seg(text: str, speaker: str = "SPEAKER_01", start: float = 0, end: float = 10) -> dict:
    return {"text": text, "speaker": speaker, "start": start, "end": end}


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

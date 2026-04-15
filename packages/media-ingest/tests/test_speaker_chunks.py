"""Tests for speaker-aware chunking."""

from media_ingest.assets.chunks import MAX_CHUNK_CHARS, _speaker_turn_chunks

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
    assert "Hello" in chunks[0].text


def test_no_speaker_prefix_in_text():
    """Chunk text should NOT have [SPEAKER_XX] prefix."""
    segments = [_seg("Some text here.", "SPEAKER_01")]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})
    assert "[SPEAKER_01]" not in chunks[0].text
    assert "Some text here." in chunks[0].text


def test_oversized_turn_gets_split():
    long_text = "This is a very long monologue sentence. " * 100
    assert len(long_text) > MAX_CHUNK_CHARS
    segments = [_seg(long_text, "SPEAKER_01")]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})
    assert len(chunks) > 1
    assert all(c.metadata["strategy"] == "speaker_turn_split" for c in chunks)


def test_mixed_turns():
    segments = [
        _seg("Short turn.", "SPEAKER_01", 0, 2),
        _seg("Another long monologue. " * 100, "SPEAKER_02", 2, 60),
        _seg("Quick reply.", "SPEAKER_01", 60, 62),
    ]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})
    assert chunks[0].metadata["strategy"] == "speaker_turn"
    assert "speaker_turn_split" in [c.metadata["strategy"] for c in chunks]
    assert chunks[-1].metadata["strategy"] == "speaker_turn"


def test_title_prepended():
    segments = [_seg("Some text.", "SPEAKER_01")]
    chunks = _speaker_turn_chunks(segments, "doc-1", "My Video Title", ChunkingResource(), {})
    assert chunks[0].text.startswith("My Video Title\n\n")


def test_empty_segments():
    assert _speaker_turn_chunks([], "doc-1", "", ChunkingResource(), {}) == []


def test_speaker_metadata_preserved():
    segments = [_seg("Hello there.", "SPEAKER_02", 10.5, 12.3)]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})
    assert chunks[0].metadata["speaker"] == "SPEAKER_02"
    assert chunks[0].metadata["start_s"] == 10.5
    assert chunks[0].metadata["end_s"] == 12.3


def test_total_chunks_backfilled():
    segments = [_seg("Turn one.", "S1", 0, 5), _seg("Turn two.", "S2", 5, 10), _seg("Turn three.", "S1", 10, 15)]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})
    assert all(c.total_chunks == 3 for c in chunks)


def test_oversized_turn_has_sequential_timestamps():
    """Split sub-chunks have sequential timestamps from word-level data."""
    word_list = []
    text_parts = []
    for i in range(300):
        w = f" something{i:04d}"
        text_parts.append(w)
        word_list.append({"word": w, "start": float(i), "end": float(i) + 0.5})
    long_text = "".join(text_parts)
    assert len(long_text) > MAX_CHUNK_CHARS

    segments = [_seg(long_text, "SPEAKER_01", 0, 300, words=word_list)]
    chunks = _speaker_turn_chunks(segments, "doc-1", "", ChunkingResource(), {})

    assert len(chunks) > 1
    assert chunks[0].metadata["start_s"] < 5.0
    assert chunks[-1].metadata["end_s"] > 100.0

    for i in range(1, len(chunks)):
        prev_start = chunks[i - 1].metadata["start_s"]
        curr_start = chunks[i].metadata["start_s"]
        assert curr_start >= prev_start, f"chunk-{i} start ({curr_start}) before chunk-{i-1} ({prev_start})"

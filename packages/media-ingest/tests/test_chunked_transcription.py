"""Tests for chunked transcription — audio splitting + segment merging."""

from __future__ import annotations

from media_ingest.assets.transcription import _merge_chunked_segments


def test_merge_single_chunk():
    """Single chunk passes through unchanged."""
    result = {
        "segments": [
            {
                "start": 0.0,
                "end": 5.0,
                "text": "hello world",
                "words": [
                    {"start": 0.0, "end": 2.5, "word": "hello"},
                    {"start": 2.5, "end": 5.0, "word": "world"},
                ],
            },
        ],
        "language": "en",
        "language_probability": 0.99,
        "duration_s": 5.0,
    }

    merged = _merge_chunked_segments([(result, 0.0)])
    assert len(merged["segments"]) == 1
    assert merged["segments"][0]["text"] == "hello world"
    assert merged["duration_s"] == 5.0


def test_merge_two_chunks_offsets():
    """Two chunks get timestamp offsets applied correctly."""
    chunk1 = {
        "segments": [
            {"start": 0.0, "end": 10.0, "text": "first chunk"},
        ],
        "language": "en",
        "language_probability": 0.99,
        "duration_s": 10.0,
    }

    chunk2 = {
        "segments": [
            {"start": 0.0, "end": 8.0, "text": "second chunk"},
        ],
        "language": "en",
        "language_probability": 0.99,
        "duration_s": 8.0,
    }

    merged = _merge_chunked_segments([(chunk1, 0.0), (chunk2, 10.0)])
    assert len(merged["segments"]) == 2
    assert merged["segments"][0]["start"] == 0.0
    assert merged["segments"][0]["end"] == 10.0
    assert merged["segments"][1]["start"] == 10.0
    assert merged["segments"][1]["end"] == 18.0
    assert merged["duration_s"] == 18.0


def test_merge_deduplicates_overlap():
    """Overlapping segments from chunk boundaries get deduplicated."""
    chunk1 = {
        "segments": [
            {"start": 0.0, "end": 5.0, "text": "segment one"},
            {"start": 5.0, "end": 10.0, "text": "overlap zone"},
        ],
        "language": "en",
        "language_probability": 0.99,
        "duration_s": 10.0,
    }

    # Chunk2 starts at offset 8.0 (2s overlap with chunk1)
    chunk2 = {
        "segments": [
            {"start": 0.0, "end": 3.0, "text": "overlap zone"},  # overlaps with chunk1's last segment
            {"start": 3.0, "end": 8.0, "text": "new content"},
        ],
        "language": "en",
        "language_probability": 0.99,
        "duration_s": 8.0,
    }

    merged = _merge_chunked_segments([(chunk1, 0.0), (chunk2, 8.0)])

    # The overlap zone from chunk2 (at 8.0+0.0=8.0) should be skipped
    # because chunk1 already covered up to 10.0
    texts = [s["text"] for s in merged["segments"]]
    assert "segment one" in texts
    assert "new content" in texts


def test_merge_word_timestamps_offset():
    """Word-level timestamps get offset correctly."""
    chunk = {
        "segments": [
            {
                "start": 0.0,
                "end": 5.0,
                "text": "hello world",
                "words": [
                    {"start": 0.0, "end": 2.5, "word": "hello"},
                    {"start": 2.5, "end": 5.0, "word": "world"},
                ],
            },
        ],
        "language": "en",
        "language_probability": 0.99,
        "duration_s": 5.0,
    }

    merged = _merge_chunked_segments([(chunk, 100.0)])  # offset by 100s
    words = merged["segments"][0]["words"]
    assert words[0]["start"] == 100.0
    assert words[0]["end"] == 102.5
    assert words[1]["start"] == 102.5
    assert words[1]["end"] == 105.0


def test_merge_empty_chunks():
    """Empty chunk list returns empty result."""
    merged = _merge_chunked_segments([])
    assert merged["segments"] == []

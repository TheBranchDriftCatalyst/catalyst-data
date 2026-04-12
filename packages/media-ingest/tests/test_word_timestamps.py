"""Unit tests for _estimate_word_timestamps (OpenVINO word timing fallback)."""

from media_ingest.assets.transcription import _estimate_word_timestamps


def test_basic_word_splitting():
    """Words are produced for each whitespace-delimited token."""
    words = _estimate_word_timestamps("hello world", 0.0, 2.0)
    assert len(words) == 2
    assert words[0]["word"] == "hello"
    assert words[1]["word"] == " world"


def test_timestamps_cover_segment():
    """First word starts at seg_start, last word ends at seg_end."""
    words = _estimate_word_timestamps("the quick brown fox", 1.0, 5.0)
    assert words[0]["start"] == 1.0
    assert abs(words[-1]["end"] - 5.0) < 0.01


def test_timestamps_are_contiguous():
    """Each word starts where the previous one ended (no gaps)."""
    words = _estimate_word_timestamps("one two three four five", 0.0, 10.0)
    for i in range(1, len(words)):
        assert abs(words[i]["start"] - words[i - 1]["end"]) < 0.001


def test_probability_is_zero():
    """Estimated words have probability 0.0 (no confidence available)."""
    words = _estimate_word_timestamps("test words", 0.0, 1.0)
    assert all(w["probability"] == 0.0 for w in words)


def test_empty_text_returns_empty():
    """Empty or whitespace-only text returns no words."""
    assert _estimate_word_timestamps("", 0.0, 1.0) == []
    assert _estimate_word_timestamps("   ", 0.0, 1.0) == []


def test_single_word():
    """A single word spans the entire segment."""
    words = _estimate_word_timestamps("hello", 2.0, 4.0)
    assert len(words) == 1
    assert words[0]["word"] == "hello"
    assert words[0]["start"] == 2.0
    assert abs(words[0]["end"] - 4.0) < 0.01


def test_leading_space_convention():
    """All words after the first have a leading space (matches faster-whisper)."""
    words = _estimate_word_timestamps("one two three", 0.0, 3.0)
    assert not words[0]["word"].startswith(" ")
    for w in words[1:]:
        assert w["word"].startswith(" ")

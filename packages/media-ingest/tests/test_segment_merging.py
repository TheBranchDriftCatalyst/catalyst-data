"""Tests for _merge_same_speaker_segments in diarization.py."""

from media_ingest.assets.diarization import _merge_same_speaker_segments


def _seg(start, end, text, speaker="S0", words=None):
    s = {"start": start, "end": end, "text": text, "speaker": speaker}
    if words is not None:
        s["words"] = words
    return s


def test_merge_same_speaker_consecutive():
    segments = [
        _seg(0, 1, "Hello"),
        _seg(1.2, 2, "world"),
        _seg(2.1, 3, "how are you"),
    ]
    merged = _merge_same_speaker_segments(segments, gap_threshold_s=1.5)
    assert len(merged) == 1
    assert merged[0]["text"] == "Hello world how are you"
    assert merged[0]["start"] == 0
    assert merged[0]["end"] == 3
    assert merged[0]["speaker"] == "S0"


def test_no_merge_different_speakers():
    segments = [
        _seg(0, 1, "Hello", speaker="S0"),
        _seg(1.2, 2, "Hi there", speaker="S1"),
    ]
    merged = _merge_same_speaker_segments(segments, gap_threshold_s=1.5)
    assert len(merged) == 2


def test_no_merge_large_gap():
    segments = [
        _seg(0, 1, "Hello"),
        _seg(5, 6, "world"),  # 4s gap
    ]
    merged = _merge_same_speaker_segments(segments, gap_threshold_s=1.5)
    assert len(merged) == 2


def test_merge_preserves_words():
    segments = [
        _seg(0, 1, "Hello", words=[{"word": "Hello", "start": 0, "end": 1}]),
        _seg(1.1, 2, "world", words=[{"word": " world", "start": 1.1, "end": 2}]),
    ]
    merged = _merge_same_speaker_segments(segments, gap_threshold_s=1.5)
    assert len(merged) == 1
    assert len(merged[0]["words"]) == 2


def test_merge_speaker_change_boundary():
    """Speaker changes create segment boundaries even with small gaps."""
    segments = [
        _seg(0, 1, "I think", speaker="S0"),
        _seg(1.1, 2, "that's right", speaker="S0"),
        _seg(2.1, 3, "No way", speaker="S1"),
        _seg(3.1, 4, "really", speaker="S1"),
        _seg(4.1, 5, "Yes", speaker="S0"),
    ]
    merged = _merge_same_speaker_segments(segments, gap_threshold_s=1.5)
    assert len(merged) == 3
    assert merged[0]["text"] == "I think that's right"
    assert merged[0]["speaker"] == "S0"
    assert merged[1]["text"] == "No way really"
    assert merged[1]["speaker"] == "S1"
    assert merged[2]["text"] == "Yes"
    assert merged[2]["speaker"] == "S0"


def test_empty_segments():
    assert _merge_same_speaker_segments([]) == []


def test_single_segment():
    segments = [_seg(0, 1, "Hello")]
    merged = _merge_same_speaker_segments(segments, gap_threshold_s=1.5)
    assert len(merged) == 1
    assert merged[0]["text"] == "Hello"


def test_none_speaker_merges():
    """Segments with None speaker should merge with other None speakers."""
    segments = [
        _seg(0, 1, "Hello", speaker=None),
        _seg(1.1, 2, "world", speaker=None),
    ]
    merged = _merge_same_speaker_segments(segments, gap_threshold_s=1.5)
    assert len(merged) == 1

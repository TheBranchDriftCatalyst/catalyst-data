"""Tests for speaker-aware chunking with speech pause splitting."""

from media_ingest.assets.chunks import MAX_CHUNK_CHARS, _speaker_turn_chunks, _split_on_pauses

from dagster_io import ChunkingResource


def _seg(text, speaker="SPEAKER_01", start=0.0, end=10.0, words=None):
    return {"text": text, "speaker": speaker, "start": start, "end": end, "words": words or []}


# ── Speaker turn strategy ────────────────────────────────────────────


class TestSpeakerTurns:
    def test_short_turns_kept_whole(self):
        chunks = _speaker_turn_chunks(
            [_seg("Hello.", "S1", 0, 5), _seg("Hi.", "S2", 5, 8)], "d", "", ChunkingResource(), {}
        )
        assert len(chunks) == 2
        assert all(c.metadata["strategy"] == "speaker_turn" for c in chunks)

    def test_title_prepended(self):
        chunks = _speaker_turn_chunks([_seg("Hello.")], "d", "Title", ChunkingResource(), {})
        assert chunks[0].text.startswith("Title\n\n")

    def test_empty_segments(self):
        assert _speaker_turn_chunks([], "d", "", ChunkingResource(), {}) == []

    def test_metadata_preserved(self):
        chunks = _speaker_turn_chunks([_seg("Hello.", "S2", 10.5, 12.3)], "d", "", ChunkingResource(), {})
        assert chunks[0].metadata["speaker"] == "S2"
        assert chunks[0].metadata["start_s"] == 10.5
        assert chunks[0].metadata["end_s"] == 12.3

    def test_total_chunks_backfilled(self):
        chunks = _speaker_turn_chunks([_seg("A.", "S1", 0, 5), _seg("B.", "S2", 5, 10)], "d", "", ChunkingResource(), {})
        assert all(c.total_chunks == 2 for c in chunks)

    def test_no_speaker_prefix_in_text(self):
        chunks = _speaker_turn_chunks([_seg("Hello.")], "d", "", ChunkingResource(), {})
        assert "[SPEAKER" not in chunks[0].text


# ── Speech pause splitting ───────────────────────────────────────────


class TestPauseSplitting:
    def test_splits_at_pause(self):
        words = [
            {"word": " Hello", "start": 0.0, "end": 0.5},
            {"word": " world.", "start": 0.5, "end": 1.0},
            {"word": " Goodbye", "start": 3.0, "end": 3.5},
            {"word": " now.", "start": 3.5, "end": 4.0},
        ]
        subs = _split_on_pauses(words, "Hello world. Goodbye now.", threshold=1.0)
        assert len(subs) == 2
        assert subs[0].end == 1.0
        assert subs[1].start == 3.0

    def test_no_qualifying_pauses_returns_single(self):
        words = [{"word": " Hello", "start": 0.0, "end": 0.3}, {"word": " world.", "start": 0.3, "end": 0.6}]
        subs = _split_on_pauses(words, "Hello world.", threshold=1.0)
        assert len(subs) == 1

    def test_empty_words_returns_single(self):
        subs = _split_on_pauses([], "some text")
        assert len(subs) == 1
        assert subs[0].text == "some text"

    def test_precise_timestamps(self):
        words = [
            {"word": " First", "start": 10.0, "end": 10.5},
            {"word": " sentence.", "start": 10.5, "end": 11.0},
            {"word": " Second", "start": 13.0, "end": 13.5},
            {"word": " sentence.", "start": 13.5, "end": 14.0},
        ]
        subs = _split_on_pauses(words, "First sentence. Second sentence.", threshold=1.0)
        assert subs[0].start == 10.0
        assert subs[0].end == 11.0
        assert subs[1].start == 13.0
        assert subs[1].end == 14.0


# ── End-to-end ───────────────────────────────────────────────────────


class TestEndToEnd:
    def test_oversized_turn_splits_at_pauses(self):
        words = []
        for i in range(300):
            gap = 1.5 if i > 0 and i % 60 == 0 else 0.02
            start = words[-1]["end"] + gap if words else 0.0
            words.append({"word": f" word{i}", "start": start, "end": start + 0.3})
        long_text = " ".join(f"word{i}" for i in range(300))
        assert len(long_text) > MAX_CHUNK_CHARS

        chunks = _speaker_turn_chunks([_seg(long_text, "S1", 0, words[-1]["end"], words)], "d", "", ChunkingResource(), {})
        pause_chunks = [c for c in chunks if c.metadata["strategy"] == "speech_pause_split"]
        assert len(pause_chunks) >= 3

        for i in range(1, len(chunks)):
            assert chunks[i].metadata["start_s"] >= chunks[i - 1].metadata["start_s"]

    def test_mixed_strategies(self):
        """Short turn + long monologue with pauses produces mixed strategies."""
        words = []
        for i in range(300):
            gap = 2.0 if i > 0 and i % 80 == 0 else 0.02
            start = words[-1]["end"] + gap if words else 100.0
            words.append({"word": f" word{i}", "start": start, "end": start + 0.3})
        long_text = " ".join(f"word{i}" for i in range(300))

        segments = [_seg("Short reply.", "S1", 0, 2), _seg(long_text, "S2", 100, words[-1]["end"], words)]
        chunks = _speaker_turn_chunks(segments, "d", "", ChunkingResource(), {})

        strategies = {c.metadata["strategy"] for c in chunks}
        assert "speaker_turn" in strategies
        assert "speech_pause_split" in strategies

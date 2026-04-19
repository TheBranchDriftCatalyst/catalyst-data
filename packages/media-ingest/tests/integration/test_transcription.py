"""Tests for transcription internals: chunked merging, word timestamps, text normalization."""

from media_ingest.assets.transcription import (
    _estimate_word_timestamps,
    _merge_chunked_segments,
    _normalize_text,
)

# ═══════════════════════════════════════════════════════════════════════════
# Chunked transcription merging
# ═══════════════════════════════════════════════════════════════════════════


class TestMergeChunkedSegments:
    def test_single_chunk(self):
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

    def test_two_chunks_offsets(self):
        chunk1 = {
            "segments": [{"start": 0.0, "end": 10.0, "text": "first chunk"}],
            "language": "en",
            "language_probability": 0.99,
            "duration_s": 10.0,
        }
        chunk2 = {
            "segments": [{"start": 0.0, "end": 8.0, "text": "second chunk"}],
            "language": "en",
            "language_probability": 0.99,
            "duration_s": 8.0,
        }
        merged = _merge_chunked_segments([(chunk1, 0.0), (chunk2, 10.0)])
        assert len(merged["segments"]) == 2
        assert merged["segments"][0]["start"] == 0.0
        assert merged["segments"][1]["start"] == 10.0
        assert merged["segments"][1]["end"] == 18.0
        assert merged["duration_s"] == 18.0

    def test_deduplicates_overlap(self):
        chunk1 = {
            "segments": [
                {"start": 0.0, "end": 5.0, "text": "segment one"},
                {"start": 5.0, "end": 10.0, "text": "overlap zone"},
            ],
            "language": "en",
            "language_probability": 0.99,
            "duration_s": 10.0,
        }
        chunk2 = {
            "segments": [
                {"start": 0.0, "end": 3.0, "text": "overlap zone"},
                {"start": 3.0, "end": 8.0, "text": "new content"},
            ],
            "language": "en",
            "language_probability": 0.99,
            "duration_s": 8.0,
        }
        merged = _merge_chunked_segments([(chunk1, 0.0), (chunk2, 8.0)])
        texts = [s["text"] for s in merged["segments"]]
        assert "segment one" in texts
        assert "new content" in texts

    def test_word_timestamps_offset(self):
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
        merged = _merge_chunked_segments([(chunk, 100.0)])
        words = merged["segments"][0]["words"]
        assert words[0]["start"] == 100.0
        assert words[1]["end"] == 105.0

    def test_empty_chunks(self):
        merged = _merge_chunked_segments([])
        assert merged["segments"] == []


# ═══════════════════════════════════════════════════════════════════════════
# Word timestamp estimation
# ═══════════════════════════════════════════════════════════════════════════


class TestWordTimestamps:
    def test_basic_word_splitting(self):
        words = _estimate_word_timestamps("hello world", 0.0, 2.0)
        assert len(words) == 2
        assert words[0]["word"] == "hello"
        assert words[1]["word"] == " world"

    def test_timestamps_cover_segment(self):
        words = _estimate_word_timestamps("the quick brown fox", 1.0, 5.0)
        assert words[0]["start"] == 1.0
        assert abs(words[-1]["end"] - 5.0) < 0.01

    def test_timestamps_are_contiguous(self):
        words = _estimate_word_timestamps("one two three four five", 0.0, 10.0)
        for i in range(1, len(words)):
            assert abs(words[i]["start"] - words[i - 1]["end"]) < 0.001

    def test_probability_is_zero(self):
        words = _estimate_word_timestamps("test words", 0.0, 1.0)
        assert all(w["probability"] == 0.0 for w in words)

    def test_empty_text(self):
        assert _estimate_word_timestamps("", 0.0, 1.0) == []
        assert _estimate_word_timestamps("   ", 0.0, 1.0) == []

    def test_single_word(self):
        words = _estimate_word_timestamps("hello", 2.0, 4.0)
        assert len(words) == 1
        assert words[0]["start"] == 2.0
        assert abs(words[0]["end"] - 4.0) < 0.01

    def test_leading_space_convention(self):
        words = _estimate_word_timestamps("one two three", 0.0, 3.0)
        assert not words[0]["word"].startswith(" ")
        for w in words[1:]:
            assert w["word"].startswith(" ")


# ═══════════════════════════════════════════════════════════════════════════
# Text normalization (ALL CAPS, punctuation spacing, camelCase)
# ═══════════════════════════════════════════════════════════════════════════


class TestNormalizeText:
    def test_passthrough_normal_text(self):
        assert _normalize_text("Normal sentence that should not change.") == "Normal sentence that should not change."

    def test_passthrough_short_text(self):
        assert _normalize_text("Hi") == "Hi"

    def test_passthrough_empty(self):
        assert _normalize_text("") == ""
        assert _normalize_text(None) is None

    def test_all_caps_sentence_cased(self):
        text = "IMPORTANTLY, HE IS A PRODUCT OF A CULTURE WHICH HAS TURNED ON ITS OWN."
        result = _normalize_text(text)
        assert result[0] == "I"
        assert not result.isupper()

    def test_all_caps_multi_sentence(self):
        text = "HE IS EITHER A LITMUS TEST. HE IS ALSO A WALKING MANIFESTATION OF SOMETHING."
        result = _normalize_text(text)
        assert result.startswith("He is either")
        assert "He is also" in result

    def test_mixed_case_not_converted(self):
        text = (
            "Nick Fuentes really passed that test with flying colors. That fragmentation is being caused purposefully."
        )
        assert _normalize_text(text) == text

    def test_short_caps_not_converted(self):
        assert _normalize_text("THIS IS SHORT") == "THIS IS SHORT"

    def test_space_after_period(self):
        assert _normalize_text("Fuentes.The main reason") == "Fuentes. The main reason"

    def test_space_after_exclamation(self):
        assert _normalize_text("incredible!But then") == "incredible! But then"

    def test_space_after_question(self):
        assert _normalize_text("really?The answer") == "really? The answer"

    def test_space_after_comma(self):
        assert _normalize_text("people,especially young men") == "people, especially young men"

    def test_multiple_punctuation_fixes(self):
        assert _normalize_text("first.Second,third!Fourth?Fifth") == "first. Second, third! Fourth? Fifth"

    def test_camel_case_split(self):
        assert _normalize_text("NickFuentes") == "Nick Fuentes"

    def test_camel_case_in_sentence(self):
        assert "Nick Fuentes" in _normalize_text("talking about NickFuentes on the show")

    def test_camel_case_multiple(self):
        result = _normalize_text("JoeBiden met with BarackObama")
        assert "Joe Biden" in result
        assert "Barack Obama" in result

    def test_combined_caps_and_punctuation(self):
        text = "THE TRUTH IS IT DOESN'T MAKE A LOT OF SENSE.PEOPLE WHO SAY YOU SHOULDN'T GIVE HIM A PLATFORM."
        result = _normalize_text(text)
        assert not result.isupper()
        assert ". " in result

    def test_realistic_transcript_fragment(self):
        text = "hearingabout him is because he'spopular.A large number of people,especially young men"
        result = _normalize_text(text)
        assert ". A large" in result
        assert ", especially" in result

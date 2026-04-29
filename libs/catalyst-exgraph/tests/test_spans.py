"""Behavioral tests for span computation utilities."""

from __future__ import annotations

from catalyst_exgraph.nodes.spans import (
    compute_correct_spans,
    correct_candidate_spans,
    find_all_spans,
    find_best_span,
)

# ── find_all_spans ──────────────────────────────────────────────────────────


def test_find_all_spans_finds_multiple_occurrences(sample_source_text: str):
    spans = find_all_spans(sample_source_text, "Alice")
    assert len(spans) == 2
    for start, end in spans:
        assert sample_source_text[start:end] == "Alice"


def test_find_all_spans_returns_empty_when_not_found(sample_source_text: str):
    spans = find_all_spans(sample_source_text, "Charlie")
    assert spans == []


def test_find_all_spans_exact_offsets():
    text = "XX Alice YY Alice ZZ"
    spans = find_all_spans(text, "Alice")
    assert spans == [(3, 8), (12, 17)]
    assert text[3:8] == "Alice"
    assert text[12:17] == "Alice"


def test_find_all_spans_single_occurrence():
    text = "Hello world"
    spans = find_all_spans(text, "world")
    assert spans == [(6, 11)]


def test_find_all_spans_overlapping_pattern():
    """Overlapping matches: 'aa' in 'aaa' should find positions 0 and 1."""
    spans = find_all_spans("aaa", "aa")
    assert spans == [(0, 2), (1, 3)]


# ── find_best_span ──────────────────────────────────────────────────────────


def test_find_best_span_returns_exact_match(sample_source_text: str):
    start, end = find_best_span(sample_source_text, "Bob")
    assert sample_source_text[start:end] == "Bob"


def test_find_best_span_falls_back_to_case_insensitive():
    text = "The President spoke."
    start, end = find_best_span(text, "the president")
    # Case-insensitive match: offsets index into the ORIGINAL text
    assert text[start:end].lower() == "the president"


def test_find_best_span_returns_zero_zero_when_not_found(sample_source_text: str):
    assert find_best_span(sample_source_text, "Zephyr") == (0, 0)


def test_find_best_span_prefers_exact_over_case_insensitive():
    text = "alice met Alice"
    start, end = find_best_span(text, "Alice")
    # Exact match "Alice" is at index 10, not the lowercase "alice" at 0
    assert text[start:end] == "Alice"


# ── compute_correct_spans ──────────────────────────────────────────────────


def test_compute_correct_spans_multiple_candidates(sample_source_text: str):
    candidates = [
        {"text": "Alice"},
        {"text": "Bob"},
    ]
    result = compute_correct_spans(candidates, sample_source_text)

    assert "Alice" in result
    assert "Bob" in result
    # Alice appears twice
    assert len(result["Alice"]) == 2
    for span in result["Alice"]:
        assert sample_source_text[span["start"] : span["end"]] == "Alice"


def test_compute_correct_spans_deduplicates_by_text(sample_source_text: str):
    candidates = [
        {"text": "Alice"},
        {"text": "Alice"},  # duplicate
        {"text": "Bob"},
    ]
    result = compute_correct_spans(candidates, sample_source_text)

    # "Alice" key should appear exactly once (deduplication)
    assert list(result.keys()).count("Alice") == 1


def test_compute_correct_spans_case_insensitive_fallback():
    text = "The President signed the bill."
    candidates = [{"text": "the president"}]
    result = compute_correct_spans(candidates, text)

    assert "the president" in result
    assert len(result["the president"]) >= 1
    span = result["the president"][0]
    assert text[span["start"] : span["end"]].lower() == "the president"


def test_compute_correct_spans_missing_entity_excluded():
    text = "Alice met Bob."
    candidates = [{"text": "Charlie"}]
    result = compute_correct_spans(candidates, text)

    assert "Charlie" not in result


def test_compute_correct_spans_empty_text_candidate_skipped():
    text = "Alice met Bob."
    candidates = [{"text": ""}, {"text": "Alice"}]
    result = compute_correct_spans(candidates, text)

    assert "" not in result
    assert "Alice" in result


def test_span_start_end_correctly_indexes_source_text():
    """Verify that source[start:end] == entity for all returned spans."""
    text = "Bob went to see Bob and then Bob again."
    candidates = [{"text": "Bob"}]
    result = compute_correct_spans(candidates, text)

    for span in result["Bob"]:
        assert text[span["start"] : span["end"]] == "Bob"
    assert len(result["Bob"]) == 3


# ── correct_candidate_spans ───────────────────────────────────────────────


def test_correct_candidate_spans_fixes_wrong_offsets():
    """LLM guesses (0, 5) but 'Alice' is actually at (20, 25)."""
    text = "The quick brown fox Alice went home."
    idx = text.index("Alice")  # 20
    candidates = [
        {"text": "Alice", "span_start": 0, "span_end": 5, "mention_type": "PERSON"},
    ]
    result = correct_candidate_spans(candidates, text)
    assert result[0]["span_start"] == idx
    assert result[0]["span_end"] == idx + 5
    assert text[result[0]["span_start"] : result[0]["span_end"]] == "Alice"


def test_correct_candidate_spans_uses_proximity_hint():
    """'Alice' at 10 and 200; LLM guessed 195; should pick 200."""
    prefix = "1234567890Alice"  # Alice at 10
    middle = "x" * (200 - len(prefix))
    suffix = "Alice rest of text"
    text = prefix + middle + suffix
    # Verify our offsets
    assert text[10:15] == "Alice"
    assert text[200:205] == "Alice"

    candidates = [
        {"text": "Alice", "span_start": 195, "span_end": 200, "mention_type": "PERSON"},
    ]
    result = correct_candidate_spans(candidates, text)
    assert result[0]["span_start"] == 200
    assert result[0]["span_end"] == 205


def test_correct_candidate_spans_leaves_not_found_unchanged():
    """Text not in source; spans left as-is."""
    text = "The quick brown fox."
    candidates = [
        {"text": "Zephyr", "span_start": 5, "span_end": 11, "mention_type": "PERSON"},
    ]
    result = correct_candidate_spans(candidates, text)
    assert result[0]["span_start"] == 5
    assert result[0]["span_end"] == 11


def test_correct_candidate_spans_idempotent():
    """Already-correct spans should remain unchanged."""
    text = "Alice met Bob at the park."
    alice_idx = text.index("Alice")
    bob_idx = text.index("Bob")
    candidates = [
        {"text": "Alice", "span_start": alice_idx, "span_end": alice_idx + 5},
        {"text": "Bob", "span_start": bob_idx, "span_end": bob_idx + 3},
    ]
    result = correct_candidate_spans(candidates, text)
    assert result[0]["span_start"] == alice_idx
    assert result[0]["span_end"] == alice_idx + 5
    assert result[1]["span_start"] == bob_idx
    assert result[1]["span_end"] == bob_idx + 3


def test_correct_candidate_spans_case_insensitive():
    """'the president' matches 'The President' via case-insensitive fallback."""
    text = "The President spoke today."
    candidates = [
        {"text": "the president", "span_start": 0, "span_end": 13, "mention_type": "PERSON"},
    ]
    result = correct_candidate_spans(candidates, text)
    assert result[0]["span_start"] == 0
    assert result[0]["span_end"] == 13
    # Text should be corrected to match original case in source
    assert result[0]["text"] == "The President"


def test_correct_candidate_spans_empty_list():
    """Empty list returns empty."""
    result = correct_candidate_spans([], "some text")
    assert result == []


def test_correct_candidate_spans_avoids_duplicates():
    """Two candidates 'Alice'; one gets offset 10, other gets 200."""
    prefix = "1234567890Alice"  # Alice at 10
    middle = "x" * (200 - len(prefix))
    suffix = "Alice rest of text"
    text = prefix + middle + suffix

    candidates = [
        {"text": "Alice", "span_start": 8, "span_end": 13, "mention_type": "PERSON"},
        {"text": "Alice", "span_start": 198, "span_end": 203, "mention_type": "PERSON"},
    ]
    result = correct_candidate_spans(candidates, text)

    spans = {(c["span_start"], c["span_end"]) for c in result}
    assert (10, 15) in spans
    assert (200, 205) in spans
    assert len(spans) == 2  # no duplicates

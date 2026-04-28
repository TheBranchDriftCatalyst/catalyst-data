"""Behavioral tests for span computation utilities."""

from __future__ import annotations

from catalyst_exgraph.nodes.spans import (
    compute_correct_spans,
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

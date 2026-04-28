"""Shared span computation utilities for extraction nodes.

Extracted from catalyst-langgraph-aio repair_mentions.py and the various
client adapters (GLiNER, NuExtract, UniversalNER) which all independently
implemented span finding.
"""

from __future__ import annotations


def find_all_spans(text: str, entity_text: str) -> list[tuple[int, int]]:
    """Find all occurrences of entity_text in text, return (start, end) pairs."""
    spans = []
    start = 0
    while True:
        idx = text.find(entity_text, start)
        if idx == -1:
            break
        spans.append((idx, idx + len(entity_text)))
        start = idx + 1
    return spans


def find_best_span(
    source_text: str,
    entity_text: str,
) -> tuple[int, int]:
    """Find the best span for entity_text in source_text.

    Tries exact match first, then case-insensitive. Returns (0, 0) if not found.
    """
    spans = find_all_spans(source_text, entity_text)
    if spans:
        return spans[0]

    # Case-insensitive fallback
    lower_spans = find_all_spans(source_text.lower(), entity_text.lower())
    if lower_spans:
        return lower_spans[0]

    return (0, 0)


def compute_correct_spans(
    candidates: list[dict],
    source_text: str,
) -> dict[str, list[dict[str, int]]]:
    """Pre-compute correct span offsets for all candidate texts.

    Returns a map: {text: [{start, end}, ...]} so repair nodes get exact
    offsets instead of relying on the LLM to guess them.

    This is extracted from repair_mentions.py._find_correct_spans().
    """
    spans_map: dict[str, list[dict[str, int]]] = {}
    for candidate in candidates:
        text = candidate.get("text", "")
        if not text or text in spans_map:
            continue
        spans = find_all_spans(source_text, text)
        if not spans:
            spans = find_all_spans(source_text.lower(), text.lower())
        if spans:
            spans_map[text] = [{"start": s, "end": e} for s, e in spans]
    return spans_map

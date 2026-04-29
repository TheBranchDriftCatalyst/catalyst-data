"""Span correction utilities for v1 extraction nodes.

NOTE: The canonical implementation lives in
``catalyst_exgraph.nodes.spans.correct_candidate_spans``.
This module is a local copy because catalyst-langgraph-aio does NOT depend
on catalyst-exgraph (the dependency runs the other direction).
If the dependency is ever inverted or a shared package is extracted,
replace this with a re-export.
"""

from __future__ import annotations


def _find_all_spans(text: str, entity_text: str) -> list[tuple[int, int]]:
    """Find all occurrences of entity_text in text, return (start, end) pairs."""
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        idx = text.find(entity_text, start)
        if idx == -1:
            break
        spans.append((idx, idx + len(entity_text)))
        start = idx + 1
    return spans


def correct_candidate_spans(candidates: list[dict], source_text: str) -> list[dict]:
    """Deterministically correct span offsets on candidates using text search.

    See ``catalyst_exgraph.nodes.spans.correct_candidate_spans`` for full
    documentation.  This is a local copy to avoid a circular dependency.
    """
    if not candidates:
        return candidates

    assigned: set[tuple[int, int]] = set()

    for candidate in candidates:
        text = candidate.get("text", "")
        if not text:
            continue

        spans = _find_all_spans(source_text, text)
        case_insensitive = False
        if not spans:
            spans = _find_all_spans(source_text.lower(), text.lower())
            case_insensitive = True

        if not spans:
            continue

        hint = candidate.get("span_start", 0)

        unassigned = [s for s in spans if s not in assigned]
        pool = unassigned if unassigned else spans

        best = min(pool, key=lambda s: abs(s[0] - hint))
        assigned.add(best)

        candidate["span_start"] = best[0]
        candidate["span_end"] = best[1]

        if case_insensitive:
            candidate["text"] = source_text[best[0] : best[1]]

    return candidates

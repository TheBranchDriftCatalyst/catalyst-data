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


def correct_candidate_spans(candidates: list[dict], source_text: str) -> list[dict]:
    """Deterministically correct span offsets on candidates using text search.

    LLMs guess character-level span_start/span_end and frequently get them
    wrong (SPAN_MISMATCH validation errors).  This function finds the actual
    offsets by searching for the candidate text in the source.

    Algorithm (proximity-aware):
    1. For each candidate, search for ``candidate["text"]`` in *source_text*.
    2. If exactly 1 match  -> use it.
    3. If N matches        -> pick the one closest to the LLM's guess
       (``candidate.get("span_start", 0)``), preferring spans not yet
       assigned to another candidate with the same text.
    4. If 0 matches        -> retry case-insensitive.
    5. If still 0 matches  -> leave candidate unchanged (genuine
       hallucination; let the validator catch it).

    Candidates are modified **in-place** and also returned for convenience.
    """
    if not candidates:
        return candidates

    # Track which (start, end) spans have already been claimed so that two
    # candidates with identical text can be assigned to distinct occurrences.
    assigned: set[tuple[int, int]] = set()

    for candidate in candidates:
        text = candidate.get("text", "")
        if not text:
            continue

        # --- find all occurrences (exact, then case-insensitive) ----------
        spans = find_all_spans(source_text, text)
        case_insensitive = False
        if not spans:
            spans = find_all_spans(source_text.lower(), text.lower())
            case_insensitive = True

        if not spans:
            # Genuine hallucination — leave span fields as-is.
            continue

        # --- pick the best span -----------------------------------------
        hint = candidate.get("span_start", 0)

        # Prefer unassigned spans; fall back to any span if all are taken.
        unassigned = [s for s in spans if s not in assigned]
        pool = unassigned if unassigned else spans

        best = min(pool, key=lambda s: abs(s[0] - hint))
        assigned.add(best)

        candidate["span_start"] = best[0]
        candidate["span_end"] = best[1]

        # If we matched case-insensitively, keep the original-case text
        # from the source so downstream validators see an exact slice.
        if case_insensitive:
            candidate["text"] = source_text[best[0] : best[1]]

    return candidates

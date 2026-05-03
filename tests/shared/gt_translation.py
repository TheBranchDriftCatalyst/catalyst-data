"""Ground truth span translation helpers — chunk-frame ↔ doc-frame.

Phase 0 of the v3 chunking epic (CD-9wno).

The new GT format anchors every mention to absolute document character
positions (doc_char_start / doc_char_end) so it survives any future
chunker change.  These helpers translate between the two frames and build
the IntervalTree index used by the scorer to join GT entries to whatever
chunks the *current* chunker emits.

Translation math
----------------
Forward (chunk → doc frame):
    doc_char_start = chunk.metadata["chunk_char_offset"] + mention_span_start

Reverse (doc → chunk frame, at score time):
    chunk_span_start = doc_char_start - chunk.metadata["chunk_char_offset"]

Edge cases
----------
- chunk_char_offset is None (e.g. chunk_speaker_segments; no title prepend):
  callers must treat the chunk as un-mappable and skip doc-frame translation.
  The helpers raise ValueError so callers can catch and skip gracefully.
- GT entry overlaps multiple chunks (overlap region): the scorer picks the
  chunk with the largest overlap; duplicate-scored entries are logged as
  diagnostics.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Primitive translators
# ---------------------------------------------------------------------------


def chunk_to_doc(chunk_metadata: dict, span_start: int, span_end: int) -> tuple[int, int]:
    """Translate a chunk-relative span to document-absolute coordinates.

    Args:
        chunk_metadata: The ``metadata`` dict from a ``TextChunk`` object.
            Must contain ``chunk_char_offset`` (int).
        span_start: Chunk-relative character start (inclusive).
        span_end: Chunk-relative character end (exclusive).

    Returns:
        ``(doc_char_start, doc_char_end)`` — absolute doc-frame positions.

    Raises:
        ValueError: if ``chunk_char_offset`` is missing or None (un-mappable chunk).
        ValueError: if the resulting doc positions are negative or span is empty.
    """
    offset = chunk_metadata.get("chunk_char_offset")
    if offset is None:
        raise ValueError(
            "chunk_char_offset is None — chunk was produced by a chunker that does not "
            "track doc-frame offsets (e.g. chunk_speaker_segments without title prepend)."
        )
    doc_start = offset + span_start
    doc_end = offset + span_end
    if doc_start < 0 or doc_end < doc_start:
        raise ValueError(
            f"Invalid doc-frame span [{doc_start}:{doc_end}] from offset={offset}, chunk-span=[{span_start}:{span_end}]"
        )
    return doc_start, doc_end


def doc_to_chunk(chunk_metadata: dict, doc_char_start: int, doc_char_end: int) -> tuple[int, int]:
    """Translate document-absolute coordinates back to chunk-relative span.

    Args:
        chunk_metadata: The ``metadata`` dict from a ``TextChunk`` object.
            Must contain ``chunk_char_offset`` (int).
        doc_char_start: Absolute doc-frame character start (inclusive).
        doc_char_end: Absolute doc-frame character end (exclusive).

    Returns:
        ``(chunk_span_start, chunk_span_end)`` — positions within the chunk text.

    Raises:
        ValueError: if ``chunk_char_offset`` is missing or None.
        ValueError: if the resulting chunk positions are negative or span is empty.
    """
    offset = chunk_metadata.get("chunk_char_offset")
    if offset is None:
        raise ValueError("chunk_char_offset is None — cannot reverse-translate to chunk frame.")
    chunk_start = doc_char_start - offset
    chunk_end = doc_char_end - offset
    if chunk_start < 0 or chunk_end < chunk_start:
        raise ValueError(
            f"Invalid chunk-frame span [{chunk_start}:{chunk_end}] from offset={offset}, "
            f"doc-span=[{doc_char_start}:{doc_char_end}]"
        )
    return chunk_start, chunk_end


# ---------------------------------------------------------------------------
# IntervalTree index builder
# ---------------------------------------------------------------------------


def build_gt_index(gt_chunks: list[dict]) -> dict:
    """Build a per-doc IntervalTree index from a list of doc-anchored GT entries.

    Each GT entry in ``gt_chunks`` must have ``doc_id``, ``doc_char_start``,
    and ``doc_char_end`` fields.

    Returns a dict mapping ``doc_id -> IntervalTree`` where each Interval's
    ``data`` attribute is the full GT chunk dict.

    The caller can then do::

        tree = index[doc_id]
        overlapping = tree[chunk_doc_start:chunk_doc_end]

    and get back the GT entries that overlap the queried range.
    """
    try:
        from intervaltree import IntervalTree
    except ImportError as exc:
        raise ImportError(
            "intervaltree is required for doc-anchored GT scoring. Add it to pyproject.toml: intervaltree>=3.1"
        ) from exc

    index: dict[str, IntervalTree] = {}
    for entry in gt_chunks:
        doc_id = entry.get("doc_id")
        start = entry.get("doc_char_start")
        end = entry.get("doc_char_end")
        if doc_id is None or start is None or end is None:
            logger.warning("GT entry missing doc_id/doc_char_start/doc_char_end — skipped: %s", entry)
            continue
        if end <= start:
            logger.warning(
                "GT entry has zero-length or inverted range [%d:%d] for doc_id=%s — skipped",
                start,
                end,
                doc_id,
            )
            continue
        if doc_id not in index:
            index[doc_id] = IntervalTree()
        index[doc_id][start:end] = entry
    return index


# ---------------------------------------------------------------------------
# Score-time join: resolve GT entries for a given chunk
# ---------------------------------------------------------------------------


def resolve_gt_for_chunk(
    chunk: dict,
    gt_index: dict,
    *,
    diagnostic_logger: logging.Logger | None = None,
) -> list[dict]:
    """Find the GT entries that overlap a model-output chunk.

    Args:
        chunk: A chunk dict with ``document_id`` (or ``doc_id``) and
            ``metadata.chunk_char_offset`` + ``text`` fields.
        gt_index: Output of :func:`build_gt_index` — ``{doc_id: IntervalTree}``.
        diagnostic_logger: Optional logger; when provided, overlaps that
            cover more than one chunk are logged at DEBUG level.

    Returns:
        List of GT entry dicts whose doc-frame range overlaps this chunk's
        doc-frame range.  Empty list when no overlap or when the chunk is
        un-mappable (missing chunk_char_offset).
    """
    doc_id = chunk.get("document_id") or chunk.get("doc_id") or ""
    metadata = chunk.get("metadata") or {}
    offset = metadata.get("chunk_char_offset")
    if offset is None:
        return []

    chunk_text = chunk.get("text") or ""
    chunk_doc_start = offset
    chunk_doc_end = offset + len(chunk_text)

    tree = gt_index.get(doc_id)
    if tree is None:
        return []

    overlapping = tree[chunk_doc_start:chunk_doc_end]
    if not overlapping:
        return []

    # Sort by overlap size descending so the primary match is first
    results = []
    for iv in overlapping:
        gt_entry = iv.data
        overlap_start = max(chunk_doc_start, iv.begin)
        overlap_end = min(chunk_doc_end, iv.end)
        overlap_size = max(0, overlap_end - overlap_start)
        results.append((overlap_size, gt_entry))

    results.sort(key=lambda x: -x[0])

    if len(results) > 1 and (diagnostic_logger or logger).isEnabledFor(logging.DEBUG):
        _log = diagnostic_logger or logger
        _log.debug(
            "GT overlap edge case: chunk %s (doc_id=%s, [%d:%d]) matches %d GT entries — "
            "scoring against all, tagged as duplicates",
            chunk.get("chunk_id", "?"),
            doc_id,
            chunk_doc_start,
            chunk_doc_end,
            len(results),
        )

    return [gt_entry for _, gt_entry in results]


# ---------------------------------------------------------------------------
# Mention span translation helpers used at score time
# ---------------------------------------------------------------------------


def translate_gt_mentions_to_chunk_frame(
    gt_entry: dict,
    chunk_metadata: dict,
) -> list[dict]:
    """Return a copy of gt_entry's mentions with spans translated to chunk-frame.

    Mentions that fall fully outside the chunk's char range are silently
    dropped (they were picked up due to overlap but don't actually land
    in this chunk's text window).

    The returned list is safe to pass directly to
    :func:`tests.shared.extraction_scoring.score_mentions`.
    """
    offset = chunk_metadata.get("chunk_char_offset")
    if offset is None:
        # Un-mappable chunk — return mentions as-is (doc_char_* fields)
        return list(gt_entry.get("mentions", []))

    chunk_text_len = chunk_metadata.get("chunk_text_len")
    translated = []
    for m in gt_entry.get("mentions", []):
        doc_start = m.get("doc_char_start")
        doc_end = m.get("doc_char_end")
        if doc_start is None or doc_end is None:
            # Legacy mention without doc-frame span — include with no span
            translated.append({**m, "span_start": None, "span_end": None})
            continue

        chunk_start = doc_start - offset
        chunk_end = doc_end - offset

        # Drop mentions that fall entirely outside this chunk
        if chunk_text_len is not None and (chunk_end <= 0 or chunk_start >= chunk_text_len):
            continue

        if chunk_start < 0 or chunk_end < chunk_start:
            continue

        translated.append(
            {
                **m,
                "span_start": chunk_start,
                "span_end": chunk_end,
            }
        )

    return translated

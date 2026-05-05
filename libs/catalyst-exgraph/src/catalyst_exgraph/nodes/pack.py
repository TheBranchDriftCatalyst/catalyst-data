"""Evidence packing node — pack entity clusters into model-context windows.

Phase 2 of the entity-anchored flow (CD-j6d3).

For each entity cluster produced by ``ClusterEntitiesNode``:
  1. Build an evidence window: ``text[cluster.start - ctx//2 : cluster.end + ctx//2]``
     clipped to doc bounds.
  2. If the window exceeds the target model's token budget, split greedily on
     sentence boundaries.
  3. Emit a ``packed`` audit event.

Output: ``state["evidence_windows"]: list[EvidenceWindow]``

MODEL_WINDOWS
-------------
The canonical registry lives in ``dagster_io.chunking.MODEL_WINDOWS`` (Phase 3,
CD-80ic).  This module re-exports it for backward-compat and delegates all
window-size lookups to ``window_for_model`` from the same module.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from typing import Any

from catalyst_exgraph.nodes._audit import make_audit_event
from catalyst_exgraph.state import EntityCluster, EvidenceWindow, ExGraphState
from dagster_io import event_store
from dagster_io.chunking import window_for_model

logger = logging.getLogger(__name__)

# Density-pruning thresholds (CD-lxcf research follow-up — captures ~70%
# of the wall-clock win a reranker would have provided, for free).
# A window is dropped if EITHER:
#   - it carries fewer than ``PACK_MIN_MENTIONS`` cluster mentions (entity
#     barely appears → SPO call rarely produces useful triples), OR
#   - its char/mention density exceeds ``PACK_MAX_CHARS_PER_MENTION``
#     (very long window with few mentions → boilerplate / cable masthead /
#     intro greeting).
# Override via env to disable pruning entirely (set MIN=0, MAX=0).
_PACK_MIN_MENTIONS = int(os.environ.get("PACK_MIN_MENTIONS", "2"))
_PACK_MAX_CHARS_PER_MENTION = int(os.environ.get("PACK_MAX_CHARS_PER_MENTION", "800"))

# ── Approximate chars-per-token for context sizing ───────────────────────────
# GPT/Llama tokenisers average ~4 chars/token; GLiNER uses sub-word pieces.
# We use 4 chars/token as a conservative floor.
_CHARS_PER_TOKEN = 4

# Context padding on each side of a cluster bounding box (tokens)
_CONTEXT_TOKENS = 256  # 256 tok ≈ 1024 chars

# Default context window when model is unknown (mirrors window_for_model fallback of 4000)
_DEFAULT_CONTEXT_TOKENS = 4000


def _resolve_context_window(model: str | None) -> int:
    """Look up the context window for a model name via the canonical registry.

    Delegates to ``dagster_io.chunking.window_for_model`` which performs
    exact-match → longest-substring → heuristic-pattern fallback in order.
    """
    if not model:
        return _DEFAULT_CONTEXT_TOKENS
    return window_for_model(model)


def _split_on_sentences(text: str, max_chars: int) -> list[str]:
    """Split text greedily on sentence boundaries without exceeding max_chars.

    If a single sentence exceeds ``max_chars``, it is hard-split at the
    character boundary to guarantee every returned window fits.
    """
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    windows: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for sent in sentences:
        sent_len = len(sent) + 1  # +1 for the space

        if sent_len > max_chars:
            # Hard-split oversized sentence first
            if current_parts:
                windows.append(" ".join(current_parts))
                current_parts = []
                current_len = 0
            # Chop the long sentence into max_chars chunks
            for i in range(0, len(sent), max_chars):
                windows.append(sent[i : i + max_chars])
            continue

        if current_len + sent_len > max_chars and current_parts:
            windows.append(" ".join(current_parts))
            current_parts = [sent]
            current_len = sent_len
        else:
            current_parts.append(sent)
            current_len += sent_len

    if current_parts:
        windows.append(" ".join(current_parts))

    return windows or [text[:max_chars]]


class PackEvidenceNode:
    """Pack entity clusters into evidence windows sized for the target model."""

    def __init__(self, context_tokens: int | None = None) -> None:
        # When set, overrides MODEL_WINDOWS lookup (useful in tests)
        self._override_context_tokens = context_tokens

    async def __call__(self, state: ExGraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        node_name = "pack_evidence"

        raw_text: str = state.get("raw_text", "") or ""
        model: str | None = state.get("model")
        clusters: list[EntityCluster] = state.get("entity_clusters") or []

        if self._override_context_tokens is not None:
            context_tokens = self._override_context_tokens
        else:
            context_tokens = _resolve_context_window(model)

        max_chars = context_tokens * _CHARS_PER_TOKEN
        context_chars = _CONTEXT_TOKENS * _CHARS_PER_TOKEN

        evidence_windows: list[EvidenceWindow] = []
        total_tokens = 0
        window_token_counts: list[int] = []

        for cluster in clusters:
            cluster_start: int = cluster.get("doc_char_start", 0)
            cluster_end: int = cluster.get("doc_char_end", cluster_start)
            cluster_id: str = cluster.get("cluster_id", "")
            mention_indices: list[int] = cluster.get("mention_indices", [])

            # Build evidence window text (clipped to doc bounds)
            win_start = max(0, cluster_start - context_chars // 2)
            win_end = min(len(raw_text), cluster_end + context_chars // 2)
            window_text = raw_text[win_start:win_end]

            # Split if window exceeds model context
            sub_windows = _split_on_sentences(window_text, max_chars) if len(window_text) > max_chars else [window_text]

            for sub_idx, sub_text in enumerate(sub_windows):
                tok_count = max(1, len(sub_text) // _CHARS_PER_TOKEN)
                total_tokens += tok_count
                window_token_counts.append(tok_count)

                win_id = f"win-{uuid.uuid4().hex[:8]}" if sub_idx == 0 else f"win-{uuid.uuid4().hex[:8]}-{sub_idx}"
                # Compute char offsets for the sub-window within the doc
                # (approximate, based on proportional position within window_text)
                sub_offset = len(" ".join(sub_windows[:sub_idx])) if sub_idx > 0 else 0
                sub_doc_start = win_start + sub_offset
                sub_doc_end = min(len(raw_text), sub_doc_start + len(sub_text))

                evidence_windows.append(
                    EvidenceWindow(
                        window_id=win_id,
                        doc_char_start=sub_doc_start,
                        doc_char_end=sub_doc_end,
                        text=sub_text,
                        mention_indices=mention_indices,
                        cluster_id=cluster_id,
                    )
                )

        # ── Density pruning ─────────────────────────────────────────────────
        # Drop low-signal windows BEFORE the SPO fan-out hits them. Each
        # pruned window emits its own audit event so the State Inspector
        # surfaces a card with the reason — they're not silently dropped.
        kept_windows: list[EvidenceWindow] = []
        pruned_records: list[dict[str, Any]] = []
        doc_id = state.get("doc_id") or (state.get("source_metadata") or {}).get("document_id") or ""
        for w in evidence_windows:
            n_mentions = len(w.get("mention_indices") or [])
            char_count = len(w.get("text") or "")
            chars_per_mention = (char_count / n_mentions) if n_mentions > 0 else float("inf")

            reason: str | None = None
            if _PACK_MIN_MENTIONS > 0 and n_mentions < _PACK_MIN_MENTIONS:
                reason = f"too_few_mentions ({n_mentions} < {_PACK_MIN_MENTIONS})"
            elif _PACK_MAX_CHARS_PER_MENTION > 0 and chars_per_mention > _PACK_MAX_CHARS_PER_MENTION:
                reason = f"sparse_density ({chars_per_mention:.0f} > {_PACK_MAX_CHARS_PER_MENTION} chars/mention)"

            if reason is None:
                kept_windows.append(w)
                continue

            record = {
                "window_id": w.get("window_id", ""),
                "cluster_id": w.get("cluster_id", ""),
                "mention_count": n_mentions,
                "char_count": char_count,
                "chars_per_mention": (round(chars_per_mention, 1) if chars_per_mention != float("inf") else None),
                "reason": reason,
            }
            pruned_records.append(record)

            # Per-window audit event so the State Inspector surfaces one
            # card per pruned window with the reason — same pattern as
            # consensus mention_rejected events.
            if event_store.is_configured():
                event_store.append(
                    source="exgraph",
                    node_name="evidence_window_pruned",
                    status="info",
                    model=state.get("model"),
                    doc_id=doc_id,
                    chunk_id=f"{doc_id}:{w.get('window_id', '')}" if doc_id else None,
                    details=record,
                )

        elapsed = time.perf_counter() - t0
        mean_tokens = sum(window_token_counts) / len(window_token_counts) if window_token_counts else 0.0

        # Surface kept-window doc ranges so the State Inspector can paint each
        # window over the doc-source panel without re-deriving from spo
        # chunk_extracted events (those don't always carry char offsets).
        kept_records: list[dict[str, Any]] = [
            {
                "window_id": w.get("window_id", ""),
                "cluster_id": w.get("cluster_id", ""),
                "doc_char_start": w.get("doc_char_start"),
                "doc_char_end": w.get("doc_char_end"),
                "mention_count": len(w.get("mention_indices") or []),
                "char_count": len(w.get("text") or ""),
            }
            for w in kept_windows
        ]

        logger.info(
            "%s: %d clusters → %d windows kept, %d pruned (model=%s, total_tokens≈%d)",
            node_name,
            len(clusters),
            len(kept_windows),
            len(pruned_records),
            model,
            total_tokens,
        )

        return {
            "evidence_windows": kept_windows,
            "pruned_evidence_windows": pruned_records,
            "audit_events": list(state.get("audit_events") or [])
            + [
                make_audit_event(
                    node_name,
                    "completed",
                    state=state,
                    duration_s=elapsed,
                    window_count=len(kept_windows),
                    pruned_count=len(pruned_records),
                    total_tokens=total_tokens,
                    mean_tokens_per_window=round(mean_tokens, 1),
                    context_tokens=context_tokens,
                    prune_min_mentions=_PACK_MIN_MENTIONS,
                    prune_max_chars_per_mention=_PACK_MAX_CHARS_PER_MENTION,
                    kept_windows=kept_records,
                    pruned_windows=pruned_records,
                )
            ],
        }

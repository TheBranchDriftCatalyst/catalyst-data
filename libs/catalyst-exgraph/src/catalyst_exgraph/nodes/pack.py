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
import re
import time
import uuid
from typing import Any

from catalyst_exgraph.nodes._audit import make_audit_event
from catalyst_exgraph.state import EntityCluster, EvidenceWindow, ExGraphState
from dagster_io.chunking import window_for_model

logger = logging.getLogger(__name__)

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

        elapsed = time.perf_counter() - t0
        mean_tokens = sum(window_token_counts) / len(window_token_counts) if window_token_counts else 0.0

        logger.info(
            "%s: %d clusters → %d evidence windows, total_tokens≈%d, model=%s",
            node_name,
            len(clusters),
            len(evidence_windows),
            total_tokens,
            model,
        )

        return {
            "evidence_windows": evidence_windows,
            "audit_events": list(state.get("audit_events") or [])
            + [
                make_audit_event(
                    node_name,
                    "completed",
                    state=state,
                    duration_s=elapsed,
                    window_count=len(evidence_windows),
                    total_tokens=total_tokens,
                    mean_tokens_per_window=round(mean_tokens, 1),
                    context_tokens=context_tokens,
                )
            ],
        }

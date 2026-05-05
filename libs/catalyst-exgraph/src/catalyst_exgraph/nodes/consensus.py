"""ConsensusNode — cluster per-encoder mentions into consensus mentions.

Phase B of the v4 NER-ensemble extraction epic (CD-94ow / CD-y4u0).

Consumes ``state["per_encoder_mentions"]`` (produced by Phase A's
``NerEnsembleNode``) and produces:

- ``state["consensus_mentions"]`` — accepted ``ConsensusMention`` list
- ``state["rejected_mentions"]`` — raw cluster dicts that didn't reach quorum

Every decision (accept, reject, span pick, type vote) emits an audit event
via ``event_store.append`` with ``chunk_id = "{doc_id}:_consensus"`` so the
State Inspector can render the consensus card with full provenance.

**No silent drops.**  Every mention that enters consensus must result in
either a ``mention_decision`` (accepted) or ``mention_rejected`` (below
quorum) event.

Algorithm overview
------------------
1. Canonicalize all per-encoder mentions:
   - ``canonical_text = text.lower().strip()``
   - ``canonical_type`` via ``consensus_taxonomy.canonicalize_type()``

2. Cluster: group mentions where
   ``canonical_text`` matches **and**
   span overlap ≥ 50% (``overlap_chars / max(len_a, len_b)``).

3. Within each cluster:
   - Type vote: majority on canonical_type; ties broken by highest mean
     confidence among tied types.
   - Span: from the highest-confidence encoder mention in the cluster.
   - ``vote_count = len(unique source_models)``

4. Quorum filter:
   - Default ``K = ceil(N / 2)`` where N = ``len(encoders)``.
   - Per-type override: PII_TYPES → K=1 by default (gliner-pii is the
     only encoder that reliably finds these).
   - Configurable via constructor args ``quorum`` and ``per_type_quorum``.

5. Emit audit events for every decision.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections import Counter, defaultdict
from typing import Any

from catalyst_exgraph.consensus_taxonomy import PII_TYPES, canonicalize_type
from catalyst_exgraph.state import ExGraphState
from dagster_io import event_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _span_overlap_ratio(start_a: int, end_a: int, start_b: int, end_b: int) -> float:
    """Compute ``overlap_chars / max(len_a, len_b)``.

    Returns 0.0 when either span is zero-length or there is no overlap.
    This is the simpler of the two overlap metrics discussed in the design;
    it's intuitively the "fraction of the longer span that is shared".
    """
    len_a = end_a - start_a
    len_b = end_b - start_b
    if len_a <= 0 or len_b <= 0:
        return 0.0
    overlap_start = max(start_a, start_b)
    overlap_end = min(end_a, end_b)
    overlap = max(0, overlap_end - overlap_start)
    if overlap == 0:
        return 0.0
    return overlap / max(len_a, len_b)


def _mention_id(canonical_text: str, canonical_type: str, span_start: int) -> str:
    """Stable deterministic mention id from ``(canonical_text, canonical_type, span_start)``."""
    raw = f"{canonical_text}|{canonical_type}|{span_start}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]  # noqa: S324 — not crypto


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


# ---------------------------------------------------------------------------
# ConsensusNode
# ---------------------------------------------------------------------------


class ConsensusNode:
    """Cluster per-encoder mentions into consensus mentions with quorum gating.

    Constructor args
    ----------------
    encoders:
        List of encoder names that participated in the ensemble.  Used as
        ``n_encoders`` denominator in vote fractions and quorum computation.
    quorum:
        Override the default quorum ``K = ceil(n_encoders / 2)``.  When
        ``None`` the default is used.
    per_type_quorum:
        Per-canonical-type quorum overrides.  When ``None``, PII_TYPES are
        initialised to K=1 (asymmetric coverage).  Pass an explicit empty
        dict ``{}`` to disable PII overrides.
    span_overlap_threshold:
        Minimum ``overlap_chars / max(len_a, len_b)`` ratio to consider two
        spans "the same".  Default 0.5 (≥50%).
    """

    def __init__(
        self,
        encoders: list[str],
        quorum: int | None = None,
        per_type_quorum: dict[str, int] | None = None,
        span_overlap_threshold: float = 0.5,
        predicate: Any = None,
    ) -> None:
        self.encoder_names = list(encoders)
        self.n_encoders = len(encoders)
        self.default_quorum = quorum if quorum is not None else max(1, math.ceil(self.n_encoders / 2))
        # PII override: K=1 unless caller overrides explicitly.
        if per_type_quorum is None:
            self.per_type_quorum: dict[str, int] = {t: 1 for t in PII_TYPES}
        else:
            self.per_type_quorum = dict(per_type_quorum)
        self.span_overlap_threshold = span_overlap_threshold
        # Optional CompiledPredicate (catalyst_exgraph.consensus_predicate).
        # When set, replaces the integer-quorum check with arbitrary
        # boolean/arithmetic logic over the encoder vote vector.
        self.predicate = predicate

    # ── Main entry point ────────────────────────────────────────────────────

    async def __call__(self, state: ExGraphState) -> dict[str, Any]:
        src = state.get("source_metadata") or {}
        doc_id = state.get("doc_id") or src.get("document_id") or ""
        consensus_chunk_id = f"{doc_id}:_consensus"

        per_encoder: dict[str, list[dict]] = state.get("per_encoder_mentions") or {}

        # Flatten all per-encoder mentions into a single list with encoder tag
        all_mentions: list[dict] = []
        for encoder_name, mentions in per_encoder.items():
            for m in mentions:
                # _source_encoder should already be set by NerEnsembleNode,
                # but defensively fall back to the dict key.
                m_copy = dict(m)
                m_copy.setdefault("_source_encoder", encoder_name)
                all_mentions.append(m_copy)

        total_input = len(all_mentions)

        event_store.append(
            source="consensus",
            node_name="consensus_started",
            status="started",
            doc_id=doc_id,
            chunk_id=consensus_chunk_id,
            details={
                "n_encoders": self.n_encoders,
                "total_input_mentions": total_input,
            },
        )

        logger.info(
            "consensus: doc_id=%r, n_encoders=%d, total_input=%d",
            doc_id,
            self.n_encoders,
            total_input,
        )

        if not all_mentions:
            event_store.append(
                source="consensus",
                node_name="consensus_completed",
                status="completed",
                doc_id=doc_id,
                chunk_id=consensus_chunk_id,
                details={
                    "accepted_count": 0,
                    "rejected_count": 0,
                    "mean_vote_count": 0.0,
                    "type_distribution": {},
                    "span_disagreement_rate": 0.0,
                },
            )
            return {"consensus_mentions": [], "rejected_mentions": []}

        # ── Step 1: Canonicalize ─────────────────────────────────────────
        canonical_mentions = self._canonicalize(all_mentions)

        # ── Step 2: Cluster ──────────────────────────────────────────────
        clusters = self._cluster(canonical_mentions)

        # ── Step 3 + 4: Vote + quorum ────────────────────────────────────
        accepted: list[dict] = []
        rejected: list[dict] = []
        span_disagreements: list[int] = []

        for cluster in clusters:
            result = self._resolve_cluster(cluster)
            canonical_type = result["canonical_type"]
            vote_count = result["vote_count"]

            # Determine acceptance: predicate (when set) overrides the
            # integer-quorum check. The predicate takes a per-encoder
            # vote dict ``{encoder_name: bool}`` derived from
            # source_models.  Per-type quorum still applies as a floor —
            # it's checked first so PII overrides keep working.
            k = self.per_type_quorum.get(canonical_type, self.default_quorum)
            if self.predicate is not None:
                votes = {name: name in result["source_models"] for name in self.encoder_names}
                accepted_by_rule = bool(self.predicate.evaluate(votes))
            else:
                accepted_by_rule = vote_count >= k

            if accepted_by_rule:
                # Span disagreement: max chars difference between span from the
                # chosen provider and the worst-case other span in the cluster
                span_disagree = self._span_disagreement(cluster, result["span_start"], result["span_end"])
                span_disagreements.append(span_disagree)

                mid = _mention_id(result["canonical_text"], canonical_type, result["span_start"])
                consensus_mention: dict = {
                    "mention_id": mid,
                    "text": result["canonical_text"],
                    "canonical_type": canonical_type,
                    "span_start": result["span_start"],
                    "span_end": result["span_end"],
                    "span_provenance": result["span_provenance"],
                    "source_models": result["source_models"],
                    "vote_count": vote_count,
                    "n_encoders": self.n_encoders,
                    "mean_confidence": result["mean_confidence"],
                    "type_votes": result["type_votes"],
                    "raw_mentions": cluster,
                }
                accepted.append(consensus_mention)

                event_store.append(
                    source="consensus",
                    node_name="mention_decision",
                    status="accepted",
                    doc_id=doc_id,
                    chunk_id=consensus_chunk_id,
                    details={
                        "text": result["canonical_text"],
                        "canonical_type": canonical_type,
                        "vote_count": vote_count,
                        "n_encoders": self.n_encoders,
                        "source_models": result["source_models"],
                        "mean_confidence": round(result["mean_confidence"], 4),
                        "type_votes": result["type_votes"],
                        "span_provenance": result["span_provenance"],
                        "span_disagreement_chars": span_disagree,
                    },
                )
            else:
                rule_text = self.predicate.expr_text if self.predicate is not None else f"vote_count >= {k}"
                reason = "below_predicate" if self.predicate is not None else "below_quorum"
                rejected_rec = {
                    "text": result["canonical_text"],
                    "canonical_type": canonical_type,
                    "vote_count": vote_count,
                    "n_encoders": self.n_encoders,
                    "quorum": k,
                    "rule": rule_text,
                    "reason": reason,
                    "source_models": result["source_models"],
                    "raw_mentions": cluster,
                }
                rejected.append(rejected_rec)

                event_store.append(
                    source="consensus",
                    node_name="mention_rejected",
                    status="rejected",
                    doc_id=doc_id,
                    chunk_id=consensus_chunk_id,
                    details={
                        "text": result["canonical_text"],
                        "vote_count": vote_count,
                        "n_encoders": self.n_encoders,
                        "quorum": k,
                        "rule": rule_text,
                        "reason": reason,
                        # Gap #9 — surface which encoders argued for the
                        # rejected mention so the data scientist can spot
                        # asymmetric-coverage cases (e.g. gliner-pii alone
                        # below quorum) and tune per_type_quorum overrides.
                        # Mirrors mention_decision's source_models field.
                        "source_models": result["source_models"],
                    },
                )

        # ── Step 5: Summary event ────────────────────────────────────────
        type_distribution: dict[str, int] = Counter(m["canonical_type"] for m in accepted)
        mean_vote = _mean([m["vote_count"] for m in accepted]) if accepted else 0.0
        disagree_rate = (
            sum(1 for d in span_disagreements if d > 0) / len(span_disagreements) if span_disagreements else 0.0
        )

        event_store.append(
            source="consensus",
            node_name="consensus_completed",
            status="completed",
            doc_id=doc_id,
            chunk_id=consensus_chunk_id,
            details={
                "accepted_count": len(accepted),
                "rejected_count": len(rejected),
                "mean_vote_count": round(mean_vote, 3),
                "type_distribution": dict(type_distribution),
                "span_disagreement_rate": round(disagree_rate, 3),
            },
        )

        # Emit chunk_loaded (idempotent) + chunk_extracted so the State
        # Inspector surfaces the consensus card with mention counts.
        # chunk_loaded carries the raw doc text so the inspector INPUT pane
        # can display the source text aligned with the consensus result.
        raw_text = state.get("raw_text") or ""
        src_domain = (state.get("source_metadata") or {}).get("domain")
        event_store.emit_chunk_text(
            consensus_chunk_id,
            raw_text,
            doc_id=doc_id,
            domain=src_domain,
            chunk_metadata={
                "kind": "consensus",
                "n_encoders": self.n_encoders,
                "quorum": self.default_quorum,
                "accepted_count": len(accepted),
                "rejected_count": len(rejected),
            },
        )
        event_store.emit_chunk_extracted(
            consensus_chunk_id,
            model="ensemble",
            doc_id=doc_id,
            mentions=accepted,
            propositions=[],
        )

        logger.info(
            "consensus: accepted=%d, rejected=%d, span_disagree_rate=%.2f",
            len(accepted),
            len(rejected),
            disagree_rate,
        )

        return {"consensus_mentions": accepted, "rejected_mentions": rejected}

    # ── Internal helpers ────────────────────────────────────────────────────

    def _canonicalize(self, mentions: list[dict]) -> list[dict]:
        """Return a new list where each mention has ``_canonical_text`` and
        ``_canonical_type`` injected (originals preserved).
        """
        result = []
        for m in mentions:
            c = dict(m)
            c["_canonical_text"] = (m.get("text") or "").lower().strip()
            raw_type = m.get("mention_type") or m.get("entity_type") or m.get("canonical_type") or "OTHER"
            encoder = m.get("_source_encoder", "")
            c["_canonical_type"] = canonicalize_type(encoder, str(raw_type))
            result.append(c)
        return result

    def _cluster(self, mentions: list[dict]) -> list[list[dict]]:
        """Group mentions that share canonical_text and have ≥50% span overlap.

        Uses a greedy single-pass with union-find to merge transitive clusters.
        Two mentions join a cluster if:
        1. Same ``_canonical_text``, AND
        2. span overlap ratio ≥ ``self.span_overlap_threshold``
           (or both have zero spans, which counts as a match).
        """
        n = len(mentions)
        parent = list(range(n))

        def _find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def _union(x: int, y: int) -> None:
            parent[_find(x)] = _find(y)

        # Group by canonical_text first to reduce the O(n²) inner loop to
        # per-text subsets.
        by_text: dict[str, list[int]] = defaultdict(list)
        for i, m in enumerate(mentions):
            by_text[m["_canonical_text"]].append(i)

        for indices in by_text.values():
            if len(indices) < 2:
                continue
            for ai in range(len(indices)):
                for bi in range(ai + 1, len(indices)):
                    i, j = indices[ai], indices[bi]
                    if _find(i) == _find(j):
                        continue
                    m_i = mentions[i]
                    m_j = mentions[j]
                    s_i = m_i.get("span_start") or 0
                    e_i = m_i.get("span_end") or 0
                    s_j = m_j.get("span_start") or 0
                    e_j = m_j.get("span_end") or 0

                    # Both zero-length spans → same mention, merge
                    if (e_i - s_i) == 0 and (e_j - s_j) == 0:
                        _union(i, j)
                        continue

                    ratio = _span_overlap_ratio(s_i, e_i, s_j, e_j)
                    if ratio >= self.span_overlap_threshold:
                        _union(i, j)

        # Regroup
        groups: dict[int, list[dict]] = {}
        for i, m in enumerate(mentions):
            root = _find(i)
            groups.setdefault(root, [])
            groups[root].append(m)

        return list(groups.values())

    def _resolve_cluster(self, cluster: list[dict]) -> dict:
        """Compute the consensus attributes for a single cluster.

        Returns a dict with:
            canonical_text, canonical_type, span_start, span_end,
            span_provenance, source_models, vote_count, mean_confidence,
            type_votes
        """
        # ── Unique source models (vote_count = unique encoders) ──────────
        source_models: list[str] = []
        seen_encoders: set[str] = set()
        for m in cluster:
            enc = m.get("_source_encoder", "")
            if enc and enc not in seen_encoders:
                seen_encoders.add(enc)
                source_models.append(enc)

        # ── Type voting ──────────────────────────────────────────────────
        # Count votes per canonical type (one vote per mention, not per encoder,
        # because an encoder could emit the same mention twice).
        type_votes: dict[str, int] = Counter(m["_canonical_type"] for m in cluster)
        max_votes = max(type_votes.values())
        tied_types = [t for t, v in type_votes.items() if v == max_votes]

        if len(tied_types) == 1:
            winning_type = tied_types[0]
        else:
            # Tie-break: pick the type whose contributing mentions have the
            # highest mean confidence.
            best_type = tied_types[0]
            best_mean = -1.0
            for t in tied_types:
                confs = [m.get("confidence") or 0.0 for m in cluster if m["_canonical_type"] == t]
                mean_c = _mean(confs)
                if mean_c > best_mean:
                    best_mean = mean_c
                    best_type = t
            winning_type = best_type

        # ── Span from highest-confidence mention ─────────────────────────
        best_mention = max(cluster, key=lambda m: m.get("confidence") or 0.0)
        span_start = best_mention.get("span_start") or 0
        span_end = best_mention.get("span_end") or 0
        span_provenance = best_mention.get("_source_encoder", "")

        # ── Mean confidence across all cluster mentions ───────────────────
        confidences = [m.get("confidence") or 0.0 for m in cluster]
        mean_conf = _mean(confidences)

        # Use the surface text from the best mention (original case preserved
        # from the highest-confidence encoder).
        canonical_text = (best_mention.get("text") or "").lower().strip()

        return {
            "canonical_text": canonical_text,
            "canonical_type": winning_type,
            "span_start": span_start,
            "span_end": span_end,
            "span_provenance": span_provenance,
            "source_models": source_models,
            "vote_count": len(seen_encoders),
            "mean_confidence": mean_conf,
            "type_votes": dict(type_votes),
        }

    @staticmethod
    def _span_disagreement(cluster: list[dict], chosen_start: int, chosen_end: int) -> int:
        """Return the maximum char offset difference between the chosen span
        and any other span in the cluster.

        This is a coarse measure of how much the encoders disagreed on span
        boundaries.  Zero means all encoders agreed exactly.
        """
        max_diff = 0
        for m in cluster:
            s = m.get("span_start") or 0
            e = m.get("span_end") or 0
            diff = abs(s - chosen_start) + abs(e - chosen_end)
            if diff > max_diff:
                max_diff = diff
        return max_diff

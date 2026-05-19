"""Concordance engine for entity resolution.

ConcordanceEngine: resolves Mentions → EntityCandidates within one code location.
CrossSourceAligner: produces AlignmentEdges between EntityCandidates across code locations.
"""

from __future__ import annotations

import math
from collections import defaultdict

from dagster_io.logging import get_logger
from dagster_io.metrics import ALIGNMENT_EDGES_TOTAL
from dagster_io.models import (
    AlignmentEdge,
    AlignmentType,
    EntityCandidate,
    Mention,
    MentionType,
)

logger = get_logger(__name__)

# Signal weight map used by CrossSourceAligner._score_pair. Keep in sync with
# the scoring logic below — this lets observability code rank an edge's
# evidence list by the same weights the scorer uses without re-running the
# scorer.
_SIGNAL_WEIGHTS: dict[str, float] = {
    "exact_name": 0.95,
    "speaker_profile": 0.92,
    "substring": 0.80,
    "embedding": 0.75,
    "jaccard": 0.70,
}


def _pick_top_signal(evidence: list[str]) -> str:
    """Return the highest-weight signal name from an edge's evidence list.

    Falls back to ``"unknown"`` when ``evidence`` is empty or contains only
    signals we don't have a weight for (forward-compat: new signal types
    should be added to ``_SIGNAL_WEIGHTS``).
    """
    if not evidence:
        return "unknown"
    best: tuple[float, str] | None = None
    for sig in evidence:
        weight = _SIGNAL_WEIGHTS.get(sig)
        if weight is None:
            continue
        if best is None or weight > best[0]:
            best = (weight, sig)
    if best is None:
        return "unknown"
    return best[1]


class _UnionFind:
    """Disjoint-set / union-find for efficient cluster merging."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._rank: dict[str, int] = {}

    def find(self, x: str) -> str:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x: str, y: str) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self._rank[rx] < self._rank[ry]:
            rx, ry = ry, rx
        self._parent[ry] = rx
        if self._rank[rx] == self._rank[ry]:
            self._rank[rx] += 1

    def clusters(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = defaultdict(list)
        for item in self._parent:
            groups[self.find(item)].append(item)
        return dict(groups)


def _tokenize(text: str) -> set[str]:
    return set(text.lower().split())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _idf_weighted_jaccard(a: set[str], b: set[str], idf: dict[str, float]) -> float:
    """Jaccard weighted by IDF — rare shared tokens count more."""
    if not a or not b:
        return 0.0
    intersection = a & b
    union = a | b
    if not union:
        return 0.0
    idf_inter = sum(idf.get(t, 1.0) for t in intersection)
    idf_union = sum(idf.get(t, 1.0) for t in union)
    return idf_inter / idf_union if idf_union > 0 else 0.0


def compute_idf(candidates: list) -> dict[str, float]:
    """Compute IDF for all tokens across entity candidate names.

    IDF(t) = log(N / df(t)) where N = number of unique entities and
    df(t) = number of entities containing token t.

    Returns dict mapping token → IDF score.
    """
    # Collect all unique entity name sets (canonical + aliases)
    entity_token_sets: list[set[str]] = []
    for cand in candidates:
        name_set: set[str] = set()
        name_set |= _tokenize(cand.canonical_name)
        for alias in cand.aliases:
            name_set |= _tokenize(alias)
        entity_token_sets.append(name_set)

    n = len(entity_token_sets)
    if n == 0:
        return {}

    # Count document frequency per token
    df: dict[str, int] = defaultdict(int)
    for token_set in entity_token_sets:
        for token in token_set:
            df[token] += 1

    # IDF with smoothing: log((N + 1) / (df + 1)) + 1
    return {token: math.log((n + 1) / (count + 1)) + 1 for token, count in df.items()}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class ConcordanceEngine:
    """Resolves Mentions → EntityCandidates within one code location.

    Multi-pass resolution:
    - Pass 1: Exact case-insensitive grouping
    - Pass 2: Substring containment (same type, ≥2 shared tokens)
    - Pass 3: Jaccard token overlap >0.6 (same type, ≥2 shared tokens)
    - Pass 4: Embedding cosine similarity >0.85 (same type, optional)
    """

    def __init__(
        self,
        jaccard_threshold: float = 0.6,
        cosine_threshold: float = 0.85,
        min_shared_tokens: int = 2,
    ) -> None:
        self.jaccard_threshold = jaccard_threshold
        self.cosine_threshold = cosine_threshold
        self.min_shared_tokens = min_shared_tokens

    def resolve(
        self,
        mentions: list[Mention],
        code_location: str,
        embeddings: dict[str, list[float]] | None = None,
    ) -> list[EntityCandidate]:
        """Resolve mentions into entity candidates.

        Args:
            mentions: List of Mention objects to resolve.
            code_location: Code location name for the candidates.
            embeddings: Optional dict mapping mention text (lowercased) to embedding vectors.

        Returns:
            List of EntityCandidate objects.
        """
        if not mentions:
            return []

        logger.info("Resolving %d mentions for code_location=%s", len(mentions), code_location)

        uf = _UnionFind()

        # Index mentions by type.
        # Wave 1 (bead llm-g0b): Mention.canonical_type is a free-form str
        # so label packs can extend the universe. Coerce to MentionType for
        # the by_type bucket; values outside the enum fall back to OTHER
        # (they still cluster together by string equality on the original
        # canonical_type, but the bucket key is normalized).
        def _coerce_mention_type(value: str) -> MentionType:
            try:
                return MentionType(value)
            except ValueError:
                return MentionType.OTHER

        by_type: dict[MentionType, list[Mention]] = defaultdict(list)
        for m in mentions:
            uf.find(m.mention_id)
            by_type[_coerce_mention_type(m.canonical_type)].append(m)

        for _mtype, typed_mentions in by_type.items():
            # Build lookup structures
            normed: dict[str, list[Mention]] = defaultdict(list)
            for m in typed_mentions:
                normed[m.text.lower().strip()].append(m)

            # Pass 1: Exact case-insensitive grouping
            for _key, group in normed.items():
                if len(group) > 1:
                    first = group[0].mention_id
                    for m in group[1:]:
                        uf.union(first, m.mention_id)

            # Build unique surface forms for pairwise comparison
            surfaces = list(normed.keys())

            # Pass 2: Substring containment (with guards)
            # Uses strict min_shared_tokens (no adaptive reduction for
            # single-token names) to prevent "Donald" bridging "Donald Trump"
            # and "Donald Rumsfeld" via transitive closure. The platinum-layer
            # CrossSourceAligner uses adaptive min(2, len_shorter) instead.
            for i, a in enumerate(surfaces):
                for b in surfaces[i + 1 :]:
                    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
                    if len(shorter) < 4:
                        continue
                    tokens_shorter = _tokenize(shorter)
                    tokens_longer = _tokenize(longer)
                    shared = len(tokens_shorter & tokens_longer)
                    if shared < self.min_shared_tokens:
                        continue
                    ratio = len(shorter) / len(longer) if len(longer) > 0 else 1.0
                    if ratio < 0.4:
                        continue
                    if a in b or b in a:
                        uf.union(normed[a][0].mention_id, normed[b][0].mention_id)

            # Pass 3: Jaccard overlap
            for i, a in enumerate(surfaces):
                tokens_a = _tokenize(a)
                for b in surfaces[i + 1 :]:
                    tokens_b = _tokenize(b)
                    shared = len(tokens_a & tokens_b)
                    if shared < self.min_shared_tokens:
                        continue
                    if _jaccard(tokens_a, tokens_b) > self.jaccard_threshold:
                        uf.union(normed[a][0].mention_id, normed[b][0].mention_id)

            # Pass 4: Embedding cosine similarity (with guards matching CrossSourceAligner)
            if embeddings:
                for i, a in enumerate(surfaces):
                    emb_a = embeddings.get(a)
                    if emb_a is None:
                        continue
                    tokens_a = _tokenize(a)
                    for b in surfaces[i + 1 :]:
                        emb_b = embeddings.get(b)
                        if emb_b is None:
                            continue
                        tokens_b = _tokenize(b)
                        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
                        if len(shorter) < 4:
                            continue
                        shared = len(tokens_a & tokens_b)
                        if shared < self.min_shared_tokens:
                            continue
                        ratio = len(shorter) / len(longer) if len(longer) > 0 else 1.0
                        if ratio < 0.4:
                            continue
                        if _cosine_similarity(emb_a, emb_b) > self.cosine_threshold:
                            uf.union(normed[a][0].mention_id, normed[b][0].mention_id)

        # Build candidates from clusters
        mention_by_id = {m.mention_id: m for m in mentions}
        clusters = uf.clusters()
        candidates: list[EntityCandidate] = []

        for _root, member_ids in clusters.items():
            cluster_mentions = [mention_by_id[mid] for mid in member_ids if mid in mention_by_id]
            if not cluster_mentions:
                continue

            # Pick canonical name: most frequent surface form
            name_counts: dict[str, int] = defaultdict(int)
            for m in cluster_mentions:
                name_counts[m.text] += 1
            canonical_name = max(name_counts, key=name_counts.get)  # type: ignore[arg-type]

            # Collect unique aliases (excluding canonical name)
            aliases = sorted({m.text for m in cluster_mentions if m.text != canonical_name})

            # Dominant type
            type_counts: dict[MentionType, int] = defaultdict(int)
            for m in cluster_mentions:
                type_counts[_coerce_mention_type(m.canonical_type)] += 1
            candidate_type = max(type_counts, key=type_counts.get)  # type: ignore[arg-type]

            # Unique source documents. Wave 1: document_id moved off Mention
            # itself onto Mention.provenance.source_document_id.
            source_docs = sorted({m.provenance.source_document_id for m in cluster_mentions})

            # Pick embedding if available
            emb = None
            if embeddings:
                emb = embeddings.get(canonical_name.lower().strip())

            candidate = EntityCandidate(
                canonical_name=canonical_name,
                candidate_type=candidate_type,
                aliases=aliases,
                mention_ids=[m.mention_id for m in cluster_mentions],
                mention_count=len(cluster_mentions),
                source_documents=source_docs,
                code_location=code_location,
                embedding=emb,
            )
            candidates.append(candidate)

        logger.info(
            "Resolved %d mentions into %d candidates for code_location=%s",
            len(mentions),
            len(candidates),
            code_location,
        )
        return candidates


class CrossSourceAligner:
    """Produces AlignmentEdges between EntityCandidates across code locations.

    Multi-signal scoring:
    - Exact name match: 0.95
    - Substring containment: 0.80
    - Jaccard token overlap: 0.70
    - Embedding cosine similarity: 0.75

    Combined score = max(signals) + 0.05 per additional signal, capped at 1.0
    Thresholds: ≥0.85 → sameAs, ≥0.65 → possibleSameAs
    """

    def __init__(
        self,
        same_as_threshold: float = 0.65,
        possible_same_as_threshold: float = 0.50,
    ) -> None:
        self.same_as_threshold = same_as_threshold
        self.possible_same_as_threshold = possible_same_as_threshold

    def align(
        self,
        sources: dict[str, list[EntityCandidate]],
    ) -> list[AlignmentEdge]:
        """Align entity candidates across code locations.

        Args:
            sources: Dict mapping code_location name to its EntityCandidates.

        Returns:
            List of AlignmentEdge objects.
        """
        edges: list[AlignmentEdge] = []
        locations = list(sources.keys())
        all_candidates = [c for cands in sources.values() for c in cands]
        total_candidates = len(all_candidates)
        logger.info(
            "Cross-source alignment starting: %d locations, %d total candidates",
            len(locations),
            total_candidates,
        )

        # Compute IDF across the full corpus for token weighting
        idf = compute_idf(all_candidates) if all_candidates else {}

        for i, loc_a in enumerate(locations):
            for loc_b in locations[i + 1 :]:
                for cand_a in sources[loc_a]:
                    for cand_b in sources[loc_b]:
                        # Only compare same entity type
                        if cand_a.candidate_type != cand_b.candidate_type:
                            continue

                        edge = self._score_pair(cand_a, cand_b, idf)
                        if edge is not None:
                            edges.append(edge)
                            top_signal = _pick_top_signal(edge.evidence)
                            ALIGNMENT_EDGES_TOTAL.labels(
                                source_location=loc_a,
                                target_location=loc_b,
                                alignment_type=edge.alignment_type.value,
                                top_signal=top_signal,
                            ).inc()

        logger.info("Cross-source alignment complete: %d edges produced", len(edges))
        return edges

    def intra_source_align(
        self,
        candidates: list[EntityCandidate],
        code_location: str,
    ) -> list[AlignmentEdge]:
        """Align entity candidates WITHIN a single code location.

        Same scoring logic as cross-source align, but pairwise over
        candidates from the same source. This collapses duplicates that
        ConcordanceEngine.resolve() couldn't merge because they were in
        different partitions (e.g. "Joe Biden" in 15 different videos).
        """
        idf = compute_idf(candidates) if candidates else {}
        edges: list[AlignmentEdge] = []
        for i, cand_a in enumerate(candidates):
            for cand_b in candidates[i + 1 :]:
                if cand_a.candidate_type != cand_b.candidate_type:
                    continue
                edge = self._score_pair(cand_a, cand_b, idf)
                if edge is not None:
                    edges.append(edge)
                    top_signal = _pick_top_signal(edge.evidence)
                    ALIGNMENT_EDGES_TOTAL.labels(
                        source_location=code_location,
                        target_location=code_location,  # same source!
                        alignment_type=edge.alignment_type.value,
                        top_signal=top_signal,
                    ).inc()
        logger.info(
            "Intra-source alignment for %s: %d candidates → %d edges",
            code_location,
            len(candidates),
            len(edges),
        )
        return edges

    def _score_pair(
        self,
        a: EntityCandidate,
        b: EntityCandidate,
        idf: dict[str, float] | None = None,
    ) -> AlignmentEdge | None:
        signals: list[tuple[float, str]] = []
        all_names_a = {a.canonical_name.lower().strip()} | {alias.lower().strip() for alias in a.aliases}
        all_names_b = {b.canonical_name.lower().strip()} | {alias.lower().strip() for alias in b.aliases}

        # Signal 1: Exact name match (any name from either side)
        if all_names_a & all_names_b:
            signals.append((1.0, "exact_name"))

        # Signal 2: Substring containment (with guards + IDF modulation)
        if not signals:
            for na in all_names_a:
                for nb in all_names_b:
                    if na in nb or nb in na:
                        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
                        if len(shorter) < 4:
                            continue
                        tokens_shorter = _tokenize(shorter)
                        tokens_longer = _tokenize(longer)
                        shared = tokens_shorter & tokens_longer
                        shared_count = len(shared)
                        min_required = min(2, len(tokens_shorter))
                        if shared_count < min_required:
                            continue
                        ratio = len(shorter) / len(longer) if len(longer) > 0 else 1.0
                        sub_weight = 0.80 if ratio >= 0.4 else 0.60

                        # IDF modulation: boost if shared tokens are rare,
                        # penalize if shared tokens are common (e.g. "National",
                        # "John"). Normalized to [0.7, 1.3] range so IDF
                        # adjusts but doesn't dominate.
                        if idf and shared:
                            avg_shared_idf = sum(idf.get(t, 1.0) for t in shared) / len(shared)
                            all_tokens = tokens_shorter | tokens_longer
                            avg_corpus_idf = sum(idf.get(t, 1.0) for t in all_tokens) / max(len(all_tokens), 1)
                            idf_ratio = avg_shared_idf / avg_corpus_idf if avg_corpus_idf > 0 else 1.0
                            idf_modifier = max(0.7, min(1.3, idf_ratio))
                            sub_weight *= idf_modifier

                        signals.append((min(sub_weight, 1.0), "substring"))
                        break
                if signals:
                    break

        # Signal 3: IDF-weighted Jaccard token overlap (continuous)
        # Guard: require >= 2 shared tokens.
        tokens_a = set()
        for n in all_names_a:
            tokens_a |= _tokenize(n)
        tokens_b = set()
        for n in all_names_b:
            tokens_b |= _tokenize(n)
        shared_tokens = len(tokens_a & tokens_b)

        # Use IDF-weighted jaccard when IDF is available, raw jaccard otherwise
        jac = _idf_weighted_jaccard(tokens_a, tokens_b, idf) if idf else _jaccard(tokens_a, tokens_b)
        if jac >= 0.5 and shared_tokens >= 2:
            signals.append((jac, "jaccard"))

        # Signal 4: Embedding cosine similarity (continuous)
        # Guard: require >= 2 shared tokens.
        if a.embedding and b.embedding and shared_tokens >= 2:
            cos = _cosine_similarity(a.embedding, b.embedding)
            if cos > 0.80:
                signals.append((cos, "embedding"))

        # Signal 5: Speaker profile match — same voice across documents.
        # Weight 0.92 sits just under exact_name (0.95) but above substring.
        # Voice identity is a strong same-person signal but noisier than an
        # exact string match on a well-known name.
        if a.profile_id and b.profile_id and a.profile_id == b.profile_id:
            signals.append((0.92, "speaker_profile"))

        if not signals:
            return None

        # ── Weighted-average scoring with corroboration rule ──────────
        #
        # Replaces the old max-plus-bonus formula which created a bimodal
        # dead-zone (487 edges stuck at exactly 0.80, zero between 0.86-0.99).
        #
        # Each signal contributes (importance_weight × signal_value) to a
        # weighted average. Signal values are now continuous (actual jaccard
        # coefficient, actual cosine similarity) instead of fixed constants,
        # so the combined score forms a smooth continuum.
        #
        # Corroboration rule: sameAs requires EITHER exact_name OR >= 2
        # signals above threshold. This ensures substring alone (even with
        # guards) can never trigger a merge — it needs embedding or jaccard
        # corroboration. Prevents false merges where substring containment
        # is coincidental (e.g. "New York" in "New York Times" if both
        # mis-tagged as the same entity type).
        #
        # References: Fellegi-Sunter (additive log-likelihood ratios),
        # Splink (weighted comparison), cognitive council Sprint 2.
        _SIGNAL_IMPORTANCE: dict[str, float] = {
            "exact_name": 1.0,
            "speaker_profile": 0.9,
            "substring": 0.7,
            "embedding": 0.6,
            "jaccard": 0.5,
        }

        weighted_sum = 0.0
        weight_total = 0.0
        for value, sig_name in signals:
            w = _SIGNAL_IMPORTANCE.get(sig_name, 0.5)
            weighted_sum += w * value
            weight_total += w

        combined = weighted_sum / weight_total if weight_total > 0 else 0.0
        combined = min(combined, 1.0)

        evidence = [s[1] for s in signals]
        has_exact = "exact_name" in evidence
        has_speaker_profile = "speaker_profile" in evidence
        has_corroboration = len(signals) >= 2

        # Decision: sameAs requires exact_name, speaker_profile, OR multi-signal corroboration
        if has_exact or has_speaker_profile or (has_corroboration and combined >= self.same_as_threshold):
            alignment_type = AlignmentType.SAME_AS
        elif combined >= self.possible_same_as_threshold:
            alignment_type = AlignmentType.POSSIBLE_SAME_AS
        else:
            return None

        return AlignmentEdge(
            source_entity_id=a.candidate_id,
            target_entity_id=b.candidate_id,
            alignment_type=alignment_type,
            score=round(combined, 3),
            evidence=evidence,
            method="cross_source_aligner_v2",
        )


def check_cluster_coherence(
    cluster_ids: list[str],
    edges: list[AlignmentEdge],
    min_pairwise_score: float = 0.45,
) -> list[str]:
    """Check cluster coherence and remove weakest members.

    For a cluster of N candidates, ensures every member has at least one
    sameAs edge to another cluster member with score >= min_pairwise_score.
    Members with no qualifying edge are ejected as singletons.

    This catches cases where transitive closure pulls in a weakly-connected
    member: A↔B (0.9) + B↔C (0.7) → cluster {A,B,C}, but A↔C may have
    no direct edge or a score below threshold.

    Args:
        cluster_ids: List of candidate IDs in the cluster.
        edges: All sameAs alignment edges.
        min_pairwise_score: Minimum score for a member's best edge.

    Returns:
        List of member IDs that should remain in the cluster.
        Ejected members become singletons in the caller.
    """
    if len(cluster_ids) <= 2:
        return cluster_ids

    cluster_set = set(cluster_ids)

    # Build adjacency: member_id → best_score_to_any_other_cluster_member
    best_score: dict[str, float] = {cid: 0.0 for cid in cluster_ids}
    for edge in edges:
        if edge.source_entity_id in cluster_set and edge.target_entity_id in cluster_set:
            score = edge.score
            if score > best_score.get(edge.source_entity_id, 0.0):
                best_score[edge.source_entity_id] = score
            if score > best_score.get(edge.target_entity_id, 0.0):
                best_score[edge.target_entity_id] = score

    # Keep members whose best intra-cluster edge score meets threshold
    coherent = [cid for cid in cluster_ids if best_score.get(cid, 0.0) >= min_pairwise_score]

    # If coherence check would eject everyone (degenerate case), keep all
    if not coherent:
        return cluster_ids

    return coherent

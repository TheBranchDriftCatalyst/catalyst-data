"""Concordance engine for entity resolution.

ConcordanceEngine: resolves Mentions → EntityCandidates within one code location.
CrossSourceAligner: produces AlignmentEdges between EntityCandidates across code locations.
"""

from __future__ import annotations

import math
from collections import defaultdict

from dagster_io.logging import get_logger
from dagster_io.models import (
    AlignmentEdge,
    AlignmentType,
    EntityCandidate,
    Mention,
    MentionType,
)

logger = get_logger(__name__)


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


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
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

        # Index mentions by type
        by_type: dict[MentionType, list[Mention]] = defaultdict(list)
        for m in mentions:
            uf.find(m.mention_id)
            by_type[m.mention_type].append(m)

        for mtype, typed_mentions in by_type.items():
            # Build lookup structures
            normed: dict[str, list[Mention]] = defaultdict(list)
            for m in typed_mentions:
                normed[m.text.lower().strip()].append(m)

            # Pass 1: Exact case-insensitive grouping
            for key, group in normed.items():
                if len(group) > 1:
                    first = group[0].mention_id
                    for m in group[1:]:
                        uf.union(first, m.mention_id)

            # Build unique surface forms for pairwise comparison
            surfaces = list(normed.keys())

            # Pass 2: Substring containment
            for i, a in enumerate(surfaces):
                tokens_a = _tokenize(a)
                for b in surfaces[i + 1:]:
                    tokens_b = _tokenize(b)
                    shared = len(tokens_a & tokens_b)
                    if shared < self.min_shared_tokens:
                        continue
                    if a in b or b in a:
                        rep_a = normed[a][0].mention_id
                        rep_b = normed[b][0].mention_id
                        uf.union(rep_a, rep_b)

            # Pass 3: Jaccard overlap
            for i, a in enumerate(surfaces):
                tokens_a = _tokenize(a)
                for b in surfaces[i + 1:]:
                    tokens_b = _tokenize(b)
                    shared = len(tokens_a & tokens_b)
                    if shared < self.min_shared_tokens:
                        continue
                    if _jaccard(tokens_a, tokens_b) > self.jaccard_threshold:
                        uf.union(normed[a][0].mention_id, normed[b][0].mention_id)

            # Pass 4: Embedding cosine similarity (optional)
            if embeddings:
                for i, a in enumerate(surfaces):
                    emb_a = embeddings.get(a)
                    if emb_a is None:
                        continue
                    for b in surfaces[i + 1:]:
                        emb_b = embeddings.get(b)
                        if emb_b is None:
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
                type_counts[m.mention_type] += 1
            candidate_type = max(type_counts, key=type_counts.get)  # type: ignore[arg-type]

            # Unique source documents
            source_docs = sorted({m.document_id for m in cluster_mentions})

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

        logger.info("Resolved %d mentions into %d candidates for code_location=%s", len(mentions), len(candidates), code_location)
        return candidates


class CrossSourceAligner:
    """Produces AlignmentEdges between EntityCandidates across code locations.

    Multi-signal scoring:
    - Exact name match: 0.95
    - Substring containment: 0.80
    - Jaccard token overlap: 0.70
    - Embedding cosine similarity: 0.75

    Combined score = max(signals) + 0.05 per additional signal, capped at 1.0
    Thresholds: ≥0.90 → sameAs, ≥0.65 → possibleSameAs
    """

    def __init__(
        self,
        same_as_threshold: float = 0.90,
        possible_same_as_threshold: float = 0.65,
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
        total_candidates = sum(len(v) for v in sources.values())
        logger.info("Cross-source alignment starting: %d locations, %d total candidates", len(locations), total_candidates)

        for i, loc_a in enumerate(locations):
            for loc_b in locations[i + 1:]:
                for cand_a in sources[loc_a]:
                    for cand_b in sources[loc_b]:
                        # Only compare same entity type
                        if cand_a.candidate_type != cand_b.candidate_type:
                            continue

                        edge = self._score_pair(cand_a, cand_b)
                        if edge is not None:
                            edges.append(edge)

        logger.info("Cross-source alignment complete: %d edges produced", len(edges))
        return edges

    def _score_pair(
        self, a: EntityCandidate, b: EntityCandidate
    ) -> AlignmentEdge | None:
        signals: list[tuple[float, str]] = []
        all_names_a = {a.canonical_name.lower().strip()} | {
            alias.lower().strip() for alias in a.aliases
        }
        all_names_b = {b.canonical_name.lower().strip()} | {
            alias.lower().strip() for alias in b.aliases
        }

        # Signal 1: Exact name match (any name from either side)
        if all_names_a & all_names_b:
            signals.append((0.95, "exact_name"))

        # Signal 2: Substring containment
        if not signals:
            for na in all_names_a:
                for nb in all_names_b:
                    if na in nb or nb in na:
                        signals.append((0.80, "substring"))
                        break
                if signals:
                    break

        # Signal 3: Jaccard token overlap
        tokens_a = set()
        for n in all_names_a:
            tokens_a |= _tokenize(n)
        tokens_b = set()
        for n in all_names_b:
            tokens_b |= _tokenize(n)
        jac = _jaccard(tokens_a, tokens_b)
        if jac > 0.5:
            signals.append((0.70, "jaccard"))

        # Signal 4: Embedding cosine similarity
        if a.embedding and b.embedding:
            cos = _cosine_similarity(a.embedding, b.embedding)
            if cos > 0.80:
                signals.append((0.75, "embedding"))

        if not signals:
            return None

        # Combined score = max(signals) + 0.05 per additional signal, capped at 1.0
        max_score = max(s[0] for s in signals)
        bonus = 0.05 * (len(signals) - 1)
        combined = min(max_score + bonus, 1.0)

        if combined >= self.same_as_threshold:
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
            evidence=[s[1] for s in signals],
            method="cross_source_aligner_v1",
        )

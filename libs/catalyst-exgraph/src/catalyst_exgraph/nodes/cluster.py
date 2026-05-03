"""Entity clustering node — proximity + embedding-merge.

Phase 2 of the entity-anchored flow (CD-j6d3).

Algorithm
---------
1. **Proximity pass** (linear time):
   Sort accepted NER mentions by their doc-char start offset.  Walk a sliding
   window; any pair of consecutive mentions whose gap is ≤ ``proximity_radius``
   chars joins the same cluster.

2. **Embedding merge**:
   For each local cluster build a context snippet (±50 tokens ≈ ±200 chars
   around the cluster's bounding box) and embed via the injected
   ``EmbeddingResource``.  Compute pairwise cosine similarity; merge cluster
   pairs where ``cosine ≥ embed_merge_threshold`` AND they share ≥1 entity by
   surface form.  The shared-entity guard prevents merging unrelated topics that
   happen to be embedding-similar.

   Uses ``EmbeddingCache.get_or_compute`` so reruns are free.

3. Emits a ``clustered`` audit event with pre/post cluster counts, mean cluster
   size, and cluster bounding boxes.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from catalyst_exgraph.nodes._audit import make_audit_event
from catalyst_exgraph.state import EntityCluster, ExGraphState

logger = logging.getLogger(__name__)

# Context window around a cluster bounding box for embedding (chars ≈ 200)
_CONTEXT_HALF_CHARS = 200

# Default embedding dimension for Qwen3-8B matryoshka (matches Phase 1 resource)
_EMBED_DIM = 2048


def _cosine(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _surface_forms(cluster_indices: list[int], mentions: list[dict[str, Any]]) -> set[str]:
    """Return the lowercased surface forms for a cluster."""
    result: set[str] = set()
    for idx in cluster_indices:
        if idx < len(mentions):
            text = mentions[idx].get("text", "")
            if text:
                result.add(text.strip().lower())
    return result


def _proximity_cluster(
    mentions: list[dict[str, Any]],
    proximity_radius: int,
) -> list[list[int]]:
    """Linear-time proximity clustering.

    Returns a list of clusters, where each cluster is a list of indices
    into ``mentions`` (sorted by doc_char_start / span_start).
    """
    if not mentions:
        return []

    # Sort by start offset; prefer doc_char_start (GT format), fall back to span_start
    def _start(m: dict[str, Any]) -> int:
        return m.get("doc_char_start") or m.get("span_start") or 0

    indexed = sorted(enumerate(mentions), key=lambda x: _start(x[1]))

    clusters: list[list[int]] = []
    current: list[int] = [indexed[0][0]]
    prev_end = indexed[0][1].get("doc_char_end") or indexed[0][1].get("span_end") or 0

    for orig_idx, m in indexed[1:]:
        start = _start(m)
        if start - prev_end <= proximity_radius:
            current.append(orig_idx)
        else:
            clusters.append(current)
            current = [orig_idx]
        end = m.get("doc_char_end") or m.get("span_end") or start
        if end > prev_end:
            prev_end = end

    if current:
        clusters.append(current)

    return clusters


def _cluster_bounding_box(
    cluster_indices: list[int],
    mentions: list[dict[str, Any]],
) -> tuple[int, int]:
    """Return (doc_char_start, doc_char_end) for a cluster."""
    starts = []
    ends = []
    for idx in cluster_indices:
        if idx < len(mentions):
            m = mentions[idx]
            s = m.get("doc_char_start") or m.get("span_start") or 0
            e = m.get("doc_char_end") or m.get("span_end") or s
            starts.append(s)
            ends.append(e)
    return (min(starts) if starts else 0, max(ends) if ends else 0)


def _context_snippet(raw_text: str, char_start: int, char_end: int) -> str:
    """Extract a ±_CONTEXT_HALF_CHARS snippet around a bounding box."""
    lo = max(0, char_start - _CONTEXT_HALF_CHARS)
    hi = min(len(raw_text), char_end + _CONTEXT_HALF_CHARS)
    return raw_text[lo:hi]


class ClusterEntitiesNode:
    """Hybrid entity clustering: proximity pass + embedding merge.

    Constructor args
    ----------------
    embedder:
        An ``EmbeddingResource`` instance (provider=local, Qwen3-8B).
        May be ``None`` — when ``None`` the embedding-merge step is skipped
        and only the proximity pass runs (useful in tests + encoder-only paths).
    cache:
        An ``EmbeddingCache`` instance.  Defaults to a fresh in-memory
        ``EmbeddingCache()`` when ``None``.
    proximity_radius:
        Max char gap between consecutive mentions that still joins them into
        the same proximity cluster.  Default 200 (≈ token-window used in design).
    embed_merge_threshold:
        Cosine similarity threshold for merging clusters.  Default 0.75.
    """

    def __init__(
        self,
        embedder: Any = None,
        cache: Any = None,
        proximity_radius: int = 200,
        embed_merge_threshold: float = 0.75,
    ) -> None:
        self.embedder = embedder
        if cache is None:
            from dagster_io.embedding_cache import EmbeddingCache

            cache = EmbeddingCache()
        self.cache = cache
        self.proximity_radius = proximity_radius
        self.embed_merge_threshold = embed_merge_threshold

    async def __call__(self, state: ExGraphState) -> dict[str, Any]:
        t0 = time.perf_counter()
        node_name = "cluster_entities"

        raw_text: str = state.get("raw_text", "")
        mentions: list[dict[str, Any]] = (state.get("stages") or {}).get("ner", {}).get("accepted") or []

        if not mentions:
            logger.info("%s: no accepted NER mentions — emitting empty clusters", node_name)
            elapsed = time.perf_counter() - t0
            return {
                "entity_clusters": [],
                "audit_events": list(state.get("audit_events") or [])
                + [
                    make_audit_event(
                        node_name,
                        "completed",
                        state=state,
                        duration_s=elapsed,
                        pre_merge_count=0,
                        post_merge_count=0,
                        mean_cluster_size=0.0,
                        cluster_bboxes=[],
                    )
                ],
            }

        # ── Step 1: Proximity pass ────────────────────────────────────────────
        local_clusters: list[list[int]] = _proximity_cluster(mentions, self.proximity_radius)
        pre_merge_count = len(local_clusters)
        logger.info(
            "%s: proximity pass → %d clusters from %d mentions",
            node_name,
            pre_merge_count,
            len(mentions),
        )

        # ── Step 2: Embedding merge (skipped when no embedder) ────────────────
        merged_clusters = local_clusters
        if self.embedder is not None and len(local_clusters) > 1:
            merged_clusters = await self._embedding_merge(local_clusters, mentions, raw_text)

        post_merge_count = len(merged_clusters)
        logger.info(
            "%s: post-merge → %d clusters (proximity=%d, merged=%d)",
            node_name,
            post_merge_count,
            pre_merge_count,
            pre_merge_count - post_merge_count,
        )

        # ── Build EntityCluster objects ───────────────────────────────────────
        entity_clusters: list[EntityCluster] = []
        cluster_bboxes: list[dict[str, int]] = []
        for i, indices in enumerate(merged_clusters):
            doc_start, doc_end = _cluster_bounding_box(indices, mentions)
            cluster: EntityCluster = {
                "cluster_id": f"cluster-{i:04d}-{uuid.uuid4().hex[:6]}",
                "mention_indices": sorted(indices),
                "doc_char_start": doc_start,
                "doc_char_end": doc_end,
            }
            entity_clusters.append(cluster)
            cluster_bboxes.append({"start": doc_start, "end": doc_end, "size": len(indices)})

        mean_size = sum(len(c["mention_indices"]) for c in entity_clusters) / max(len(entity_clusters), 1)
        elapsed = time.perf_counter() - t0

        return {
            "entity_clusters": entity_clusters,
            "audit_events": list(state.get("audit_events") or [])
            + [
                make_audit_event(
                    node_name,
                    "completed",
                    state=state,
                    duration_s=elapsed,
                    pre_merge_count=pre_merge_count,
                    post_merge_count=post_merge_count,
                    mean_cluster_size=round(mean_size, 2),
                    cluster_bboxes=cluster_bboxes[:50],  # cap for JSONL size
                )
            ],
        }

    async def _embedding_merge(
        self,
        clusters: list[list[int]],
        mentions: list[dict[str, Any]],
        raw_text: str,
    ) -> list[list[int]]:
        """Merge clusters using embedding similarity + shared-entity guard."""
        # Build context snippets for each cluster
        snippets: list[str] = []
        bboxes: list[tuple[int, int]] = []
        for c in clusters:
            s, e = _cluster_bounding_box(c, mentions)
            bboxes.append((s, e))
            snippets.append(_context_snippet(raw_text, s, e))

        # Determine model name for cache key
        model_name = "qwen3-8b-matryoshka"
        if self.embedder is not None:
            model_name = getattr(self.embedder, "model_name", model_name) or model_name

        # Compute embeddings via cache
        def _compute(texts: list[str]) -> list[list[float]]:
            if self.embedder is None:
                return [[0.0] * _EMBED_DIM for _ in texts]
            return self.embedder.embed(texts)

        vectors: list[list[float]] = self.cache.get_or_compute(snippets, model_name, _EMBED_DIM, _compute)

        # Build surface-form sets for each cluster
        surface_sets = [_surface_forms(c, mentions) for c in clusters]

        # Union-Find for merging
        parent = list(range(len(clusters)))

        def _find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def _union(x: int, y: int) -> None:
            parent[_find(x)] = _find(y)

        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                cos = _cosine(vectors[i], vectors[j])
                shared = surface_sets[i] & surface_sets[j]
                if cos >= self.embed_merge_threshold and shared:
                    _union(i, j)

        # Regroup
        groups: dict[int, list[int]] = {}
        for i, c in enumerate(clusters):
            root = _find(i)
            groups.setdefault(root, [])
            groups[root].extend(c)

        return list(groups.values())

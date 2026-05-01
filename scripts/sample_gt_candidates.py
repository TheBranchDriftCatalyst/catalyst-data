#!/usr/bin/env python3
"""Sample a stratified, extraction-aware diverse GT candidate set across all 3 domains.

The benchmark currently uses 10 curated chunks; SFT/DPO fine-tuning wants 200+
annotated chunks. This script picks a defensibly-diverse subset from the
materialized medallion tree (``.test-output/<domain>/<layer>/.../*_chunks/.../data.jsonl``)
so a human reviewer (via the viewer-ui GT editor) is annotating chunks that
actually exercise the model — not topic-redundant boilerplate.

Selection pipeline (per-domain, deterministic given ``--seed``):

    1. Load chunks from the medallion tree via tests/shared/medallion.load_chunks.
    2. Open-leaks two-stage subsample: 3.6M chunks → ``--leaks-prefilter`` random
       chunks (default 5000) before any expensive work.
    3. (Optional, ``--score-extractions``) Cheap NER pre-filter using GLiNER:
       drop chunks producing zero mentions. Caches results to
       ``.test-output/gt-sampler-cache/<domain>/ner_pass.json`` so re-runs skip
       the encoder.
    4. Embed remaining chunks via ``dagster_io.EmbeddingResource``
       (text-embedding-3-small by default; falls back to in-process
       sentence-transformers when ``EMBEDDING_PROVIDER=huggingface``).
       Cached to ``.test-output/gt-sampler-cache/<domain>/embeddings.npz``.
    5. Build per-domain feature vectors that capture **extraction-relevant**
       diversity (not just topic-distance):
         media-ingest:   [primary_speaker_onehot, speaker_count_norm,
                          mention_type_histogram, embedding]
         congress-data:  [chunk_strategy_onehot, section_depth_norm,
                          mention_type_histogram, embedding]
         open-leaks:     [document_type_onehot, mention_type_histogram, embedding]
       Histograms are L1-normalized; embedding is L2-normalized; the embedding
       block is weighted (default 1.0) relative to scalar/categorical blocks.
    6. Greedy farthest-point (k-center) sampling on the combined feature
       vector. Seeds with a domain-deterministic chunk index, then iteratively
       picks the chunk maximizing the minimum distance to the already-selected
       set. O(N·K) per domain. Deterministic given a seed.

Stratification: the global ``--target`` is split per-domain via ``--media``,
``--congress``, ``--leaks`` (default 80/60/60 = 200) so a single domain (most
notably open-leaks's 3.6M-chunk corpus) can't dominate the sample.

Diagnostics (``--diagnostics``): per-domain coverage report — distinct mention
types in pool vs sample, embedding-space spread, scalar-feature coverage. Lets
us see whether the sample is actually capturing variability.

Output: ``.test-output/gt-candidates.json`` with the chosen
``(domain, document_id, chunk_id, index)`` tuples. Re-running with the same
``--seed`` produces a byte-identical file.

Usage::

    # Embedding-only diversity (fast, ~1 min after caches warm):
    python scripts/sample_gt_candidates.py --target 200 --seed 42

    # Extraction-aware (recommended, ~5-10 min first run for the NER pass):
    python scripts/sample_gt_candidates.py --target 200 --seed 42 --score-extractions

    # Diagnostic dump:
    python scripts/sample_gt_candidates.py --target 200 --diagnostics

The script does NOT write anything to ground-truth/ — it only writes the
candidate list. Human annotation happens via the viewer-ui GT editor against
``ground-truth/active.json``, which the GT generation flow is responsible for
seeding (e.g. ``task bench:ground-truth`` after ``task bench:run``).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.shared.medallion import load_chunks  # noqa: E402

CACHE_ROOT = ROOT / ".test-output" / "gt-sampler-cache"
OUTPUT_PATH = ROOT / ".test-output" / "gt-candidates.json"

DOMAIN_DIRS = {
    "media-ingest": "media",
    "congress-data": "congress",
    "open-leaks": "leaks",
}

DEFAULT_PER_DOMAIN = {
    "media-ingest": 80,
    "congress-data": 60,
    "open-leaks": 60,
}


# ────────────────────────────────────────────────────────────────────────────
# Determinism helpers
# ────────────────────────────────────────────────────────────────────────────


def _stable_hash_int(s: str) -> int:
    """Stable, non-PYTHONHASHSEED-dependent hash for reproducible tiebreakers."""
    return int(hashlib.sha256(s.encode("utf-8")).hexdigest()[:16], 16)


def _seed_for(seed: int, domain: str) -> int:
    """Domain-derived sub-seed so each domain's RNG is independent but reproducible."""
    return (seed * 1_000_003 + _stable_hash_int(domain)) % (2**32)


# ────────────────────────────────────────────────────────────────────────────
# Stage 1 — load + per-domain bucket
# ────────────────────────────────────────────────────────────────────────────


def _bucket_by_domain(chunks: list[dict]) -> dict[str, list[dict]]:
    """Bucket merged chunks by domain. Domain comes from the medallion path
    (already in load_chunks's per_domain_count, but not propagated to rows),
    so we recover it from the chunk's metadata.source / domain hints."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        meta = c.get("metadata", {}) or {}
        source = (meta.get("source") or "").lower()
        domain_hint = (meta.get("domain") or "").lower()

        if source == "media_ingest":
            domain = "media-ingest"
        elif source == "congress.gov" or domain_hint == "congress":
            domain = "congress-data"
        elif source == "wikileaks" or domain_hint == "open_leaks":
            domain = "open-leaks"
        else:
            # Fall back to chunk_id namespacing pattern
            cid = c.get("chunk_id", "")
            if cid.startswith("congress-"):
                domain = "congress-data"
            elif cid.startswith("wikileaks-"):
                domain = "open-leaks"
            else:
                domain = "media-ingest"
        buckets[domain].append(c)
    return buckets


def _stable_sort(chunks: list[dict]) -> list[dict]:
    """Sort by chunk_id so the sampler input is invariant to glob/FS order."""
    return sorted(chunks, key=lambda c: c.get("chunk_id", ""))


# ────────────────────────────────────────────────────────────────────────────
# Stage 2 — open-leaks pre-subsample (3.6M → ~5K)
# ────────────────────────────────────────────────────────────────────────────


def _prefilter_leaks(chunks: list[dict], target: int, seed: int) -> list[dict]:
    """Random pre-subsample for open-leaks. Embedding 3.6M chunks is intractable.

    Reservoir-style sampling on a sorted list with a seeded RNG so the result
    is deterministic and independent of FS iteration order.
    """
    if len(chunks) <= target:
        return chunks
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(chunks), size=target, replace=False)
    idx.sort()
    return [chunks[i] for i in idx]


# ────────────────────────────────────────────────────────────────────────────
# Stage 3 — optional GLiNER NER pre-filter
# ────────────────────────────────────────────────────────────────────────────


def _ner_cache_path(domain: str) -> Path:
    return CACHE_ROOT / domain / "ner_pass.json"


async def _gliner_predict_batch(texts: list[str]) -> list[list[dict]]:
    """Run GLiNER on a list of texts. Returns one entity-list per text."""
    from catalyst_langgraph.clients.gliner import (
        MENTION_TYPE_TO_GLINER_LABEL,
        GLiNERClient,
    )

    client = GLiNERClient()
    model = client._get_model()  # noqa: SLF001 — internal API, but lazy-load is what we want
    labels = list(MENTION_TYPE_TO_GLINER_LABEL.values())
    results: list[list[dict]] = []
    for text in texts:
        if not text.strip():
            results.append([])
            continue
        try:
            ents = model.predict_entities(text, labels, threshold=client.threshold)
        except Exception as e:
            print(f"  GLiNER failed on chunk: {e}", file=sys.stderr)
            ents = []
        results.append(
            [
                {
                    "text": e["text"],
                    "mention_type": e["label"],
                    "score": float(e["score"]),
                }
                for e in ents
            ]
        )
    return results


def _load_or_compute_ner(domain: str, chunks: list[dict], force: bool = False) -> dict[str, list[dict]]:
    """Cache GLiNER results keyed by chunk_id. Returns chunk_id → entity list."""
    cache_path = _ner_cache_path(domain)
    cache: dict[str, list[dict]] = {}
    if cache_path.exists() and not force:
        cache = json.loads(cache_path.read_text())

    missing = [c for c in chunks if c["chunk_id"] not in cache]
    if not missing:
        return cache

    print(f"  [{domain}] Running GLiNER on {len(missing)} uncached chunks…")
    texts = [c.get("text", "") for c in missing]
    entities = asyncio.run(_gliner_predict_batch(texts))

    for c, ents in zip(missing, entities, strict=True):
        cache[c["chunk_id"]] = ents

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2))
    return cache


def _filter_extractive(chunks: list[dict], ner_cache: dict[str, list[dict]], min_mentions: int = 1) -> list[dict]:
    """Drop chunks producing fewer than ``min_mentions`` GLiNER mentions."""
    return [c for c in chunks if len(ner_cache.get(c["chunk_id"], [])) >= min_mentions]


# ────────────────────────────────────────────────────────────────────────────
# Stage 4 — embeddings (cached)
# ────────────────────────────────────────────────────────────────────────────


def _embed_cache_path(domain: str) -> Path:
    return CACHE_ROOT / domain / "embeddings.npz"


def _load_or_compute_embeddings(domain: str, chunks: list[dict], force: bool = False) -> np.ndarray:
    """Embed each chunk's text. Cached as .npz keyed by chunk_id order.

    Cache layout: ``{ "chunk_ids": <U..>, "vectors": float32[N, D] }``.
    On cache hit + identical chunk_ids order, returns the cached array.
    On partial hit, recomputes only the new chunk_ids and merges.
    """
    cache_path = _embed_cache_path(domain)
    chunk_ids = [c["chunk_id"] for c in chunks]
    texts = [c.get("text", "") for c in chunks]

    cached_ids: list[str] = []
    cached_vecs: np.ndarray | None = None
    if cache_path.exists() and not force:
        data = np.load(cache_path, allow_pickle=False)
        cached_ids = list(data["chunk_ids"].astype(str))
        cached_vecs = data["vectors"].astype(np.float32)

    cached_lookup = {cid: i for i, cid in enumerate(cached_ids)}
    missing_idx = [i for i, cid in enumerate(chunk_ids) if cid not in cached_lookup]

    if missing_idx:
        # Lazy import — only require dagster_io / langchain when actually embedding.
        sys.path.insert(0, str(ROOT / "libs" / "dagster-io" / "src"))
        from dagster_io import EmbeddingResource

        embedder = EmbeddingResource()
        embedder.setup_for_execution(None)

        missing_texts = [texts[i] for i in missing_idx]
        print(f"  [{domain}] Embedding {len(missing_texts)} new chunks (model={embedder.model})…")
        new_vecs_list = embedder.embed(missing_texts)
        new_vecs = np.array(new_vecs_list, dtype=np.float32)

        if cached_vecs is None:
            all_ids = [chunk_ids[i] for i in missing_idx]
            all_vecs = new_vecs
        else:
            all_ids = list(cached_ids) + [chunk_ids[i] for i in missing_idx]
            all_vecs = np.vstack([cached_vecs, new_vecs])

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache_path, chunk_ids=np.array(all_ids), vectors=all_vecs)
        cached_lookup = {cid: i for i, cid in enumerate(all_ids)}
        cached_vecs = all_vecs

    # Reorder to match input chunks order
    assert cached_vecs is not None
    out = np.stack([cached_vecs[cached_lookup[cid]] for cid in chunk_ids])
    # L2 normalize so cosine == dot
    norms = np.linalg.norm(out, axis=1, keepdims=True) + 1e-12
    return (out / norms).astype(np.float32)


# ────────────────────────────────────────────────────────────────────────────
# Stage 5 — extraction-relevant feature vectors
# ────────────────────────────────────────────────────────────────────────────


def _onehot(values: list[str], vocab: list[str]) -> np.ndarray:
    """One-hot encode a list of categorical values against a fixed vocab."""
    idx = {v: i for i, v in enumerate(vocab)}
    out = np.zeros((len(values), len(vocab)), dtype=np.float32)
    for r, v in enumerate(values):
        if v in idx:
            out[r, idx[v]] = 1.0
    return out


def _mention_histogram(chunks: list[dict], ner_cache: dict[str, list[dict]], vocab: list[str]) -> np.ndarray:
    """Per-chunk normalized mention-type histogram from GLiNER output."""
    idx = {v: i for i, v in enumerate(vocab)}
    out = np.zeros((len(chunks), len(vocab)), dtype=np.float32)
    for r, c in enumerate(chunks):
        for ent in ner_cache.get(c["chunk_id"], []):
            label = ent.get("mention_type", "")
            if label in idx:
                out[r, idx[label]] += 1.0
        s = out[r].sum()
        if s > 0:
            out[r] /= s
    return out


def _build_feature_vectors(
    domain: str,
    chunks: list[dict],
    embeddings: np.ndarray,
    ner_cache: dict[str, list[dict]] | None,
    embedding_weight: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Compose per-domain feature vector capturing extraction-relevant axes.

    Returns (features, vocab_metadata) where vocab_metadata captures the
    one-hot/histogram bins used (for diagnostics).
    """
    n = len(chunks)
    vocab_meta: dict[str, Any] = {}

    # Mention-type histogram (only when NER pass was run)
    if ner_cache:
        gliner_label_vocab = sorted({e.get("mention_type", "") for ents in ner_cache.values() for e in ents})
        gliner_label_vocab = [v for v in gliner_label_vocab if v]
        mention_hist = _mention_histogram(chunks, ner_cache, gliner_label_vocab)
        vocab_meta["mention_types"] = gliner_label_vocab
    else:
        mention_hist = np.zeros((n, 0), dtype=np.float32)
        vocab_meta["mention_types"] = []

    # Per-domain scalar/categorical features
    if domain == "media-ingest":
        speakers = [(c.get("metadata", {}) or {}).get("primary_speaker", "UNKNOWN") or "UNKNOWN" for c in chunks]
        speaker_vocab = sorted(set(speakers))
        vocab_meta["primary_speaker"] = speaker_vocab
        sp_oh = _onehot(speakers, speaker_vocab)

        sp_count = np.array(
            [(c.get("metadata", {}) or {}).get("speaker_count", 1) for c in chunks],
            dtype=np.float32,
        ).reshape(-1, 1)
        sp_count_max = float(sp_count.max() or 1.0)
        sp_count = sp_count / sp_count_max

        scalar = np.hstack([sp_oh, sp_count])
    elif domain == "congress-data":
        strategies = [(c.get("metadata", {}) or {}).get("strategy", "unknown") or "unknown" for c in chunks]
        strat_vocab = sorted(set(strategies))
        vocab_meta["strategy"] = strat_vocab
        st_oh = _onehot(strategies, strat_vocab)

        depths = []
        for c in chunks:
            meta = c.get("metadata", {}) or {}
            sn = meta.get("section_number")
            if isinstance(sn, str):
                depths.append(float(sn.count(".") + 1))
            else:
                depths.append(0.0)
        depth_arr = np.array(depths, dtype=np.float32).reshape(-1, 1)
        depth_max = float(depth_arr.max() or 1.0)
        depth_arr = depth_arr / depth_max

        scalar = np.hstack([st_oh, depth_arr])
    elif domain == "open-leaks":
        doctypes = [(c.get("metadata", {}) or {}).get("document_type", "unknown") or "unknown" for c in chunks]
        dt_vocab = sorted(set(doctypes))
        vocab_meta["document_type"] = dt_vocab
        dt_oh = _onehot(doctypes, dt_vocab)
        scalar = dt_oh
    else:
        scalar = np.zeros((n, 0), dtype=np.float32)

    # L1-normalize scalar block per row so it doesn't dominate by magnitude
    scalar_norm = np.linalg.norm(scalar, axis=1, keepdims=True) + 1e-12
    scalar = scalar / scalar_norm

    # Compose: [scalar | mention_hist | embedding_weight * embedding]
    features = np.hstack([scalar, mention_hist, embedding_weight * embeddings]).astype(np.float32)
    return features, vocab_meta


# ────────────────────────────────────────────────────────────────────────────
# Stage 6 — greedy farthest-point (k-center) sampling
# ────────────────────────────────────────────────────────────────────────────


def _farthest_point_sampling(features: np.ndarray, k: int, seed: int) -> list[int]:
    """Greedy k-center selection. O(N·k). Deterministic given a seed.

    Distance metric: Euclidean on the (already-normalized) feature vectors.
    Ties broken by smaller index (stable). The seed only chooses the initial
    point, so re-runs with the same seed are byte-identical.
    """
    n = features.shape[0]
    k = min(k, n)
    if k == 0:
        return []
    rng = np.random.default_rng(seed)
    first = int(rng.integers(0, n))
    selected = [first]
    # min_dist[i] = min distance from chunk i to any already-selected chunk
    diff = features - features[first]
    min_dist = np.einsum("ij,ij->i", diff, diff)  # squared euclidean

    for _ in range(1, k):
        # Argmax with stable tiebreak (lowest index)
        best = int(np.argmax(min_dist))
        selected.append(best)
        diff = features - features[best]
        new_dist = np.einsum("ij,ij->i", diff, diff)
        min_dist = np.minimum(min_dist, new_dist)
        # Mark selected so we don't re-pick (its min_dist is 0 anyway)
        min_dist[best] = -np.inf

    return selected


# ────────────────────────────────────────────────────────────────────────────
# Diagnostics
# ────────────────────────────────────────────────────────────────────────────


def _diagnostics(
    domain: str,
    pool_chunks: list[dict],
    selected_idx: list[int],
    ner_cache: dict[str, list[dict]] | None,
    vocab_meta: dict[str, Any],
) -> dict[str, Any]:
    """Per-domain coverage report — distinct vocab values in pool vs sample."""
    selected = [pool_chunks[i] for i in selected_idx]
    out: dict[str, Any] = {
        "pool_size": len(pool_chunks),
        "selected_size": len(selected),
    }

    if ner_cache:
        pool_types: Counter[str] = Counter()
        sample_types: Counter[str] = Counter()
        for c in pool_chunks:
            for e in ner_cache.get(c["chunk_id"], []):
                pool_types[e.get("mention_type", "")] += 1
        for c in selected:
            for e in ner_cache.get(c["chunk_id"], []):
                sample_types[e.get("mention_type", "")] += 1
        out["mention_type_coverage"] = {
            "pool_distinct": len(pool_types),
            "sample_distinct": len(sample_types),
            "coverage_pct": round(100 * len(sample_types) / max(len(pool_types), 1), 1),
        }

    for key, vocab in vocab_meta.items():
        if not vocab or key == "mention_types":
            continue
        if domain == "media-ingest" and key == "primary_speaker":
            getter = lambda c, k=key: (c.get("metadata", {}) or {}).get("primary_speaker") or "UNKNOWN"  # noqa: E731
        elif domain == "congress-data" and key == "strategy":
            getter = lambda c, k=key: (c.get("metadata", {}) or {}).get("strategy") or "unknown"  # noqa: E731
        elif domain == "open-leaks" and key == "document_type":
            getter = lambda c, k=key: (c.get("metadata", {}) or {}).get("document_type") or "unknown"  # noqa: E731
        else:
            continue
        pool_vals = {getter(c) for c in pool_chunks}
        sample_vals = {getter(c) for c in selected}
        out[f"{key}_coverage"] = {
            "pool_distinct": len(pool_vals),
            "sample_distinct": len(sample_vals),
            "coverage_pct": round(100 * len(sample_vals) / max(len(pool_vals), 1), 1),
        }

    return out


# ────────────────────────────────────────────────────────────────────────────
# Orchestration
# ────────────────────────────────────────────────────────────────────────────


def _sample_domain(
    domain: str,
    chunks: list[dict],
    k: int,
    *,
    seed: int,
    score_extractions: bool,
    embedding_weight: float,
    leaks_prefilter: int,
    force_recompute: bool,
) -> tuple[list[dict], dict[str, Any]]:
    """End-to-end pipeline for one domain. Returns (selected_rows, diagnostics)."""
    if not chunks:
        return [], {"pool_size": 0, "selected_size": 0}

    chunks = _stable_sort(chunks)
    print(f"\n[{domain}] pool size: {len(chunks)}")

    # Open-leaks pre-subsample
    if domain == "open-leaks" and len(chunks) > leaks_prefilter:
        chunks = _prefilter_leaks(chunks, leaks_prefilter, _seed_for(seed, domain + ":prefilter"))
        print(f"[{domain}] after prefilter: {len(chunks)}")

    # NER pre-filter (optional but recommended)
    ner_cache: dict[str, list[dict]] | None = None
    if score_extractions:
        ner_cache = _load_or_compute_ner(domain, chunks, force=force_recompute)
        before = len(chunks)
        chunks = _filter_extractive(chunks, ner_cache, min_mentions=1)
        print(f"[{domain}] after NER filter (>=1 mention): {len(chunks)} (dropped {before - len(chunks)})")

    if not chunks:
        return [], {"pool_size": 0, "selected_size": 0, "note": "all chunks filtered out"}

    # Embeddings
    embeddings = _load_or_compute_embeddings(domain, chunks, force=force_recompute)

    # Feature composition
    features, vocab_meta = _build_feature_vectors(domain, chunks, embeddings, ner_cache, embedding_weight)

    # Greedy farthest-point selection
    selected_idx = _farthest_point_sampling(features, k, _seed_for(seed, domain + ":fps"))
    selected_idx.sort()  # stable output order (by pool index)

    rows = [
        {
            "domain": domain,
            "document_id": chunks[i].get("document_id", ""),
            "chunk_id": chunks[i]["chunk_id"],
            "index": chunks[i].get("index", 0),
        }
        for i in selected_idx
    ]
    diag = _diagnostics(domain, chunks, selected_idx, ner_cache, vocab_meta)
    return rows, diag


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--target", type=int, default=200, help="Total candidates across all domains.")
    parser.add_argument("--media", type=int, help="Override media-ingest quota (default: 80).")
    parser.add_argument("--congress", type=int, help="Override congress-data quota (default: 60).")
    parser.add_argument("--leaks", type=int, help="Override open-leaks quota (default: 60).")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic seed for sampling.")
    parser.add_argument(
        "--score-extractions",
        action="store_true",
        help="Run GLiNER NER pre-filter + use mention-type histogram in feature vector. "
        "Recommended; takes ~5-10 min on first run, instant after cache.",
    )
    parser.add_argument(
        "--embedding-weight",
        type=float,
        default=1.0,
        help="Weight of embedding block relative to scalar/categorical blocks (default 1.0).",
    )
    parser.add_argument(
        "--leaks-prefilter",
        type=int,
        default=5000,
        help="Random subsample size for open-leaks before embedding (default 5000).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore cached embeddings/NER and recompute.",
    )
    parser.add_argument("--diagnostics", action="store_true", help="Print per-domain coverage report.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Output JSON path.")
    args = parser.parse_args()

    # Resolve per-domain quotas — if --target is given but per-domain not, scale defaults proportionally
    if any(x is not None for x in (args.media, args.congress, args.leaks)):
        quotas = {
            "media-ingest": args.media if args.media is not None else DEFAULT_PER_DOMAIN["media-ingest"],
            "congress-data": (args.congress if args.congress is not None else DEFAULT_PER_DOMAIN["congress-data"]),
            "open-leaks": args.leaks if args.leaks is not None else DEFAULT_PER_DOMAIN["open-leaks"],
        }
    else:
        # Scale the default 80/60/60 = 200 split to the target
        scale = args.target / sum(DEFAULT_PER_DOMAIN.values())
        quotas = {d: max(1, int(round(v * scale))) for d, v in DEFAULT_PER_DOMAIN.items()}

    print("=" * 70)
    print("  GT Candidate Sampler")
    print("=" * 70)
    print(f"  Quotas: {quotas}  (total target={args.target}, seed={args.seed})")
    print(
        f"  Mode: {'extraction-aware (GLiNER + embeddings)' if args.score_extractions else 'embedding-only diversity'}"
    )
    print()

    print("Loading materialized chunks across all domains…")
    all_chunks = load_chunks()
    if not all_chunks:
        print("ERROR: no chunks materialized. Run `task bench:chunks:regen` first.", file=sys.stderr)
        return 2

    buckets = _bucket_by_domain(all_chunks)
    for d in ["media-ingest", "congress-data", "open-leaks"]:
        print(f"  {d}: {len(buckets.get(d, []))} chunks")

    output: dict[str, Any] = {
        "schema_version": "1",
        "seed": args.seed,
        "target": args.target,
        "quotas": quotas,
        "score_extractions": args.score_extractions,
        "embedding_weight": args.embedding_weight,
        "candidates": [],
        "diagnostics": {},
    }

    for domain in ["media-ingest", "congress-data", "open-leaks"]:
        rows, diag = _sample_domain(
            domain,
            buckets.get(domain, []),
            quotas[domain],
            seed=args.seed,
            score_extractions=args.score_extractions,
            embedding_weight=args.embedding_weight,
            leaks_prefilter=args.leaks_prefilter,
            force_recompute=args.force,
        )
        output["candidates"].extend(rows)
        output["diagnostics"][domain] = diag
        print(f"[{domain}] selected: {len(rows)}")

    output["total_selected"] = len(output["candidates"])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True))
    print(f"\nWrote {len(output['candidates'])} candidates to {args.output}")

    if args.diagnostics:
        print("\nPer-domain diagnostics:")
        print(json.dumps(output["diagnostics"], indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":
    sys.exit(main())

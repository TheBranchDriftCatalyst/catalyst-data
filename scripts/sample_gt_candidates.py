#!/usr/bin/env python3
"""Sample a stratified, extraction-aware diverse GT candidate set across all domains.

The benchmark currently uses 10 curated chunks; SFT/DPO fine-tuning wants 200+
annotated chunks. This script picks a defensibly-diverse subset from the
materialized medallion tree (``.test-output/<domain>/<layer>/.../*_chunks/.../data.jsonl``)
so a human reviewer (via the viewer-ui GT editor) is annotating chunks that
actually exercise the model — not topic-redundant boilerplate.

See ``docs/SEED.md`` for the methodology and the determinism contract.

Selection pipeline (per-domain, deterministic given ``--seed``):

    1. Load chunks via ``tests.shared.medallion.load_chunks``.
    2. Optional pre-subsample (per-domain ``prefilter_max``) — random down-sample
       before any expensive work. Used by open-leaks (3.6M-chunk corpus).
    3. Optional GLiNER NER pre-filter (``--score-extractions``) drops chunks
       producing zero mentions. Caches results to
       ``.test-output/gt-sampler-cache/<domain>/ner_pass.json``.
    4. Embed remaining chunks via ``dagster_io.EmbeddingResource``
       (text-embedding-3-small by default; sentence-transformers fallback when
       ``EMBEDDING_PROVIDER=huggingface``). Cached to
       ``.test-output/gt-sampler-cache/<domain>/embeddings.npz``.
    5. Build per-domain feature vectors that capture **extraction-relevant**
       diversity. Each domain registers its own ``DomainSpec`` (categorical
       and scalar feature getters + a default quota and prefilter ceiling).
    6. Greedy farthest-point (k-center) sampling on the combined feature
       vector. O(N·K) per domain. Deterministic given a seed.

Adding a new domain is a single ``register_domain(...)`` call — no core code
changes. See ``DomainSpec`` and the registrations near the top of this file
for the shape.

Output: ``.test-output/gt-candidates.json`` with the chosen
``(domain, document_id, chunk_id, index)`` tuples. Re-running with the same
``--seed`` produces a byte-identical file.

Usage::

    python scripts/sample_gt_candidates.py --target 200 --seed 42
    python scripts/sample_gt_candidates.py --target 200 --seed 42 --score-extractions --diagnostics
    python scripts/sample_gt_candidates.py --target 200 --seed 42 --media 80 --congress 60 --leaks 60

The script does NOT write anything to ground-truth/ — it only writes the
candidate list. Human annotation happens via the viewer-ui GT editor against
``ground-truth/active.json``, which the GT generation flow seeds (e.g.
``task bench:ground-truth`` after ``task bench:run``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.shared.medallion import load_chunks  # noqa: E402

CACHE_ROOT = ROOT / ".test-output" / "gt-sampler-cache"
OUTPUT_PATH = ROOT / ".test-output" / "gt-candidates.json"


# ────────────────────────────────────────────────────────────────────────────
# DomainSpec registry — adding a new domain is a single register_domain() call.
# ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CategoricalFeature:
    """A categorical chunk attribute that becomes a one-hot block in the feature vector.

    ``key`` is the metadata field name (purely for diagnostics).
    ``get`` extracts the value from a chunk dict; missing/None becomes ``"unknown"``.
    """

    key: str
    get: Callable[[dict], str]


@dataclass(frozen=True)
class ScalarFeature:
    """A scalar chunk attribute (e.g. speaker_count, section_depth) — normalized to [0, 1]."""

    key: str
    get: Callable[[dict], float]


@dataclass(frozen=True)
class DomainSpec:
    """Everything the orchestrator needs to sample one domain.

    Adding a domain: register_domain(DomainSpec(name=..., ...)) — no core
    pipeline changes. The orchestrator iterates registered domains in
    insertion order so output is deterministic.
    """

    name: str  # exact domain dir name under .test-output (e.g. "media-ingest")
    default_quota: int  # default samples-per-domain when --target is split proportionally
    categorical_features: tuple[CategoricalFeature, ...] = ()
    scalar_features: tuple[ScalarFeature, ...] = ()
    prefilter_max: int | None = None  # if pool > this, random pre-subsample first

    # Extension-point hook: domains can register a tiny block of extra
    # numeric features computed from their chunks (e.g. ``embeddings`` for
    # speaker identity). Default: no extra columns.
    extra_features: Callable[[list[dict]], np.ndarray] | None = None

    def categorical_keys(self) -> list[str]:
        return [f.key for f in self.categorical_features]


_DOMAIN_REGISTRY: dict[str, DomainSpec] = {}


def register_domain(spec: DomainSpec) -> None:
    if spec.name in _DOMAIN_REGISTRY:
        raise ValueError(f"domain {spec.name!r} already registered")
    _DOMAIN_REGISTRY[spec.name] = spec


def registered_domains() -> list[DomainSpec]:
    """Return registered domains in insertion order (deterministic)."""
    return list(_DOMAIN_REGISTRY.values())


# Per-domain registrations. Each chooses extraction-relevant axes for its
# corpus — what makes media-ingest content vary is different from what makes
# congress-data content vary.

register_domain(
    DomainSpec(
        name="media-ingest",
        default_quota=80,
        # Speaker identity + count are the dominant variability axes in podcasts/interviews.
        categorical_features=(
            CategoricalFeature(
                key="primary_speaker",
                get=lambda c: (c.get("metadata", {}) or {}).get("primary_speaker") or "UNKNOWN",
            ),
        ),
        scalar_features=(
            ScalarFeature(
                key="speaker_count",
                get=lambda c: float((c.get("metadata", {}) or {}).get("speaker_count", 1) or 1),
            ),
        ),
    )
)


def _section_depth(chunk: dict) -> float:
    """Section depth derived from `<section>.<sub>.<sub-sub>` numbering."""
    sn = (chunk.get("metadata", {}) or {}).get("section_number")
    if isinstance(sn, str):
        return float(sn.count(".") + 1)
    return 0.0


register_domain(
    DomainSpec(
        name="congress-data",
        default_quota=60,
        # Bill structure varies by chunk strategy + nesting depth (preamble vs section vs subsection).
        categorical_features=(
            CategoricalFeature(
                key="strategy",
                get=lambda c: (c.get("metadata", {}) or {}).get("strategy") or "unknown",
            ),
        ),
        scalar_features=(ScalarFeature(key="section_depth", get=_section_depth),),
    )
)


register_domain(
    DomainSpec(
        name="open-leaks",
        default_quota=60,
        # Document-type dominates variability: cable vs offshore_entity vs court_document
        # are extraction-wise very different.
        categorical_features=(
            CategoricalFeature(
                key="document_type",
                get=lambda c: (c.get("metadata", {}) or {}).get("document_type") or "unknown",
            ),
        ),
        prefilter_max=5000,  # 3.6M chunks — random down-sample before embedding/NER.
    )
)


# ────────────────────────────────────────────────────────────────────────────
# Determinism helpers
# ────────────────────────────────────────────────────────────────────────────


def _stable_hash_int(s: str) -> int:
    """Stable, non-PYTHONHASHSEED-dependent hash for reproducible tiebreakers."""
    return int(hashlib.sha256(s.encode("utf-8")).hexdigest()[:16], 16)


def _seed_for(seed: int, label: str) -> int:
    """Domain/stage-derived sub-seed so each RNG is independent but reproducible."""
    return (seed * 1_000_003 + _stable_hash_int(label)) % (2**32)


def _stable_sort(chunks: list[dict]) -> list[dict]:
    """Sort by chunk_id for a deterministic input order regardless of glob ordering."""
    return sorted(chunks, key=lambda c: c.get("chunk_id", ""))


# ────────────────────────────────────────────────────────────────────────────
# Stage 1 — load + per-domain bucket
# ────────────────────────────────────────────────────────────────────────────


def _bucket_by_domain(chunks: list[dict]) -> dict[str, list[dict]]:
    """Bucket all chunks by their domain (path-derived, set on each chunk by load_chunks)."""
    out: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        d = c.get("__domain__") or _domain_from_chunk(c)
        out[d].append(c)
    return out


def _domain_from_chunk(chunk: dict) -> str:
    """Best-effort domain inference from chunk metadata when not annotated upstream."""
    meta = chunk.get("metadata", {}) or {}
    src = meta.get("source", "")
    if "media" in src:
        return "media-ingest"
    if "congress" in src or src == "bill":
        return "congress-data"
    if "open_leaks" in src or "leak" in src:
        return "open-leaks"
    # Fall back to chunk_id prefix
    cid = chunk.get("chunk_id", "")
    if cid.startswith("congress-bill-"):
        return "congress-data"
    if cid.startswith(("epstein-", "icij-", "wikileaks-")):
        return "open-leaks"
    return "media-ingest"


# ────────────────────────────────────────────────────────────────────────────
# Stage 2 — pre-subsample (for the leaks domain, etc.)
# ────────────────────────────────────────────────────────────────────────────


def _prefilter(chunks: list[dict], n: int, seed: int) -> list[dict]:
    """Deterministic random subsample of n chunks. Used for huge corpora."""
    if n >= len(chunks):
        return chunks
    rng = np.random.default_rng(seed)
    indices = sorted(rng.choice(len(chunks), size=n, replace=False).tolist())
    return [chunks[i] for i in indices]


# ────────────────────────────────────────────────────────────────────────────
# Stage 3 — NER pre-filter (optional; gated by --score-extractions)
# ────────────────────────────────────────────────────────────────────────────


def _ner_cache_path(domain: str) -> Path:
    return CACHE_ROOT / domain / "ner_pass.json"


def _load_or_compute_ner(domain: str, chunks: list[dict], force: bool) -> dict[str, list[dict]]:
    """Return {chunk_id: [{mention_type, text, ...}]}. Cached per-domain."""
    p = _ner_cache_path(domain)
    cached: dict[str, list[dict]] = {}
    if p.exists() and not force:
        cached = json.loads(p.read_text())

    missing = [c for c in chunks if c["chunk_id"] not in cached]
    if not missing:
        return cached

    print(f"  [{domain}] NER pass on {len(missing)} new chunks (GLiNER)…")
    try:
        from gliner import GLiNER  # type: ignore[import-untyped]
    except ImportError:
        print(
            f"  [{domain}] gliner not installed — skipping NER pass. "
            "Install via `uv pip install gliner` to enable --score-extractions.",
            file=sys.stderr,
        )
        return cached

    model = GLiNER.from_pretrained("urchade/gliner_medium-v2.1")
    labels = ["PERSON", "ORGANIZATION", "LOCATION", "EVENT", "DATE", "MONEY", "PRODUCT"]
    for c in missing:
        ents = model.predict_entities(c.get("text", ""), labels, threshold=0.5)
        cached[c["chunk_id"]] = [
            {"mention_type": e["label"], "text": e["text"], "score": float(e["score"])} for e in ents
        ]

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cached, indent=2, sort_keys=True))
    return cached


def _filter_extractive(chunks: list[dict], ner_cache: dict[str, list[dict]], min_mentions: int) -> list[dict]:
    return [c for c in chunks if len(ner_cache.get(c["chunk_id"], [])) >= min_mentions]


# ────────────────────────────────────────────────────────────────────────────
# Stage 4 — embed
# ────────────────────────────────────────────────────────────────────────────


def _embeddings_cache_path(domain: str) -> Path:
    return CACHE_ROOT / domain / "embeddings.npz"


def _load_or_compute_embeddings(domain: str, chunks: list[dict], force: bool) -> np.ndarray:
    p = _embeddings_cache_path(domain)
    cached_ids: list[str] = []
    cached_vecs: np.ndarray | None = None
    if p.exists() and not force:
        z = np.load(p, allow_pickle=False)
        cached_ids = list(z["chunk_ids"].astype(str))
        # Cache schema: prefer "vectors" (legacy / compat with prior versions);
        # fall back to "embeddings" for forward compat.
        cached_vecs = z["vectors"] if "vectors" in z else z["embeddings"]

    needed = [c for c in chunks if c["chunk_id"] not in set(cached_ids)]
    if needed:
        from dagster_io import EmbeddingResource

        embedder = EmbeddingResource()
        embedder.setup_for_execution(None)
        print(f"  [{domain}] Embedding {len(needed)} new chunks (model={embedder.model})…")
        new_vecs = np.asarray(embedder.embed([c["text"] for c in needed]), dtype=np.float32)
        new_ids = [c["chunk_id"] for c in needed]

        if cached_vecs is not None:
            cached_vecs = np.vstack([cached_vecs, new_vecs])
            cached_ids = cached_ids + new_ids
        else:
            cached_vecs = new_vecs
            cached_ids = new_ids

        p.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(p, chunk_ids=np.array(cached_ids), vectors=cached_vecs)

    cached_lookup = {cid: i for i, cid in enumerate(cached_ids)}
    chunk_ids = [c["chunk_id"] for c in chunks]
    out = np.stack([cached_vecs[cached_lookup[cid]] for cid in chunk_ids])
    norms = np.linalg.norm(out, axis=1, keepdims=True) + 1e-12
    return (out / norms).astype(np.float32)


# ────────────────────────────────────────────────────────────────────────────
# Stage 5 — feature vectors (registry-driven, NOT per-domain inlined)
# ────────────────────────────────────────────────────────────────────────────


def _onehot(values: list[str], vocab: list[str]) -> np.ndarray:
    idx = {v: i for i, v in enumerate(vocab)}
    out = np.zeros((len(values), len(vocab)), dtype=np.float32)
    for r, v in enumerate(values):
        if v in idx:
            out[r, idx[v]] = 1.0
    return out


def _mention_histogram(chunks: list[dict], ner_cache: dict[str, list[dict]], vocab: list[str]) -> np.ndarray:
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
    spec: DomainSpec,
    chunks: list[dict],
    embeddings: np.ndarray,
    ner_cache: dict[str, list[dict]] | None,
    embedding_weight: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Compose feature vector from the DomainSpec's registered features + embeddings.

    Layout: [categorical one-hots | scalar features | mention_type_hist | weighted_embedding]
    """
    n = len(chunks)
    vocab_meta: dict[str, Any] = {}

    # Categorical one-hot blocks
    cat_blocks: list[np.ndarray] = []
    for cf in spec.categorical_features:
        values = [cf.get(c) or "unknown" for c in chunks]
        vocab = sorted(set(values))
        vocab_meta[cf.key] = vocab
        cat_blocks.append(_onehot(values, vocab))

    # Scalar features (max-normalized so they're roughly comparable to one-hot magnitudes)
    scalar_cols: list[np.ndarray] = []
    for sf in spec.scalar_features:
        col = np.array([sf.get(c) for c in chunks], dtype=np.float32).reshape(-1, 1)
        col_max = float(col.max() or 1.0)
        scalar_cols.append(col / col_max)

    # Mention-type histogram (only when NER pass was run)
    if ner_cache:
        gliner_label_vocab = sorted({e.get("mention_type", "") for ents in ner_cache.values() for e in ents})
        gliner_label_vocab = [v for v in gliner_label_vocab if v]
        mention_hist = _mention_histogram(chunks, ner_cache, gliner_label_vocab)
        vocab_meta["mention_types"] = gliner_label_vocab
    else:
        mention_hist = np.zeros((n, 0), dtype=np.float32)
        vocab_meta["mention_types"] = []

    # Domain-supplied extras (e.g. speaker embeddings)
    if spec.extra_features:
        extra = spec.extra_features(chunks).astype(np.float32)
    else:
        extra = np.zeros((n, 0), dtype=np.float32)

    # Combine. L1-normalize the categorical/scalar block per row so it doesn't
    # dominate by magnitude; embedding block carries its own weight.
    structural = np.hstack([*cat_blocks, *scalar_cols]) if (cat_blocks or scalar_cols) else np.zeros((n, 0), np.float32)
    if structural.size:
        structural_norm = np.linalg.norm(structural, axis=1, keepdims=True) + 1e-12
        structural = structural / structural_norm

    features = np.hstack(
        [
            structural,
            mention_hist,
            extra,
            embedding_weight * embeddings,
        ]
    ).astype(np.float32)
    return features, vocab_meta


# ────────────────────────────────────────────────────────────────────────────
# Stage 6 — greedy farthest-point (k-center) sampling
# ────────────────────────────────────────────────────────────────────────────


def _farthest_point_sampling(features: np.ndarray, k: int, seed: int) -> list[int]:
    """Greedy k-center selection. O(N·k). Deterministic given a seed.

    Distance metric: Euclidean on (already-normalized) feature vectors. Ties
    broken by smaller index (stable). The seed only chooses the initial point,
    so re-runs with the same seed are byte-identical.
    """
    n = features.shape[0]
    k = min(k, n)
    if k == 0:
        return []
    rng = np.random.default_rng(seed)
    first = int(rng.integers(0, n))
    selected = [first]
    diff = features - features[first]
    min_dist = np.einsum("ij,ij->i", diff, diff)

    for _ in range(1, k):
        best = int(np.argmax(min_dist))
        selected.append(best)
        diff = features - features[best]
        new_dist = np.einsum("ij,ij->i", diff, diff)
        min_dist = np.minimum(min_dist, new_dist)
        min_dist[best] = -np.inf

    return selected


# ────────────────────────────────────────────────────────────────────────────
# Diagnostics
# ────────────────────────────────────────────────────────────────────────────


def _diagnostics(
    spec: DomainSpec,
    pool_chunks: list[dict],
    selected_idx: list[int],
    ner_cache: dict[str, list[dict]] | None,
    vocab_meta: dict[str, Any],
) -> dict[str, Any]:
    """Per-domain coverage report — how much of the pool's variability the sample captured."""
    selected = [pool_chunks[i] for i in selected_idx]
    out: dict[str, Any] = {"pool_size": len(pool_chunks), "selected_size": len(selected)}

    # Mention-type coverage (only when NER pass was run)
    if ner_cache:
        pool_types: Counter = Counter()
        sample_types: Counter = Counter()
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

    # Categorical feature coverage (registry-driven — works for any new domain)
    for cf in spec.categorical_features:
        pool_vals = {cf.get(c) or "unknown" for c in pool_chunks}
        sample_vals = {cf.get(c) or "unknown" for c in selected}
        out[f"{cf.key}_coverage"] = {
            "pool_distinct": len(pool_vals),
            "sample_distinct": len(sample_vals),
            "coverage_pct": round(100 * len(sample_vals) / max(len(pool_vals), 1), 1),
        }

    return out


# ────────────────────────────────────────────────────────────────────────────
# Orchestration
# ────────────────────────────────────────────────────────────────────────────


def _sample_domain(
    spec: DomainSpec,
    chunks: list[dict],
    k: int,
    *,
    seed: int,
    score_extractions: bool,
    embedding_weight: float,
    force_recompute: bool,
) -> tuple[list[dict], dict[str, Any]]:
    """End-to-end pipeline for one domain. Returns (selected_rows, diagnostics)."""
    if not chunks:
        return [], {"pool_size": 0, "selected_size": 0}

    chunks = _stable_sort(chunks)
    print(f"\n[{spec.name}] pool size: {len(chunks)}")

    # Stage 2: per-domain prefilter (DomainSpec.prefilter_max)
    if spec.prefilter_max is not None and len(chunks) > spec.prefilter_max:
        chunks = _prefilter(chunks, spec.prefilter_max, _seed_for(seed, spec.name + ":prefilter"))
        print(f"[{spec.name}] after prefilter: {len(chunks)}")

    # Stage 3: optional NER pre-filter
    ner_cache: dict[str, list[dict]] | None = None
    if score_extractions:
        ner_cache = _load_or_compute_ner(spec.name, chunks, force=force_recompute)
        before = len(chunks)
        chunks = _filter_extractive(chunks, ner_cache, min_mentions=1)
        print(f"[{spec.name}] after NER filter (>=1 mention): {len(chunks)} (dropped {before - len(chunks)})")

    if not chunks:
        return [], {"pool_size": 0, "selected_size": 0, "note": "all chunks filtered out"}

    # Stage 4: embeddings
    embeddings = _load_or_compute_embeddings(spec.name, chunks, force=force_recompute)

    # Stage 5: feature vectors (registry-driven)
    features, vocab_meta = _build_feature_vectors(spec, chunks, embeddings, ner_cache, embedding_weight)

    # Stage 6: greedy farthest-point selection
    selected_idx = _farthest_point_sampling(features, k, _seed_for(seed, spec.name + ":fps"))
    selected_idx.sort()  # stable output order (by pool index)

    rows = [
        {
            "domain": spec.name,
            "document_id": chunks[i].get("document_id", ""),
            "chunk_id": chunks[i]["chunk_id"],
            "index": chunks[i].get("index", 0),
        }
        for i in selected_idx
    ]
    diag = _diagnostics(spec, chunks, selected_idx, ner_cache, vocab_meta)
    return rows, diag


def _resolve_quotas(target: int, overrides: dict[str, int | None]) -> dict[str, int]:
    """Map --target + --<domain> overrides to a per-domain quota."""
    specs = registered_domains()
    if any(v is not None for v in overrides.values()):
        return {s.name: overrides.get(s.name) or s.default_quota for s in specs}
    total_default = sum(s.default_quota for s in specs)
    scale = target / total_default if total_default else 0
    return {s.name: max(1, int(round(s.default_quota * scale))) for s in specs}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--target", type=int, default=200, help="Total candidates across all domains.")
    # One per-domain quota flag per registered domain (so adding a new domain
    # auto-gets a CLI override flag — `--<domain-with-hyphens>`).
    for spec in registered_domains():
        flag = "--" + spec.name.replace("_", "-")
        parser.add_argument(flag, dest=spec.name, type=int, help=f"Override quota for {spec.name}.")
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
    parser.add_argument("--force", action="store_true", help="Ignore cached embeddings/NER and recompute.")
    parser.add_argument("--diagnostics", action="store_true", help="Print per-domain coverage report.")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="Output JSON path.")
    args = parser.parse_args()

    overrides = {s.name: getattr(args, s.name) for s in registered_domains()}
    quotas = _resolve_quotas(args.target, overrides)

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
    for spec in registered_domains():
        print(f"  {spec.name}: {len(buckets.get(spec.name, []))} chunks")

    output: dict[str, Any] = {
        "schema_version": "2",
        "seed": args.seed,
        "target": args.target,
        "quotas": quotas,
        "score_extractions": args.score_extractions,
        "embedding_weight": args.embedding_weight,
        "candidates": [],
        "diagnostics": {},
    }

    for spec in registered_domains():
        rows, diag = _sample_domain(
            spec,
            buckets.get(spec.name, []),
            quotas[spec.name],
            seed=args.seed,
            score_extractions=args.score_extractions,
            embedding_weight=args.embedding_weight,
            force_recompute=args.force,
        )
        output["candidates"].extend(rows)
        output["diagnostics"][spec.name] = diag
        print(f"[{spec.name}] selected: {len(rows)}")

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

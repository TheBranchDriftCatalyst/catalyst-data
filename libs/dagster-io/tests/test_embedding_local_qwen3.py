"""Integration test for provider='local' with Qwen/Qwen3-Embedding-8B on MPS.

Skipped when:
  - sentence_transformers is not installed (dagster-io[local-embed] not present)
  - torch.backends.mps.is_available() returns False (not on Apple Silicon / MPS)

Marked ``slow`` because the first run downloads the model from Hugging Face
(multi-GB) and the encode pass takes ~30 s on a cold model.  Subsequent runs
are fast if the HF cache is warm.

Run manually::

    pytest libs/dagster-io/tests/test_embedding_local_qwen3.py -v -m slow

Do NOT include in normal CI — the model is too large.
"""

from __future__ import annotations

import math
import os

os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
os.environ.setdefault("OTEL_METRICS_EXPORTER", "none")

import pytest

# ── availability guards ───────────────────────────────────────────────────────

_st_available = False
_mps_available = False

try:
    import sentence_transformers  # noqa: F401

    _st_available = True
except ImportError:
    pass

try:
    import torch

    _mps_available = torch.backends.mps.is_available()
except ImportError:
    pass

_skip_reason = ""
if not _st_available:
    _skip_reason = "sentence_transformers not installed — run: pip install 'dagster-io[local-embed]'"
elif not _mps_available:
    _skip_reason = "torch.backends.mps not available — requires Apple Silicon / MPS"

requires_local_embed = pytest.mark.skipif(
    not (_st_available and _mps_available),
    reason=_skip_reason or "local-embed prerequisites not met",
)

# ── tests ─────────────────────────────────────────────────────────────────────


@requires_local_embed
@pytest.mark.slow
def test_qwen3_embedding_shape_and_normalisation():
    """Load Qwen3-Embedding-8B, embed 3 strings, assert shape (3, 2048) and unit norm."""
    from dagster_io.llm import EmbeddingResource

    resource = EmbeddingResource(
        provider="local",
        model="Qwen/Qwen3-Embedding-8B",
        dimensions=2048,
        batch_size=3,
        enable_cache=False,
    )
    resource.setup_for_execution(None)

    texts = [
        "The Federal Reserve raised interest rates by 25 basis points.",
        "Monetary policy tightening continues amid inflation concerns.",
        "The quick brown fox jumps over the lazy dog.",
    ]
    vectors = resource.embed(texts)

    # Shape
    assert len(vectors) == 3, f"Expected 3 vectors, got {len(vectors)}"
    for i, v in enumerate(vectors):
        assert len(v) == 2048, f"Vector {i} has dim {len(v)}, expected 2048"

    # All vectors should be approximately unit-normalised (L2 norm ≈ 1)
    for i, v in enumerate(vectors):
        norm = math.sqrt(sum(x * x for x in v))
        assert abs(norm - 1.0) < 1e-3, f"Vector {i} norm={norm:.6f} not ≈ 1.0"


@requires_local_embed
@pytest.mark.slow
def test_qwen3_semantic_similarity():
    """Paraphrase similarity must exceed unrelated-text similarity.

    'Federal Reserve raises rates' ↔ 'Monetary policy tightening' should score
    higher cosine similarity than either ↔ 'quick brown fox'.
    """
    from dagster_io.llm import EmbeddingResource

    resource = EmbeddingResource(
        provider="local",
        model="Qwen/Qwen3-Embedding-8B",
        dimensions=2048,
        batch_size=3,
        enable_cache=False,
    )
    resource.setup_for_execution(None)

    fed = "The Federal Reserve raised interest rates by 25 basis points."
    monetary = "Monetary policy tightening continues amid inflation concerns."
    fox = "The quick brown fox jumps over the lazy dog."

    vecs = resource.embed([fed, monetary, fox])
    v_fed, v_monetary, v_fox = vecs

    def cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        # Vectors are already normalised, so dot == cosine
        return dot

    sim_paraphrase = cosine(v_fed, v_monetary)
    sim_unrelated = cosine(v_fed, v_fox)

    assert sim_paraphrase > sim_unrelated, (
        f"Paraphrase similarity ({sim_paraphrase:.4f}) should be > unrelated similarity ({sim_unrelated:.4f})"
    )


@requires_local_embed
@pytest.mark.slow
def test_qwen3_embed_single_consistent_with_embed():
    """embed_single should return the same vector as embed([text])[0]."""
    import math

    from dagster_io.llm import EmbeddingResource

    resource = EmbeddingResource(
        provider="local",
        model="Qwen/Qwen3-Embedding-8B",
        dimensions=2048,
        batch_size=1,
        enable_cache=False,
    )
    resource.setup_for_execution(None)

    text = "Consistency check between embed and embed_single."
    v_batch = resource.embed([text])[0]
    v_single = resource.embed_single(text)

    assert len(v_single) == 2048
    # Vectors should be identical (same encode call, no randomness)
    diff = math.sqrt(sum((a - b) ** 2 for a, b in zip(v_batch, v_single, strict=True)))
    assert diff < 1e-5, f"embed vs embed_single L2 diff = {diff:.2e}"

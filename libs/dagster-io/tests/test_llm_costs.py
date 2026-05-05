"""Tests for ``dagster_io.bench.llm_costs.compute_cost_usd`` (Gap #5).

Table-driven: exact-match wins over substring; substring fallback resolves
provider-prefixed model ids; unknown models return ``None``.
"""

from __future__ import annotations

import pytest

from dagster_io.bench.llm_costs import COSTS_USD_PER_M_TOK, compute_cost_usd


@pytest.mark.parametrize(
    ("model", "tokens_in", "tokens_out", "expected"),
    [
        # Exact matches — known cloud models
        ("gpt-4o", 1_000_000, 0, 2.50),
        ("gpt-4o", 0, 1_000_000, 10.00),
        ("gpt-4o-mini", 1_000_000, 1_000_000, 0.15 + 0.60),
        ("claude-haiku-4-5-20251001", 100_000, 50_000, (100_000 * 1.00 + 50_000 * 5.00) / 1_000_000),
        ("claude-sonnet-4-6", 1_000, 2_000, (1_000 * 3.00 + 2_000 * 15.00) / 1_000_000),
        # Local/Ollama models — zero cost
        ("gemma3-12b", 1_000_000, 1_000_000, 0.0),
        ("llama3.1:8b", 100, 100, 0.0),
        ("qwen3-8b", 999, 999, 0.0),
    ],
)
def test_compute_cost_exact_match(model: str, tokens_in: int, tokens_out: int, expected: float) -> None:
    got = compute_cost_usd(model, tokens_in, tokens_out)
    assert got is not None
    assert got == pytest.approx(expected)


def test_compute_cost_substring_fallback_provider_prefix() -> None:
    # Provider-prefixed cloud model ids should fall back to the rate
    # of the matched key. ``gpt-4o-mini-2024-07-18`` is the longest
    # match and must beat ``gpt-4o``.
    got = compute_cost_usd("openai/gpt-4o-mini-2024-07-18", 1_000_000, 0)
    assert got == pytest.approx(0.15)


def test_compute_cost_substring_fallback_anthropic_prefix() -> None:
    got = compute_cost_usd("anthropic/claude-haiku-4-5-20251001", 1_000_000, 0)
    assert got == pytest.approx(1.00)


def test_compute_cost_unknown_model_returns_none() -> None:
    assert compute_cost_usd("totally-fake-model-xyz", 100, 100) is None


def test_compute_cost_empty_model_returns_none() -> None:
    assert compute_cost_usd("", 100, 100) is None


def test_compute_cost_zero_tokens() -> None:
    # Known model with zero tokens is a zero-cost call, not None.
    assert compute_cost_usd("gpt-4o", 0, 0) == pytest.approx(0.0)


def test_rate_table_has_expected_models() -> None:
    # Sentinel — guards accidental deletion. If you remove a model
    # from the table on purpose, update this list.
    for key in ("gpt-4o", "gpt-4o-mini", "gemma3-12b"):
        assert key in COSTS_USD_PER_M_TOK

"""Per-model USD/M-token rate table for SPO-call cost attribution (Gap #5).

Exact-match lookup first, then a substring-match fallback so that
provider-prefixed or version-suffixed model ids (``openai/gpt-4o-mini-2024-07-18``,
``anthropic/claude-haiku-4-5-20251001``) still resolve.

Rates are USD per 1,000,000 tokens, ``(input_rate, output_rate)``. Local
Ollama / vLLM models report (0.0, 0.0). Add new entries as new bench
configs land — keep the dict small and authoritative.
"""

from __future__ import annotations

# USD per 1M tokens, (input_rate, output_rate). Public list-prices as of
# the project's currency check; revise inline rather than threading a
# vendored pricing feed.
COSTS_USD_PER_M_TOK: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    # Local / Ollama models — zero cost
    "gemma3-12b": (0.0, 0.0),
    "llama3.1:8b": (0.0, 0.0),
    "qwen3-8b": (0.0, 0.0),
}


def compute_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """Return USD cost for a single LLM call, or ``None`` if the model
    isn't in the rate table.

    Exact-match wins. Falls back to substring match (longest key first)
    so ``openai/gpt-4o-mini-2024-07-18`` resolves to ``gpt-4o-mini`` rates.
    """
    if not model:
        return None
    rate = COSTS_USD_PER_M_TOK.get(model)
    if rate is None:
        # Substring fallback — try the longest known key first so
        # ``gpt-4o-mini`` doesn't get masked by ``gpt-4o`` for the
        # provider-prefixed mini variant.
        for key in sorted(COSTS_USD_PER_M_TOK, key=len, reverse=True):
            if key in model:
                rate = COSTS_USD_PER_M_TOK[key]
                break
    if rate is None:
        return None
    return (tokens_in * rate[0] + tokens_out * rate[1]) / 1_000_000.0


__all__ = ["COSTS_USD_PER_M_TOK", "compute_cost_usd"]

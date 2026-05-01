"""Cross-domain LLM extraction tests against medallion-tree chunks.

Consumes whatever chunks have been materialized by the per-domain integration
tests (``task bench:chunks:regen``). No domain-specific fixtures here — chunks
come from ``load_chunks()`` which globs ``.test-output/<domain>/...``.

Run:
    # First materialize chunks at least once (any subset of domains):
    task bench:chunks:regen:media   # or :congress / :leaks / meta target

    # Then run extraction against whatever chunks exist:
    LLM_MODEL=gpt-4o-mini pytest tests/test_extraction_e2e.py -v -s

Cross-model comparison: re-run with a different ``LLM_MODEL`` env value;
fixtures save per-model so prior runs are preserved.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests.shared.medallion import load_chunks
from tests.shared.store import BenchmarkStore

_store = BenchmarkStore()


def _llm_model() -> str:
    return os.environ.get("LLM_MODEL", "gpt-4o-mini")


def _needs_llm():
    base_url = os.environ.get("LLM_BASE_URL", "")
    is_local = "localhost" in base_url or "127.0.0.1" in base_url or "192.168" in base_url
    if not is_local and not (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        pytest.skip("LLM_API_KEY / OPENAI_API_KEY not set — required for non-local extraction")


@pytest.fixture(scope="session")
def extraction_result():
    """Run validated extraction via ``dagster_io.extraction.extract_validated``.

    Chunks are loaded from the medallion tree via ``load_chunks()``. If empty,
    the test skips with instructions to run the chunks regen first.
    Per-model output is cached in BenchmarkStore for cross-model comparison.
    """
    model = _llm_model()
    cached = _store.load_fixture(f"extraction_{model}")
    if cached:
        return cached

    _needs_llm()

    shared_prompts = Path(__file__).resolve().parents[1] / "k8s" / "shared" / "prompts"
    if shared_prompts.exists():
        os.environ.setdefault("PROMPT_REGISTRY_DIR", str(shared_prompts.resolve()))

    from dagster_io import TextChunk
    from dagster_io.extraction import extract_validated

    medallion_chunks = load_chunks()
    if not medallion_chunks:
        pytest.skip(
            "no chunks found at .test-output/<domain>/<layer>/.../*chunks/.../data.jsonl — "
            "run `task bench:chunks:regen` first"
        )
    eval_chunks = [TextChunk(**c) for c in medallion_chunks]
    print(f"\n  Using medallion chunks: {len(eval_chunks)} from .test-output/")

    print(f"  Running validated extraction (model={model}, concurrency=1)...")
    start = time.monotonic()
    mentions, assertions = extract_validated(
        eval_chunks,
        code_location="media_ingest",
        max_concurrency=1,
    )
    duration = time.monotonic() - start

    pipeline_stats = getattr(extract_validated, "last_stats", {})

    total_input_chars = sum(len(c.text) for c in eval_chunks)
    est_input_tokens = total_input_chars // 4
    est_output_tokens = (len(mentions) + len(assertions)) * 50
    est_total_tokens = est_input_tokens + est_output_tokens
    tokens_per_sec = est_total_tokens / duration if duration > 0 else 0

    output = {
        "model": model,
        "base_url": os.environ.get("LLM_BASE_URL", ""),
        "structured_method": os.environ.get("LLM_STRUCTURED_METHOD", "function_calling"),
        "mentions": [m.model_dump(mode="json") for m in mentions],
        "assertions": [a.model_dump(mode="json") for a in assertions],
        "stats": {
            "chunk_count": len(eval_chunks),
            "duration_s": round(duration, 1),
            "total_input_chars": total_input_chars,
            "est_total_tokens": est_total_tokens,
            "tokens_per_sec": round(tokens_per_sec, 1),
            "mention_count": len(mentions),
            "assertion_count": len(assertions),
            "mention_retries": pipeline_stats.get("mention_retries", 0),
            "proposition_retries": pipeline_stats.get("proposition_retries", 0),
            "errors": pipeline_stats.get("errors", 0),
            "llm_call_count": pipeline_stats.get("llm_call_count", 0),
            "pipeline": pipeline_stats.get("pipeline", {}),
            "audit_events": pipeline_stats.get("audit_events", []) if os.environ.get("SAVE_AUDIT_LOG") else [],
        },
    }

    print(f"  Extraction complete: {len(mentions)} mentions, {len(assertions)} assertions in {duration:.1f}s")
    print(f"  Estimated {est_total_tokens:,} tokens, {tokens_per_sec:.1f} tok/s")
    print(
        f"  Retries: {pipeline_stats.get('mention_retries', 0)} mention, "
        f"{pipeline_stats.get('proposition_retries', 0)} proposition, "
        f"{pipeline_stats.get('errors', 0)} errors"
    )
    _store.save_fixture(f"extraction_{model}", output)
    return output


@pytest.mark.llm
def test_extraction_produces_mentions(extraction_result):
    assert len(extraction_result["mentions"]) > 0, "Should extract at least one mention"


@pytest.mark.llm
def test_extraction_produces_assertions(extraction_result):
    assert len(extraction_result["assertions"]) > 0, "Should extract at least one assertion"


@pytest.mark.llm
def test_mentions_have_valid_types(extraction_result):
    from catalyst_contracts_core.enums import MentionType

    valid_types = {t.value for t in MentionType}
    for m in extraction_result["mentions"]:
        assert m["mention_type"] in valid_types, f"Invalid type: {m['mention_type']}"


@pytest.mark.llm
def test_mentions_have_valid_spans(extraction_result):
    """Verify span offsets actually match the source text (loaded from medallion)."""
    chunk_texts = {c["chunk_id"]: c["text"] for c in load_chunks()}
    checked = 0
    for m in extraction_result["mentions"]:
        s, e = m.get("span_start"), m.get("span_end")
        chunk_id = m.get("chunk_id", "")
        if s is not None and e is not None and chunk_id in chunk_texts:
            source = chunk_texts[chunk_id]
            if 0 <= s < e <= len(source):
                assert source[s:e] == m["text"], f"Span mismatch: '{m['text']}' != source[{s}:{e}]='{source[s:e]}'"
                checked += 1
    print(f"\n  Verified {checked} mention spans against source text")


@pytest.mark.llm
def test_assertions_have_valid_structure(extraction_result):
    for a in extraction_result["assertions"]:
        subject = a.get("subject_text") or a.get("subject", "")
        predicate = a.get("predicate", "")
        obj = a.get("object_text") or a.get("object", "")
        assert subject, f"Assertion missing subject: {a}"
        assert predicate, f"Assertion missing predicate: {a}"
        assert obj, f"Assertion missing object: {a}"


@pytest.mark.llm
def test_extraction_type_distribution(extraction_result):
    """Print mention type distribution for inspection."""
    types: dict[str, int] = {}
    for m in extraction_result["mentions"]:
        t = m.get("mention_type", "?")
        types[t] = types.get(t, 0) + 1
    print(f"\n  Mention type distribution (model={extraction_result['model']}):")
    for t, c in sorted(types.items(), key=lambda x: -x[1]):
        print(f"    {t}: {c}")
    print(f"  Total: {sum(types.values())} mentions, {len(extraction_result['assertions'])} assertions")

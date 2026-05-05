"""S3-backed prompt + raw-response archive for SPO LLM calls (Gap #5).

Bench audit events carry truncated previews inline (so the StateInspector
can render without an extra round-trip), but the full prompt/response
text is content-addressed in S3 and fetched on-demand by the viewer when
the data scientist expands a "show full prompt" pane.

Layout:

    bench/prompts/<prompt_hash>.txt
        -- content-addressed; one write per unique rendered prompt across
           the run. ``prompt_hash`` is sha256(system + "\n\n" + user)[:16].

    bench/responses/<run_id>/<chunk_id_safe>.txt
        -- one write per LLM call. ``chunk_id_safe`` is the chunk_id with
           ``/`` replaced by ``_`` (S3 keys can contain ``:``, but ``/``
           would create unwanted prefixes).

Puts are idempotent — a re-run that re-renders the same prompt skips
the S3 write rather than duplicating.

Helpers take an :class:`S3BenchmarkStore` and reuse its ``client`` (the
existing ``put_object`` / ``get_object`` wrappers handle metrics +
retries) — no fresh S3 client wiring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dagster_io.bench.store import S3BenchmarkStore


# Prefixes mirror the rest of the bench S3 layout (``bench/...``). Kept
# here as constants so callers don't reach into hardcoded strings.
_PROMPTS_PREFIX = "bench/prompts"
_RESPONSES_PREFIX = "bench/responses"


def _prompt_key(prompt_hash: str) -> str:
    return f"{_PROMPTS_PREFIX}/{prompt_hash}.txt"


def _safe_chunk_id(chunk_id: str) -> str:
    """Make ``chunk_id`` safe to embed in an S3 key.

    S3 itself accepts most characters, but ``/`` would partition the
    response under a fake hierarchy. Replace it with ``_`` and otherwise
    pass through (chunk_ids carry ``:`` as a separator, which is fine).
    """
    return chunk_id.replace("/", "_")


def _response_key(run_id: str, chunk_id: str) -> str:
    return f"{_RESPONSES_PREFIX}/{run_id}/{_safe_chunk_id(chunk_id)}.txt"


def put_prompt(store: S3BenchmarkStore, prompt_hash: str, full_text: str) -> str:
    """Persist a full rendered prompt to S3, keyed by content hash.

    Idempotent: skips the write if the key already exists. Returns the
    S3 key in either case so callers can record it alongside the audit
    event.
    """
    key = _prompt_key(prompt_hash)
    if store.client.head_object(key) is not None:
        return key
    store.client.put_object(key, full_text.encode("utf-8"))
    return key


def get_prompt(store: S3BenchmarkStore, prompt_hash: str) -> str | None:
    """Fetch a previously-stored full prompt by hash, or ``None`` on miss."""
    key = _prompt_key(prompt_hash)
    try:
        return store.client.get_object(key).decode("utf-8")
    except Exception:
        return None


def put_response(store: S3BenchmarkStore, run_id: str, chunk_id: str, full_text: str) -> str:
    """Persist a full raw LLM response to S3 under the run + chunk_id.

    Idempotent: skips the write if the key already exists (re-runs of
    the same chunk in the same run shouldn't happen, but we tolerate it).
    """
    key = _response_key(run_id, chunk_id)
    if store.client.head_object(key) is not None:
        return key
    store.client.put_object(key, full_text.encode("utf-8"))
    return key


def get_response(store: S3BenchmarkStore, run_id: str, chunk_id: str) -> str | None:
    """Fetch a previously-stored raw response, or ``None`` on miss.

    ``chunk_id`` is normalised the same way the put path does so callers
    can pass either the original or the safe form.
    """
    key = _response_key(run_id, chunk_id)
    try:
        return store.client.get_object(key).decode("utf-8")
    except Exception:
        return None


__all__ = [
    "get_prompt",
    "get_response",
    "put_prompt",
    "put_response",
]

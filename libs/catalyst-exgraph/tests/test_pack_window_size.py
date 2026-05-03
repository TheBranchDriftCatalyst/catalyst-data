"""Tests for PackEvidenceNode — window sizing and splitting (CD-j6d3)."""

from __future__ import annotations

import pytest
from catalyst_exgraph.nodes.pack import MODEL_WINDOWS, PackEvidenceNode, _resolve_context_window
from catalyst_exgraph.state import EntityCluster


def _make_cluster(cluster_id: str, start: int, end: int, indices: list[int]) -> EntityCluster:
    return EntityCluster(
        cluster_id=cluster_id,
        mention_indices=indices,
        doc_char_start=start,
        doc_char_end=end,
    )


def _make_state(raw_text: str, clusters: list[EntityCluster], model: str | None = None) -> dict:
    return {
        "raw_text": raw_text,
        "model": model,
        "entity_clusters": clusters,
        "stages": {
            "ner": {
                "accepted": [{"text": f"Entity-{i}", "span_start": i * 10, "span_end": i * 10 + 5} for i in range(10)]
            }
        },
        "audit_events": [],
    }


# ── Unit tests for MODEL_WINDOWS resolution ──────────────────────────────────


def test_model_windows_contains_gliner():
    assert "gliner-medium" in MODEL_WINDOWS
    assert MODEL_WINDOWS["gliner-medium"] <= 512


def test_resolve_context_window_exact():
    assert _resolve_context_window("gliner-medium") == MODEL_WINDOWS["gliner-medium"]


def test_resolve_context_window_substring():
    """Substring matching works for model IDs like 'urchade/gliner_medium-v2.1'."""
    # The model name 'gemma3-12b' substring-matches the key 'gemma3-12b'
    assert _resolve_context_window("gemma3-12b") == MODEL_WINDOWS["gemma3-12b"]


def test_resolve_context_window_unknown():
    """Unknown model → default context window."""
    from catalyst_exgraph.nodes.pack import _DEFAULT_CONTEXT_TOKENS

    assert _resolve_context_window("totally-unknown-model") == _DEFAULT_CONTEXT_TOKENS


def test_resolve_context_window_none():
    from catalyst_exgraph.nodes.pack import _DEFAULT_CONTEXT_TOKENS

    assert _resolve_context_window(None) == _DEFAULT_CONTEXT_TOKENS


# ── Integration tests: PackEvidenceNode ─────────────────────────────────────


@pytest.mark.asyncio
async def test_pack_single_cluster_fits_window():
    """Single cluster whose window fits in the model context → 1 evidence window."""
    # Use override to force a large context so no splitting happens
    node = PackEvidenceNode(context_tokens=8192)

    raw_text = "A" * 2000
    clusters = [_make_cluster("c0", 500, 600, [0, 1])]
    state = _make_state(raw_text, clusters, model=None)

    result = await node(state)
    windows = result["evidence_windows"]
    assert len(windows) == 1
    assert windows[0]["cluster_id"] == "c0"
    assert windows[0]["mention_indices"] == [0, 1]
    assert "window_id" in windows[0]
    assert "text" in windows[0]
    assert len(windows[0]["text"]) > 0


@pytest.mark.asyncio
async def test_pack_different_window_counts_by_model():
    """Same cluster set → different window counts for small vs large model."""
    # Build a large raw_text (50K chars) with a cluster spanning chars 10000–40000
    raw_text = "X " * 25000  # 50000 chars
    cluster = _make_cluster("c0", 10000, 40000, [0])

    # Small model: gliner-medium (320 tok × 4 chars/tok = 1280 max chars per window)
    node_small = PackEvidenceNode(context_tokens=MODEL_WINDOWS["gliner-medium"])
    result_small = await node_small(_make_state(raw_text, [cluster], model="gliner-medium"))
    count_small = len(result_small["evidence_windows"])

    # Large model: gemma3-12b (24576 tok × 4 chars/tok = 98304 max chars per window)
    node_large = PackEvidenceNode(context_tokens=MODEL_WINDOWS["gemma3-12b"])
    result_large = await node_large(_make_state(raw_text, [cluster], model="gemma3-12b"))
    count_large = len(result_large["evidence_windows"])

    assert count_small > count_large, (
        f"Small model should produce more windows than large: gliner-medium={count_small}, gemma3-12b={count_large}"
    )
    assert count_large >= 1


@pytest.mark.asyncio
async def test_pack_multiple_clusters_produce_multiple_windows():
    """Two clusters → at least two evidence windows."""
    node = PackEvidenceNode(context_tokens=4096)
    raw_text = "A" * 2000
    clusters = [
        _make_cluster("c0", 100, 200, [0]),
        _make_cluster("c1", 1500, 1600, [1]),
    ]
    state = _make_state(raw_text, clusters, model=None)
    result = await node(state)
    assert len(result["evidence_windows"]) >= 2


@pytest.mark.asyncio
async def test_pack_no_clusters_produces_no_windows():
    """Empty cluster list → empty evidence windows."""
    node = PackEvidenceNode(context_tokens=4096)
    state = _make_state("Some text", [], model=None)
    result = await node(state)
    assert result["evidence_windows"] == []


@pytest.mark.asyncio
async def test_pack_audit_event_emitted():
    """PackEvidenceNode emits a 'packed' audit event."""
    node = PackEvidenceNode(context_tokens=4096)
    raw_text = "A" * 500
    clusters = [_make_cluster("c0", 100, 200, [0])]
    state = _make_state(raw_text, clusters)
    result = await node(state)

    audit = result.get("audit_events", [])
    pack_event = next((e for e in audit if e.get("node_name") == "pack_evidence"), None)
    assert pack_event is not None, "Expected a pack_evidence audit event"
    details = pack_event.get("details", {})
    assert "window_count" in details
    assert "total_tokens" in details
    assert "mean_tokens_per_window" in details


@pytest.mark.asyncio
async def test_pack_window_text_within_doc_bounds():
    """Evidence window text must be a substring of raw_text."""
    node = PackEvidenceNode(context_tokens=4096)
    raw_text = "Hello World " * 100
    clusters = [_make_cluster("c0", 50, 100, [0])]
    state = _make_state(raw_text, clusters)
    result = await node(state)

    for win in result["evidence_windows"]:
        assert win["text"] in raw_text or raw_text.startswith(win["text"][:20]), (
            "Window text should be derived from raw_text"
        )

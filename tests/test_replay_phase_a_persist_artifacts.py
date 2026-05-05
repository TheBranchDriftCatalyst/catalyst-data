"""Regression test for CD-gf6f.

`_replay_phase_a_events_from_cache` was silently swallowing an
``AttributeError`` raised by calling ``.values()`` on the
``CachedNerResult.clusters`` *list* (the type signature is
``list[dict]``, not ``dict[str, ...]``). The bare ``except Exception:
pass`` masked the failure, so warm-cache replays emitted ZERO
``persist_artifacts`` events and the State Inspector's downstream
lineage panel rendered empty on cache-hit runs.

This test exercises the cache-replay branch end-to-end:
1. Configures the module-global ``event_store`` to a temp shard dir
2. Constructs a ``CachedNerResult`` with two cluster dicts (each with
   a ``propositions`` list) and a couple of mentions
3. Calls ``_replay_phase_a_events_from_cache`` with the cached result
4. Asserts a ``persist_artifacts`` event with the expected counts +
   ``from_cache=True`` lands in the audit log

If the AttributeError fix regresses, ``mentions_written`` /
``propositions_written`` / ``from_cache`` will all be missing/None and
the assertions will fail loudly.
"""

from __future__ import annotations

import pytest

from dagster_io.bench import event_store
from dagster_io.cluster_cache import CachedNerResult


@pytest.fixture(autouse=True)
def _configure_event_store(tmp_path):
    """Bind the module-global event_store to a temp shard dir per test.

    Mirrors the pattern in ``test_phase_a_per_encoder_stats.py`` so a
    leaked configure() doesn't trip the "already configured" guard.
    """
    event_store.close()
    event_store.configure(run_id="test-run-replay", run_dir=tmp_path)
    yield
    event_store.close()


def test_replay_emits_persist_artifacts_with_correct_counts():
    """Warm-cache replay must emit a ``persist_artifacts`` event with the
    cached mention + proposition counts and ``from_cache=True``.

    Regression for CD-gf6f: previously, ``cached.clusters`` (a
    ``list[dict]``) was being treated as a dict via ``.values()``,
    raising ``AttributeError`` that the bare except swallowed — so no
    event was emitted at all.
    """
    from tests.benchmark_harness import _replay_phase_a_events_from_cache

    # Two cluster dicts, each with a propositions list — proposition_count
    # should sum to 2 + 3 = 5. Two accepted mentions.
    clusters = [
        {"id": "c1", "propositions": [{"s": "x", "p": "y", "o": "z1"}, {"s": "x", "p": "y", "o": "z2"}]},
        {
            "id": "c2",
            "propositions": [
                {"s": "a", "p": "b", "o": "c1"},
                {"s": "a", "p": "b", "o": "c2"},
                {"s": "a", "p": "b", "o": "c3"},
            ],
        },
    ]
    mentions = [
        {"id": "m1", "text": "alpha", "label": "PERSON"},
        {"id": "m2", "text": "beta", "label": "ORG"},
    ]
    cached = CachedNerResult(
        clusters=clusters,
        mentions=mentions,
        per_encoder_mentions={"gliner-medium": mentions},  # non-empty so
        # replay proceeds
        evidence_windows=[],
        rejected_mentions=[],
    )

    # Run the replay — the AttributeError previously raised here was
    # swallowed by the bare except. With the fix it must complete cleanly
    # AND emit the persist_artifacts event.
    _replay_phase_a_events_from_cache("doc-replay-1", cached)

    events = event_store.read_events_for_test()
    persist_events = [e for e in events if e.get("node_name") == "persist_artifacts"]

    assert len(persist_events) >= 1, (
        "cache-replay must emit at least one persist_artifacts event "
        "(CD-gf6f regression: previously bare-except swallowed the AttributeError "
        "from cached.clusters.values() and zero events landed)"
    )

    ev = persist_events[0]
    details = ev.get("details") or {}

    assert ev.get("doc_id") == "doc-replay-1"
    assert details.get("mentions_written") == 2, (
        f"expected mentions_written=2 (len(cached.mentions)), got {details.get('mentions_written')!r}"
    )
    assert details.get("propositions_written") == 5, (
        f"expected propositions_written=5 (2 + 3 across cluster dicts), got "
        f"{details.get('propositions_written')!r} — likely the dict-key "
        f"access regressed back to getattr()"
    )
    assert details.get("from_cache") is True, f"expected details.from_cache=True, got {details.get('from_cache')!r}"
    # Sanity: the per-asset row_counts dict carries the same numbers.
    row_counts = details.get("row_counts") or {}
    assert row_counts.get("media_ingest/mention_artifacts") == 2
    assert row_counts.get("media_ingest/proposition_artifacts") == 5


def test_replay_with_empty_clusters_still_emits_persist_artifacts():
    """If clusters is empty (no propositions cached) but mentions exist,
    a ``persist_artifacts`` event should still emit with
    ``propositions_written=0`` — the downstream panel needs the event
    to render mentions even when no SPO ran.
    """
    from tests.benchmark_harness import _replay_phase_a_events_from_cache

    mentions = [{"id": "m1", "text": "alpha", "label": "PERSON"}]
    cached = CachedNerResult(
        clusters=[],
        mentions=mentions,
        per_encoder_mentions={"gliner-medium": mentions},
        evidence_windows=[],
        rejected_mentions=[],
    )

    _replay_phase_a_events_from_cache("doc-replay-2", cached)

    events = event_store.read_events_for_test()
    persist_events = [
        e for e in events if e.get("node_name") == "persist_artifacts" and e.get("doc_id") == "doc-replay-2"
    ]
    assert len(persist_events) == 1
    details = persist_events[0].get("details") or {}
    assert details.get("mentions_written") == 1
    assert details.get("propositions_written") == 0
    assert details.get("from_cache") is True

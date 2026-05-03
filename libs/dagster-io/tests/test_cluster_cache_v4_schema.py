"""Tests for CachedNerResult v4 schema fields (CD-z6xe).

Verifies:
- Round-trip serialisation of per_encoder_mentions, evidence_windows, rejected_mentions.
- Legacy v2/v3 entries (no extra fields) load with empty defaults — backwards compat.
- Mixed old/new shapes don't corrupt each other in the same store.
"""

import json

from dagster_io.cluster_cache import (
    CachedNerResult,
    ClusterCache,
    _from_payload,
    _InMemoryStore,
    _make_cluster_key,
    _to_payload,
)


def _make_cluster(idx: int) -> dict:
    return {"cluster_id": f"cl-{idx}", "mention_indices": [idx]}


def _make_mention(idx: int, encoder: str = "enc-a") -> dict:
    return {"text": f"Entity{idx}", "label": "PERSON", "start": idx * 20, "_source_encoder": encoder}


def _make_evidence_window(idx: int) -> dict:
    return {"window_id": f"w-{idx}", "text": f"window text {idx}", "mention_indices": [idx]}


def _make_rejected(idx: int) -> dict:
    return {"text": f"Rejected{idx}", "canonical_type": "ORG", "vote_count": 1, "quorum": 2}


# ── v4 round-trip ────────────────────────────────────────────────────────────


class TestV4RoundTrip:
    def _cache(self) -> ClusterCache:
        return ClusterCache(store=_InMemoryStore(), code_location="test")

    def test_per_encoder_mentions_round_trip(self):
        """per_encoder_mentions survives put → get."""
        cache = self._cache()
        result = CachedNerResult(
            clusters=[_make_cluster(0)],
            mentions=[_make_mention(0)],
            per_encoder_mentions={
                "gliner-large": [_make_mention(0, "gliner-large"), _make_mention(1, "gliner-large")],
                "gliner-pii": [_make_mention(2, "gliner-pii")],
            },
            evidence_windows=[_make_evidence_window(0)],
            rejected_mentions=[_make_rejected(0)],
        )
        cache.put("doc-1", "some text", "ensemble_v4", {}, result)
        got = cache.get("doc-1", "some text", "ensemble_v4", {})

        assert got is not None
        assert set(got.per_encoder_mentions.keys()) == {"gliner-large", "gliner-pii"}
        assert len(got.per_encoder_mentions["gliner-large"]) == 2
        assert len(got.per_encoder_mentions["gliner-pii"]) == 1

    def test_evidence_windows_round_trip(self):
        """evidence_windows survives put → get."""
        cache = self._cache()
        windows = [_make_evidence_window(0), _make_evidence_window(1), _make_evidence_window(2)]
        result = CachedNerResult(
            clusters=[_make_cluster(0)],
            mentions=[],
            per_encoder_mentions={},
            evidence_windows=windows,
            rejected_mentions=[],
        )
        cache.put("doc-2", "evidence text", "ensemble_v4", {}, result)
        got = cache.get("doc-2", "evidence text", "ensemble_v4", {})

        assert got is not None
        assert len(got.evidence_windows) == 3
        assert got.evidence_windows[0]["window_id"] == "w-0"
        assert got.evidence_windows[2]["window_id"] == "w-2"

    def test_rejected_mentions_round_trip(self):
        """rejected_mentions survives put → get."""
        cache = self._cache()
        rejected = [_make_rejected(0), _make_rejected(1)]
        result = CachedNerResult(
            clusters=[],
            mentions=[],
            per_encoder_mentions={},
            evidence_windows=[],
            rejected_mentions=rejected,
        )
        cache.put("doc-3", "reject text", "ensemble_v4", {}, result)
        got = cache.get("doc-3", "reject text", "ensemble_v4", {})

        assert got is not None
        assert len(got.rejected_mentions) == 2
        assert got.rejected_mentions[0]["text"] == "Rejected0"

    def test_all_v4_fields_preserved_in_one_entry(self):
        """All five CachedNerResult fields survive a round-trip together."""
        cache = self._cache()
        result = CachedNerResult(
            clusters=[_make_cluster(0), _make_cluster(1)],
            mentions=[_make_mention(0)],
            per_encoder_mentions={"enc-a": [_make_mention(0, "enc-a")], "enc-b": []},
            evidence_windows=[_make_evidence_window(0)],
            rejected_mentions=[_make_rejected(0)],
        )
        cache.put("doc-4", "full text", "ensemble_v4", {}, result)
        got = cache.get("doc-4", "full text", "ensemble_v4", {})

        assert got is not None
        assert len(got.clusters) == 2
        assert len(got.mentions) == 1
        assert set(got.per_encoder_mentions.keys()) == {"enc-a", "enc-b"}
        assert len(got.evidence_windows) == 1
        assert len(got.rejected_mentions) == 1


# ── Legacy backwards-compat ──────────────────────────────────────────────────


class TestLegacyBackwardsCompat:
    def _cache_with_store(self):
        store = _InMemoryStore()
        return ClusterCache(store=store, code_location="test"), store

    def test_v2_bare_list_loads_with_empty_v4_fields(self):
        """v2 cache entry (bare JSON list) loads; v4 fields default to empty."""
        cache, store = self._cache_with_store()
        key = _make_cluster_key("legacy-text", "gliner-large", {})
        legacy_bytes = json.dumps([_make_cluster(0), _make_cluster(1)]).encode()
        store.write_raw("test", key, legacy_bytes)

        got = cache.get("legacy-doc", "legacy-text", "gliner-large", {})

        assert got is not None
        assert len(got.clusters) == 2
        assert got.mentions == []
        assert got.per_encoder_mentions == {}
        assert got.evidence_windows == []
        assert got.rejected_mentions == []

    def test_v3_clusters_and_mentions_only_loads_with_empty_v4_fields(self):
        """v3 entry (clusters + mentions, no v4 extras) loads; v4 fields default empty."""
        cache, store = self._cache_with_store()
        key = _make_cluster_key("v3-text", "gliner-large", {})
        v3_bytes = json.dumps(
            {
                "clusters": [_make_cluster(0)],
                "mentions": [_make_mention(0)],
            }
        ).encode()
        store.write_raw("test", key, v3_bytes)

        got = cache.get("v3-doc", "v3-text", "gliner-large", {})

        assert got is not None
        assert len(got.clusters) == 1
        assert len(got.mentions) == 1
        assert got.per_encoder_mentions == {}
        assert got.evidence_windows == []
        assert got.rejected_mentions == []

    def test_legacy_and_v4_entries_coexist_in_same_store(self):
        """Legacy and v4 entries under different keys don't corrupt each other."""
        cache, store = self._cache_with_store()

        # Plant a legacy entry for doc-A
        key_a = _make_cluster_key("text-a", "gliner-large", {})
        store.write_raw("test", key_a, json.dumps([_make_cluster(0)]).encode())

        # Write a v4 entry for doc-B
        v4_result = CachedNerResult(
            clusters=[_make_cluster(1)],
            mentions=[_make_mention(1)],
            per_encoder_mentions={"enc-a": [_make_mention(1, "enc-a")]},
            evidence_windows=[_make_evidence_window(0)],
            rejected_mentions=[],
        )
        cache.put("doc-b", "text-b", "gliner-large", {}, v4_result)

        # Read both back
        got_a = cache.get("doc-a", "text-a", "gliner-large", {})
        got_b = cache.get("doc-b", "text-b", "gliner-large", {})

        assert got_a is not None
        assert got_a.per_encoder_mentions == {}  # legacy — empty v4 fields

        assert got_b is not None
        assert got_b.per_encoder_mentions == {"enc-a": [_make_mention(1, "enc-a")]}


# ── _to_payload / _from_payload unit tests ──────────────────────────────────


class TestPayloadSerde:
    def test_to_payload_produces_all_five_keys(self):
        result = CachedNerResult(
            clusters=[_make_cluster(0)],
            mentions=[_make_mention(0)],
            per_encoder_mentions={"enc": [_make_mention(0, "enc")]},
            evidence_windows=[_make_evidence_window(0)],
            rejected_mentions=[_make_rejected(0)],
        )
        payload = json.loads(_to_payload(result).decode())
        assert set(payload.keys()) == {
            "clusters",
            "mentions",
            "per_encoder_mentions",
            "evidence_windows",
            "rejected_mentions",
        }

    def test_from_payload_v4_full(self):
        data = json.dumps(
            {
                "clusters": [_make_cluster(0)],
                "mentions": [_make_mention(0)],
                "per_encoder_mentions": {"enc": [_make_mention(0, "enc")]},
                "evidence_windows": [_make_evidence_window(0)],
                "rejected_mentions": [_make_rejected(0)],
            }
        ).encode()
        result = _from_payload(data)
        assert result is not None
        assert len(result.clusters) == 1
        assert len(result.per_encoder_mentions["enc"]) == 1
        assert len(result.evidence_windows) == 1
        assert len(result.rejected_mentions) == 1

    def test_from_payload_corrupt_returns_none(self):
        assert _from_payload(b"not json") is None

    def test_from_payload_empty_v4_fields_when_absent(self):
        """Dict payload missing v4 keys → all three default to empty."""
        data = json.dumps({"clusters": [], "mentions": []}).encode()
        result = _from_payload(data)
        assert result is not None
        assert result.per_encoder_mentions == {}
        assert result.evidence_windows == []
        assert result.rejected_mentions == []

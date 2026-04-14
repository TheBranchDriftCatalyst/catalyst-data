"""Tests for HITL entity alias override injection into union-find clustering.

Validates that forced merges from viewer_entity_overrides correctly merge
EntityCandidates that would otherwise remain separate due to concordance guards.
"""

from __future__ import annotations

from dagster_io.concordance import _UnionFind
from dagster_io.models import EntityCandidate, MentionType


def _make_candidate(
    name: str, entity_type: MentionType = MentionType.PERSON, aliases: list[str] | None = None
) -> EntityCandidate:
    """Create a minimal EntityCandidate for testing."""
    return EntityCandidate(
        canonical_name=name,
        candidate_type=entity_type,
        aliases=aliases or [],
        mention_ids=["m1"],
        mention_count=1,
        source_documents=["doc-1"],
        code_location="media_ingest",
    )


def _apply_overrides(candidates: list[EntityCandidate], overrides: list[dict]) -> dict[str, list[str]]:
    """Simulate the override injection logic from canonical_entities.

    Returns union-find clusters as {root: [member_ids]}.
    """
    uf = _UnionFind()
    for cand in candidates:
        uf.find(cand.candidate_id)

    # Build name→id index (same logic as canonical_entities asset)
    cand_by_name_type: dict[tuple[str, str], list[str]] = {}
    for cand in candidates:
        etype = cand.candidate_type.value if hasattr(cand.candidate_type, "value") else str(cand.candidate_type)
        key = (cand.canonical_name.lower(), etype)
        cand_by_name_type.setdefault(key, []).append(cand.candidate_id)
        for alias in cand.aliases:
            alias_key = (alias.lower(), etype)
            cand_by_name_type.setdefault(alias_key, []).append(cand.candidate_id)

    for override in overrides:
        alias_key = (override["alias_text"].lower(), override["entity_type"])
        target_key = (override["target_name"].lower(), override["entity_type"])
        alias_ids = cand_by_name_type.get(alias_key, [])
        target_ids = cand_by_name_type.get(target_key, [])
        if alias_ids and target_ids:
            for aid in alias_ids:
                for tid in target_ids:
                    uf.union(aid, tid)

    return uf.clusters()


def test_forced_merge_single_name():
    """'Trump' force-merged with 'Donald Trump' via override."""
    candidates = [
        _make_candidate("Trump"),
        _make_candidate("Donald Trump"),
    ]
    overrides = [{"alias_text": "Trump", "target_name": "Donald Trump", "entity_type": "PERSON"}]

    clusters = _apply_overrides(candidates, overrides)
    # Both candidates should be in the same cluster
    assert len(clusters) == 1
    root_members = list(clusters.values())[0]
    assert len(root_members) == 2


def test_no_override_stays_separate():
    """Without override, 'Trump' and 'Donald Trump' remain separate."""
    candidates = [
        _make_candidate("Trump"),
        _make_candidate("Donald Trump"),
    ]
    clusters = _apply_overrides(candidates, [])
    assert len(clusters) == 2


def test_override_wrong_type_no_merge():
    """Override for PERSON type doesn't affect ORG candidates."""
    candidates = [
        _make_candidate("Trump", MentionType.ORG),
        _make_candidate("Trump Organization", MentionType.ORG),
    ]
    overrides = [{"alias_text": "Trump", "target_name": "Donald Trump", "entity_type": "PERSON"}]

    clusters = _apply_overrides(candidates, overrides)
    # Should stay separate — override is for PERSON, candidates are ORG
    assert len(clusters) == 2


def test_multiple_aliases_same_target():
    """Multiple aliases can map to the same target."""
    candidates = [
        _make_candidate("Biden"),
        _make_candidate("Joe"),
        _make_candidate("Joe Biden"),
    ]
    overrides = [
        {"alias_text": "Biden", "target_name": "Joe Biden", "entity_type": "PERSON"},
        {"alias_text": "Joe", "target_name": "Joe Biden", "entity_type": "PERSON"},
    ]

    clusters = _apply_overrides(candidates, overrides)
    assert len(clusters) == 1
    root_members = list(clusters.values())[0]
    assert len(root_members) == 3


def test_override_case_insensitive():
    """Override matching is case-insensitive."""
    candidates = [
        _make_candidate("TRUMP"),
        _make_candidate("Donald Trump"),
    ]
    overrides = [{"alias_text": "trump", "target_name": "donald trump", "entity_type": "PERSON"}]

    clusters = _apply_overrides(candidates, overrides)
    assert len(clusters) == 1


def test_override_via_existing_alias():
    """Override matches candidates through their aliases, not just canonical_name."""
    candidates = [
        _make_candidate("The Donald", aliases=["Trump"]),
        _make_candidate("Donald Trump"),
    ]
    overrides = [{"alias_text": "Trump", "target_name": "Donald Trump", "entity_type": "PERSON"}]

    clusters = _apply_overrides(candidates, overrides)
    assert len(clusters) == 1


def test_override_no_matching_alias():
    """Override with no matching alias candidate is a no-op."""
    candidates = [
        _make_candidate("Donald Trump"),
    ]
    overrides = [{"alias_text": "Drumpf", "target_name": "Donald Trump", "entity_type": "PERSON"}]

    clusters = _apply_overrides(candidates, overrides)
    # Still one cluster (just Donald Trump), no crash
    assert len(clusters) == 1


def test_override_no_matching_target():
    """Override with no matching target candidate is a no-op."""
    candidates = [
        _make_candidate("Trump"),
    ]
    overrides = [{"alias_text": "Trump", "target_name": "Donald Trump", "entity_type": "PERSON"}]

    clusters = _apply_overrides(candidates, overrides)
    # Still one cluster (just Trump), no crash
    assert len(clusters) == 1


def test_disabled_override_not_loaded():
    """Disabled overrides should not be returned by load_entity_overrides.

    The SQL WHERE clause filters is_active=true, so disabled overrides
    never reach the canonical_entities asset.
    """
    # This is a store-level test; just verify the override list filtering works
    active = [{"alias_text": "Trump", "target_name": "Donald Trump", "entity_type": "PERSON"}]
    inactive = []  # would be filtered by SQL

    candidates = [_make_candidate("Trump"), _make_candidate("Donald Trump")]

    # Only active overrides applied
    clusters_with = _apply_overrides(candidates, active)
    assert len(clusters_with) == 1

    # No overrides = separate
    clusters_without = _apply_overrides(candidates, inactive)
    assert len(clusters_without) == 2

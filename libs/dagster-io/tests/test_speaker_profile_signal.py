"""Tests for speaker_profile signal in CrossSourceAligner.

CD-34j.2: Voice identity as an alignment signal — two candidates sharing
a speaker profile_id should merge even without name overlap.
"""

from __future__ import annotations

from dagster_io.concordance import CrossSourceAligner
from dagster_io.models import AlignmentType, EntityCandidate, MentionType


def _cand(name: str, profile_id: str | None = None, **kwargs) -> EntityCandidate:
    return EntityCandidate(
        canonical_name=name,
        candidate_type=kwargs.get("entity_type", MentionType.PERSON),
        aliases=kwargs.get("aliases", []),
        mention_ids=["m1"],
        mention_count=kwargs.get("mention_count", 1),
        source_documents=["doc-1"],
        code_location=kwargs.get("code_location", "test"),
        embedding=kwargs.get("embedding"),
        profile_id=profile_id,
    )


class TestSpeakerProfileSignal:
    def test_same_profile_different_names_same_as(self):
        """Two candidates with different names but same profile_id → sameAs."""
        aligner = CrossSourceAligner()
        sources = {
            "a": [_cand("the interviewer", profile_id="prof-abc", code_location="a")],
            "b": [_cand("Tucker Carlson", profile_id="prof-abc", code_location="b")],
        }
        edges = aligner.align(sources)
        assert len(edges) == 1
        assert edges[0].alignment_type == AlignmentType.SAME_AS
        assert "speaker_profile" in edges[0].evidence
        assert edges[0].score >= 0.92

    def test_same_name_different_profile_still_matches(self):
        """Same name, different profile_id → exact_name match (no regression)."""
        aligner = CrossSourceAligner()
        sources = {
            "a": [_cand("Joe Biden", profile_id="prof-1", code_location="a")],
            "b": [_cand("Joe Biden", profile_id="prof-2", code_location="b")],
        }
        edges = aligner.align(sources)
        assert len(edges) == 1
        assert edges[0].alignment_type == AlignmentType.SAME_AS
        assert "exact_name" in edges[0].evidence

    def test_same_profile_plus_exact_name_high_score(self):
        """Both speaker_profile + exact_name → combined score > 0.95."""
        aligner = CrossSourceAligner()
        sources = {
            "a": [_cand("Joe Biden", profile_id="prof-x", code_location="a")],
            "b": [_cand("Joe Biden", profile_id="prof-x", code_location="b")],
        }
        edges = aligner.align(sources)
        assert len(edges) == 1
        assert edges[0].score >= 0.95
        assert "exact_name" in edges[0].evidence
        assert "speaker_profile" in edges[0].evidence

    def test_no_profile_no_signal(self):
        """Candidates without profile_id → speaker_profile signal absent."""
        aligner = CrossSourceAligner()
        sources = {
            "a": [_cand("Joe Biden", code_location="a")],
            "b": [_cand("Joe Biden", code_location="b")],
        }
        edges = aligner.align(sources)
        assert len(edges) == 1
        assert "speaker_profile" not in edges[0].evidence

    def test_one_null_profile_no_signal(self):
        """One candidate has profile_id, other doesn't → no speaker_profile signal."""
        aligner = CrossSourceAligner()
        sources = {
            "a": [_cand("Biden", profile_id="prof-1", code_location="a")],
            "b": [_cand("Biden", profile_id=None, code_location="b")],
        }
        edges = aligner.align(sources)
        # May or may not produce an edge via other signals, but speaker_profile should be absent
        for edge in edges:
            assert "speaker_profile" not in edge.evidence

    def test_different_profiles_no_speaker_signal(self):
        """Different profile_ids → no speaker_profile signal."""
        aligner = CrossSourceAligner()
        sources = {
            "a": [_cand("the host", profile_id="prof-1", code_location="a")],
            "b": [_cand("the guest", profile_id="prof-2", code_location="b")],
        }
        edges = aligner.align(sources)
        # No name overlap + different profiles → likely no edges at all
        speaker_edges = [e for e in edges if "speaker_profile" in e.evidence]
        assert len(speaker_edges) == 0

    def test_speaker_profile_alone_triggers_same_as(self):
        """speaker_profile alone (no name signals) still triggers sameAs."""
        aligner = CrossSourceAligner()
        sources = {
            "a": [_cand("the president", profile_id="prof-xyz", code_location="a")],
            "b": [_cand("POTUS", profile_id="prof-xyz", code_location="b")],
        }
        edges = aligner.align(sources)
        assert len(edges) == 1
        assert edges[0].alignment_type == AlignmentType.SAME_AS
        # The only signal should be speaker_profile (names don't match)
        assert "speaker_profile" in edges[0].evidence

    def test_intra_source_speaker_profile(self):
        """Speaker profile signal works in intra-source alignment too."""
        aligner = CrossSourceAligner()
        candidates = [
            _cand("speaker one", profile_id="prof-same", code_location="media_ingest"),
            _cand("the narrator", profile_id="prof-same", code_location="media_ingest"),
        ]
        edges = aligner.intra_source_align(candidates, "media_ingest")
        assert len(edges) == 1
        assert "speaker_profile" in edges[0].evidence

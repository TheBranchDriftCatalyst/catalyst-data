"""Bench consumers read the new gold-layer ``catalyst_contracts_core``
shape (AMR-rich Assertion + canonical_type Mention) cleanly.

Wave 1 Step 6 (bead ``llm-g0b``) — confirms ``tests/shared/`` bench
consumers (scoring, ensemble GT generation, report builder) don't drop
AMR fields when reading model fixtures serialized via
``Mention.model_dump`` / ``Assertion.model_dump``.

The synthesized assertions populate every AMR-rich field (``amr_frame``,
``polarity``, ``modality``, ``qualifiers``) so the test fails loudly if
any consumer hand-rolls a stale schema that ignores them.
"""

from __future__ import annotations

import pytest

from catalyst_contracts_core import (
    Assertion,
    ExtractionMethod,
    Mention,
    Provenance,
)
from tests.shared.extraction_scoring import (
    _assertion_object,
    _assertion_subject,
    _mention_chunk_id,
    _mention_type,
    score_mentions,
    score_per_chunk,
    score_propositions,
    score_provenance,
)
from tests.shared.ground_truth import (
    _build_assertions_by_chunk,
    _build_mentions_by_chunk,
    _ner_consensus,
    _spo_consensus,
)
from tests.shared.report import build_report_json


def _prov(chunk_id: str = "doc1:chunk-0", model: str = "gpt-4o") -> Provenance:
    return Provenance(
        source_document_id="doc1",
        chunk_id=chunk_id,
        span_start=0,
        span_end=5,
        extraction_method=ExtractionMethod.LLM,
        extraction_model=model,
        code_location="media_ingest",
    )


def _mention(text: str, kind: str, chunk_id: str = "doc1:chunk-0", model: str = "gpt-4o") -> dict:
    """Build a Mention via the canonical wire-shape model and dump it."""
    return Mention(
        mention_id=f"m-{text}-{kind}-{chunk_id}",
        text=text,
        canonical_type=kind,
        span_start=0,
        span_end=len(text),
        source_models=[model],
        provenance=_prov(chunk_id, model),
    ).model_dump(mode="json")


def _assertion(
    subject: str,
    predicate: str,
    obj: str,
    *,
    amr_frame: str | None = None,
    polarity: bool = True,
    modality: str | None = None,
    qualifiers: dict[str, str] | None = None,
    chunk_id: str = "doc1:chunk-0",
    model: str = "gpt-4o",
) -> dict:
    return Assertion(
        assertion_id=f"a-{subject}-{predicate}-{obj}-{chunk_id}",
        subject_text=subject,
        predicate=predicate,
        object_text=obj,
        amr_frame=amr_frame or predicate,
        amr_variable="i",
        amr_role_mapping={"ARG0": "subject", "ARG1": "object"},
        polarity=polarity,
        modality=modality,
        qualifiers=qualifiers or {},
        provenance=_prov(chunk_id, model),
    ).model_dump(mode="json")


@pytest.fixture
def amr_fixture() -> dict:
    """A two-chunk, two-model extraction fixture matching the Wave-1 wire
    shape (Mention.canonical_type + Assertion.subject_text/object_text +
    AMR-rich attrs)."""
    return {
        "model": "gpt-4o",
        "base_url": "https://test",
        "structured_method": "function_calling",
        "mentions": [
            _mention("Alice", "PERSON", chunk_id="doc1:chunk-0"),
            _mention("Bill 123", "BILL", chunk_id="doc1:chunk-0"),
            _mention("Alice", "PERSON", chunk_id="doc1:chunk-1"),
        ],
        "assertions": [
            _assertion(
                "Alice",
                "introduce-01",
                "Bill 123",
                amr_frame="introduce-01",
                modality="possible",
                qualifiers={"time": "yesterday"},
                chunk_id="doc1:chunk-0",
            ),
            _assertion(
                "Alice",
                "veto-01",
                "Bill 123",
                amr_frame="veto-01",
                polarity=False,  # exercises polarity / negated mirror
                chunk_id="doc1:chunk-1",
            ),
        ],
        "stats": {"chunk_count": 2, "duration_s": 0.5, "mention_count": 3, "assertion_count": 2},
    }


# ─── Accessor helpers ──────────────────────────────────────────────────────


def test_helpers_read_new_shape(amr_fixture: dict) -> None:
    m = amr_fixture["mentions"][0]
    a = amr_fixture["assertions"][0]

    # New shape — must resolve without falling through to legacy keys.
    assert _mention_type(m) == "PERSON"
    assert _mention_chunk_id(m) == "doc1:chunk-0"
    assert _assertion_subject(a) == "Alice"
    assert _assertion_object(a) == "Bill 123"


def test_helpers_still_read_legacy_shape() -> None:
    """Legacy GT files (and `_spo_consensus` output) use ``mention_type`` /
    ``subject`` / ``object`` — the accessor helpers must continue to
    resolve those for back-compat with on-disk GT artifacts."""
    legacy_m = {"text": "Alice", "mention_type": "PERSON", "chunk_id": "c1"}
    legacy_a = {"subject": "Alice", "predicate": "introduce", "object": "Bill"}
    assert _mention_type(legacy_m) == "PERSON"
    assert _mention_chunk_id(legacy_m) == "c1"
    assert _assertion_subject(legacy_a) == "Alice"
    assert _assertion_object(legacy_a) == "Bill"


# ─── Scoring ───────────────────────────────────────────────────────────────


def test_score_mentions_new_vs_legacy_gt(amr_fixture: dict) -> None:
    """Predicted mentions (new shape) scored against a legacy-shape GT
    list — both shapes must normalize to the same comparison keys."""
    predicted = amr_fixture["mentions"]
    gt = [
        {"text": "Alice", "mention_type": "PERSON"},
        {"text": "Bill 123", "mention_type": "BILL"},
    ]
    r = score_mentions(predicted, gt)
    # All three predicted texts collapse to 2 unique normalized texts,
    # both of which are in the GT — strict P/R should be 1.0/1.0.
    assert r["strict_f1"] == pytest.approx(1.0)
    assert r["relaxed_f1"] == pytest.approx(1.0)
    assert r["type_accuracy"] == pytest.approx(1.0)


def test_score_propositions_new_vs_legacy_gt(amr_fixture: dict) -> None:
    predicted = amr_fixture["assertions"]
    gt = [
        {"subject": "Alice", "predicate": "introduce-01", "object": "Bill 123"},
        {"subject": "Alice", "predicate": "veto-01", "object": "Bill 123"},
    ]
    r = score_propositions(predicted, gt)
    assert r["strict_f1"] == pytest.approx(1.0)
    assert r["relaxed_f1"] == pytest.approx(1.0)


def test_score_per_chunk_buckets_by_provenance(amr_fixture: dict) -> None:
    """``score_per_chunk`` must bucket predicted mentions by
    ``provenance.chunk_id`` (the new shape) — failing to do so would mash
    all chunks together and produce a single bucket."""
    gt = [
        {"text": "Alice", "mention_type": "PERSON", "chunk_id": "doc1:chunk-0"},
        {"text": "Alice", "mention_type": "PERSON", "chunk_id": "doc1:chunk-1"},
    ]
    per_chunk = score_per_chunk(amr_fixture["mentions"], gt)
    assert set(per_chunk.keys()) == {"doc1:chunk-0", "doc1:chunk-1"}, (
        "score_per_chunk failed to bucket via provenance.chunk_id"
    )


def test_score_provenance_full(amr_fixture: dict) -> None:
    """Every Mention/Assertion in the new wire shape carries a populated
    Provenance — provenance scoring should report 100% coverage on the
    document_id / chunk_id / extraction_model / code_location axes."""
    r = score_provenance(amr_fixture["mentions"], amr_fixture["assertions"])
    assert r["mention_has_provenance"] == 1.0
    assert r["has_document_id"] == 1.0
    assert r["has_chunk_id"] == 1.0
    assert r["has_extraction_model"] == 1.0
    assert r["has_code_location"] == 1.0
    assert r["assertion_has_provenance"] == 1.0


# ─── Ensemble GT generation ────────────────────────────────────────────────


def test_build_mentions_by_chunk_uses_provenance(amr_fixture: dict) -> None:
    by_chunk = _build_mentions_by_chunk(amr_fixture)
    assert set(by_chunk.keys()) == {"doc1:chunk-0", "doc1:chunk-1"}, (
        "ensemble GT generator failed to bucket mentions via provenance.chunk_id"
    )
    assert len(by_chunk["doc1:chunk-0"]) == 2
    assert len(by_chunk["doc1:chunk-1"]) == 1


def test_build_assertions_by_chunk_uses_provenance(amr_fixture: dict) -> None:
    by_chunk = _build_assertions_by_chunk(amr_fixture)
    assert set(by_chunk.keys()) == {"doc1:chunk-0", "doc1:chunk-1"}


def test_ner_consensus_reads_canonical_type(amr_fixture: dict) -> None:
    """`_ner_consensus` must read Mention.canonical_type (new shape) so
    two encoders that agree on (text, canonical_type) form a consensus."""
    # Two model fixtures, both emit Alice/PERSON for chunk-0.
    model_a = {"gpt-4o": [_mention("Alice", "PERSON")]}
    model_b = {"claude-3-5": [_mention("Alice", "PERSON")]}
    all_mentions = {**model_a, **model_b}
    source_text = "Alice was here."
    accepted = _ner_consensus(all_mentions, source_text, threshold=2)
    assert len(accepted) == 1
    assert accepted[0]["text"] == "Alice"
    assert accepted[0]["mention_type"] == "PERSON"


def test_spo_consensus_reads_subject_text(amr_fixture: dict) -> None:
    """`_spo_consensus` must read Assertion.subject_text/object_text (new
    shape). Two models emitting the same triple should produce one row."""
    a = _assertion("Alice", "introduce-01", "Bill 123", chunk_id="c1", model="gpt-4o")
    b = _assertion("Alice", "introduce-01", "Bill 123", chunk_id="c1", model="claude-3-5")
    accepted = _spo_consensus({"gpt-4o": [a], "claude-3-5": [b]}, threshold=2)
    assert len(accepted) == 1
    # GT row uses the legacy ``subject`` / ``object`` keys (intentional —
    # see _spo_consensus docstring).
    assert accepted[0]["subject"] == "Alice"
    assert accepted[0]["object"] == "Bill 123"
    assert accepted[0]["predicate"] == "introduce-01"


# ─── Report builder ────────────────────────────────────────────────────────


def test_report_builder_surfaces_amr_fields(amr_fixture: dict) -> None:
    """build_report_json must surface AMR-rich fields (amr_frame, polarity,
    modality, qualifiers) on each SPO row so the bench viewer can show
    graph-native semantics, not just the flat SPO triple."""
    results = [{"model": "gpt-4o", "tags": ["llm"], "fixture": amr_fixture}]
    # Pass chunks=[] so report builder doesn't reach for MinIO during unit tests.
    report = build_report_json(results, chunks=[])
    propositions = report["propositions"]
    assert len(propositions) == 2

    by_pred = {p["predicate"]: p for p in propositions}

    # introduce-01: AMR fields populated, polarity=True, modality=possible,
    # qualifiers has time=yesterday.
    assert by_pred["introduce-01"]["amr_frame"] == "introduce-01"
    assert by_pred["introduce-01"]["polarity"] is True
    assert by_pred["introduce-01"]["modality"] == "possible"
    assert by_pred["introduce-01"]["qualifiers"] == {"time": "yesterday"}

    # veto-01: polarity=False (the AMR ":polarity -" branch); modality None.
    assert by_pred["veto-01"]["polarity"] is False
    assert by_pred["veto-01"]["modality"] is None


def test_report_builder_entity_matrix_uses_canonical_type(amr_fixture: dict) -> None:
    """Entity matrix rows must report the Wave-1 canonical_type (not '?')
    for each predicted mention."""
    results = [{"model": "gpt-4o", "tags": ["llm"], "fixture": amr_fixture}]
    report = build_report_json(results, chunks=[])
    by_text = {e["text"]: e for e in report["entities"]}
    assert by_text["Alice"]["consensus_type"] == "PERSON"
    assert by_text["Bill 123"]["consensus_type"] == "BILL"

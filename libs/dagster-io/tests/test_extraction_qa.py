"""QA-3 adversarial test pyramid for Wave 1, Step 3 (bead llm-g0b).

This file is the **non-tautological** strength test for the
catalyst-data side of the AMR-as-spine refactor:

  * ``dagster_io.models.{Mention, Assertion, Provenance}`` are now
    re-exports from ``catalyst_contracts_core``. The re-exports must
    be *identical objects*, not just equal.
  * ``dagster_io.extraction_schemas`` has been deleted — legacy
    extraction-output schemas (MentionExtraction, QualifiedAssertion)
    must no longer be importable.
  * ``dagster_io.extraction.extract_validated()`` is a thin 119-line
    wrapper around ``ExtractionResource.extract_assertions()``. The
    code_location → label_pack mapping must be stable, the wrapper
    must short-circuit on empty input *without importing the
    resource*, and the new keyword-only ``max_concurrency`` surface
    must not silently accept positional misuse.

Dev-3's three thin smoke tests live in ``test_extraction.py``; this
file does NOT duplicate them. Coverage here goes after the things
that *aren't* obvious from the wrapper's docstring — wire-shape
contract leaks, frozen-instance enforcement, enum identity, etc.

Pyramid:

  T1 — Adversarial unit (60%)
  T2 — Property-based (25%) [hypothesis]
  T3 — Differential / cross-layer (10%)
  T4 — Scenario (5%)
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Test-side helper that already knows how to build new-shape Mentions.
from concordance_helpers import make_mention
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from dagster_io.models import (
    AlignmentType,
    Assertion,
    ExtractionMethod,
    Mention,
    MentionType,
    Provenance,
)

# ───────────────────────────────────────────────────────────────────
# T1 — Adversarial unit
# ───────────────────────────────────────────────────────────────────


class TestReExportIdentity:
    """The re-export must be the SAME class object — not a re-defined
    Pydantic model that *happens* to validate-compatibly. Accidental
    shadowing (e.g. ``class Mention(BaseModel): ...`` left in
    ``dagster_io.models``) would silently break ``isinstance`` checks
    across the codebase. Pin identity with ``is``.
    """

    def test_mention_is_contracts_core(self):
        from catalyst_contracts_core import Mention as M_core

        assert Mention is M_core, (
            "dagster_io.models.Mention is NOT the contracts-core Mention "
            "— someone shadowed the re-export."
        )

    def test_assertion_is_contracts_core(self):
        from catalyst_contracts_core import Assertion as A_core

        assert Assertion is A_core

    def test_provenance_is_contracts_core(self):
        from catalyst_contracts_core import Provenance as P_core

        assert Provenance is P_core

    def test_mentiontype_is_contracts_core(self):
        from catalyst_contracts_core import MentionType as MT_core

        assert MentionType is MT_core

    def test_extractionmethod_is_contracts_core(self):
        from catalyst_contracts_core import ExtractionMethod as EM_core

        assert ExtractionMethod is EM_core

    def test_alignmenttype_is_contracts_core(self):
        from catalyst_contracts_core import AlignmentType as AT_core

        assert AlignmentType is AT_core


class TestNewMentionEnumValues:
    """``ExtractionMethod`` gained AMR_PROJECTION + NER_ENSEMBLE in
    Wave 1. Pin the enum values so a string-typo-rename elsewhere is
    caught (e.g. ``"amr-projection"`` vs ``"amr_projection"``).
    """

    def test_amr_projection_value(self):
        assert ExtractionMethod.AMR_PROJECTION.value == "amr_projection"

    def test_ner_ensemble_value(self):
        assert ExtractionMethod.NER_ENSEMBLE.value == "ner_ensemble"


class TestOldShapeRejection:
    """Wave 1 + QA-1 set ``extra="forbid"`` on Mention. The legacy
    field names (``document_id``, ``chunk_id``, ``mention_type``) must
    be rejected with a clear error pointing at the offending field.
    """

    def test_old_mention_shape_rejected(self):
        # The legacy shape had ``document_id`` / ``chunk_id`` /
        # ``mention_type`` as top-level fields on Mention. They've moved
        # (document_id + chunk_id → Provenance; mention_type → canonical_type).
        with pytest.raises(ValidationError) as exc_info:
            Mention(
                document_id="d",
                chunk_id="c",
                mention_type="PERSON",
                text="x",
                span_start=0,
                span_end=1,
            )
        err_str = str(exc_info.value)
        # Could be flagged as 'missing' (required new fields) or 'extra_forbidden'.
        # Either way, the error string MUST surface the legacy field names so a
        # human reading the traceback can see what to fix.
        assert (
            "document_id" in err_str
            or "chunk_id" in err_str
            or "mention_type" in err_str
            or "extra" in err_str.lower()
        ), f"Error didn't surface legacy field names: {err_str}"

    def test_old_assertion_shape_rejected(self):
        # The legacy Assertion had ``subject`` (not ``subject_text``) and
        # didn't require ``predicate`` to be non-empty.
        with pytest.raises(ValidationError):
            Assertion(
                subject="X",  # legacy field name
                predicate="cites",
                object_text="Y",
                provenance=Provenance(source_document_id="d", chunk_id="c"),
            )


class TestFrozenContract:
    """``frozen=True`` on Mention + Assertion is wire-shape protection
    — downstream nodes mutating an emit-and-forget object is a
    contract bug that QA-1 hardened against. Verify directly.
    """

    def _mention(self) -> Mention:
        prov = Provenance(source_document_id="d", chunk_id="c")
        return Mention(
            mention_id="m1",
            text="x",
            canonical_type="PERSON",
            span_start=0,
            span_end=1,
            provenance=prov,
        )

    def test_mention_frozen_text(self):
        m = self._mention()
        with pytest.raises(ValidationError) as exc_info:
            m.text = "changed"
        assert "frozen" in str(exc_info.value).lower()

    def test_mention_frozen_canonical_type(self):
        m = self._mention()
        with pytest.raises(ValidationError):
            m.canonical_type = "ORG"

    def test_assertion_frozen(self):
        prov = Provenance(source_document_id="d", chunk_id="c")
        a = Assertion(
            assertion_id="a1",
            subject_text="X",
            predicate="cites",
            object_text="Y",
            provenance=prov,
        )
        with pytest.raises(ValidationError):
            a.predicate = "amends"


class TestExtractionMethodEnumOnProvenance:
    """``Provenance.extraction_method`` is an enum, not a free-form
    string. Plain strings that match an enum value are accepted (Pydantic
    coerces), but a *non-matching* string must be rejected.
    """

    def test_string_enum_value_accepted(self):
        prov = Provenance(
            source_document_id="d",
            chunk_id="c",
            extraction_method="amr_projection",  # matches AMR_PROJECTION
        )
        assert prov.extraction_method is ExtractionMethod.AMR_PROJECTION

    def test_non_enum_string_rejected(self):
        with pytest.raises(ValidationError):
            Provenance(
                source_document_id="d",
                chunk_id="c",
                extraction_method="not-a-real-method",
            )


class TestNegatedPolaritySync:
    """``Assertion.negated`` must auto-sync to ``not polarity`` via the
    model_validator. If polarity=False is passed but negated=False,
    the validator must flip negated to True.
    """

    def _prov(self) -> Provenance:
        return Provenance(source_document_id="d", chunk_id="c")

    def test_polarity_false_forces_negated_true(self):
        a = Assertion(
            assertion_id="a1",
            subject_text="X",
            predicate="cites",
            object_text="Y",
            polarity=False,
            negated=False,  # caller's mistake — should be overridden
            provenance=self._prov(),
        )
        assert a.negated is True
        assert a.polarity is False

    def test_polarity_true_forces_negated_false(self):
        a = Assertion(
            assertion_id="a1",
            subject_text="X",
            predicate="cites",
            object_text="Y",
            polarity=True,
            negated=True,  # caller's mistake — should be overridden
            provenance=self._prov(),
        )
        assert a.negated is False
        assert a.polarity is True


class TestExtractValidatedEmptyShortCircuit:
    """The empty-chunks short-circuit MUST not import
    ``catalyst_exgraph.resource`` (which can be heavy / not yet
    importable during test collection). Stub the resource module to
    *raise on import* and verify the empty path still returns cleanly.
    """

    def test_empty_chunks_does_not_import_resource(self, monkeypatch):
        from dagster_io.extraction import extract_validated

        class _RaisingModule(types.ModuleType):
            def __getattr__(self, name):
                # Anything that touches this stub raises.
                raise ImportError(f"resource module deliberately broken in test: {name}")

        # Inject a poisoned ``catalyst_exgraph.resource`` BEFORE the wrapper
        # runs. If the wrapper tries to ``from catalyst_exgraph.resource
        # import ExtractionResource`` on the empty path, we'll see ImportError.
        monkeypatch.setitem(
            sys.modules,
            "catalyst_exgraph.resource",
            _RaisingModule("catalyst_exgraph.resource"),
        )

        result = extract_validated([], "media_ingest")
        assert result.mentions == []
        assert result.assertions == []


class TestExtractValidatedLabelPackMapping:
    """The code_location → label_pack lookup is the wrapper's only
    real logic. Parametrize every documented mapping + the unknown
    fallback, asserting the constructed ExtractionResource receives
    the right ``label_pack_id``.
    """

    @pytest.fixture
    def stub_resource_module(self):
        """Inject a stub ``catalyst_exgraph.resource`` that captures the
        constructor kwargs passed to ``ExtractionResource``.
        """
        captured: dict = {}

        instance = MagicMock()
        instance.extract_assertions.return_value = MagicMock(mentions=[], assertions=[])

        def _capture_kwargs(**kwargs):
            captured.update(kwargs)
            return instance

        stub = types.ModuleType("catalyst_exgraph.resource")
        stub.ExtractionResource = MagicMock(side_effect=_capture_kwargs)

        with patch.dict(sys.modules, {"catalyst_exgraph.resource": stub}):
            yield captured

    @pytest.mark.parametrize(
        ("code_location", "expected_pack"),
        [
            ("congress", "congress"),
            ("congress_data", "congress"),
            ("media", "media"),
            ("media_ingest", "media"),
            ("open_leaks", "generic"),
            ("", "generic"),
            ("unknown_domain", "generic"),
            ("CONGRESS", "generic"),  # case-sensitive — caps don't match
        ],
    )
    def test_label_pack_resolution(self, stub_resource_module, code_location, expected_pack):
        from dagster_io.extraction import extract_validated

        extract_validated([MagicMock()], code_location)

        assert stub_resource_module["label_pack_id"] == expected_pack, (
            f"code_location={code_location!r} should map to "
            f"label_pack_id={expected_pack!r}, got "
            f"{stub_resource_module['label_pack_id']!r}"
        )


class TestExtractValidatedKwargSurface:
    """``max_concurrency`` is keyword-only per the new signature. Make
    sure positional misuse fails with a clear TypeError (catches a
    refactor that accidentally drops the ``*``).
    """

    def test_positional_concurrency_rejected(self, monkeypatch):
        # Reach into sys.modules via monkeypatch's setitem
        instance = MagicMock()
        instance.extract_assertions.return_value = MagicMock(mentions=[], assertions=[])
        stub = types.ModuleType("catalyst_exgraph.resource")
        stub.ExtractionResource = MagicMock(return_value=instance)
        monkeypatch.setitem(sys.modules, "catalyst_exgraph.resource", stub)

        from dagster_io.extraction import extract_validated

        with pytest.raises(TypeError):
            # ``max_concurrency`` is keyword-only — this should be a TypeError
            # at call site, not silently accepted as a positional 3rd arg.
            extract_validated([MagicMock()], "media_ingest", 3)  # type: ignore[misc]

    def test_keyword_concurrency_forwarded(self, monkeypatch):
        instance = MagicMock()
        instance.extract_assertions.return_value = MagicMock(mentions=[], assertions=[])
        mock_cls = MagicMock(return_value=instance)
        stub = types.ModuleType("catalyst_exgraph.resource")
        stub.ExtractionResource = mock_cls
        monkeypatch.setitem(sys.modules, "catalyst_exgraph.resource", stub)

        from dagster_io.extraction import extract_validated

        extract_validated([MagicMock()], "media_ingest", max_concurrency=7)

        assert mock_cls.call_args.kwargs["max_concurrency"] == 7


class TestExtractValidatedErrorPropagation:
    """If ``ExtractionResource.extract_assertions`` raises, the wrapper
    propagates — it does NOT swallow exceptions and silently return
    an empty ExtractionResult. (A swallowed error would silently zero
    out an entire run's data with no signal to the asset.)
    """

    def test_resource_exception_propagates(self, monkeypatch):
        from dagster_io.extraction import extract_validated

        instance = MagicMock()
        instance.extract_assertions.side_effect = RuntimeError("boom: resource exploded")
        mock_cls = MagicMock(return_value=instance)
        stub = types.ModuleType("catalyst_exgraph.resource")
        stub.ExtractionResource = mock_cls
        monkeypatch.setitem(sys.modules, "catalyst_exgraph.resource", stub)

        with pytest.raises(RuntimeError, match="boom: resource exploded"):
            extract_validated([MagicMock()], "media_ingest")


class TestExtractionSchemasRetired:
    """The legacy extraction-output schemas module was deleted in Step
    3. Attempts to import it must fail with a clear error so callers
    know to migrate (not silently get a stale local copy from
    ``__pycache__``).
    """

    def test_extraction_schemas_module_gone(self):
        with pytest.raises(ModuleNotFoundError):
            import dagster_io.extraction_schemas  # noqa: F401

    def test_legacy_symbol_not_in_dagster_io(self):
        # ``from dagster_io import MentionExtraction`` was the legacy import
        # pattern in callers. It must no longer resolve.
        with pytest.raises(ImportError):
            from dagster_io import MentionExtraction  # noqa: F401

    def test_legacy_qualified_assertion_gone(self):
        with pytest.raises(ImportError):
            from dagster_io import QualifiedAssertion  # noqa: F401


# ───────────────────────────────────────────────────────────────────
# T2 — Property-based (hypothesis)
# ───────────────────────────────────────────────────────────────────


def _make_text_chunk(document_id: str, chunk_id: str, text: str):
    """Build a duck-typed TextChunk shim — the wrapper only reads
    ``.text`` / ``.document_id`` / ``.chunk_id`` so any object with
    those attributes is acceptable.
    """
    chunk = MagicMock()
    chunk.text = text
    chunk.document_id = document_id
    chunk.chunk_id = chunk_id
    chunk.metadata = {}
    return chunk


class TestExtractValidatedProperties:
    """Property-based: across a wide range of random inputs the
    wrapper must (a) never raise a bare Exception (it either
    returns an ExtractionResult or propagates a known
    ImportError/TypeError), and (b) map code_location → label_pack
    deterministically into the known set.
    """

    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    @given(
        chunks_data=st.lists(
            st.tuples(
                st.text(min_size=1, max_size=8),
                st.text(min_size=1, max_size=8),
                st.text(min_size=0, max_size=100),
            ),
            min_size=0,
            max_size=5,
        ),
        code_location=st.text(min_size=0, max_size=20),
    )
    def test_wrapper_returns_extraction_result(self, chunks_data, code_location, monkeypatch):
        from catalyst_exgraph.protocol import ExtractionResult

        from dagster_io.extraction import extract_validated

        # Stub the resource so we don't run AMR on every hypothesis example.
        instance = MagicMock()
        instance.extract_assertions.return_value = ExtractionResult()
        stub = types.ModuleType("catalyst_exgraph.resource")
        stub.ExtractionResource = MagicMock(return_value=instance)
        monkeypatch.setitem(sys.modules, "catalyst_exgraph.resource", stub)

        chunks = [_make_text_chunk(d, c, t) for (d, c, t) in chunks_data]

        result = extract_validated(chunks, code_location)
        assert isinstance(result, ExtractionResult)

    @settings(max_examples=100, deadline=None)
    @given(code_location=st.text())
    def test_resolve_label_pack_total(self, code_location):
        """``_resolve_label_pack`` is total: every string maps to one of
        the documented pack ids — never None, never raises.
        """
        from dagster_io.extraction import _resolve_label_pack

        result = _resolve_label_pack(code_location)
        assert result in {"congress", "media", "generic"}, (
            f"label_pack for {code_location!r} = {result!r} is not in "
            "the documented {'congress', 'media', 'generic'} set"
        )


class TestReExportRoundTrip:
    """The re-exported binding must round-trip through JSON identically
    to the contracts-core binding. Catches accidental shadowing where
    the dagster_io re-export is a slightly-different copy.
    """

    @settings(max_examples=20, deadline=None)
    @given(
        text=st.text(min_size=1, max_size=40),
        canonical_type=st.sampled_from(["PERSON", "ORG", "GPE", "BILL"]),
        span_start=st.integers(min_value=0, max_value=1000),
        span_len=st.integers(min_value=0, max_value=100),
    )
    def test_mention_json_round_trip(self, text, canonical_type, span_start, span_len):
        prov = Provenance(source_document_id="d", chunk_id="c")
        original = Mention(
            mention_id="m1",
            text=text,
            canonical_type=canonical_type,
            span_start=span_start,
            span_end=span_start + span_len,
            provenance=prov,
        )
        # Round trip through the re-exported binding
        raw = original.model_dump_json()
        restored = Mention.model_validate_json(raw)
        assert restored == original

        # And cross-binding parses identically
        from catalyst_contracts_core import Mention as M_core

        restored_core = M_core.model_validate_json(raw)
        assert restored_core == original


# ───────────────────────────────────────────────────────────────────
# T3 — Differential / cross-layer
# ───────────────────────────────────────────────────────────────────


class TestCrossImportPathEquality:
    """Same Mention constructed via either import path must be both
    equal AND constructed by the same class.
    """

    def test_mention_cross_path_equal(self):
        from catalyst_contracts_core import Mention as M_core

        # Use a SHARED Provenance instance — Provenance.timestamp is a
        # ``default_factory=datetime.now()`` so two independently-constructed
        # Provenance objects compare unequal even with identical kwargs.
        # That's a Provenance-side property, not a cross-binding issue.
        shared_prov = Provenance(source_document_id="d", chunk_id="c")

        kw = dict(
            mention_id="m1",
            text="Joe Biden",
            canonical_type="PERSON",
            span_start=0,
            span_end=9,
            provenance=shared_prov,
        )

        m1 = Mention(**kw)
        m2 = M_core(**kw)
        assert m1 == m2
        # Identity of class (already pinned in T1 but verify here at instance level)
        assert type(m1) is type(m2)


class TestConcordanceRoundTripsProvenance:
    """``concordance.py`` was ported to the new Mention shape. The
    critical invariant: cluster → EntityCandidate must preserve
    Provenance.source_document_id for every member mention. If the
    port silently dropped Provenance, downstream KG ingestion would
    show orphan mentions with no source.
    """

    def test_resolve_preserves_source_document_ids(self):
        from dagster_io.concordance import ConcordanceEngine

        mentions = [
            make_mention("Donald Trump", MentionType.PERSON, "doc-A", "chunk-1"),
            make_mention("Trump", MentionType.PERSON, "doc-A", "chunk-2"),
            make_mention("Donald Trump", MentionType.PERSON, "doc-B", "chunk-1"),
            make_mention("Joe Biden", MentionType.PERSON, "doc-A", "chunk-3"),
        ]
        # Sanity check the fixtures: every input mention has a Provenance
        # with the right source_document_id.
        for m in mentions:
            assert isinstance(m, Mention)
            assert isinstance(m.provenance, Provenance)
            assert m.provenance.source_document_id  # non-empty

        engine = ConcordanceEngine()
        candidates = engine.resolve(mentions, code_location="test_loc")

        # The Trump cluster (3 mentions across doc-A and doc-B) must surface
        # BOTH source docs. If concordance dropped Provenance access, this
        # list would be empty or wrong.
        trump_cand = next(
            (c for c in candidates if "trump" in c.canonical_name.lower()),
            None,
        )
        assert trump_cand is not None
        assert set(trump_cand.source_documents) == {"doc-A", "doc-B"}, (
            f"Expected both doc-A and doc-B in source_documents, "
            f"got {trump_cand.source_documents!r}"
        )

    def test_resolve_uses_canonical_type_not_mention_type(self):
        """The port replaced ``mention.mention_type`` (legacy) with
        ``mention.canonical_type`` (new). Verify clustering still
        respects type by feeding two mentions with the same text but
        different types — they must NOT cluster together.
        """
        from dagster_io.concordance import ConcordanceEngine

        prov = Provenance(source_document_id="d", chunk_id="c")
        # Same text "Washington" but PERSON vs GPE — must not cluster.
        m_person = Mention(
            mention_id="m-person",
            text="Washington",
            canonical_type="PERSON",
            span_start=0,
            span_end=10,
            provenance=prov,
        )
        m_gpe = Mention(
            mention_id="m-gpe",
            text="Washington",
            canonical_type="GPE",
            span_start=10,
            span_end=20,
            provenance=prov,
        )
        engine = ConcordanceEngine()
        candidates = engine.resolve([m_person, m_gpe], code_location="t")
        # Two distinct types → two distinct candidates.
        assert len(candidates) == 2, (
            f"Same-text-different-type mentions should not cluster; "
            f"got {len(candidates)} candidates"
        )


class TestCodeLocationStampSurvivesWrapper:
    """When extract_validated is called with code_location='congress',
    the wrapper must forward that string to the resource so that
    every Provenance ends up stamped. Stub the resource to verify
    the chunks AND the code_location arg both arrive.
    """

    def test_code_location_forwarded(self, monkeypatch):
        from catalyst_exgraph.protocol import ExtractionResult

        from dagster_io.extraction import extract_validated

        # Build a pre-baked assertion the stub will hand back so we can
        # check the wrapper does NOT mutate / drop it on the way out.
        prov = Provenance(
            source_document_id="d",
            chunk_id="c",
            extraction_method=ExtractionMethod.AMR_PROJECTION,
            code_location="congress",
        )
        baked_assertion = Assertion(
            assertion_id="a1",
            subject_text="X",
            predicate="cites",
            object_text="Y",
            provenance=prov,
        )

        instance = MagicMock()
        instance.extract_assertions.return_value = ExtractionResult(
            mentions=[], assertions=[baked_assertion]
        )
        stub = types.ModuleType("catalyst_exgraph.resource")
        stub.ExtractionResource = MagicMock(return_value=instance)
        monkeypatch.setitem(sys.modules, "catalyst_exgraph.resource", stub)

        chunks = [_make_text_chunk("doc-1", "chunk-1", "hello")]
        result = extract_validated(chunks, code_location="congress")

        # The wrapper forwards code_location verbatim.
        kwargs = instance.extract_assertions.call_args.kwargs
        assert kwargs["code_location"] == "congress"

        # And the assertion comes back unchanged with code_location stamped.
        assert len(result.assertions) == 1
        assert result.assertions[0].provenance.code_location == "congress"
        assert result.assertions[0].provenance.extraction_method is ExtractionMethod.AMR_PROJECTION


# ───────────────────────────────────────────────────────────────────
# T4 — Scenario
# ───────────────────────────────────────────────────────────────────


class TestEndToEndShape:
    """End-to-end smoke (resource stubbed) that the wrapper produces
    an ExtractionResult whose shape downstream consumers (asset_factory,
    bench harness, gold-layer assets) expect: mentions + assertions
    lists, each carrying a Provenance with the correct code_location +
    extraction_method.
    """

    def test_full_extraction_result_shape(self, monkeypatch):
        from catalyst_exgraph.protocol import ExtractionResult

        from dagster_io.extraction import extract_validated

        # Build canned outputs in the NEW shape — the wrapper just passes
        # them through, so this is what callers will see.
        prov_m = Provenance(
            source_document_id="doc-1",
            chunk_id="chunk-1",
            span_start=0,
            span_end=11,
            extraction_method=ExtractionMethod.NER_ENSEMBLE,
            extraction_model="ner_ensemble+gliner",
            code_location="congress_data",
        )
        mention = Mention(
            mention_id="m-trump",
            text="Donald Trump",
            canonical_type="PERSON",
            span_start=0,
            span_end=12,
            provenance=prov_m,
        )
        prov_a = Provenance(
            source_document_id="doc-1",
            chunk_id="chunk-1",
            extraction_method=ExtractionMethod.AMR_PROJECTION,
            code_location="congress_data",
        )
        assertion = Assertion(
            assertion_id="a-cites",
            subject_text="Donald Trump",
            predicate="introduces",
            object_text="HR 1234",
            amr_frame="introduce-01",
            provenance=prov_a,
        )

        baked = ExtractionResult(
            mentions=[mention],
            assertions=[assertion],
            stats={
                "chunk_count": 1,
                "mention_count": 1,
                "assertion_count": 1,
                "pipeline": "amr",
            },
        )

        instance = MagicMock()
        instance.extract_assertions.return_value = baked
        stub = types.ModuleType("catalyst_exgraph.resource")
        stub.ExtractionResource = MagicMock(return_value=instance)
        monkeypatch.setitem(sys.modules, "catalyst_exgraph.resource", stub)

        chunks = [_make_text_chunk("doc-1", "chunk-1", "Donald Trump introduces HR 1234.")]
        result = extract_validated(chunks, code_location="congress_data")

        # Downstream-consumer contract:
        #   result.mentions : list[Mention]
        #   result.assertions : list[Assertion]
        #   each has a .provenance with code_location stamped
        assert isinstance(result, ExtractionResult)
        assert len(result.mentions) == 1
        assert len(result.assertions) == 1
        assert isinstance(result.mentions[0], Mention)
        assert isinstance(result.assertions[0], Assertion)
        assert result.mentions[0].provenance.code_location == "congress_data"
        assert result.assertions[0].provenance.code_location == "congress_data"
        # The AMR projection enum survives end-to-end.
        assert result.assertions[0].provenance.extraction_method is ExtractionMethod.AMR_PROJECTION
        # Stats dict surfaces pipeline marker so the asset factory can log it.
        assert result.stats["pipeline"] == "amr"

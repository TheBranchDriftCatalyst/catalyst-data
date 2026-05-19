"""Tests for structured-entity → Assertion converters (bead llm-mln).

4-tier strength pyramid:
  T1 unit — per-converter shape contracts (8 tests)
  T2 property — invariants across many inputs (3 tests)
  T3 differential — STRUCTURED vs AMR_PROJECTION provenance distinction (2 tests)
  T4 scenario — end-to-end point-in-time validity query (2 tests)
"""

from __future__ import annotations

from datetime import date

import pytest
from congress_data.assets.structured_assertions import (
    cosponsor_to_assertion,
    public_law_to_assertion,
    term_to_membership_assertion,
    term_to_party_assertion,
)
from congress_data.entities import Cosponsor, PublicLaw, Term

from catalyst_contracts_core import Assertion, ExtractionMethod

# ===========================================================================
# T1 — Unit contracts
# ===========================================================================


def test_cosponsor_to_assertion_stamps_validity_window():
    """Both sponsorship_date and withdrawn_date land on the assertion."""
    cosponsor = Cosponsor(
        bioguide_id="J000001",
        bill_id="hr1-119",
        name="Rep. Jones",
        sponsorship_date=date(2025, 1, 15),
        withdrawn_date=date(2025, 4, 1),
    )

    a = cosponsor_to_assertion(cosponsor)

    assert a.predicate == "co_sponsors"
    assert a.subject_text == "Rep. Jones"
    assert a.object_text == "hr1-119"
    assert a.t_valid_from == "2025-01-15"
    assert a.t_valid_until == "2025-04-01"
    assert a.is_atemporal is False


def test_cosponsor_open_ended_when_not_withdrawn():
    """No withdrawn_date → t_valid_until=None (still in effect)."""
    cosponsor = Cosponsor(
        bioguide_id="S000001",
        bill_id="hr1-119",
        name="Sen. Smith",
        sponsorship_date=date(2025, 2, 1),
    )

    a = cosponsor_to_assertion(cosponsor)

    assert a.t_valid_from == "2025-02-01"
    assert a.t_valid_until is None
    assert a.polarity is True  # still active
    assert a.negated is False


def test_cosponsor_withdrawn_flips_polarity():
    """A withdrawn cosponsor → polarity=False / negated=True for downstream
    'this person USED to cosponsor' querying."""
    cosponsor = Cosponsor(
        bioguide_id="J000001",
        bill_id="hr1-119",
        sponsorship_date=date(2025, 1, 1),
        withdrawn_date=date(2025, 6, 1),
    )

    a = cosponsor_to_assertion(cosponsor)

    assert a.polarity is False
    assert a.negated is True


def test_cosponsor_qualifiers_carry_party_state():
    """Party/state/is_original land as qualifiers, not as separate assertions."""
    cosponsor = Cosponsor(
        bioguide_id="J000001",
        bill_id="hr1-119",
        party="D",
        state="CA",
        is_original=True,
    )

    a = cosponsor_to_assertion(cosponsor)

    assert a.qualifiers.get("party") == "D"
    assert a.qualifiers.get("state") == "CA"
    assert a.qualifiers.get("is_original") == "true"


def test_term_membership_year_to_iso_window():
    """start_year/end_year stamp as YYYY-01-01 / YYYY-12-31 (year-resolution)."""
    term = Term(
        id="J000001:118:House",
        bioguide_id="J000001",
        congress=118,
        chamber="House",
        start_year=2023,
        end_year=2025,
        party="D",
        state="CA",
    )

    a = term_to_membership_assertion(term)

    assert a.predicate == "member_of"
    assert a.subject_text == "J000001"
    assert a.object_text == "House (Congress 118)"
    assert a.t_valid_from == "2023-01-01"
    assert a.t_valid_until == "2025-12-31"


def test_term_party_assertion_emitted_only_when_party_present():
    """Independents / missing-party records → None, not a degenerate row."""
    term_no_party = Term(
        id="X000001:118:House",
        bioguide_id="X000001",
        congress=118,
        chamber="House",
        start_year=2023,
        end_year=2025,
    )
    term_with_party = Term(
        id="J000001:118:House",
        bioguide_id="J000001",
        congress=118,
        chamber="House",
        start_year=2023,
        end_year=2025,
        party="R",
    )

    assert term_to_party_assertion(term_no_party) is None
    a = term_to_party_assertion(term_with_party)
    assert a is not None
    assert a.predicate == "member_of_party"
    assert a.object_text == "R"


def test_public_law_signed_date_opens_window_no_close():
    """A signed PublicLaw → t_valid_from = signed_date, t_valid_until = None
    (laws don't auto-expire; repeals would be separate assertions)."""
    law = PublicLaw(
        id="PL119-1",
        law_type="public",
        law_number=1,
        congress=119,
        bill_id="hr1-119",
        signed_date=date(2025, 3, 17),
    )

    a = public_law_to_assertion(law)

    assert a.predicate == "became_law"
    assert a.subject_text == "hr1-119"
    assert a.object_text == "PL119-1"
    assert a.t_valid_from == "2025-03-17"
    assert a.t_valid_until is None


def test_all_structured_assertions_carry_structured_provenance():
    """STRUCTURED extraction_method is the wire-level marker that downstream
    consumers use to distinguish API-derived facts from AMR-projected ones."""
    cosponsor = Cosponsor(bioguide_id="J", bill_id="b", sponsorship_date=date(2025, 1, 1))
    term = Term(id="J:118:H", bioguide_id="J", congress=118, chamber="House", start_year=2023, party="D")
    law = PublicLaw(id="PL119-1", congress=119, bill_id="b", signed_date=date(2025, 3, 1))

    for a in (
        cosponsor_to_assertion(cosponsor),
        term_to_membership_assertion(term),
        term_to_party_assertion(term),
        public_law_to_assertion(law),
    ):
        assert a is not None
        assert a.provenance.extraction_method == ExtractionMethod.STRUCTURED
        assert a.provenance.code_location == "congress_data"


# ===========================================================================
# T2 — Property invariants
# ===========================================================================


@pytest.mark.parametrize(
    "sponsorship,withdrawn",
    [
        (date(2025, 1, 1), None),
        (date(2024, 6, 15), date(2025, 1, 1)),
        (None, None),  # missing date — should not crash
        (None, date(2025, 1, 1)),  # weird but possible
    ],
)
def test_property_cosponsor_validity_window_never_inverts(sponsorship, withdrawn):
    """t_valid_until, when both bounds present, must be >= t_valid_from."""
    cosponsor = Cosponsor(
        bioguide_id="J",
        bill_id="b",
        sponsorship_date=sponsorship,
        withdrawn_date=withdrawn,
    )

    a = cosponsor_to_assertion(cosponsor)

    if a.t_valid_from and a.t_valid_until:
        assert a.t_valid_from <= a.t_valid_until


def test_property_assertion_id_stable_across_calls():
    """Same input → same assertion_id (deterministic hash)."""
    cosponsor = Cosponsor(
        bioguide_id="J000001",
        bill_id="hr1-119",
        sponsorship_date=date(2025, 1, 1),
    )

    a1 = cosponsor_to_assertion(cosponsor)
    a2 = cosponsor_to_assertion(cosponsor)

    assert a1.assertion_id == a2.assertion_id


def test_property_assertion_id_differs_per_predicate_for_same_term():
    """Term → membership + party assertions must NOT collide on assertion_id
    even though they share the same source term row."""
    term = Term(
        id="J:118:H",
        bioguide_id="J",
        congress=118,
        chamber="House",
        start_year=2023,
        party="D",
    )

    membership = term_to_membership_assertion(term)
    party = term_to_party_assertion(term)

    assert membership.assertion_id != party.assertion_id


# ===========================================================================
# T3 — Differential: STRUCTURED vs AMR_PROJECTION
# ===========================================================================


def test_differential_structured_is_not_amr_projection():
    """Sanity: STRUCTURED and AMR_PROJECTION are distinct enum values, and
    structured converters emit STRUCTURED."""
    cosponsor = Cosponsor(bioguide_id="J", bill_id="b", sponsorship_date=date(2025, 1, 1))

    a = cosponsor_to_assertion(cosponsor)

    assert a.provenance.extraction_method == ExtractionMethod.STRUCTURED
    assert a.provenance.extraction_method != ExtractionMethod.AMR_PROJECTION


def test_differential_structured_has_no_amr_frame():
    """STRUCTURED assertions don't go through PropBank — amr_frame stays
    None. (AmrToAssertionNode always populates amr_frame; this is a
    contract divergence test, not an oversight.)"""
    cosponsor = Cosponsor(bioguide_id="J", bill_id="b", sponsorship_date=date(2025, 1, 1))

    a = cosponsor_to_assertion(cosponsor)

    assert a.amr_frame is None
    assert a.amr_variable is None
    assert a.amr_role_mapping == {}


# ===========================================================================
# T4 — Scenario: point-in-time validity query
# ===========================================================================


def test_scenario_point_in_time_cosponsorship_active_window():
    """A cosponsor active 2025-01-15 → 2025-04-01 should be 'valid' for a
    query at 2025-02-15 and 'invalid' for a query at 2025-05-01.

    This is the downstream consumer pattern that justifies stamping these
    fields at projection time vs. recomputing at query time.
    """
    cosponsor = Cosponsor(
        bioguide_id="J",
        bill_id="b",
        sponsorship_date=date(2025, 1, 15),
        withdrawn_date=date(2025, 4, 1),
    )
    a: Assertion = cosponsor_to_assertion(cosponsor)

    def valid_at(t: str) -> bool:
        return (
            (a.t_valid_from is None or a.t_valid_from <= t)
            and (a.t_valid_until is None or t <= a.t_valid_until)
        )

    assert valid_at("2025-02-15") is True
    assert valid_at("2025-05-01") is False
    assert valid_at("2025-01-01") is False  # before sponsorship


def test_scenario_open_ended_term_still_valid_today():
    """A term with start_year=2023 and no end_year should be 'valid' for any
    query date >= 2023-01-01. Common case: sitting member."""
    term = Term(
        id="J:118:H",
        bioguide_id="J",
        congress=118,
        chamber="House",
        start_year=2023,
    )
    a = term_to_membership_assertion(term)

    def valid_at(t: str) -> bool:
        return (
            (a.t_valid_from is None or a.t_valid_from <= t)
            and (a.t_valid_until is None or t <= a.t_valid_until)
        )

    assert valid_at("2024-05-15") is True
    assert valid_at("2030-01-01") is True  # open-ended → still valid
    assert valid_at("2022-01-01") is False  # before term start

"""Structured-entity → Assertion converters (bead llm-mln).

The AMR-as-spine extraction path projects free-text bill bodies into
``contracts_core.Assertion`` records via PropBank frames. But Congress.gov
also publishes structured records whose temporal validity is encoded
directly in the API response — Cosponsor rows carry ``sponsorship_date``,
Term rows carry ``start_year`` / ``end_year``, PublicLaw carries
``signed_date``. These are first-class, ground-truth temporal facts.

This module turns those structured records into ``Assertion`` rows with
``t_valid_from`` / ``t_valid_until`` stamped from the source dates and
``provenance.extraction_method = STRUCTURED`` to distinguish them from
AMR-projected assertions downstream.

Roll-forward note: STRUCTURED assertions are not a fallback — they are the
preferred shape when the field exists. AMR projection still runs on the
narrative bill text; the two streams merge in the gold layer and downstream
graph writers dedup on assertion_id.
"""

import hashlib
from datetime import date

from dagster import (
    AssetExecutionContext,
    Output,
    asset,
)

from catalyst_contracts_core import Assertion, ExtractionMethod, Provenance
from congress_data.entities import BillDetail, Cosponsor, PublicLaw, Term
from congress_data.partitions import bill_partitions

_CODE_LOCATION = "congress_data"
_EXTRACTION_MODEL = "congress_structured_v1"


def _assertion_id(subject: str, predicate: str, obj: str, source: str) -> str:
    """Stable 16-hex SPO+source hash. Same recipe as AmrToAssertionNode for
    downstream dedup compatibility."""
    return hashlib.md5(f"{subject}|{predicate}|{obj}|{source}".encode()).hexdigest()[:16]


def _structured_provenance(
    source_document_id: str,
    chunk_id: str = "",
) -> Provenance:
    """Provenance stamp for structured-extraction assertions.

    ``chunk_id=""`` is intentional: structured facts come from API row data,
    not from a text chunk. The empty string preserves the field's
    not-None contract while signalling 'no text anchor'.
    """
    return Provenance(
        source_document_id=source_document_id,
        chunk_id=chunk_id,
        extraction_method=ExtractionMethod.STRUCTURED,
        extraction_model=_EXTRACTION_MODEL,
        confidence=1.0,
        code_location=_CODE_LOCATION,
    )


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


# ─── Cosponsor → co_sponsors assertion ─────────────────────────────────────


def cosponsor_to_assertion(cosponsor: Cosponsor) -> Assertion:
    """A Cosponsor row → one ``co_sponsors`` assertion stamped with the
    cosponsorship window. ``withdrawn_date`` closes the window when present;
    otherwise the cosponsorship is open-ended (still in effect)."""
    subject = cosponsor.name or cosponsor.bioguide_id
    predicate = "co_sponsors"
    obj = cosponsor.bill_id
    aid = _assertion_id(subject, predicate, obj, f"cosponsor:{cosponsor.bioguide_id}:{cosponsor.bill_id}")

    return Assertion(
        assertion_id=aid,
        subject_text=subject,
        predicate=predicate,
        object_text=obj,
        t_valid_from=_iso(cosponsor.sponsorship_date),
        t_valid_until=_iso(cosponsor.withdrawn_date),
        is_atemporal=False,
        polarity=cosponsor.withdrawn_date is None,
        negated=cosponsor.withdrawn_date is not None,
        qualifiers={
            **({"party": cosponsor.party} if cosponsor.party else {}),
            **({"state": cosponsor.state} if cosponsor.state else {}),
            **({"is_original": "true"} if cosponsor.is_original else {}),
        },
        provenance=_structured_provenance(source_document_id=cosponsor.bill_id),
    )


# ─── Term → membership + party assertions ──────────────────────────────────


def term_to_membership_assertion(term: Term) -> Assertion:
    """A Term → one ``member_of`` assertion. The chamber+congress is the
    object; the start/end years bound the validity window."""
    subject = term.bioguide_id
    predicate = "member_of"
    chamber = term.chamber or "Congress"
    congress = term.congress if term.congress is not None else "unknown"
    obj = f"{chamber} (Congress {congress})"
    aid = _assertion_id(subject, predicate, obj, f"term:{term.id}")

    return Assertion(
        assertion_id=aid,
        subject_text=subject,
        predicate=predicate,
        object_text=obj,
        t_valid_from=f"{term.start_year}-01-01" if term.start_year else None,
        t_valid_until=f"{term.end_year}-12-31" if term.end_year else None,
        is_atemporal=False,
        qualifiers={
            **({"state": term.state} if term.state else {}),
            **({"district": term.district} if term.district else {}),
            **({"member_type": term.member_type} if term.member_type else {}),
        },
        provenance=_structured_provenance(source_document_id=term.bioguide_id),
    )


def term_to_party_assertion(term: Term) -> Assertion | None:
    """A Term with a party → one ``member_of_party`` assertion. Returns None
    for terms with no party (e.g. independents with a missing field)."""
    if not term.party:
        return None

    subject = term.bioguide_id
    predicate = "member_of_party"
    obj = term.party
    aid = _assertion_id(subject, predicate, obj, f"term:{term.id}:party")

    return Assertion(
        assertion_id=aid,
        subject_text=subject,
        predicate=predicate,
        object_text=obj,
        t_valid_from=f"{term.start_year}-01-01" if term.start_year else None,
        t_valid_until=f"{term.end_year}-12-31" if term.end_year else None,
        is_atemporal=False,
        provenance=_structured_provenance(source_document_id=term.bioguide_id),
    )


# ─── PublicLaw → became_law assertion ──────────────────────────────────────


def public_law_to_assertion(law: PublicLaw) -> Assertion:
    """A PublicLaw row → one ``became_law`` assertion. ``signed_date`` opens
    the window; there is no closing date (laws don't auto-expire; repeals
    are separate assertions). ``is_atemporal=False`` because the signing is
    an event in time, not a structural fact."""
    subject = law.bill_id
    predicate = "became_law"
    obj = law.id
    aid = _assertion_id(subject, predicate, obj, f"public_law:{law.id}")

    return Assertion(
        assertion_id=aid,
        subject_text=subject,
        predicate=predicate,
        object_text=obj,
        t_valid_from=_iso(law.signed_date),
        t_valid_until=None,
        is_atemporal=False,
        qualifiers={"law_type": law.law_type, "law_number": str(law.law_number)},
        provenance=_structured_provenance(source_document_id=law.bill_id),
    )


# ─── Dagster asset: assemble per-bill structured assertions ────────────────


@asset(
    group_name="congress",
    description=(
        "Structured-entity → Assertion projection. Cosponsor + PublicLaw "
        "rows for the partition's bill, stamped with temporal validity from "
        "the source date fields. STRUCTURED (not AMR) provenance."
    ),
    compute_kind="project",
    metadata={"layer": "gold"},
    partitions_def=bill_partitions,
    io_manager_key="append_io_manager",
)
def congress_structured_assertions(
    context: AssetExecutionContext,
    bill_cosponsors: list[Cosponsor],
    bill_detail: BillDetail,
) -> Output[list[Assertion]]:
    """One row per cosponsor + optional PublicLaw row, all temporally
    stamped from source date fields."""
    assertions: list[Assertion] = [cosponsor_to_assertion(c) for c in bill_cosponsors]

    public_law = PublicLaw.from_bill_detail(bill_detail)
    if public_law is not None:
        assertions.append(public_law_to_assertion(public_law))

    n_cosponsors = len(bill_cosponsors)
    n_laws = 1 if public_law is not None else 0
    context.log.info(
        "congress_structured_assertions: %d cosponsors + %d laws = %d assertions",
        n_cosponsors,
        n_laws,
        len(assertions),
    )

    return Output(
        assertions,
        metadata={
            "count": len(assertions),
            "n_cosponsors": n_cosponsors,
            "n_public_laws": n_laws,
        },
    )

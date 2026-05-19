"""Quick smoke test for structured-entity → Assertion projection.

Builds 5 synthetic Cosponsor + 1 PublicLaw records spanning the
temporal-validity edge cases (open-ended, closed, withdrawn, original,
party-line) and runs them through the same converters Dagster calls.

Mirrors the live e2e path but avoids the Congress API + amrlib model
weights so it runs in <100ms. Use this to verify the converters keep
working after refactors before paying the price of `task seed:congress
--with-gold`.
"""

from __future__ import annotations

import json
from datetime import date

from congress_data.assets.structured_assertions import (
    cosponsor_to_assertion,
    public_law_to_assertion,
    term_to_membership_assertion,
    term_to_party_assertion,
)
from congress_data.entities import Cosponsor, PublicLaw, Term


def _fmt_window(t_from: str | None, t_until: str | None) -> str:
    lo = t_from or "-∞"
    hi = t_until or "+∞"
    return f"[{lo} → {hi}]"


def main() -> None:
    print("=" * 72)
    print("Structured-entity → Assertion smoke test")
    print("=" * 72)

    cosponsors = [
        Cosponsor(
            bioguide_id="J000001",
            bill_id="hr1-119",
            name="Rep. Jones",
            party="D",
            state="CA",
            sponsorship_date=date(2025, 1, 15),
            withdrawn_date=None,
            is_original=True,
        ),
        Cosponsor(
            bioguide_id="S000001",
            bill_id="hr1-119",
            name="Rep. Smith",
            party="R",
            state="TX",
            sponsorship_date=date(2025, 2, 1),
            withdrawn_date=date(2025, 4, 1),
            is_original=False,
        ),
        Cosponsor(
            bioguide_id="W000001",
            bill_id="hr1-119",
            name="Rep. Williams",
            party="D",
            state="NY",
            sponsorship_date=date(2025, 2, 10),
        ),
        Cosponsor(
            bioguide_id="B000001",
            bill_id="hr1-119",
            name="Rep. Brown",
            party="I",
            state="VT",
            sponsorship_date=date(2025, 3, 5),
        ),
        Cosponsor(
            bioguide_id="D000001",
            bill_id="hr1-119",
            name="Rep. Davis",
            party="R",
            state="FL",
            sponsorship_date=date(2025, 3, 15),
            withdrawn_date=date(2025, 5, 1),
        ),
    ]
    public_law = PublicLaw(
        id="PL119-1",
        law_type="public",
        law_number=1,
        congress=119,
        bill_id="hr1-119",
        signed_date=date(2025, 6, 17),
    )
    term_with_party = Term(
        id="J000001:118:House",
        bioguide_id="J000001",
        congress=118,
        chamber="House",
        start_year=2023,
        end_year=2025,
        state="CA",
        district="12",
        party="D",
        member_type="Representative",
    )

    print("\n─── 5 Cosponsor → co_sponsors assertions ────────────────────────────")
    for c in cosponsors:
        a = cosponsor_to_assertion(c)
        status = "ACTIVE" if a.polarity else "WITHDRAWN"
        window = _fmt_window(a.t_valid_from, a.t_valid_until)
        print(f"  {a.subject_text:<18} {status:<10} {window}  qual={dict(a.qualifiers)}")

    print("\n─── 1 PublicLaw → became_law assertion ──────────────────────────────")
    pl = public_law_to_assertion(public_law)
    print(f"  {pl.subject_text} → {pl.object_text}  signed={pl.t_valid_from}")
    print(f"  qualifiers: {dict(pl.qualifiers)}")

    print("\n─── 1 Term → member_of + member_of_party ────────────────────────────")
    m = term_to_membership_assertion(term_with_party)
    p = term_to_party_assertion(term_with_party)
    print(f"  membership: {m.subject_text} → {m.object_text}  window={_fmt_window(m.t_valid_from, m.t_valid_until)}")
    print(f"  party:      {p.subject_text} → {p.object_text}  window={_fmt_window(p.t_valid_from, p.t_valid_until)}")

    # Point-in-time validity probe — would a query for 2025-03-15 see
    # which cosponsors as active? This is the load-bearing downstream
    # question the temporal fields were added to answer.
    probe_date = "2025-03-15"
    print(f"\n─── Point-in-time validity probe @ {probe_date} ─────────────────────")
    for c in cosponsors:
        a = cosponsor_to_assertion(c)
        active = (a.t_valid_from is None or a.t_valid_from <= probe_date) and (
            a.t_valid_until is None or probe_date <= a.t_valid_until
        )
        marker = "✓" if active else "✗"
        print(f"  {marker} {a.subject_text:<18} {_fmt_window(a.t_valid_from, a.t_valid_until)}")

    print("\n─── Full wire shape (first assertion as JSON) ───────────────────────")
    first = cosponsor_to_assertion(cosponsors[0])
    print(json.dumps(first.model_dump(), indent=2, default=str)[:1200])
    print("...\n")
    print("Smoke test complete.")


if __name__ == "__main__":
    main()

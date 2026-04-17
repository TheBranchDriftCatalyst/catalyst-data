"""Congress.gov domain entities for the head/tail pipeline.

All entities use Pydantic BaseModel with from_api_response() factories.
Designed for MERGE-based idempotent writes to Neo4j.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

# ── Helpers ──────────────────────────────────────────────────────────────────


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _parse_year(value: int | str | None, month: int = 1, day: int = 1) -> date | None:
    if not value:
        return None
    try:
        return date(int(value), month, day)
    except (ValueError, TypeError):
        return None


# ── Bill ─────────────────────────────────────────────────────────────────────


class Bill(BaseModel):
    """A congressional bill (list-level data from /bill/{congress})."""

    id: str = Field(description="Canonical key: {bill_type}{number}-{congress}")
    congress: int
    bill_type: str
    number: int
    display_number: str = Field(description="e.g. H.R.1234")
    title: str
    origin_chamber: str = ""
    introduced_date: date | None = None
    update_date: datetime | None = None
    update_date_including_text: datetime | None = None
    latest_action_date: date | None = None
    latest_action_text: str | None = None
    policy_area: str | None = None
    api_url: str | None = None

    @classmethod
    def from_api_list_item(cls, data: dict[str, Any], congress: int = 119) -> Bill:
        """Parse from a list-endpoint item (/bill/{congress})."""
        bill_type = data.get("type", "").lower()
        number = int(data.get("number", 0))
        latest_action = data.get("latestAction", {})
        policy_area = data.get("policyArea", {})

        return cls(
            id=f"{bill_type}{number}-{congress}",
            congress=congress,
            bill_type=bill_type,
            number=number,
            display_number=f"{bill_type.upper()}.{number}",
            title=data.get("title", ""),
            origin_chamber=data.get("originChamber", ""),
            introduced_date=_parse_date(data.get("introducedDate")),
            update_date=_parse_datetime(data.get("updateDate")),
            update_date_including_text=_parse_datetime(data.get("updateDateIncludingText")),
            latest_action_date=_parse_date(latest_action.get("actionDate")),
            latest_action_text=latest_action.get("text"),
            policy_area=policy_area.get("name") if policy_area else None,
            api_url=data.get("url"),
        )


class BillDetail(BaseModel):
    """Full bill detail from /bill/{congress}/{type}/{number}.

    Includes inline sponsor data (no extra API call needed).
    """

    id: str
    congress: int
    bill_type: str
    number: int
    title: str
    short_title: str | None = None
    origin_chamber: str = ""
    introduced_date: date | None = None
    constitutional_authority_text: str | None = None
    # Inline sponsor (embedded in detail response)
    sponsor_bioguide_id: str | None = None
    sponsor_name: str | None = None
    sponsor_party: str | None = None
    sponsor_state: str | None = None
    # Counts for sub-resources
    cosponsor_count: int = 0
    action_count: int = 0
    amendment_count: int = 0
    committee_count: int = 0
    # Policy
    policy_area: str | None = None
    subjects: list[str] = Field(default_factory=list)
    # Status
    latest_action_date: date | None = None
    latest_action_text: str | None = None
    became_law: bool = False
    law_number: str | None = None
    # Metadata
    update_date: datetime | None = None
    api_url: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> BillDetail:
        """Parse from detail endpoint response['bill']."""
        bill = data.get("bill", data)
        bill_type = bill.get("type", "").lower()
        number = int(bill.get("number", 0))
        congress = int(bill.get("congress", 0))

        # Inline sponsor
        sponsors = bill.get("sponsors", [])
        sponsor = sponsors[0] if sponsors else {}

        latest_action = bill.get("latestAction", {})
        policy_area = bill.get("policyArea", {})

        # Law info
        laws = bill.get("laws", [])
        law = laws[0] if laws else {}

        # Subject terms
        subjects_obj = bill.get("subjects", {})
        subject_items = subjects_obj.get("legislativeSubjects", []) if isinstance(subjects_obj, dict) else []

        return cls(
            id=f"{bill_type}{number}-{congress}",
            congress=congress,
            bill_type=bill_type,
            number=number,
            title=bill.get("title", ""),
            short_title=bill.get("shortTitle"),
            origin_chamber=bill.get("originChamber", ""),
            introduced_date=_parse_date(bill.get("introducedDate")),
            constitutional_authority_text=bill.get("constitutionalAuthorityStatementText"),
            sponsor_bioguide_id=sponsor.get("bioguideId"),
            sponsor_name=sponsor.get("fullName") or sponsor.get("name"),
            sponsor_party=sponsor.get("party"),
            sponsor_state=sponsor.get("state"),
            cosponsor_count=bill.get("cosponsors", {}).get("count", 0)
            if isinstance(bill.get("cosponsors"), dict)
            else 0,
            action_count=bill.get("actions", {}).get("count", 0) if isinstance(bill.get("actions"), dict) else 0,
            amendment_count=bill.get("amendments", {}).get("count", 0)
            if isinstance(bill.get("amendments"), dict)
            else 0,
            committee_count=bill.get("committees", {}).get("count", 0)
            if isinstance(bill.get("committees"), dict)
            else 0,
            policy_area=policy_area.get("name") if policy_area else None,
            subjects=[s.get("name", "") for s in subject_items if s.get("name")],
            latest_action_date=_parse_date(latest_action.get("actionDate")),
            latest_action_text=latest_action.get("text"),
            became_law=bool(laws),
            law_number=f"{law.get('type', '')}{law.get('number', '')}" if law else None,
            update_date=_parse_datetime(bill.get("updateDate")),
            api_url=bill.get("url"),
        )


# ── Action ───────────────────────────────────────────────────────────────────


class Action(BaseModel):
    """A bill action event (append-only, immutable once written).

    Note: actionCode is only present for House (sourceSystem=2) and
    Library of Congress (sourceSystem=9). Senate actions (sourceSystem=0)
    have NO actionCode — fall back to text parsing for those.
    """

    id: str = Field(description="Content-hash: sha1(bill_id + action_date + text + actor)")
    bill_id: str
    action_date: date | None = None
    action_time: datetime | None = None
    text: str = ""
    action_type: str | None = None
    action_code: str | None = None
    source_system: str | None = None
    source_system_code: int | None = None
    committees: list[dict[str, str]] = Field(default_factory=list)
    recorded_votes: list[dict[str, str]] = Field(default_factory=list)
    sequence: int = 0

    @classmethod
    def from_api_response(cls, data: dict[str, Any], bill_id: str, sequence: int = 0) -> Action:
        source = data.get("sourceSystem", {})
        action_date_str = data.get("actionDate", "")
        text = data.get("text", "")
        actor = source.get("name", "")

        # Content-hash for idempotent MERGE
        hash_input = f"{bill_id}:{action_date_str}:{text}:{actor}"
        content_hash = hashlib.sha1(hash_input.encode()).hexdigest()[:12]

        return cls(
            id=f"{bill_id}:action:{content_hash}",
            bill_id=bill_id,
            action_date=_parse_date(action_date_str),
            action_time=_parse_datetime(data.get("actionTime")),
            text=text,
            action_type=data.get("type"),
            action_code=data.get("actionCode"),
            source_system=actor,
            source_system_code=source.get("code"),
            committees=[
                {"system_code": c.get("systemCode", ""), "name": c.get("name", "")} for c in data.get("committees", [])
            ],
            recorded_votes=[
                {
                    "roll_number": str(rv.get("rollNumber", "")),
                    "chamber": rv.get("chamber", ""),
                    "url": rv.get("url", ""),
                }
                for rv in data.get("recordedVotes", [])
            ],
            sequence=sequence,
        )


# ── Cosponsor ────────────────────────────────────────────────────────────────


class Cosponsor(BaseModel):
    """A bill cosponsor with temporal data."""

    bioguide_id: str
    bill_id: str
    name: str = ""
    party: str | None = None
    state: str | None = None
    district: str | None = None
    sponsorship_date: date | None = None
    withdrawn_date: date | None = None
    is_original: bool = False

    @classmethod
    def from_api_response(cls, data: dict[str, Any], bill_id: str) -> Cosponsor:
        return cls(
            bioguide_id=data.get("bioguideId", ""),
            bill_id=bill_id,
            name=data.get("fullName", "") or data.get("name", ""),
            party=data.get("party"),
            state=data.get("state"),
            district=str(data["district"]) if data.get("district") is not None else None,
            sponsorship_date=_parse_date(data.get("sponsorshipDate")),
            withdrawn_date=_parse_date(data.get("sponsorshipWithdrawnDate")),
            is_original=bool(data.get("isOriginalCosponsor", False)),
        )


# ── BillVersion ──────────────────────────────────────────────────────────────


class BillVersion(BaseModel):
    """A published text version of a bill (immutable once published)."""

    id: str = Field(description="{bill_id}:{version_code}")
    bill_id: str
    version_code: str = Field(description="ih, rh, eh, pcs, enr, pl, etc.")
    version_name: str = ""
    publish_date: date | None = None
    formats: list[dict[str, str]] = Field(default_factory=list, description="[{type, url}]")

    @classmethod
    def from_api_response(cls, data: dict[str, Any], bill_id: str) -> BillVersion:
        # Version code from the "type" field (e.g. "Introduced in House" → need code)
        version_name = data.get("type", "")
        # Try to extract code from formats URL or use a slug
        formats = [{"type": f.get("type", ""), "url": f.get("url", "")} for f in data.get("formats", [])]
        # Derive version code from the format URL if possible
        version_code = _extract_version_code(formats, version_name)

        return cls(
            id=f"{bill_id}:{version_code}",
            bill_id=bill_id,
            version_code=version_code,
            version_name=version_name,
            publish_date=_parse_date(data.get("date")),
            formats=formats,
        )


def _extract_version_code(formats: list[dict[str, str]], version_name: str) -> str:
    """Try to extract the short version code (ih, rh, enr, etc.) from format URLs."""
    for fmt in formats:
        url = fmt.get("url", "")
        # URLs typically contain /BILLS-119hr1234ih.xml or similar
        if "/BILLS-" in url:
            # Extract the suffix before the file extension
            parts = url.split("/BILLS-")[-1]
            # The version code is the trailing alpha chars before .xml/.pdf/.htm
            import re

            match = re.search(r"(\d+)([a-z]+)\.", parts)
            if match:
                return match.group(2)
    # Fallback: slugify the version name
    return version_name.lower().replace(" ", "_")[:10] if version_name else "unknown"


# ── Amendment ────────────────────────────────────────────────────────────────


class Amendment(BaseModel):
    """A bill amendment."""

    id: str = Field(description="{amdt_type}{number}-{congress}")
    congress: int
    amendment_type: str
    number: int
    purpose: str | None = None
    description: str | None = None
    submitted_date: date | None = None
    proposed_date: date | None = None
    latest_action_date: date | None = None
    latest_action_text: str | None = None
    sponsor_bioguide_id: str | None = None
    sponsor_name: str | None = None
    amended_bill_id: str | None = None
    chamber: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any], bill_id: str | None = None) -> Amendment:
        amdt_type = data.get("type", "").lower()
        number = int(data.get("number", 0))
        congress = int(data.get("congress", 0))
        latest_action = data.get("latestAction", {})

        # Sponsor (may be nested)
        sponsors = data.get("sponsors", [])
        sponsor = sponsors[0] if sponsors else {}

        # Amended bill
        amended_bill = data.get("amendedBill", {})
        if amended_bill:
            ab_type = amended_bill.get("type", "").lower()
            ab_num = amended_bill.get("number", "")
            ab_congress = amended_bill.get("congress", congress)
            amended_bill_id = f"{ab_type}{ab_num}-{ab_congress}"
        else:
            amended_bill_id = bill_id

        return cls(
            id=f"{amdt_type}{number}-{congress}",
            congress=congress,
            amendment_type=amdt_type,
            number=number,
            purpose=data.get("purpose"),
            description=data.get("description"),
            submitted_date=_parse_date(data.get("submittedDate")),
            proposed_date=_parse_date(data.get("proposedDate")),
            latest_action_date=_parse_date(latest_action.get("actionDate")),
            latest_action_text=latest_action.get("text"),
            sponsor_bioguide_id=sponsor.get("bioguideId"),
            sponsor_name=sponsor.get("fullName") or sponsor.get("name"),
            amended_bill_id=amended_bill_id,
            chamber=data.get("chamber"),
        )


# ── Member ───────────────────────────────────────────────────────────────────


class Member(BaseModel):
    """A member of Congress (identity node — state/party live on Term)."""

    bioguide_id: str
    name: str = ""
    first_name: str | None = None
    last_name: str | None = None
    birth_year: int | None = None
    death_year: int | None = None
    official_url: str | None = None
    depiction_url: str | None = None
    api_url: str | None = None
    update_date: datetime | None = None

    @classmethod
    def from_api_list_item(cls, data: dict[str, Any]) -> Member:
        """Parse from list endpoint item."""
        depiction = data.get("depiction", {})
        return cls(
            bioguide_id=data.get("bioguideId", ""),
            name=data.get("name", ""),
            first_name=data.get("firstName"),
            last_name=data.get("lastName"),
            birth_year=data.get("birthYear"),
            death_year=data.get("deathYear"),
            official_url=data.get("officialWebsiteUrl"),
            depiction_url=depiction.get("imageUrl") if depiction else None,
            api_url=data.get("url"),
            update_date=_parse_datetime(data.get("updateDate")),
        )

    @classmethod
    def from_api_detail(cls, data: dict[str, Any]) -> Member:
        """Parse from detail endpoint response['member']."""
        member = data.get("member", data)
        depiction = member.get("depiction", {})
        return cls(
            bioguide_id=member.get("bioguideId", ""),
            name=member.get("directOrderName", "") or member.get("invertedOrderName", ""),
            first_name=member.get("firstName"),
            last_name=member.get("lastName"),
            birth_year=member.get("birthYear"),
            death_year=member.get("deathYear"),
            official_url=member.get("officialWebsiteUrl"),
            depiction_url=depiction.get("imageUrl") if depiction else None,
            api_url=member.get("url"),
            update_date=_parse_datetime(member.get("updateDate")),
        )


# ── Term ─────────────────────────────────────────────────────────────────────


class Term(BaseModel):
    """A member's term in a specific congress/chamber.

    Party, state, district live HERE, not on Member — handles party switches
    and redistricting correctly.
    """

    id: str = Field(description="{bioguide_id}:{congress}:{chamber}")
    bioguide_id: str
    congress: int | None = None
    chamber: str | None = None
    start_year: int | None = None
    end_year: int | None = None
    state: str | None = None
    district: str | None = None
    party: str | None = None
    member_type: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any], bioguide_id: str) -> Term:
        chamber = data.get("chamber", "")
        congress = data.get("congress")
        # Some terms don't have congress number, derive from years
        start_year = data.get("startYear")

        term_id = f"{bioguide_id}:{congress or 'unknown'}:{chamber}"

        return cls(
            id=term_id,
            bioguide_id=bioguide_id,
            congress=congress,
            chamber=chamber,
            start_year=start_year,
            end_year=data.get("endYear"),
            state=data.get("stateCode") or data.get("state"),
            district=str(data["district"]) if data.get("district") is not None else None,
            party=data.get("partyName"),
            member_type=data.get("memberType"),
        )

    @classmethod
    def from_member_detail(cls, member_data: dict[str, Any]) -> list[Term]:
        """Extract all terms from a member detail response."""
        member = member_data.get("member", member_data)
        bioguide_id = member.get("bioguideId", "")
        terms_data = member.get("terms", [])

        # terms can be a list directly or nested under "item"
        if isinstance(terms_data, dict):
            terms_data = terms_data.get("item", [])

        return [cls.from_api_response(t, bioguide_id) for t in terms_data]


# ── Committee ────────────────────────────────────────────────────────────────


class Committee(BaseModel):
    """A congressional committee (passive node — seeded from bill referrals)."""

    system_code: str
    name: str = ""
    chamber: str | None = None
    committee_type: str | None = None
    url: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> Committee:
        return cls(
            system_code=data.get("systemCode", ""),
            name=data.get("name", ""),
            chamber=data.get("chamber"),
            committee_type=data.get("committeeTypeCode") or data.get("type"),
            url=data.get("url"),
        )


# ── PolicyArea / Subject ─────────────────────────────────────────────────────


class PolicyArea(BaseModel):
    """Controlled vocabulary policy area (~32 values from congress.gov)."""

    name: str

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> PolicyArea:
        return cls(name=data.get("name", ""))


class Subject(BaseModel):
    """Legislative subject term (open vocabulary, thousands of values)."""

    name: str

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> Subject:
        return cls(name=data.get("name", ""))


# ── PublicLaw ────────────────────────────────────────────────────────────────


class PublicLaw(BaseModel):
    """Terminal state: a bill that became law."""

    id: str = Field(description="e.g. PL119-1")
    law_type: str = "public"
    law_number: int = 0
    congress: int = 0
    bill_id: str = ""
    signed_date: date | None = None

    @classmethod
    def from_bill_detail(cls, bill: BillDetail) -> PublicLaw | None:
        """Extract PublicLaw if the bill became law."""
        if not bill.became_law or not bill.law_number:
            return None
        # Parse "publ123" format
        law_num_str = bill.law_number.replace("pub", "").replace("l", "")
        try:
            law_number = int(law_num_str)
        except ValueError:
            law_number = 0

        return cls(
            id=f"PL{bill.congress}-{law_number}",
            law_type="public",
            law_number=law_number,
            congress=bill.congress,
            bill_id=bill.id,
        )


# ── RollCallVote ─────────────────────────────────────────────────────────────


class RollCallVote(BaseModel):
    """A roll-call vote (House via API beta, Senate via LIS XML)."""

    id: str = Field(description="{chamber}-{congress}-{session}-{roll_number}")
    chamber: str
    congress: int
    session: int
    roll_number: int
    vote_date: datetime | None = None
    question: str = ""
    result: str = ""
    yea_count: int = 0
    nay_count: int = 0
    present_count: int = 0
    not_voting_count: int = 0
    bill_id: str | None = None

    @classmethod
    def from_house_api(cls, data: dict[str, Any], congress: int, session: int) -> RollCallVote:
        """Parse from /house-vote/{congress}/{session}/{voteNumber}."""
        vote = data.get("vote", data)
        roll_number = int(vote.get("rollCallNumber", vote.get("voteNumber", 0)))

        # Extract totals from party breakdown
        totals = vote.get("totals", vote.get("votePartyTotal", {}))
        yea = nay = present = nv = 0
        if isinstance(totals, dict):
            yea = totals.get("yea", 0)
            nay = totals.get("nay", 0)
            present = totals.get("present", 0)
            nv = totals.get("notVoting", 0)

        return cls(
            id=f"house-{congress}-{session}-{roll_number}",
            chamber="House",
            congress=congress,
            session=session,
            roll_number=roll_number,
            vote_date=_parse_datetime(vote.get("date") or vote.get("startDate")),
            question=vote.get("question", vote.get("voteQuestion", "")),
            result=vote.get("result", ""),
            yea_count=yea,
            nay_count=nay,
            present_count=present,
            not_voting_count=nv,
        )


class MemberVote(BaseModel):
    """A single member's position on a roll-call vote."""

    bioguide_id: str
    roll_call_id: str
    position: str = Field(description="Yea, Nay, Present, Not Voting")
    party: str | None = None
    state: str | None = None

    @classmethod
    def from_house_api(cls, data: dict[str, Any], roll_call_id: str) -> MemberVote:
        """Parse from /house-vote/.../members list item."""
        member = data.get("member", data)
        return cls(
            bioguide_id=member.get("bioguideId", data.get("bioguideId", "")),
            roll_call_id=roll_call_id,
            position=data.get("votecast", data.get("position", "")),
            party=member.get("party", data.get("party")),
            state=member.get("state", data.get("state")),
        )

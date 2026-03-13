"""Congress.gov domain entities."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import Field

from congress_data.core.base_entity import BaseEntity


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _parse_year(value: int | str | None, month: int = 1, day: int = 1) -> date | None:
    if not value:
        return None
    try:
        return date(int(value), month, day)
    except (ValueError, TypeError):
        return None


class Bill(BaseEntity):
    """A congressional bill."""

    number: str = Field(description="Bill number (e.g. 'H.R.1234')")
    congress: int
    bill_type: str
    title: str
    short_title: str | None = None
    summary: str | None = None
    chamber: str = ""
    introduced_date: date | None = None
    latest_action_date: date | None = None
    latest_action_text: str | None = None
    policy_area: str | None = None
    api_url: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any], congress: int = 118, **kwargs: Any) -> Bill:
        bill_type = data.get("type", "").lower()
        number = data.get("number", "")
        bill_number = f"{bill_type.upper()}.{number}" if bill_type else str(number)
        latest_action = data.get("latestAction", {})

        return cls(
            id=f"{bill_type}{number}-{congress}",
            number=bill_number,
            congress=congress,
            bill_type=bill_type,
            title=data.get("title", ""),
            short_title=data.get("shortTitle"),
            summary=None,
            chamber=data.get("originChamber", ""),
            introduced_date=_parse_date(data.get("introducedDate")),
            latest_action_date=_parse_date(latest_action.get("actionDate")),
            latest_action_text=latest_action.get("text"),
            policy_area=data.get("policyArea", {}).get("name") if data.get("policyArea") else None,
            source_url=data.get("url"),
            api_url=data.get("url"),
        )


class Member(BaseEntity):
    """A member of Congress."""

    bioguide_id: str
    name: str
    first_name: str | None = None
    last_name: str | None = None
    party: str | None = None
    state: str | None = None
    district: str | None = None
    chamber: str | None = None
    terms_served: int = 0
    current_term_start: date | None = None
    current_term_end: date | None = None
    url: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any], **kwargs: Any) -> Member:
        bioguide_id = data.get("bioguideId", "")
        terms = data.get("terms", {}).get("item", [])
        current_term = terms[-1] if terms else {}

        return cls(
            id=bioguide_id,
            bioguide_id=bioguide_id,
            name=data.get("name", ""),
            first_name=data.get("firstName"),
            last_name=data.get("lastName"),
            party=data.get("partyName", "")[:1] if data.get("partyName") else None,
            state=data.get("state"),
            district=str(data["district"]) if data.get("district") is not None else None,
            chamber=current_term.get("chamber"),
            terms_served=len(terms),
            current_term_start=_parse_year(current_term.get("startYear"), 1, 1),
            current_term_end=_parse_year(current_term.get("endYear"), 12, 31),
            url=data.get("url"),
            source_url=data.get("url"),
        )


class Committee(BaseEntity):
    """A congressional committee."""

    system_code: str
    name: str
    chamber: str | None = None
    committee_type: str | None = None
    parent_committee: str | None = None
    url: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any], **kwargs: Any) -> Committee:
        system_code = data.get("systemCode", "")

        return cls(
            id=system_code,
            system_code=system_code,
            name=data.get("name", ""),
            chamber=data.get("chamber"),
            committee_type=data.get("committeeTypeCode"),
            parent_committee=data.get("parent", {}).get("systemCode"),
            url=data.get("url"),
            source_url=data.get("url"),
        )

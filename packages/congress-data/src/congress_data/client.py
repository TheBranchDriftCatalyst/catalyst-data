"""Congress.gov API v3 client with full endpoint coverage.

Covers: bills, members, amendments, house votes (beta).
All sub-endpoints validated against API swagger docs (2026-04-16).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from congress_data.core.base_api_client import BaseAPIClient
from dagster_io.logging import get_logger

logger = get_logger(__name__)


class CongressAPIClient(BaseAPIClient):
    """Client for the Congress.gov API v3."""

    def __init__(self, api_key: str | None = None):
        api_key = api_key or os.environ.get("CONGRESS_API_KEY", "")
        if not api_key:
            raise ValueError(
                "CONGRESS_API_KEY is required. Set it as an environment variable or pass api_key to the constructor."
            )
        super().__init__(api_key=api_key, requests_per_hour=5000, timeout=30.0)

    @property
    def base_url(self) -> str:
        return "https://api.congress.gov/v3"

    @property
    def default_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "X-Api-Key": self.api_key,
        }

    # ── Helper for fromDateTime/toDateTime params ────────────────────────────

    @staticmethod
    def _date_params(
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        sort: str | None = None,
    ) -> dict[str, str]:
        """Build date-filter query params."""
        params: dict[str, str] = {}
        if from_dt:
            params["fromDateTime"] = from_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        if to_dt:
            params["toDateTime"] = to_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        if sort:
            params["sort"] = sort
        return params

    # ══════════════════════════════════════════════════════════════════════════
    # BILLS
    # ══════════════════════════════════════════════════════════════════════════

    # -- List --

    def get_bills(
        self,
        congress: int = 119,
        limit: int = 250,
        offset: int = 0,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        sort: str = "updateDate+asc",
    ) -> dict[str, Any]:
        params = {"limit": limit, "offset": offset, **self._date_params(from_dt, to_dt, sort)}
        return self.get(f"/bill/{congress}", params=params)

    def iterate_bills(
        self,
        congress: int = 119,
        max_bills: int | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        sort: str = "updateDate+asc",
    ) -> Iterator[dict[str, Any]]:
        logger.info("Iterating bills congress=%d from=%s", congress, from_dt)
        params = self._date_params(from_dt, to_dt, sort)
        yield from self.paginate(f"/bill/{congress}", results_key="bills", params=params, max_items=max_bills)

    # -- Detail --

    def get_bill_detail(self, congress: int, bill_type: str, number: int) -> dict[str, Any]:
        return self.get(f"/bill/{congress}/{bill_type.lower()}/{number}")

    # -- Sub-endpoints (per-bill) --

    def get_bill_actions(self, congress: int, bill_type: str, number: int) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/bill/{congress}/{bill_type.lower()}/{number}/actions",
            results_key="actions",
        )

    def get_bill_cosponsors(self, congress: int, bill_type: str, number: int) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/bill/{congress}/{bill_type.lower()}/{number}/cosponsors",
            results_key="cosponsors",
        )

    def get_bill_text_versions(self, congress: int, bill_type: str, number: int) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/bill/{congress}/{bill_type.lower()}/{number}/text",
            results_key="textVersions",
        )

    def get_bill_amendments(self, congress: int, bill_type: str, number: int) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/bill/{congress}/{bill_type.lower()}/{number}/amendments",
            results_key="amendments",
        )

    def get_bill_summaries(self, congress: int, bill_type: str, number: int) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/bill/{congress}/{bill_type.lower()}/{number}/summaries",
            results_key="summaries",
        )

    def get_bill_subjects(self, congress: int, bill_type: str, number: int) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/bill/{congress}/{bill_type.lower()}/{number}/subjects",
            results_key="legislativeSubjects",
        )

    def get_bill_related_bills(self, congress: int, bill_type: str, number: int) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/bill/{congress}/{bill_type.lower()}/{number}/relatedbills",
            results_key="relatedBills",
        )

    def get_bill_committees(self, congress: int, bill_type: str, number: int) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/bill/{congress}/{bill_type.lower()}/{number}/committees",
            results_key="committees",
        )

    def get_bill_titles(self, congress: int, bill_type: str, number: int) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/bill/{congress}/{bill_type.lower()}/{number}/titles",
            results_key="titles",
        )

    # ══════════════════════════════════════════════════════════════════════════
    # MEMBERS
    # ══════════════════════════════════════════════════════════════════════════

    # -- List (incremental) --
    # NOTE: /member/congress/{c} does NOT support fromDateTime.
    #       Use /member?fromDateTime=...&currentMember=True instead.

    def iterate_members(
        self,
        congress: int | None = None,
        max_members: int | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        current_member: bool | None = None,
    ) -> Iterator[dict[str, Any]]:
        if congress and not from_dt:
            # Non-incremental: use /member/congress/{c}
            endpoint = f"/member/congress/{congress}"
            params: dict[str, Any] = {}
        else:
            # Incremental: use /member with fromDateTime
            endpoint = "/member"
            params = self._date_params(from_dt, to_dt)
            if current_member is not None:
                params["currentMember"] = str(current_member).lower()

        logger.info("Iterating members endpoint=%s from=%s", endpoint, from_dt)
        yield from self.paginate(endpoint, results_key="members", params=params, max_items=max_members)

    # -- Detail --

    def get_member_detail(self, bioguide_id: str) -> dict[str, Any]:
        return self.get(f"/member/{bioguide_id}")

    # -- Sub-endpoints (per-member) --

    def get_member_sponsored(self, bioguide_id: str) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/member/{bioguide_id}/sponsored-legislation",
            results_key="sponsoredLegislation",
        )

    def get_member_cosponsored(self, bioguide_id: str) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/member/{bioguide_id}/cosponsored-legislation",
            results_key="cosponsoredLegislation",
        )

    # ══════════════════════════════════════════════════════════════════════════
    # AMENDMENTS
    # ══════════════════════════════════════════════════════════════════════════

    def iterate_amendments(
        self,
        congress: int = 119,
        max_amendments: int | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
    ) -> Iterator[dict[str, Any]]:
        params = self._date_params(from_dt, to_dt)
        yield from self.paginate(
            f"/amendment/{congress}",
            results_key="amendments",
            params=params,
            max_items=max_amendments,
        )

    def get_amendment_detail(self, congress: int, amdt_type: str, number: int) -> dict[str, Any]:
        return self.get(f"/amendment/{congress}/{amdt_type.lower()}/{number}")

    def get_amendment_actions(self, congress: int, amdt_type: str, number: int) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/amendment/{congress}/{amdt_type.lower()}/{number}/actions",
            results_key="actions",
        )

    # ══════════════════════════════════════════════════════════════════════════
    # HOUSE VOTES (Beta — 118th/119th Congress only)
    # ══════════════════════════════════════════════════════════════════════════

    def iterate_house_votes(
        self,
        congress: int = 119,
        session: int | None = None,
        max_votes: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """List House roll-call votes. Beta endpoint."""
        endpoint = f"/house-vote/{congress}/{session}" if session else f"/house-vote/{congress}"
        yield from self.paginate(endpoint, results_key="houseVotes", max_items=max_votes)

    def get_house_vote_detail(self, congress: int, session: int, vote_number: int) -> dict[str, Any]:
        return self.get(f"/house-vote/{congress}/{session}/{vote_number}")

    def get_house_vote_members(self, congress: int, session: int, vote_number: int) -> Iterator[dict[str, Any]]:
        """Get member-level vote positions. This is the key endpoint for query #2."""
        yield from self.paginate(
            f"/house-vote/{congress}/{session}/{vote_number}/members",
            results_key="members",
        )

    # ══════════════════════════════════════════════════════════════════════════
    # COMMITTEES (passive — used for referral data, not a partition set)
    # ══════════════════════════════════════════════════════════════════════════

    def iterate_committees(
        self,
        congress: int = 119,
        max_committees: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        yield from self.paginate(
            f"/committee/{congress}",
            results_key="committees",
            max_items=max_committees,
        )

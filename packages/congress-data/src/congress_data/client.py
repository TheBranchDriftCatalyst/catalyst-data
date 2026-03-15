"""Congress.gov API v3 client."""

from __future__ import annotations

import os
from typing import Any, Iterator

from dagster_io.logging import get_logger
from congress_data.core.base_api_client import BaseAPIClient

logger = get_logger(__name__)


class CongressAPIClient(BaseAPIClient):
    """Client for the Congress.gov API v3."""

    def __init__(self, api_key: str | None = None):
        api_key = api_key or os.environ.get("CONGRESS_API_KEY", "")
        if not api_key:
            raise ValueError(
                "CONGRESS_API_KEY is required. Set it as an environment variable "
                "or pass api_key to the constructor."
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

    # -- Bills --

    def get_bills(
        self, congress: int = 118, limit: int = 250, offset: int = 0
    ) -> dict[str, Any]:
        return self.get(f"/bill/{congress}", params={"limit": limit, "offset": offset})

    def get_bill_detail(
        self, congress: int, bill_type: str, number: int
    ) -> dict[str, Any]:
        return self.get(f"/bill/{congress}/{bill_type.lower()}/{number}")

    def get_bill_text(
        self, congress: int, bill_type: str, number: int
    ) -> dict[str, Any]:
        return self.get(f"/bill/{congress}/{bill_type.lower()}/{number}/text")

    def iterate_bills(
        self, congress: int = 118, max_bills: int | None = None
    ) -> Iterator[dict[str, Any]]:
        logger.info("Iterating bills for congress=%d max_bills=%s", congress, max_bills)
        yield from self.paginate(
            f"/bill/{congress}",
            results_key="bills",
            max_items=max_bills,
        )

    # -- Members --

    def get_members(
        self, congress: int = 118, limit: int = 250, offset: int = 0
    ) -> dict[str, Any]:
        return self.get(f"/member/congress/{congress}", params={"limit": limit, "offset": offset})

    def get_member_detail(self, bioguide_id: str) -> dict[str, Any]:
        return self.get(f"/member/{bioguide_id}")

    def iterate_members(
        self, congress: int = 118, max_members: int | None = None
    ) -> Iterator[dict[str, Any]]:
        logger.info("Iterating members for congress=%d max_members=%s", congress, max_members)
        yield from self.paginate(
            f"/member/congress/{congress}",
            results_key="members",
            max_items=max_members,
        )

    # -- Committees --

    def get_committees(
        self,
        congress: int = 118,
        chamber: str | None = None,
        limit: int = 250,
        offset: int = 0,
    ) -> dict[str, Any]:
        endpoint = f"/committee/{congress}"
        if chamber:
            endpoint = f"/committee/{congress}/{chamber.lower()}"
        return self.get(endpoint, params={"limit": limit, "offset": offset})

    def get_committee_detail(
        self, congress: int, chamber: str, committee_code: str
    ) -> dict[str, Any]:
        return self.get(f"/committee/{congress}/{chamber.lower()}/{committee_code}")

    def iterate_committees(
        self, congress: int = 118, max_committees: int | None = None
    ) -> Iterator[dict[str, Any]]:
        logger.info("Iterating committees for congress=%d max_committees=%s", congress, max_committees)
        yield from self.paginate(
            f"/committee/{congress}",
            results_key="committees",
            max_items=max_committees,
        )

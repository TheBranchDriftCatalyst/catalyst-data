"""Dagster configuration for Congress.gov pipeline."""

import os

from dagster import Config


class CongressionalConfig(Config):
    """Runtime configuration for congressional data extraction.

    Head assets use this for API key + congress number.
    Tail assets inherit partition key and resolve congress/type/number from it.
    """

    congress_api_key: str = os.environ.get("CONGRESS_API_KEY", "")
    congress_number: int = 119
    bill_types: list[str] = ["hr", "s", "hjres", "sjres", "hconres", "sconres", "hres", "sres"]


class WatermarkConfig(Config):
    """Configuration for incremental watermark state."""

    s3_state_prefix: str = "silver/congress_data/state"
    bills_watermark_key: str = "bills_watermark.json"
    members_watermark_key: str = "members_watermark.json"
    bills_manifest_key: str = "silver/congress_data/manifests/bills_manifest.jsonl"
    members_manifest_key: str = "silver/congress_data/manifests/members_manifest.jsonl"

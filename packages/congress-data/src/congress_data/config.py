"""Dagster configuration for Congress.gov pipeline."""

import os

from dagster import Config


class CongressionalConfig(Config):
    """Runtime configuration for congressional data extraction."""

    congress_api_key: str = os.environ.get("CONGRESS_API_KEY", "")
    congress_number: int = 118
    days_back: int = 30
    max_bills: int = 1000
    max_members: int | None = None
    max_committees: int | None = None
    bill_types: list[str] = ["hr", "s"]
    download_full_text: bool = False

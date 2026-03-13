"""Dagster configuration for open-leaks pipeline."""

import os
import tempfile

from dagster import Config


class OpenLeaksConfig(Config):
    """Top-level pipeline configuration."""

    cache_dir: str = os.environ.get(
        "OPEN_LEAKS_CACHE_DIR",
        str(os.path.join(tempfile.gettempdir(), "open-leaks-cache")),
    )
    batch_size: int = int(os.environ.get("OPEN_LEAKS_BATCH_SIZE", "100"))

    # Download URLs (overridable for mirrors)
    icij_bulk_url: str = os.environ.get(
        "ICIJ_BULK_URL",
        "https://offshoreleaks-data.icij.org/offshoreleaks/csv/full-oldb.LATEST.zip",
    )
    cablegate_csv_url: str = os.environ.get(
        "CABLEGATE_CSV_URL",
        "https://archive.org/download/wikileaks-cables-csv/cables.csv",
    )
    epstein_api_url: str = os.environ.get(
        "EPSTEIN_API_URL",
        "https://www.epsteininvestigation.org/api/v1",
    )

    # Max records per source (0 = unlimited)
    max_cables: int = int(os.environ.get("MAX_CABLES", "0"))
    max_icij_entities: int = int(os.environ.get("MAX_ICIJ_ENTITIES", "0"))
    max_icij_relationships: int = int(os.environ.get("MAX_ICIJ_RELATIONSHIPS", "0"))
    max_epstein_docs: int = int(os.environ.get("MAX_EPSTEIN_DOCS", "0"))

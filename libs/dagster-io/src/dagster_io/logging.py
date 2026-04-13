"""Logging configuration for catalyst-data pipelines.

In Dagster runs: managed_python_loggers in dagster.yaml captures our loggers
into the structured Events tab with run/step metadata. We just set levels.

Outside Dagster (local dev, scripts, tests): simple console handler to stdout.
"""

import logging
import os
import sys

_NAMESPACES = (
    "dagster_io",
    "media_ingest",
    "congress_data",
    "open_leaks",
    "knowledge_graph",
    "data_explorer",
    "catalyst",
)

_NOISY_THIRD_PARTY = (
    "botocore",
    "boto3",
    "s3transfer",
    "httpx",
    "httpcore",
    "openai",
    "langchain",
    "chromadb",
    "fsspec",
    "aiobotocore",
    "urllib3",
)

_configured = False


def configure_logging(level: str | None = None) -> None:
    """Configure logging for catalyst-data.

    In Dagster step pods (detected via DAGSTER_RUN_JOB_NAME): just set log levels.
    Dagster's managed_python_loggers handles capture into the Events tab.

    Outside Dagster: add a simple text handler to stdout for console output.
    """
    global _configured
    if _configured:
        return
    _configured = True

    log_level = getattr(logging, (level or os.getenv("LOG_LEVEL", "INFO")).upper(), logging.INFO)
    in_dagster = os.getenv("DAGSTER_RUN_JOB_NAME") is not None

    for ns in _NAMESPACES:
        logging.getLogger(ns).setLevel(log_level)

    if not in_dagster:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-8s] %(name)-40s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        for ns in _NAMESPACES:
            ns_logger = logging.getLogger(ns)
            ns_logger.handlers.clear()
            ns_logger.addHandler(handler)
            ns_logger.propagate = False

    for noisy in _NOISY_THIRD_PARTY:
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger. Call configure_logging() first (idempotent)."""
    return logging.getLogger(name)

"""Environment-based configuration — reads the same env vars as Dagster code locations."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class S3Config:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str
    embedding_model: str = "text-embedding-3-small"


@dataclass(frozen=True)
class MediaConfig:
    metube_path: str = "/data/metube"
    tubesync_path: str = "/data/tubesync"


def get_s3_config() -> S3Config:
    return S3Config(
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
        access_key=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minioadmin"),
        secret_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minioadmin"),
        bucket=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
    )


def get_llm_config() -> LLMConfig:
    return LLMConfig(
        base_url=os.environ.get("EMBEDDING_BASE_URL", os.environ.get("LLM_BASE_URL", "http://localhost:4000/v1")),
        api_key=os.environ.get("OPENAI_API_KEY", os.environ.get("LLM_API_KEY", "")),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small"),
    )


def get_media_config() -> MediaConfig:
    return MediaConfig(
        metube_path=os.environ.get("METUBE_PATH", "/data/metube"),
        tubesync_path=os.environ.get("TUBESYNC_PATH", "/data/tubesync"),
    )

"""Dagster configuration for media ingest pipeline."""

from dagster import Config


class MediaIngestConfig(Config):
    """Runtime configuration for media file processing."""

    metube_path: str = "/data/metube"
    tubesync_path: str = "/data/tubesync"
    extensions: str = ".mp4,.mkv,.webm,.mp3,.m4a,.wav,.flac"
    whisper_model: str = "base"

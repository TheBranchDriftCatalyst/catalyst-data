"""Dagster configuration for media ingest pipeline."""

from dagster import Config


class MediaIngestConfig(Config):
    """Runtime configuration for media file processing."""

    metube_path: str = "/data/metube"
    tubesync_path: str = "/data/tubesync"
    extensions: str = ".mp4,.mkv,.webm,.mp3,.m4a,.wav,.flac"
    whisper_model: str = "large-v3"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    enable_diarization: bool = True
    hf_token: str = ""  # Falls back to HF_TOKEN env var

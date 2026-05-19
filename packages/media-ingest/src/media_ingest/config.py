"""Dagster configuration for media ingest pipeline."""

import os

from dagster import Config


class MediaIngestConfig(Config):
    """Runtime configuration for media file processing.

    Defaults match the prod talos00 deployment (NFS volumes at
    ``/data/metube`` + ``/data/tubesync``). For local dev the per-asset
    Config defaults can be overridden via Dagster run config OR — for
    the discovery roots — via the ``CATALYST_MEDIA_ROOT_METUBE`` /
    ``CATALYST_MEDIA_ROOT_TUBESYNC`` env vars (same convention the
    viewer-api uses at media-ingest/viewer/routes/media.py:32 — keeps
    the path source-of-truth in one place across the two services).
    """

    metube_path: str = os.environ.get("CATALYST_MEDIA_ROOT_METUBE", "/data/metube")
    tubesync_path: str = os.environ.get("CATALYST_MEDIA_ROOT_TUBESYNC", "/data/tubesync")
    extensions: str = ".mp4,.mkv,.webm,.mp3,.m4a,.wav,.flac"
    whisper_backend: str = "openvino"  # faster-whisper (CPU) | openvino (Intel GPU) | mlx-whisper (Apple Silicon Metal)
    whisper_model: str = "large-v3"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    # OpenVINO-specific: HuggingFace model ID for pre-converted OV models
    openvino_model_id: str = "OpenVINO/whisper-large-v3-fp16-ov"
    openvino_device: str = "GPU"  # "CPU" or "GPU"
    # MLX-specific: HuggingFace model ID for MLX-converted models (e.g. mlx-community/whisper-large-v3-mlx)
    mlx_model_id: str = "mlx-community/whisper-large-v3-mlx"
    enable_diarization: bool = True
    hf_token: str = ""  # Falls back to HF_TOKEN env var

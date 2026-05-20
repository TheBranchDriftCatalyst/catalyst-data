"""Dagster configuration for media ingest pipeline."""

import os

from dagster import Config

from dagster_io.paths import METUBE_DIR, TUBESYNC_DIR


class MediaIngestConfig(Config):
    """Runtime configuration for media file processing.

    Filesystem paths derive from ``CATALYST_DATA_ROOT`` (see
    ``dagster_io.paths``). Same layout prod and dev — only the root
    differs (``/data`` in k8s, ``$PROJECT_DIR/.dev-data`` on the host
    via Tilt).
    """

    metube_path: str = METUBE_DIR
    tubesync_path: str = TUBESYNC_DIR
    extensions: str = ".mp4,.mkv,.webm,.mp3,.m4a,.wav,.flac"
    whisper_backend: str = os.environ.get(
        "WHISPER_BACKEND", "openvino"
    )  # faster-whisper (CPU) | openvino (Intel GPU) | mlx-whisper (Apple Silicon Metal)
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

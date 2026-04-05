"""Dagster configuration for media ingest pipeline."""

from dagster import Config


class MediaIngestConfig(Config):
    """Runtime configuration for media file processing."""

    metube_path: str = "/data/metube"
    tubesync_path: str = "/data/tubesync"
    extensions: str = ".mp4,.mkv,.webm,.mp3,.m4a,.wav,.flac"
    whisper_backend: str = "openvino"  # "faster-whisper" or "openvino"
    whisper_model: str = "large-v3"
    whisper_device: str = "auto"
    whisper_compute_type: str = "int8"
    # OpenVINO-specific: HuggingFace model ID for pre-converted OV models
    openvino_model_id: str = "OpenVINO/whisper-large-v3-fp16-ov"
    openvino_device: str = "GPU"  # "CPU" or "GPU"
    enable_diarization: bool = True
    hf_token: str = ""  # Falls back to HF_TOKEN env var

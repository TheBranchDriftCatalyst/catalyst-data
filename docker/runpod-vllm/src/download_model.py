"""Download HuggingFace models at Docker build time.

Supports single-model mode (via MODEL_NAME env var) and --all mode
which downloads the full catalyst model set for RunPod deployment.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

# All models used across catalyst-data extraction pipelines
ALL_MODELS = [
    "google/gemma-4-31B-it",
    "google/gemma-4-26B-A4B-it",
    "Qwen/Qwen3-30B-A3B-Instruct-2507",
    "dphn/dolphin-mistral-24b-venice-edition",
]

# Safetensors preferred, fall back to bin/pt
WEIGHT_PATTERNS = [
    "*.safetensors",
    "*.safetensors.index.json",
]

# Always download these alongside weights
ALWAYS_PATTERNS = [
    "*.json",
    "*.txt",
    "*.model",
    "tokenizer*",
    "*.tiktoken",
]


def download_model(
    model_name: str,
    revision: str | None = None,
    cache_dir: str | None = None,
) -> str:
    """Download a single model to the cache directory."""
    cache_dir = cache_dir or os.environ.get("HF_HOME", "/runpod-volume/huggingface-cache/hub")

    print(f"Downloading: {model_name} (revision={revision or 'main'})")
    print(f"Cache dir: {cache_dir}")

    allow_patterns = WEIGHT_PATTERNS + ALWAYS_PATTERNS

    path = snapshot_download(
        model_name,
        revision=revision or None,
        cache_dir=cache_dir,
        allow_patterns=allow_patterns,
        ignore_patterns=["*.bin", "*.pt", "*.gguf", "*.onnx"],
    )

    print(f"Downloaded to: {path}")
    return path


def main():
    parser = argparse.ArgumentParser(description="Download HF models for RunPod")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Download all catalyst models",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override MODEL_NAME env var",
    )
    args = parser.parse_args()

    base_path = os.environ.get("BASE_PATH", "/runpod-volume")
    cache_dir = os.environ.get("HF_HOME", f"{base_path}/huggingface-cache/hub")
    revision = os.environ.get("MODEL_REVISION", None)

    downloaded = []

    if args.all:
        for model in ALL_MODELS:
            try:
                path = download_model(model, cache_dir=cache_dir)
                downloaded.append({"model": model, "path": path})
            except Exception as e:
                print(f"ERROR downloading {model}: {e}", file=sys.stderr)
                sys.exit(1)
    else:
        model_name = args.model or os.environ.get("MODEL_NAME", "")
        if not model_name:
            print("ERROR: No model specified (set MODEL_NAME or use --all)")
            sys.exit(1)
        path = download_model(model_name, revision=revision, cache_dir=cache_dir)
        downloaded.append({"model": model_name, "path": path})

    # Write manifest so handler knows where models live
    manifest_path = Path("/local_model_args.json")
    manifest_path.write_text(json.dumps(downloaded, indent=2))
    print(f"\nManifest written to {manifest_path}")
    print(f"Total models downloaded: {len(downloaded)}")


if __name__ == "__main__":
    main()

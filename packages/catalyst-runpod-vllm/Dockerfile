# RunPod vLLM serverless worker.
# Models download at runtime via RunPod's network volume cache — no baking.
#
# Build:
#   docker build -t ghcr.io/thebranchdriftcatalyst/catalyst-runpod-vllm:latest .
#
# RunPod endpoint env vars (set in RunPod UI):
#   MODEL_NAME=google/gemma-4-e4b-it
#   HF_TOKEN=hf_xxx (for gated models)
#   TENSOR_PARALLEL_SIZE=1
#   MAX_MODEL_LEN=8192

FROM nvidia/cuda:12.9.1-base-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive

# System deps (gcc required for Triton JIT kernel compilation)
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        python3-pip \
        python3-dev \
        gcc \
        curl \
    && rm -rf /var/lib/apt/lists/*

# CUDA compat for RunPod's heterogeneous GPU fleet
RUN ldconfig /usr/local/cuda-12.9/compat/

# vLLM + FlashInfer attention backend
RUN pip install --no-cache-dir --break-system-packages \
        "vllm[flashinfer]" \
        --extra-index-url https://download.pytorch.org/whl/cu129

# Upgrade transformers for gemma4 architecture support
RUN pip install --no-cache-dir --break-system-packages \
        "transformers>=4.53.0" \
        runpod>=1.7.0 \
        huggingface_hub[hf_transfer] \
        hf_transfer \
        tokenizers

# Runtime env — models come from RunPod network volume
ENV BASE_PATH="/runpod-volume" \
    HF_HOME="/runpod-volume/huggingface-cache/hub" \
    HUGGINGFACE_HUB_CACHE="/runpod-volume/huggingface-cache/hub" \
    HF_HUB_ENABLE_HF_TRANSFER=1 \
    MAX_CONCURRENCY=30 \
    GPU_MEMORY_UTILIZATION=0.95 \
    MAX_MODEL_LEN=8192 \
    DTYPE=auto \
    RAY_METRICS_EXPORT_ENABLED=0 \
    TOKENIZERS_PARALLELISM=false \
    PYTHONPATH="/"

COPY src/ /src/

CMD ["python3", "/src/handler.py"]

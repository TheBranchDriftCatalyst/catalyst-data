# Custom RunPod vLLM worker with pre-loaded models.
#
# Build modes:
#   Single model (baked):
#     docker build --build-arg MODEL_NAME="google/gemma-4-31B-it" \
#       --secret id=HF_TOKEN -t ghcr.io/thebranchdriftcatalyst/runpod-vllm:gemma4-31b .
#
#   All models (full image):
#     docker build --build-arg DOWNLOAD_ALL=true \
#       --secret id=HF_TOKEN -t ghcr.io/thebranchdriftcatalyst/runpod-vllm:full .
#
#   No model (runtime download via RunPod cache):
#     docker build -t ghcr.io/thebranchdriftcatalyst/runpod-vllm:base .

FROM nvidia/cuda:12.9.1-base-ubuntu24.04

# Avoid interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive

# System dependencies
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        python3-pip \
        python3-dev \
        git \
        curl \
        && rm -rf /var/lib/apt/lists/*

# CUDA compat for RunPod's heterogeneous GPU fleet
RUN ldconfig /usr/local/cuda-12.9/compat/

# Install vLLM with FlashInfer attention backend
RUN python3 -m pip install --no-cache-dir --upgrade pip && \
    python3 -m pip install --no-cache-dir \
        "vllm==0.9.1" \
        --extra-index-url https://download.pytorch.org/whl/cu129

# RunPod SDK + HuggingFace transfer acceleration
RUN python3 -m pip install --no-cache-dir \
        runpod>=1.7.0 \
        huggingface_hub[hf_transfer] \
        hf_transfer \
        tokenizers

# ── Build args ──────────────────────────────────────────────────────
ARG MODEL_NAME=""
ARG MODEL_REVISION=""
ARG TOKENIZER_NAME=""
ARG TOKENIZER_REVISION=""
ARG BASE_PATH="/runpod-volume"
ARG QUANTIZATION=""
ARG DOWNLOAD_ALL="false"

# ── Environment ─────────────────────────────────────────────────────
ENV MODEL_NAME=${MODEL_NAME} \
    MODEL_REVISION=${MODEL_REVISION} \
    TOKENIZER_NAME=${TOKENIZER_NAME} \
    TOKENIZER_REVISION=${TOKENIZER_REVISION} \
    BASE_PATH=${BASE_PATH} \
    QUANTIZATION=${QUANTIZATION} \
    HF_HOME="${BASE_PATH}/huggingface-cache/hub" \
    HUGGINGFACE_HUB_CACHE="${BASE_PATH}/huggingface-cache/hub" \
    HF_HUB_ENABLE_HF_TRANSFER=1 \
    TRANSFORMERS_OFFLINE=0 \
    VLLM_ATTENTION_BACKEND=FLASHINFER \
    # RunPod vLLM defaults
    MAX_CONCURRENCY=30 \
    GPU_MEMORY_UTILIZATION=0.95 \
    MAX_MODEL_LEN=8192 \
    DTYPE=auto \
    # Disable noisy telemetry
    RAY_METRICS_EXPORT_ENABLED=0 \
    TOKENIZERS_PARALLELISM=false \
    PYTHONPATH="/"

# ── Source files ────────────────────────────────────────────────────
COPY src/ /src/

# ── Model download at build time ────────────────────────────────────
# Uses BuildKit secrets to avoid baking HF_TOKEN into image layers.
# Gemma 4 models require authentication (gated).
RUN --mount=type=secret,id=HF_TOKEN,required=false \
    if [ -f /run/secrets/HF_TOKEN ]; then \
        export HF_TOKEN=$(cat /run/secrets/HF_TOKEN); \
    fi && \
    if [ "${DOWNLOAD_ALL}" = "true" ]; then \
        echo "Downloading ALL models..." && \
        python3 /src/download_model.py --all; \
    elif [ -n "${MODEL_NAME}" ]; then \
        echo "Downloading model: ${MODEL_NAME}" && \
        python3 /src/download_model.py; \
    else \
        echo "No MODEL_NAME set — models will download at runtime."; \
    fi

# Lock out network model downloads at runtime (if baked)
# Uncomment for strict offline mode:
# ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1

CMD ["python3", "/src/handler.py"]

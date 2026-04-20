# RunPod vLLM serverless worker for Gemma 4.
# Uses vllm/vllm-openai:gemma4 base (includes per-layer attention fix).
# Models download at runtime via RunPod's network volume cache — no baking.
#
# Build:
#   docker build -t ghcr.io/thebranchdriftcatalyst/catalyst-runpod-vllm:latest .
#
# RunPod endpoint env vars (set in RunPod UI):
#   MODEL_NAME=google/gemma-4-e4b-it
#   HF_TOKEN=hf_xxx (for gated models)

FROM vllm/vllm-openai:gemma4

# RunPod SDK + HF transfer acceleration
RUN pip install --no-cache-dir \
        runpod>=1.7.0 \
        huggingface_hub[hf_transfer] \
        hf_transfer

# Runtime env — models come from RunPod network volume
ENV BASE_PATH="/runpod-volume" \
    HF_HOME="/runpod-volume/huggingface-cache/hub" \
    HUGGINGFACE_HUB_CACHE="/runpod-volume/huggingface-cache/hub" \
    HF_HUB_ENABLE_HF_TRANSFER=1 \
    MAX_CONCURRENCY=30 \
    GPU_MEMORY_UTILIZATION=0.90 \
    MAX_MODEL_LEN=32768 \
    DTYPE=bfloat16 \
    LIMIT_MM_PER_PROMPT="image=0,audio=0" \
    RAY_METRICS_EXPORT_ENABLED=0 \
    TOKENIZERS_PARALLELISM=false \
    PYTHONPATH="/"

COPY src/ /src/

CMD ["python3", "/src/handler.py"]

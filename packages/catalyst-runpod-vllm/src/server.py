"""Start vLLM's OpenAI-compatible server for RunPod serverless.

RunPod routes /openai/v1/* requests directly to port 8000 when it detects
an HTTP server. This script starts vLLM's built-in API server with config
from environment variables, giving us a proper OpenAI-compatible endpoint
with structured output / function calling support.
"""

import os
import subprocess
import sys

MODEL_NAME = os.environ.get("MODEL_NAME", "google/gemma-4-31B-it")
DTYPE = os.environ.get("DTYPE", "bfloat16")
MAX_MODEL_LEN = os.environ.get("MAX_MODEL_LEN", "32768")
GPU_MEMORY_UTILIZATION = os.environ.get("GPU_MEMORY_UTILIZATION", "0.90")
TENSOR_PARALLEL_SIZE = os.environ.get("TENSOR_PARALLEL_SIZE", "1")
QUANTIZATION = os.environ.get("QUANTIZATION", "")
TRUST_REMOTE_CODE = os.environ.get("TRUST_REMOTE_CODE", "true")
ENFORCE_EAGER = os.environ.get("ENFORCE_EAGER", "false")
LIMIT_MM_PER_PROMPT = os.environ.get("LIMIT_MM_PER_PROMPT", "image=0,audio=0")
HOST = os.environ.get("VLLM_HOST", "0.0.0.0")
PORT = os.environ.get("VLLM_PORT", "8000")

cmd = [
    sys.executable,
    "-m",
    "vllm.entrypoints.openai.api_server",
    "--model",
    MODEL_NAME,
    "--dtype",
    DTYPE,
    "--max-model-len",
    MAX_MODEL_LEN,
    "--gpu-memory-utilization",
    GPU_MEMORY_UTILIZATION,
    "--tensor-parallel-size",
    TENSOR_PARALLEL_SIZE,
    "--trust-remote-code",
    "--host",
    HOST,
    "--port",
    PORT,
]

if QUANTIZATION:
    cmd.extend(["--quantization", QUANTIZATION])

if ENFORCE_EAGER.lower() == "true":
    cmd.append("--enforce-eager")

if LIMIT_MM_PER_PROMPT:
    cmd.extend(["--limit-mm-per-prompt", LIMIT_MM_PER_PROMPT])

# Disable vLLM's API key auth (RunPod handles auth at the proxy layer)
cmd.extend(["--api-key", ""])

print(f"Starting vLLM OpenAI server: {' '.join(cmd)}", flush=True)
sys.exit(subprocess.call(cmd))

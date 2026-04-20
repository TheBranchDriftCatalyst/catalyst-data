"""RunPod serverless handler for vLLM inference.

Models are resolved from MODEL_NAME env var and downloaded at cold-start
via RunPod's network volume cache (HF_HOME points to /runpod-volume/).
"""

import logging
import os
import sys
import time

import runpod
from transformers import AutoTokenizer
from vllm import AsyncLLMEngine
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.sampling_params import SamplingParams

# Structured logging setup
logging.basicConfig(
    level=logging.DEBUG if os.environ.get("DEBUG", "").lower() in ("1", "true") else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("catalyst-vllm")


def _log_env_config():
    """Log all relevant configuration at startup for debugging."""
    keys = [
        "MODEL_NAME",
        "TOKENIZER_NAME",
        "DTYPE",
        "MAX_MODEL_LEN",
        "GPU_MEMORY_UTILIZATION",
        "TENSOR_PARALLEL_SIZE",
        "QUANTIZATION",
        "TRUST_REMOTE_CODE",
        "ENFORCE_EAGER",
        "MAX_CONCURRENCY",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "HF_HUB_ENABLE_HF_TRANSFER",
        "BASE_PATH",
    ]
    log.info("=== Startup Configuration ===")
    for key in keys:
        val = os.environ.get(key, "<not set>")
        # Mask tokens
        if "TOKEN" in key and val != "<not set>":
            val = val[:4] + "***"
        log.info("  %s = %s", key, val)
    log.info("=============================")


def _build_engine() -> AsyncLLMEngine:
    """Initialize vLLM async engine from environment variables."""
    model = os.environ.get("MODEL_NAME", "")
    if not model:
        log.critical("MODEL_NAME env var is not set — cannot start")
        raise RuntimeError("MODEL_NAME env var is required")

    log.info("Initializing vLLM engine for model: %s", model)

    # Parse limit_mm_per_prompt (e.g. "image=0,audio=0" -> {"image": 0, "audio": 0})
    limit_mm = {}
    limit_mm_str = os.environ.get("LIMIT_MM_PER_PROMPT", "")
    if limit_mm_str:
        for pair in limit_mm_str.split(","):
            k, v = pair.strip().split("=")
            limit_mm[k.strip()] = int(v.strip())

    engine_args = AsyncEngineArgs(
        model=model,
        tokenizer=os.environ.get("TOKENIZER_NAME") or None,
        dtype=os.environ.get("DTYPE", "auto"),
        max_model_len=int(os.environ.get("MAX_MODEL_LEN", "32768")),
        gpu_memory_utilization=float(os.environ.get("GPU_MEMORY_UTILIZATION", "0.90")),
        tensor_parallel_size=int(os.environ.get("TENSOR_PARALLEL_SIZE", "1")),
        quantization=os.environ.get("QUANTIZATION") or None,
        trust_remote_code=os.environ.get("TRUST_REMOTE_CODE", "true").lower() == "true",
        enforce_eager=os.environ.get("ENFORCE_EAGER", "false").lower() == "true",
        limit_mm_per_prompt=limit_mm or None,
    )

    log.debug("AsyncEngineArgs: %s", engine_args)

    t0 = time.time()
    try:
        eng = AsyncLLMEngine.from_engine_args(engine_args)
    except Exception:
        log.exception("Failed to initialize vLLM engine")
        raise
    elapsed = time.time() - t0
    log.info("Engine initialized in %.1fs", elapsed)
    return eng


_log_env_config()

log.info("--- Starting engine cold-start (model download + load) ---")
t_cold = time.time()
engine = _build_engine()
log.info("--- Cold-start complete (%.1fs total) ---", time.time() - t_cold)

# Load tokenizer for chat template application
_model_name = os.environ.get("MODEL_NAME", "")
tokenizer = AutoTokenizer.from_pretrained(_model_name, trust_remote_code=True)
log.info("Tokenizer loaded: %s (chat_template=%s)", _model_name, "yes" if tokenizer.chat_template else "no")


async def handler(job):
    """Process a RunPod serverless job.

    Accepts both raw completion requests and OpenAI-compatible chat format.
    Streams results back via async generator.
    """
    job_id = job["id"]
    job_input = job["input"]
    t_start = time.time()

    log.info("[%s] Job received | stream=%s", job_id, job_input.get("stream", False))
    log.debug("[%s] Full input: %s", job_id, {k: v for k, v in job_input.items() if k != "messages"})

    # Apply chat template — always wrap into proper turn format
    if "messages" in job_input:
        messages = job_input["messages"]
        log.info("[%s] Chat format | %d messages", job_id, len(messages))
    else:
        # Wrap raw prompt as a user message so the chat template is applied
        raw_prompt = job_input.get("prompt", "")
        messages = [{"role": "user", "content": raw_prompt}] if raw_prompt else []
        log.info("[%s] Completion format (wrapped) | raw_len=%d", job_id, len(raw_prompt))

    if not messages:
        log.warning("[%s] Empty prompt — returning error", job_id)
        yield {"error": "No prompt or messages provided"}
        return

    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    log.info("[%s] Templated prompt_len=%d", job_id, len(prompt))

    sampling_params = SamplingParams(
        temperature=job_input.get("temperature", 0.7),
        top_p=job_input.get("top_p", 0.95),
        top_k=job_input.get("top_k", -1),
        max_tokens=job_input.get("max_tokens", 2048),
        stop=job_input.get("stop", None),
        presence_penalty=job_input.get("presence_penalty", 0.0),
        frequency_penalty=job_input.get("frequency_penalty", 0.0),
    )
    log.debug(
        "[%s] SamplingParams: temp=%.2f top_p=%.2f max_tokens=%d",
        job_id,
        sampling_params.temperature,
        sampling_params.top_p,
        sampling_params.max_tokens,
    )

    try:
        results_generator = engine.generate(prompt, sampling_params, job_id)
    except Exception:
        log.exception("[%s] engine.generate() failed", job_id)
        yield {"error": "Engine generation failed — check worker logs"}
        return

    full_output = ""
    token_count = 0
    async for request_output in results_generator:
        text = request_output.outputs[0].text
        new_text = text[len(full_output) :]
        full_output = text
        token_count = len(request_output.outputs[0].token_ids)

        if job_input.get("stream", False):
            yield {"text": new_text, "finished": request_output.finished}

    elapsed = time.time() - t_start
    prompt_tokens = len(request_output.prompt_token_ids)
    log.info(
        "[%s] Complete | %d prompt + %d completion tokens | %.2fs | %.1f tok/s",
        job_id,
        prompt_tokens,
        token_count,
        elapsed,
        token_count / elapsed if elapsed > 0 else 0,
    )

    if not job_input.get("stream", False):
        yield {
            "text": full_output,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": token_count,
                "total_tokens": prompt_tokens + token_count,
            },
            "finish_reason": request_output.outputs[0].finish_reason,
        }


log.info("Starting RunPod serverless loop (max_concurrency=%s)", os.environ.get("MAX_CONCURRENCY", "30"))

runpod.serverless.start(
    {
        "handler": handler,
        "concurrency_modifier": lambda x: int(os.environ.get("MAX_CONCURRENCY", "30")),
        "return_aggregate_stream": True,
    }
)

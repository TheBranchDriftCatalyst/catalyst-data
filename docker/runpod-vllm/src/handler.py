"""RunPod serverless handler for vLLM inference.

Exposes both the RunPod job handler interface and an OpenAI-compatible
API endpoint. Supports concurrent requests via vLLM's async engine.
"""

import json
import os
from pathlib import Path

import runpod
from vllm import AsyncLLMEngine
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.sampling_params import SamplingParams


def _resolve_model_name() -> str:
    """Resolve which model to serve."""
    # Check if model was baked at build time
    manifest = Path("/local_model_args.json")
    if manifest.exists():
        models = json.loads(manifest.read_text())
        if models:
            # Use first model from manifest (single-model builds)
            return models[0]["path"]

    # Fall back to env var (runtime download mode)
    model_name = os.environ.get("MODEL_NAME", "")
    if not model_name:
        raise RuntimeError(
            "No model available: set MODEL_NAME env var or bake model at build time"
        )
    return model_name


def _build_engine() -> AsyncLLMEngine:
    """Initialize vLLM async engine with env-var-driven configuration."""
    model = _resolve_model_name()

    engine_args = AsyncEngineArgs(
        model=model,
        tokenizer=os.environ.get("TOKENIZER_NAME") or None,
        dtype=os.environ.get("DTYPE", "auto"),
        max_model_len=int(os.environ.get("MAX_MODEL_LEN", "8192")),
        gpu_memory_utilization=float(os.environ.get("GPU_MEMORY_UTILIZATION", "0.95")),
        tensor_parallel_size=int(os.environ.get("TENSOR_PARALLEL_SIZE", "1")),
        quantization=os.environ.get("QUANTIZATION") or None,
        trust_remote_code=os.environ.get("TRUST_REMOTE_CODE", "true").lower() == "true",
        enforce_eager=os.environ.get("ENFORCE_EAGER", "false").lower() == "true",
    )

    return AsyncLLMEngine.from_engine_args(engine_args)


# Initialize engine at module level (runs once per cold start)
engine = _build_engine()


async def handler(job):
    """Process a RunPod serverless job.

    Accepts both raw completion requests and OpenAI-compatible chat format.
    Streams results back via async generator.
    """
    job_input = job["input"]

    # Support OpenAI chat format
    if "messages" in job_input:
        # Format messages into a single prompt (model-specific formatting
        # is handled by the tokenizer's chat template)

        prompt = job_input.get("prompt")
        if not prompt:
            # Let vLLM handle chat template application
            messages = job_input["messages"]
            # Simple fallback: concatenate messages
            prompt = "\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')}"
                for m in messages
            )
    else:
        prompt = job_input.get("prompt", "")

    if not prompt:
        yield {"error": "No prompt or messages provided"}
        return

    # Build sampling params from input
    sampling_params = SamplingParams(
        temperature=job_input.get("temperature", 0.7),
        top_p=job_input.get("top_p", 0.95),
        top_k=job_input.get("top_k", -1),
        max_tokens=job_input.get("max_tokens", 2048),
        stop=job_input.get("stop", None),
        presence_penalty=job_input.get("presence_penalty", 0.0),
        frequency_penalty=job_input.get("frequency_penalty", 0.0),
    )

    # Generate with streaming
    request_id = job["id"]
    results_generator = engine.generate(prompt, sampling_params, request_id)

    full_output = ""
    async for request_output in results_generator:
        # Get the latest token(s)
        text = request_output.outputs[0].text
        new_text = text[len(full_output):]
        full_output = text

        if job_input.get("stream", False):
            yield {"text": new_text, "finished": request_output.finished}

    if not job_input.get("stream", False):
        yield {
            "text": full_output,
            "usage": {
                "prompt_tokens": len(request_output.prompt_token_ids),
                "completion_tokens": len(request_output.outputs[0].token_ids),
                "total_tokens": len(request_output.prompt_token_ids)
                + len(request_output.outputs[0].token_ids),
            },
            "finish_reason": request_output.outputs[0].finish_reason,
        }


# RunPod serverless entrypoint
runpod.serverless.start(
    {
        "handler": handler,
        "concurrency_modifier": lambda x: int(
            os.environ.get("MAX_CONCURRENCY", "30")
        ),
        "return_aggregate_stream": True,
    }
)

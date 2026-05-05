#!/usr/bin/env python3
"""Unsloth fine-tuning stub — consumes SFT/DPO JSONL emitted by Phase 3.

This is the local-dev v1 of the fine-tuning consumer (CD-sduv). It wraps
Unsloth's ``FastLanguageModel`` + TRL's ``SFTTrainer`` / ``DPOTrainer`` to
fine-tune a base model on the JSONL produced by the Dagster training
assets (``packages/media-ingest/src/media_ingest/assets/training.py``).

Workflow:
    # 1. Pull the latest dataset from S3 (or cluster MinIO via port-forward)
    python scripts/training/pull_training_dataset.py --kind sft --output ./sft.jsonl

    # 2. Fine-tune (this script) — needs a CUDA GPU; CPU works for the
    #    smallest base models but is impractically slow.
    python scripts/training/unsloth_finetune.py \\
        --kind sft \\
        --dataset ./sft.jsonl \\
        --base-model unsloth/llama-3.2-1b-Instruct-bnb-4bit \\
        --output ./adapters/sft-media

    # 3. (Optional) push adapter to S3 for cluster reuse:
    aws s3 cp --recursive ./adapters/sft-media \\
        s3://dagster/bench/training/adapters/sft-media/

The script is a STUB — it imports Unsloth lazily so `--help` works on
dev boxes without GPU dependencies installed. Install via:

    uv pip install -e '.[unsloth]'

(see CD-sduv for the optional-dependencies group).

Out of scope here: reward-model training (DPO uses preference pairs
directly), cluster-side fine-tuning (separate workstream).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _format_sft_row(row: dict[str, Any]) -> dict[str, str]:
    """Map an SFT JSONL row → {prompt, completion} for SFTTrainer.

    The training asset emits per-chunk rows with ``chunk_text`` + accepted
    ``mentions`` + ``propositions``. We render the completion as a structured
    JSON the model is asked to extract.
    """
    chunk_text = row.get("chunk_text", "")
    mentions = row.get("mentions", []) or []
    propositions = row.get("propositions", []) or []
    completion = json.dumps(
        {"mentions": mentions, "propositions": propositions},
        ensure_ascii=False,
    )
    prompt = (
        "Extract entities and relations from the following text. "
        "Return strict JSON with two keys: 'mentions' and 'propositions'.\n\n"
        f"Text:\n{chunk_text}\n\nJSON:"
    )
    return {"prompt": prompt, "completion": completion}


def _format_dpo_row(row: dict[str, Any]) -> dict[str, str]:
    """Map a DPO JSONL row → {prompt, chosen, rejected} for DPOTrainer."""
    chunk_text = row.get("chunk_text", "")
    preferred = row.get("preferred", {}).get("extraction", {})
    rejected_obj = row.get("rejected", {}).get("extraction", {})
    prompt = (
        "Extract entities and relations from the following text. "
        "Return strict JSON.\n\nText:\n" + chunk_text + "\n\nJSON:"
    )
    return {
        "prompt": prompt,
        "chosen": json.dumps(preferred, ensure_ascii=False),
        "rejected": json.dumps(rejected_obj, ensure_ascii=False),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fine-tune a base model on Phase-3 SFT/DPO JSONL via Unsloth + TRL.",
    )
    parser.add_argument("--kind", choices=["sft", "dpo"], required=True)
    parser.add_argument("--dataset", type=Path, required=True, help="Path to JSONL file.")
    parser.add_argument(
        "--base-model",
        default="unsloth/llama-3.2-1b-Instruct-bnb-4bit",
        help="HuggingFace model id (Unsloth pre-quantized recommended).",
    )
    parser.add_argument("--output", type=Path, default=Path("./adapters/run"))
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--max-seq-len", type=int, default=2048)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print formatted rows + plan without loading the model.",
    )
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"ERROR: dataset not found: {args.dataset}", file=sys.stderr)
        return 2

    rows = _load_jsonl(args.dataset)
    formatter = _format_sft_row if args.kind == "sft" else _format_dpo_row
    formatted = [formatter(r) for r in rows]
    print(f"Loaded {len(formatted)} {args.kind.upper()} rows from {args.dataset}")

    if args.dry_run:
        if formatted:
            print("First formatted row:")
            print(json.dumps(formatted[0], indent=2, ensure_ascii=False))
        print(f"Plan: {args.kind.upper()} fine-tune {args.base_model} → {args.output}")
        print("(--dry-run: skipping model load + training)")
        return 0

    # ── Heavy imports gated behind --dry-run so the script's --help works
    # without GPU dependencies installed.
    try:
        from datasets import Dataset  # type: ignore[import-not-found]
        from trl import DPOTrainer, SFTTrainer  # type: ignore[import-not-found]
        from unsloth import FastLanguageModel  # type: ignore[import-not-found]
    except ImportError as exc:
        print(
            f"ERROR: missing fine-tuning dependencies ({exc}). "
            "Install with: uv pip install -e '.[unsloth]' (see CD-sduv).",
            file=sys.stderr,
        )
        return 3

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.base_model,
        max_seq_length=args.max_seq_len,
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        lora_alpha=16,
    )

    dataset = Dataset.from_list(formatted)

    if args.kind == "sft":
        trainer = SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=dataset,
            dataset_text_field=None,
            max_seq_length=args.max_seq_len,
            packing=False,
            args={
                "output_dir": str(args.output),
                "per_device_train_batch_size": args.batch_size,
                "num_train_epochs": args.epochs,
            },
        )
    else:
        trainer = DPOTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=dataset,
            args={
                "output_dir": str(args.output),
                "per_device_train_batch_size": args.batch_size,
                "num_train_epochs": args.epochs,
            },
        )

    trainer.train()
    args.output.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(args.output))
    tokenizer.save_pretrained(str(args.output))
    print(f"Adapter saved to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

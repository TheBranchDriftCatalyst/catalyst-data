#!/usr/bin/env python3
"""Off-cluster training-dataset puller — copies the latest SFT/DPO JSONL
from S3 to a local path so a GPU box can fine-tune without re-running the
Dagster asset.

Usage:
    python scripts/pull_training_dataset.py --kind sft
    python scripts/pull_training_dataset.py --kind dpo --domain media_ingest
    python scripts/pull_training_dataset.py --kind sft --output ~/datasets/sft.jsonl

S3 keys (matches packages/media-ingest/src/media_ingest/assets/training.py):
    bench/training/sft/<domain>/data.jsonl
    bench/training/sft/all/data.jsonl
    bench/training/dpo/<domain>/data.jsonl

Reads ``DAGSTER_S3_*`` env vars for the endpoint/bucket; defaults match
the dev-mode Tilt-managed MinIO container at localhost:9000. Use
``DAGSTER_S3_ENDPOINT_URL`` to point at the cluster Tenant via a
port-forward when pulling from prod data.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _build_client():
    # Import lazily so `--help` works without boto3 installed.
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("DAGSTER_S3_ENDPOINT_URL", "http://localhost:9000"),
        aws_access_key_id=os.environ.get("DAGSTER_S3_ACCESS_KEY", "minio"),
        aws_secret_access_key=os.environ.get("DAGSTER_S3_SECRET_KEY", "minio123"),
        region_name="us-east-1",
        config=Config(signature_version="s3v4"),
    )


def _resolve_key(kind: str, domain: str) -> str:
    if kind not in ("sft", "dpo"):
        raise SystemExit(f"--kind must be sft or dpo, got: {kind}")
    return f"bench/training/{kind}/{domain}/data.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pull the latest SFT/DPO training JSONL from S3 to a local path.",
    )
    parser.add_argument("--kind", choices=["sft", "dpo"], required=True)
    parser.add_argument(
        "--domain",
        default="all",
        help="Domain (e.g. media_ingest, congress_data, open_leaks) or 'all' for the union (sft only).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path. Default: ./<kind>-<domain>.jsonl in the current directory.",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("DAGSTER_S3_BUCKET", "dagster"),
    )
    args = parser.parse_args()

    if args.kind == "dpo" and args.domain == "all":
        # No union for DPO yet — preference pairs are per-domain.
        args.domain = "media_ingest"

    key = _resolve_key(args.kind, args.domain)
    output = args.output or Path(f"./{args.kind}-{args.domain}.jsonl")

    print(f"Source : s3://{args.bucket}/{key}")
    print(f"Target : {output}")

    client = _build_client()
    try:
        resp = client.get_object(Bucket=args.bucket, Key=key)
    except Exception as e:
        print(f"ERROR: could not fetch {key} from {args.bucket}: {e}", file=sys.stderr)
        return 2

    data = resp["Body"].read()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    line_count = data.count(b"\n")
    print(f"Wrote  : {len(data):,} bytes · {line_count:,} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Asset factory for gold-layer extraction pipelines.

Generates standardized mention, assertion, and embedding assets from a
PipelineConfig, replacing duplicated boilerplate across code locations.

Usage::

    from dagster_io.asset_factory import PipelineConfig, extraction_assets

    media_gold = extraction_assets(PipelineConfig(
        domain="media",
        code_location="media_ingest",
        chunks_asset_key="media_chunks",
        group_name="media_ingest",
        partitions_def=media_partitions,
    ))
"""

import time
from dataclasses import dataclass
from typing import Any

from dagster import (
    AssetExecutionContext,
    AssetIn,
    AssetKey,
    AssetOut,
    AssetsDefinition,
    Output,
    PartitionsDefinition,
    asset,
    multi_asset,
)

from dagster_io.asset_factories import LLM_ASSET_K8S_CONFIG
from dagster_io.extraction import extract_validated
from dagster_io.llm import EmbeddingResource
from dagster_io.logging import get_logger
from dagster_io.metrics import ASSET_RECORDS_PROCESSED

logger = get_logger(__name__)

EMBEDDING_ASSET_K8S_CONFIG = {
    "dagster-k8s/config": {
        "container_config": {
            "resources": {
                "requests": {"cpu": "1", "memory": "4Gi"},
                "limits": {"cpu": "2", "memory": "8Gi"},
            }
        }
    }
}


@dataclass
class PipelineConfig:
    """Configuration for generating extraction pipeline assets for a domain."""

    domain: str  # "media", "bill", "leak", "member"
    code_location: str  # "media_ingest", "congress_data", "open_leaks"
    chunks_asset_key: str  # e.g. "media_chunks", "bill_chunks"
    asset_prefix: str | None = None  # defaults to domain
    group_name: str | None = None  # Dagster asset group
    partitions_def: PartitionsDefinition | None = None  # for partitioned assets
    prompt_dir: str | None = None  # override PROMPT_REGISTRY_DIR
    skip_assertions: bool = False  # e.g. member_tail has no assertions
    skip_embeddings: bool = False


def extraction_assets(config: PipelineConfig) -> list[AssetsDefinition]:
    """Generate gold-layer extraction assets for a domain.

    Produces up to 3 assets:

    1. ``{prefix}_mentions`` + ``{prefix}_assertions`` — a single ``@multi_asset``
       that calls ``extract_validated()`` once and yields both outputs.
    2. ``{prefix}_embeddings`` — an ``@asset`` using ``EmbeddingResource``.

    Skipping assertions or embeddings is controlled via ``PipelineConfig`` flags.
    """
    prefix = config.asset_prefix or config.domain
    assets: list[AssetsDefinition] = []

    # ── Mentions + Assertions (multi_asset) ──────────────────────────

    mention_out = f"{prefix}_mentions"
    assertion_out = f"{prefix}_assertions"

    outs: dict[str, AssetOut] = {
        mention_out: AssetOut(
            description=f"Entity mentions extracted from {config.domain} chunks via LLM",
            metadata={"layer": "gold"},
        ),
    }
    if not config.skip_assertions:
        outs[assertion_out] = AssetOut(
            description=f"Qualified assertions extracted from {config.domain} chunks via LLM",
            metadata={"layer": "gold"},
        )

    # Bind config into closure locals so each call site is independent
    _cfg = config
    _prefix = prefix
    _mention_out = mention_out
    _assertion_out = assertion_out

    @multi_asset(
        name=f"{_prefix}_extraction",
        outs=outs,
        ins={"chunks": AssetIn(key=AssetKey(_cfg.chunks_asset_key))},
        group_name=_cfg.group_name,
        compute_kind="llm",
        partitions_def=_cfg.partitions_def,
        op_tags=LLM_ASSET_K8S_CONFIG,
    )
    def _extraction_multi_asset(
        context: AssetExecutionContext,
        chunks: list,
    ):
        chunk_count = len(chunks)
        partition_key = context.partition_key if _cfg.partitions_def else None

        context.log.info(
            "Starting %s extraction: %d chunks, code_location=%s%s",
            _cfg.domain,
            chunk_count,
            _cfg.code_location,
            f", partition={partition_key}" if partition_key else "",
        )

        # ── Empty-chunks fast path
        if not chunks:
            mention_meta: dict[str, Any] = {"mention_count": 0, "chunk_count": 0}
            if partition_key:
                mention_meta["document_id"] = partition_key
            yield Output([], output_name=_mention_out, metadata=mention_meta)

            if not _cfg.skip_assertions:
                assertion_meta: dict[str, Any] = {
                    "assertion_count": 0,
                    "negated_count": 0,
                    "hedged_count": 0,
                    "chunk_count": 0,
                }
                if partition_key:
                    assertion_meta["document_id"] = partition_key
                yield Output([], output_name=_assertion_out, metadata=assertion_meta)
            return

        # ── Single extract_validated call produces both mentions and assertions
        llm_start = time.monotonic()
        all_mentions, all_assertions = extract_validated(
            chunks,
            code_location=_cfg.code_location,
            max_concurrency=5,
        )
        llm_elapsed = time.monotonic() - llm_start

        # ── Yield mentions
        ASSET_RECORDS_PROCESSED.labels(
            code_location=_cfg.code_location,
            asset_key=_mention_out,
            layer="gold",
        ).inc(len(all_mentions))

        mention_meta = {
            "mention_count": len(all_mentions),
            "chunk_count": chunk_count,
        }
        if partition_key:
            mention_meta["document_id"] = partition_key

        context.log.info(
            "%s extraction complete in %.1fs: %d mentions from %d chunks (%.1f mentions/chunk)",
            _cfg.domain,
            llm_elapsed,
            len(all_mentions),
            chunk_count,
            len(all_mentions) / max(chunk_count, 1),
        )
        yield Output(all_mentions, output_name=_mention_out, metadata=mention_meta)

        # ── Yield assertions
        if not _cfg.skip_assertions:
            negated_count = sum(1 for a in all_assertions if a.negated)
            hedged_count = sum(1 for a in all_assertions if a.hedged)

            ASSET_RECORDS_PROCESSED.labels(
                code_location=_cfg.code_location,
                asset_key=_assertion_out,
                layer="gold",
            ).inc(len(all_assertions))

            assertion_meta = {
                "assertion_count": len(all_assertions),
                "negated_count": negated_count,
                "hedged_count": hedged_count,
                "chunk_count": chunk_count,
            }
            if partition_key:
                assertion_meta["document_id"] = partition_key

            context.log.info(
                "%s assertions: %d total (negated=%d, hedged=%d)",
                _cfg.domain,
                len(all_assertions),
                negated_count,
                hedged_count,
            )
            yield Output(all_assertions, output_name=_assertion_out, metadata=assertion_meta)

    assets.append(_extraction_multi_asset)

    # ── Embeddings ───────────────────────────────────────────────────

    if not config.skip_embeddings:
        _embedding_key = f"{prefix}_embeddings"

        @asset(
            name=_embedding_key,
            ins={"chunks": AssetIn(key=AssetKey(_cfg.chunks_asset_key))},
            group_name=_cfg.group_name,
            description=f"Vector embeddings for {_cfg.domain} chunks",
            compute_kind="ml",
            metadata={"layer": "gold"},
            partitions_def=_cfg.partitions_def,
            op_tags=EMBEDDING_ASSET_K8S_CONFIG,
        )
        def _embedding_asset(
            context: AssetExecutionContext,
            embeddings: EmbeddingResource,
            chunks: list,
        ) -> Output[list[dict[str, Any]]]:
            chunk_count = len(chunks)
            partition_key = context.partition_key if _cfg.partitions_def else None

            context.log.info(
                "Starting %s embeddings: %d chunks, model=%s%s",
                _cfg.domain,
                chunk_count,
                embeddings.model,
                f", partition={partition_key}" if partition_key else "",
            )

            if not chunks:
                meta: dict[str, Any] = {"embedding_count": 0}
                if partition_key:
                    meta["document_id"] = partition_key
                return Output([], metadata=meta)

            texts = [chunk.text for chunk in chunks]
            embed_start = time.monotonic()
            vectors = embeddings.embed(texts)
            embed_elapsed = time.monotonic() - embed_start

            ASSET_RECORDS_PROCESSED.labels(
                code_location=_cfg.code_location,
                asset_key=_embedding_key,
                layer="gold",
            ).inc(len(vectors))

            embed_records = [
                {
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "embedding": vec,
                    "model": embeddings.model,
                    "dimensions": len(vec),
                }
                for chunk, vec in zip(chunks, vectors, strict=False)
            ]

            meta = {
                "embedding_count": len(embed_records),
                "model": embeddings.model,
                "dimensions": len(vectors[0]) if vectors else 0,
            }
            if partition_key:
                meta["document_id"] = partition_key

            context.log.info(
                "%s embeddings complete in %.1fs: %d vectors (%dd)",
                _cfg.domain,
                embed_elapsed,
                len(embed_records),
                len(vectors[0]) if vectors else 0,
            )
            return Output(embed_records, metadata=meta)

        assets.append(_embedding_asset)

    return assets

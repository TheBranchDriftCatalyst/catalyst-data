"""ExtractionResource — Dagster ConfigurableResource for extraction.

The primary consumer interface for catalyst-exgraph. Assets declare this
as a dependency and call extract_mentions() or extract_assertions().

Usage in code location __init__.py:
    resources = {
        "extraction": ExtractionResource(
            ner_model="gliner",
            spo_model="mistral:latest",
            prompt_dir="k8s/media-ingest/prompts",
        ),
    }

Usage in assets:
    @asset
    def media_mentions(media_chunks, extraction: ExtractionResource):
        result = extraction.extract_mentions(media_chunks, code_location="media_ingest")
        return result.mentions
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from dagster import ConfigurableResource

from catalyst_exgraph.config import StageConfig, ner_stage_config, spo_stage_config
from catalyst_exgraph.pipeline import build_pipeline, pipeline_result_to_legacy
from catalyst_exgraph.protocol import ExtractionResult
from catalyst_exgraph.state import ExGraphState

logger = logging.getLogger(__name__)


def _resolve_client(model: str, base_url: str | None = None, api_key: str | None = None):
    """Resolve model name to the appropriate extraction client.

    Centralizes client construction so all paths (v1, v2, benchmark, resource)
    go through one place. Explicitly passes base_url and api_key to avoid
    env var leakage from parent processes.
    """
    # GLiNER model name mapping
    GLINER_MODELS = {
        "gliner": "urchade/gliner_medium-v2.1",
        "gliner-medium": "urchade/gliner_medium-v2.1",
        "gliner-large": "urchade/gliner_large-v2.1",
        "gliner-small": "urchade/gliner_small-v2.1",
        "gliner-pii": "urchade/gliner_multi_pii-v1",
    }

    model_lower = model.lower()
    if "gliner" in model_lower:
        from catalyst_langgraph.clients.gliner import GLiNERClient

        hf_model = GLINER_MODELS.get(model_lower, GLINER_MODELS.get(model, "urchade/gliner_medium-v2.1"))
        return GLiNERClient(model_name=hf_model)
    elif "nuextract" in model_lower:
        from catalyst_langgraph.clients.nuextract import NuExtractClient

        return NuExtractClient(base_url=base_url, model=model)
    elif "universalner" in model_lower or "uniner" in model_lower:
        from catalyst_langgraph.clients.universalner import UniversalNERClient

        return UniversalNERClient(base_url=base_url, model=model)
    else:
        from catalyst_langgraph.clients.llm import LLMClient

        return LLMClient(
            model=model,
            base_url=base_url or os.environ.get("LLM_BASE_URL"),
            api_key=api_key or os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", "")),
        )


def _build_mcp_client():
    """Build a DirectMCPClient with real validators."""
    from catalyst_contracts.validators.mention_validator import validate_mentions
    from catalyst_contracts.validators.proposition_validator import validate_propositions
    from catalyst_langgraph.clients.mcp import DirectMCPClient

    class _ValidatorHandler:
        def validate_mentions(self, mentions, source_text, document_id):
            result = validate_mentions(mentions, source_text, document_id)
            return result.model_dump(mode="json")

        def validate_propositions(self, propositions, known_mention_ids, source_text):
            result = validate_propositions(propositions, set(known_mention_ids), source_text)
            return result.model_dump(mode="json")

    return DirectMCPClient(_ValidatorHandler())


class ExtractionResource(ConfigurableResource):
    """Dagster resource for LLM extraction with MCP validation.

    Configurable per code location with different models for NER vs SPO,
    optional ensemble, and domain-specific prompts.
    """

    ner_model: str = "gpt-4o-mini"
    """Model for NER/mention extraction (e.g. 'gliner', 'mistral:latest')."""

    spo_model: str = "gpt-4o-mini"
    """Model for SPO/proposition extraction (e.g. 'mistral:latest', 'llama3.1:8b')."""

    prompt_dir: str = ""
    """Directory containing .prompt files. Domain-specific prompts
    (e.g. 'k8s/media-ingest/prompts', 'k8s/congress-data/prompts')."""

    max_concurrency: int = 5
    """Max parallel chunk processing."""

    ner_max_retries: int = 3
    """Max repair cycles for NER. Set to 0 for encoder models (auto-detected)."""

    spo_max_retries: int = 3
    """Max repair cycles for SPO."""

    # Phase 4: Ensemble
    ner_ensemble: list[str] | None = None
    """List of model names for ensemble NER. None = single model."""

    spo_ensemble: list[str] | None = None
    """List of model names for ensemble SPO. None = single model."""

    def _is_encoder(self, model: str) -> bool:
        """Check if model is an encoder (no repair capability)."""
        return any(x in model.lower() for x in ("gliner", "nuextract", "universalner", "uniner"))

    def _build_ner_config(self) -> StageConfig:
        max_retries = 0 if self._is_encoder(self.ner_model) else self.ner_max_retries
        return ner_stage_config(
            model=self.ner_model,
            max_retries=max_retries,
            ensemble_models=self.ner_ensemble,
        )

    def _build_spo_config(self) -> StageConfig:
        return spo_stage_config(
            model=self.spo_model,
            max_retries=self.spo_max_retries,
        )

    def extract_mentions(
        self,
        chunks: list,
        code_location: str = "",
    ) -> ExtractionResult:
        """Extract entity mentions (NER) only.

        Args:
            chunks: List of TextChunk objects.
            code_location: For metrics labeling.

        Returns:
            ExtractionResult with .mentions populated.
        """
        ner_config = self._build_ner_config()
        if self.prompt_dir:
            # Frozen dataclass — rebuild with prompt_dir
            ner_config = StageConfig(**{**ner_config.__dict__, "prompt_dir": self.prompt_dir})

        client = _resolve_client(self.ner_model)
        mcp_client = _build_mcp_client()
        pipeline = build_pipeline([ner_config], client, mcp_client)

        return self._run_pipeline(pipeline, chunks, code_location, ner_only=True)

    def extract_assertions(
        self,
        chunks: list,
        accepted_mentions: list | None = None,
        code_location: str = "",
    ) -> ExtractionResult:
        """Extract propositions (SPO) only, using accepted mentions as context.

        Args:
            chunks: List of TextChunk objects.
            accepted_mentions: Pre-extracted mentions (from NER asset dep).
            code_location: For metrics labeling.

        Returns:
            ExtractionResult with .assertions populated.
        """
        spo_config = self._build_spo_config()
        if self.prompt_dir:
            spo_config = StageConfig(**{**spo_config.__dict__, "prompt_dir": self.prompt_dir})

        client = _resolve_client(self.spo_model)
        mcp_client = _build_mcp_client()
        pipeline = build_pipeline([spo_config], client, mcp_client)

        return self._run_pipeline(
            pipeline,
            chunks,
            code_location,
            upstream_mentions=accepted_mentions or [],
        )

    def extract_all(
        self,
        chunks: list,
        code_location: str = "",
    ) -> ExtractionResult:
        """Extract both NER + SPO in one call (backward compat).

        Returns ExtractionResult with both .mentions and .assertions.
        """
        ner_config = self._build_ner_config()
        spo_config = self._build_spo_config()
        if self.prompt_dir:
            ner_config = StageConfig(**{**ner_config.__dict__, "prompt_dir": self.prompt_dir})
            spo_config = StageConfig(**{**spo_config.__dict__, "prompt_dir": self.prompt_dir})

        ner_client = _resolve_client(self.ner_model)
        spo_client = _resolve_client(self.spo_model)
        clients = {"ner": ner_client, "spo": spo_client}
        mcp_client = _build_mcp_client()

        pipeline = build_pipeline([ner_config, spo_config], clients, mcp_client)
        return self._run_pipeline(pipeline, chunks, code_location)

    def _run_pipeline(
        self,
        pipeline,
        chunks: list,
        code_location: str,
        ner_only: bool = False,
        upstream_mentions: list | None = None,
    ) -> ExtractionResult:
        """Run the pipeline across all chunks with concurrency."""
        from dagster_io.models import Assertion, Mention, MentionType, Provenance

        if not chunks:
            return ExtractionResult()

        all_mentions = []
        all_assertions = []
        all_audit_events = []
        total_mention_retries = 0
        total_proposition_retries = 0
        errors = 0

        start = time.monotonic()

        def _run_chunk(chunk) -> dict:
            loop = asyncio.new_event_loop()
            try:
                state: ExGraphState = {
                    "raw_text": chunk.text,
                    "source_metadata": {
                        "document_id": chunk.document_id,
                        "chunk_id": chunk.chunk_id,
                    },
                    "stages": {},
                    "upstream_context": {},
                    "audit_events": [],
                    "status": "pending",
                }
                if upstream_mentions is not None:
                    state["upstream_context"] = {
                        "accepted_mentions": [
                            m.model_dump(mode="json") if hasattr(m, "model_dump") else m for m in upstream_mentions
                        ],
                    }
                result = loop.run_until_complete(pipeline.ainvoke(state))
                legacy = pipeline_result_to_legacy(result)
                # Attach chunk metadata for provenance
                chunk_meta = getattr(chunk, "metadata", {}) or {}
                legacy["_chunk_metadata"] = chunk_meta
                legacy["_chunk_id"] = getattr(chunk, "chunk_id", "")
                return legacy
            finally:
                loop.close()

        with ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            futures = {pool.submit(_run_chunk, chunk): chunk for chunk in chunks}
            for future in as_completed(futures):
                result = future.result()
                chunk_meta = result.pop("_chunk_metadata", {})
                chunk_id = result.pop("_chunk_id", "")

                for m in result.get("accepted_mentions", []):
                    m["_chunk_metadata"] = chunk_meta
                    m["_chunk_id"] = chunk_id
                for a in result.get("accepted_propositions", []):
                    a["_chunk_metadata"] = chunk_meta
                    a["_chunk_id"] = chunk_id

                all_mentions.extend(result.get("accepted_mentions", []))
                all_assertions.extend(result.get("accepted_propositions", []))
                all_audit_events.extend(result.get("audit_events", []))
                total_mention_retries += result.get("mention_retry_count", 0)
                total_proposition_retries += result.get("proposition_retry_count", 0)
                if result.get("status") == "failed":
                    errors += 1

        duration = time.monotonic() - start

        # Convert to domain models
        mention_models = []
        for m in all_mentions:
            mention_type_str = m.get("mention_type", "OTHER")
            try:
                mention_type = MentionType(mention_type_str)
            except ValueError:
                mention_type = MentionType.OTHER

            chunk_meta = m.pop("_chunk_metadata", {})
            chunk_id_from_meta = m.pop("_chunk_id", "")
            prov = Provenance(
                source_document_id=m.get("document_id", ""),
                chunk_id=chunk_id_from_meta or m.get("chunk_id", ""),
                temporal_start_ms=int(chunk_meta["start_s"] * 1000) if chunk_meta.get("start_s") is not None else None,
                temporal_end_ms=int(chunk_meta["end_s"] * 1000) if chunk_meta.get("end_s") is not None else None,
                speaker_label=chunk_meta.get("speaker"),
                extraction_method="llm",
                extraction_model=self.ner_model if not all_assertions else self.spo_model,
                confidence=m.get("confidence", 1.0),
                code_location=code_location,
            )
            mention_models.append(
                Mention(
                    document_id=m.get("document_id", ""),
                    chunk_id=chunk_id_from_meta or m.get("chunk_id", ""),
                    text=m.get("text", ""),
                    mention_type=mention_type,
                    span_start=m.get("span_start"),
                    span_end=m.get("span_end"),
                    confidence=m.get("confidence", 1.0),
                    context=m.get("context", ""),
                    provenance=prov,
                )
            )

        assertion_models = []
        for a in all_assertions:
            chunk_meta = a.pop("_chunk_metadata", {})
            chunk_id_from_meta = a.pop("_chunk_id", "")
            a_prov = Provenance(
                source_document_id=a.get("document_id", ""),
                chunk_id=chunk_id_from_meta,
                temporal_start_ms=int(chunk_meta["start_s"] * 1000) if chunk_meta.get("start_s") is not None else None,
                temporal_end_ms=int(chunk_meta["end_s"] * 1000) if chunk_meta.get("end_s") is not None else None,
                speaker_label=chunk_meta.get("speaker"),
                extraction_method="llm",
                extraction_model=self.spo_model,
                code_location=code_location,
            )
            assertion_models.append(
                Assertion(
                    subject_text=a.get("subject", a.get("subject_text", "")),
                    predicate=a.get("predicate", ""),
                    object_text=a.get("object", a.get("object_text", "")),
                    confidence=a.get("confidence", 1.0),
                    negated=a.get("negated", False),
                    hedged=a.get("hedged", False),
                    qualifiers=a.get("qualifiers", {}),
                    provenance=a_prov,
                )
            )

        return ExtractionResult(
            mentions=mention_models,
            assertions=assertion_models,
            stats={
                "chunk_count": len(chunks),
                "duration_s": round(duration, 1),
                "mention_count": len(mention_models),
                "assertion_count": len(assertion_models),
                "mention_retries": total_mention_retries,
                "proposition_retries": total_proposition_retries,
                "errors": errors,
            },
            audit_events=all_audit_events,
        )

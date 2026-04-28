"""Strangler fig dispatcher — toggles between old and new extraction pipeline.

Reads EXGRAPH_ENABLED env var:
- "false" (default): delegates to catalyst_langgraph.graph.build_extraction_graph
- "true": builds pipeline via catalyst-exgraph PipelineBuilder

Same function signature and output shape as the old graph, so
dagster_io/extraction.py can swap in without consumer changes.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

EXGRAPH_ENABLED = os.environ.get("EXGRAPH_ENABLED", "false").lower() == "true"


def build_extraction_graph(
    llm_client: Any,
    mcp_client: Any,
    repository: Any,
) -> Any:
    """Build an extraction graph — dispatches to v1 or v2 based on EXGRAPH_ENABLED.

    Returns the same (compiled_graph, llm_client) tuple as the old _build_graph().
    The compiled graph's ainvoke() produces the same output dict shape:
    - accepted_mentions, accepted_propositions
    - mention_retry_count, proposition_retry_count
    - status, audit_events
    """
    if EXGRAPH_ENABLED:
        logger.info("dispatch: using catalyst-exgraph v2 pipeline")
        return _build_v2(llm_client, mcp_client), llm_client
    else:
        logger.info("dispatch: using catalyst-langgraph-aio v1 graph")
        return _build_v1(llm_client, mcp_client, repository), llm_client


def _build_v1(llm_client, mcp_client, repository):
    """Build the original hardcoded NER→SPO graph."""
    from catalyst_langgraph.graph import build_extraction_graph as _v1_build

    return _v1_build(llm_client, mcp_client, repository)


def _build_v2(llm_client, mcp_client):
    """Build the new generic pipeline via catalyst-exgraph.

    Wraps the compiled pipeline in a compatibility adapter that maps
    ExGraphState output to the legacy format expected by _extract_chunk().
    """
    from catalyst_exgraph.config import ner_stage_config, spo_stage_config
    from catalyst_exgraph.pipeline import build_pipeline

    ner_model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    is_encoder = any(x in ner_model.lower() for x in ("gliner", "nuextract", "universalner", "uniner"))

    ner_config = ner_stage_config(model=ner_model, max_retries=0 if is_encoder else 3)
    spo_config = spo_stage_config(model=ner_model, max_retries=3)

    pipeline = build_pipeline([ner_config, spo_config], llm_client, mcp_client)

    # Wrap pipeline to produce legacy output format
    return _LegacyAdapter(pipeline)


class _LegacyAdapter:
    """Adapts the exgraph pipeline's ainvoke to produce legacy output format.

    The old graph produces flat keys (accepted_mentions, etc.).
    The new pipeline produces nested ExGraphState with stages dict.
    This adapter maps between them.
    """

    def __init__(self, pipeline):
        self.pipeline = pipeline

    async def ainvoke(self, state: dict) -> dict:
        """Run the pipeline and map output to legacy format."""
        from catalyst_exgraph.pipeline import pipeline_result_to_legacy

        result = await self.pipeline.ainvoke(state)
        return pipeline_result_to_legacy(result)

"""Pipeline builder — chains extraction stages into a composable pipeline.

The PipelineBuilder takes a list of StageConfigs and produces a compiled
LangGraph where each stage's accepted output feeds into the next stage's
upstream_context. This replaces the hardcoded NER→SPO graph.

Usage:
    from catalyst_exgraph.pipeline import build_pipeline
    from catalyst_exgraph.config import ner_stage_config, spo_stage_config

    pipeline = build_pipeline(
        stages=[ner_stage_config(model="gliner"), spo_stage_config(model="mistral:latest")],
        clients={"ner": gliner_client, "spo": llm_client},
        mcp_client=mcp_client,
    )
    result = await pipeline.ainvoke({"raw_text": text, "source_metadata": {...}})

Phase 2 (CD-j6d3) adds:
    ``build_ner_pipeline`` — extract_ner → validate_ner → repair_ner →
                             cluster_entities → pack_evidence
    ``build_spo_pipeline`` — extract_spo → validate_spo → repair_spo

The legacy ``build_pipeline`` is kept for backward-compat but is deprecated.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dagster_io.chunking import ChunkConfig

from langgraph.graph import END, StateGraph

from catalyst_exgraph.config import StageConfig
from catalyst_exgraph.protocol import ExtractionClient
from catalyst_exgraph.stage import build_stage_graph
from catalyst_exgraph.state import ExGraphState, ExGraphStatus

logger = logging.getLogger(__name__)


class _StageRunner:
    """Wraps a stage subgraph as a pipeline node.

    Handles state mapping: extracts input for the stage, invokes the
    stage graph, maps output back into the pipeline state.
    """

    def __init__(
        self,
        config: StageConfig,
        client: ExtractionClient,
        mcp_client: Any,
        prev_stage_name: str | None = None,
    ) -> None:
        self.config = config
        self.stage_graph = build_stage_graph(config, client, mcp_client)
        self.prev_stage_name = prev_stage_name

    async def __call__(self, state: ExGraphState) -> dict[str, Any]:
        stage_name = self.config.stage_name
        t0 = time.perf_counter()

        # Build upstream context from previous stage's accepted output
        upstream_context = dict(state.get("upstream_context", {}))
        if self.prev_stage_name:
            prev_stage = state.get("stages", {}).get(self.prev_stage_name, {})
            accepted = prev_stage.get("accepted", [])
            # Convention: NER accepted → "accepted_mentions" for SPO
            upstream_context[f"accepted_{self.prev_stage_name}"] = accepted
            # Also set the generic key for backward compat
            if self.prev_stage_name == "ner":
                upstream_context["accepted_mentions"] = accepted

        # Invoke the stage subgraph
        stage_input: ExGraphState = {
            "raw_text": state.get("raw_text", ""),
            "source_metadata": state.get("source_metadata", {}),
            "stages": state.get("stages", {}),
            "upstream_context": upstream_context,
            "max_retries": self.config.max_retries,
            "audit_events": [],
            "status": ExGraphStatus.EXTRACTING.value,
        }

        result = await self.stage_graph.ainvoke(stage_input)
        elapsed = time.perf_counter() - t0

        logger.info(
            "pipeline: stage %s completed in %.1fs, accepted=%d",
            stage_name,
            elapsed,
            len(result.get("stages", {}).get(stage_name, {}).get("accepted", [])),
        )

        # Merge stage results back into pipeline state
        merged_stages = dict(state.get("stages", {}))
        merged_stages.update(result.get("stages", {}))

        return {
            "stages": merged_stages,
            "upstream_context": upstream_context,
            "audit_events": state.get("audit_events", []) + result.get("audit_events", []),
        }


def build_ner_pipeline(
    ner_config: StageConfig,
    client: ExtractionClient,
    mcp_client: Any,
    embedder: Any = None,
    cache: Any = None,
    chunk_config: ChunkConfig | None = None,
) -> Any:
    """Build the NER half of the entity-anchored pipeline (Phase 2, CD-j6d3).

    Graph: extract_ner → validate_ner → repair_ner → cluster_entities → pack_evidence

    Args:
        ner_config:   StageConfig for the NER stage.
        client:       ExtractionClient for the NER stage.
        mcp_client:   MCP contract validation client.
        embedder:     Optional EmbeddingResource (Qwen3-8B, provider=local).
                      When ``None`` the embedding-merge step in ClusterEntitiesNode
                      is skipped — only proximity clustering runs.
        cache:        Optional EmbeddingCache.  Defaults to a fresh in-memory cache.
        chunk_config: Optional ChunkConfig.  When provided, a ChunkNode is prepended.

    Returns:
        Compiled LangGraph ready for ``ainvoke()``.
    """
    from catalyst_exgraph.nodes.cluster import ClusterEntitiesNode
    from catalyst_exgraph.nodes.pack import PackEvidenceNode

    graph = StateGraph(ExGraphState)
    node_names: list[str] = []

    if chunk_config is not None:
        from catalyst_exgraph.nodes.chunk import ChunkNode

        graph.add_node("chunk", ChunkNode(chunk_config))
        node_names.append("chunk")

    # NER stage runner
    ner_runner = _StageRunner(ner_config, client, mcp_client, prev_stage_name=None)
    graph.add_node("stage_ner", ner_runner)
    node_names.append("stage_ner")

    # Phase 2 nodes
    graph.add_node("cluster_entities", ClusterEntitiesNode(embedder=embedder, cache=cache))
    node_names.append("cluster_entities")

    graph.add_node("pack_evidence", PackEvidenceNode())
    node_names.append("pack_evidence")

    graph.set_entry_point(node_names[0])
    for i in range(len(node_names) - 1):
        graph.add_edge(node_names[i], node_names[i + 1])
    graph.add_edge(node_names[-1], END)

    return graph.compile()


def build_ensemble_pipeline(
    encoders: list[StageConfig],
    clients: dict[str, ExtractionClient],
    mcp_client: Any,
    embedder: Any = None,
    cache: Any = None,
    chunk_config: ChunkConfig | None = None,
    per_encoder_timeout_s: float = 60.0,
    quorum: int | None = None,
    per_type_quorum: dict[str, int] | None = None,
) -> Any:
    """Build the v4 NER-ensemble pipeline (Phase B, CD-94ow).

    Graph: [chunk →] ner_ensemble → consensus → cluster_entities → pack_evidence

    Phase A added ``NerEnsembleNode``; Phase B inserts ``ConsensusNode``
    between the ensemble and cluster stages so ``ClusterEntitiesNode``
    operates on consensus-filtered mentions with full provenance rather than
    raw per-encoder output.

    ``build_ner_pipeline`` (single-NER) is kept intact for callers that
    haven't migrated to the ensemble path yet.

    Args:
        encoders: List of StageConfig objects — one per encoder model.
            ``cfg.model_override`` must match a key in ``clients``.
        clients: Pre-resolved ExtractionClient instances keyed by encoder name
            (same string as ``cfg.model_override``).
        mcp_client: MCP contract validation client.
        embedder: Optional EmbeddingResource for embedding-merge step in
            ClusterEntitiesNode.  ``None`` → proximity-only clustering.
        cache: Optional EmbeddingCache.  Defaults to a fresh in-memory cache.
        chunk_config: Optional ChunkConfig.  When provided a ChunkNode is
            prepended.
        per_encoder_timeout_s: Per-encoder asyncio.wait_for timeout.  Default 60 s.
        quorum: Override default quorum ``ceil(N/2)`` for ConsensusNode.
        per_type_quorum: Per-type quorum overrides for ConsensusNode.
            ``None`` → PII_TYPES default to K=1.

    Returns:
        Compiled LangGraph ready for ``ainvoke()``.
    """
    from catalyst_exgraph.nodes.cluster import ClusterEntitiesNode
    from catalyst_exgraph.nodes.consensus import ConsensusNode
    from catalyst_exgraph.nodes.ner_ensemble import NerEnsembleNode
    from catalyst_exgraph.nodes.pack import PackEvidenceNode

    encoder_names = [cfg.model_override or cfg.stage_name for cfg in encoders]

    graph = StateGraph(ExGraphState)
    node_names: list[str] = []

    if chunk_config is not None:
        from catalyst_exgraph.nodes.chunk import ChunkNode

        graph.add_node("chunk", ChunkNode(chunk_config))
        node_names.append("chunk")

    ensemble_node = NerEnsembleNode(
        encoders=encoders,
        clients=clients,
        mcp_client=mcp_client,
        per_encoder_timeout_s=per_encoder_timeout_s,
    )
    graph.add_node("ner_ensemble", ensemble_node)
    node_names.append("ner_ensemble")

    # Phase B: ConsensusNode sits between ensemble and clustering
    consensus_node = ConsensusNode(
        encoders=encoder_names,
        quorum=quorum,
        per_type_quorum=per_type_quorum,
    )
    graph.add_node("consensus", consensus_node)
    node_names.append("consensus")

    graph.add_node("cluster_entities", ClusterEntitiesNode(embedder=embedder, cache=cache))
    node_names.append("cluster_entities")

    graph.add_node("pack_evidence", PackEvidenceNode())
    node_names.append("pack_evidence")

    graph.set_entry_point(node_names[0])
    for i in range(len(node_names) - 1):
        graph.add_edge(node_names[i], node_names[i + 1])
    graph.add_edge(node_names[-1], END)

    return graph.compile()


def build_spo_pipeline(
    spo_config: StageConfig,
    client: ExtractionClient,
    mcp_client: Any,
    chunk_config: ChunkConfig | None = None,
) -> Any:
    """Build the SPO half of the entity-anchored pipeline (Phase 2, CD-j6d3).

    Graph: extract_spo → validate_spo → repair_spo

    The pipeline expects ``state["upstream_context"]["accepted_mentions"]`` to
    be pre-populated with the NER accepted mentions for the evidence window.

    Args:
        spo_config:   StageConfig for the SPO stage.
        client:       ExtractionClient for the SPO stage.
        mcp_client:   MCP contract validation client.
        chunk_config: Ignored (SPO runs on pre-packed evidence windows).

    Returns:
        Compiled LangGraph ready for ``ainvoke()``.
    """
    if spo_config.skip:
        graph = StateGraph(ExGraphState)
        graph.add_node("passthrough_spo", _noop)
        graph.set_entry_point("passthrough_spo")
        graph.add_edge("passthrough_spo", END)
        return graph.compile()

    graph = StateGraph(ExGraphState)
    spo_runner = _StageRunner(spo_config, client, mcp_client, prev_stage_name="ner")
    graph.add_node("stage_spo", spo_runner)
    graph.set_entry_point("stage_spo")
    graph.add_edge("stage_spo", END)
    return graph.compile()


def build_pipeline(
    stages: list[StageConfig],
    clients: dict[str, ExtractionClient] | ExtractionClient,
    mcp_client: Any,
    chunk_config: ChunkConfig | None = None,
) -> Any:
    """DEPRECATED — use ``build_ner_pipeline`` + ``build_spo_pipeline`` instead.

    Kept for backward-compat; Phase 4 cleanup (CD-j6d3 follow-up) will remove.
    """
    """Build a compiled multi-stage extraction pipeline.

    Args:
        stages: Ordered list of StageConfigs to execute.
        clients: Either a dict mapping stage_name → client (for per-stage models)
                 or a single client used for all stages.
        mcp_client: MCP contract validation client.
        chunk_config: Optional ChunkConfig. When provided, a ChunkNode is
                      prepended as the first node. When omitted, the pipeline
                      expects pre-chunked input (backward compatible).

    Returns:
        Compiled LangGraph ready for ainvoke().
    """
    active_stages = [s for s in stages if not s.skip]

    if not active_stages and chunk_config is None:
        graph = StateGraph(ExGraphState)
        graph.add_node("noop", _noop)
        graph.set_entry_point("noop")
        graph.add_edge("noop", END)
        return graph.compile()

    # Resolve per-stage clients
    def _get_client(stage: StageConfig) -> ExtractionClient:
        if isinstance(clients, dict):
            return clients.get(stage.stage_name, next(iter(clients.values())))
        return clients

    graph = StateGraph(ExGraphState)
    node_names: list[str] = []

    # Optionally prepend chunk node
    if chunk_config is not None:
        from catalyst_exgraph.nodes.chunk import ChunkNode

        chunk_node = ChunkNode(chunk_config)
        graph.add_node("chunk", chunk_node)
        node_names.append("chunk")

    # Add stage runner nodes
    prev_name = None
    for stage in active_stages:
        node_name = f"stage_{stage.stage_name}"
        client = _get_client(stage)
        runner = _StageRunner(stage, client, mcp_client, prev_stage_name=prev_name)
        graph.add_node(node_name, runner)
        node_names.append(node_name)
        prev_name = stage.stage_name

    if not node_names:
        graph.add_node("noop", _noop)
        graph.set_entry_point("noop")
        graph.add_edge("noop", END)
        return graph.compile()

    # Wire edges: linear chain
    graph.set_entry_point(node_names[0])
    for i in range(len(node_names) - 1):
        graph.add_edge(node_names[i], node_names[i + 1])
    graph.add_edge(node_names[-1], END)

    return graph.compile()


def emit_chunk_extracted_for_state(state: ExGraphState) -> None:
    """Emit the terminal ``chunk_extracted`` event from a finished
    ExGraphState. Called once per pipeline invocation to tie a chunk's
    text to the accepted NER + SPO output for the StateInspector."""
    from dagster_io import event_tail

    src = state.get("source_metadata") or {}
    chunk_id = state.get("chunk_id") or src.get("chunk_id")
    if not chunk_id:
        return
    stages = state.get("stages") or {}
    mentions = (stages.get("ner") or {}).get("accepted") or []
    propositions = (stages.get("spo") or {}).get("accepted") or []
    event_tail.emit_chunk_extracted(
        chunk_id,
        model=state.get("model"),
        doc_id=state.get("doc_id") or src.get("document_id"),
        mentions=mentions,
        propositions=propositions,
    )


def pipeline_result_to_legacy(state: ExGraphState) -> dict[str, Any]:
    """Map ExGraphState to the legacy output format expected by extract_validated().

    Also fires the terminal ``chunk_extracted`` event so the
    StateInspector can tie the chunk text to the final NER + SPO list
    without re-walking intermediate validate/repair events.
    """
    emit_chunk_extracted_for_state(state)

    stages = state.get("stages", {})

    ner_stage = stages.get("ner", {})
    spo_stage = stages.get("spo", {})

    # Determine final status
    all_statuses = [s.get("status", "pending") for s in stages.values()]
    if any(s == "error" for s in all_statuses):
        status = "failed"
    elif all(s in ("completed", "skipped") for s in all_statuses):
        status = "completed"
    else:
        status = state.get("status", "unknown")

    return {
        "accepted_mentions": ner_stage.get("accepted", []),
        "accepted_propositions": spo_stage.get("accepted", []),
        "mention_retry_count": ner_stage.get("retry_count", 0),
        "proposition_retry_count": spo_stage.get("retry_count", 0),
        "status": status,
        "audit_events": state.get("audit_events", []),
    }


async def _noop(state: ExGraphState) -> dict[str, Any]:
    """No-op for empty pipelines."""
    return {"status": ExGraphStatus.COMPLETED.value}

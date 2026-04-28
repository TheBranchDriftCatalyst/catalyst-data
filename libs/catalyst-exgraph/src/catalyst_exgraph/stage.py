"""Stage graph factory — builds a generic extract→validate→repair loop.

This is the core abstraction of catalyst-exgraph. A single StageConfig
parameterizes the loop for any extraction type (NER, SPO, etc.).

Usage:
    graph = build_stage_graph(ner_config, llm_client, mcp_client)
    result = await graph.ainvoke({"raw_text": text, "stages": {}, ...})
    accepted = result["stages"]["ner"]["accepted"]
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from catalyst_exgraph.config import StageConfig
from catalyst_exgraph.nodes.extract import ExtractNode
from catalyst_exgraph.nodes.repair import RepairNode
from catalyst_exgraph.nodes.validate import ValidateNode
from catalyst_exgraph.protocol import ExtractionClient
from catalyst_exgraph.state import ExGraphState

logger = logging.getLogger(__name__)


def _route_after_validation(state: ExGraphState, config: StageConfig) -> str:
    """Route after validation: accept, repair, or fail.

    Pure function — reads stage state and config to decide next step.
    """
    stage = state.get("stages", {}).get(config.stage_name, {})
    validation = stage.get("validation", {})
    verdict = validation.get("verdict", "invalid")

    if verdict == "valid" or stage.get("status") == "completed":
        return "done"

    retry_count = stage.get("retry_count", 0)
    max_retries = config.max_retries

    if retry_count >= max_retries:
        logger.debug(
            "route_%s: max retries reached (%d/%d) -> done",
            config.stage_name,
            retry_count,
            max_retries,
        )
        return "done"

    logger.debug(
        "route_%s: verdict=%s, retry=%d/%d -> repair",
        config.stage_name,
        verdict,
        retry_count,
        max_retries,
    )
    return "repair"


def build_stage_graph(
    config: StageConfig,
    client: ExtractionClient,
    mcp_client: Any,
) -> Any:
    """Build a compiled LangGraph for one extraction stage.

    The graph has 3 nodes:
        extract → validate → [repair loop or END]

    If config.skip is True, returns a pass-through graph.
    If config.max_retries is 0, the repair node is never reached.

    Args:
        config: Stage configuration (schema, prompts, validator, retries).
        client: Extraction client (LLM, GLiNER, NuExtract, etc.).
        mcp_client: MCP contract validation client.

    Returns:
        Compiled LangGraph ready for ainvoke().
    """
    if config.skip:
        # Pass-through: return a graph that does nothing
        graph = StateGraph(ExGraphState)
        graph.add_node("passthrough", _passthrough_node)
        graph.set_entry_point("passthrough")
        graph.add_edge("passthrough", END)
        return graph.compile()

    graph = StateGraph(ExGraphState)

    # Add nodes
    graph.add_node("extract", ExtractNode(config, client))
    graph.add_node("validate", ValidateNode(config, mcp_client))

    if config.max_retries > 0:
        graph.add_node("repair", RepairNode(config, client))

    # Entry point
    graph.set_entry_point("extract")

    # Edges
    graph.add_edge("extract", "validate")

    if config.max_retries > 0:
        graph.add_edge("repair", "validate")

    # Conditional routing after validation
    def _route(state: ExGraphState) -> str:
        return _route_after_validation(state, config)

    if config.max_retries > 0:
        graph.add_conditional_edges(
            "validate",
            _route,
            {
                "done": END,
                "repair": "repair",
            },
        )
    else:
        # No repair — always go to END after validation
        graph.add_edge("validate", END)

    return graph.compile()


async def _passthrough_node(state: ExGraphState) -> dict[str, Any]:
    """No-op node for skipped stages."""
    return {}

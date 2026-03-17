"""Build the LangGraph extraction workflow."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from catalyst_langgraph.clients.llm import LLMClient
from catalyst_langgraph.clients.mcp import MCPClient
from catalyst_langgraph.nodes.extract_mentions import make_extract_mentions
from catalyst_langgraph.nodes.extract_propositions import make_extract_propositions
from catalyst_langgraph.nodes.persist_artifacts import make_persist_artifacts
from catalyst_langgraph.nodes.repair_mentions import make_repair_mentions
from catalyst_langgraph.nodes.repair_propositions import make_repair_propositions
from catalyst_langgraph.nodes.validate_mentions import make_validate_mentions
from catalyst_langgraph.nodes.validate_propositions import make_validate_propositions
from catalyst_langgraph.repository.base import ArtifactRepository
from catalyst_langgraph.state import ExtractionState


def _route_after_mention_validation(state: ExtractionState) -> str:
    validation = state.get("latest_mention_validation", {})
    verdict = validation.get("verdict", "invalid")

    if verdict == "valid":
        return "extract_propositions"

    max_retries = state.get("max_retries", 3)
    retry_count = state.get("mention_retry_count", 0)
    if retry_count >= max_retries:
        return END

    return "repair_mentions"


def _route_after_proposition_validation(state: ExtractionState) -> str:
    validation = state.get("latest_proposition_validation", {})
    verdict = validation.get("verdict", "invalid")

    if verdict == "valid":
        return "persist_artifacts"

    max_retries = state.get("max_retries", 3)
    retry_count = state.get("proposition_retry_count", 0)
    if retry_count >= max_retries:
        return END

    return "repair_propositions"


def build_extraction_graph(
    llm_client: LLMClient,
    mcp_client: MCPClient,
    repository: ArtifactRepository,
) -> Any:
    """Build and compile the extraction StateGraph.

    Parameters
    ----------
    llm_client:
        Async LLM client for completions.
    mcp_client:
        MCP client for contract validation.
    repository:
        Artifact repository for persisting results.

    Returns
    -------
    CompiledGraph
        A compiled LangGraph ready to invoke.
    """
    graph = StateGraph(ExtractionState)

    # Add nodes (each closes over its dependencies)
    graph.add_node("extract_mentions", make_extract_mentions(llm_client))
    graph.add_node("validate_mentions", make_validate_mentions(mcp_client))
    graph.add_node("repair_mentions", make_repair_mentions(llm_client))
    graph.add_node("extract_propositions", make_extract_propositions(llm_client))
    graph.add_node(
        "validate_propositions", make_validate_propositions(mcp_client)
    )
    graph.add_node("repair_propositions", make_repair_propositions(llm_client))
    graph.add_node("persist_artifacts", make_persist_artifacts(repository))

    # Set entry point
    graph.set_entry_point("extract_mentions")

    # Linear edges
    graph.add_edge("extract_mentions", "validate_mentions")
    graph.add_edge("repair_mentions", "validate_mentions")
    graph.add_edge("extract_propositions", "validate_propositions")
    graph.add_edge("repair_propositions", "validate_propositions")
    graph.add_edge("persist_artifacts", END)

    # Conditional edges
    graph.add_conditional_edges(
        "validate_mentions",
        _route_after_mention_validation,
        {
            "extract_propositions": "extract_propositions",
            "repair_mentions": "repair_mentions",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "validate_propositions",
        _route_after_proposition_validation,
        {
            "persist_artifacts": "persist_artifacts",
            "repair_propositions": "repair_propositions",
            END: END,
        },
    )

    return graph.compile()

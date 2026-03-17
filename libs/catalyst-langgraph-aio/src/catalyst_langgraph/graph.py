"""Build the LangGraph extraction workflow."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.types import RetryPolicy

from catalyst_langgraph.clients.llm import LLMClient
from catalyst_langgraph.clients.mcp import MCPClient
from catalyst_langgraph.nodes import make_audit_event
from catalyst_langgraph.nodes.extract_mentions import ExtractMentions
from catalyst_langgraph.nodes.extract_propositions import ExtractPropositions
from catalyst_langgraph.nodes.persist_artifacts import PersistArtifacts
from catalyst_langgraph.nodes.repair_mentions import RepairMentions
from catalyst_langgraph.nodes.repair_propositions import RepairPropositions
from catalyst_langgraph.nodes.validate_mentions import ValidateMentions
from catalyst_langgraph.nodes.validate_propositions import ValidatePropositions
from catalyst_langgraph.repository.base import ArtifactRepository
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

# --- Stratified error handling: RetryPolicy for transient errors ---

TRANSIENT_ERRORS = (ConnectionError, TimeoutError, OSError)


def _is_transient(exc: Exception) -> bool:
    """Return True if the exception is transient and worth retrying."""
    if isinstance(exc, TRANSIENT_ERRORS):
        return True
    exc_str = str(exc).lower()
    if "rate limit" in exc_str or "429" in exc_str:
        return True
    return False


LLM_RETRY = RetryPolicy(
    max_attempts=3, initial_interval=1.0, backoff_factor=2.0, retry_on=_is_transient
)
MCP_RETRY = RetryPolicy(
    max_attempts=2, initial_interval=0.5, retry_on=_is_transient
)


def _route_after_mention_validation(state: ExtractionState) -> str:
    validation = state.get("latest_mention_validation", {})
    verdict = validation.get("verdict", "invalid")

    if verdict == "valid":
        return "extract_propositions"

    max_retries = state.get("max_retries", 3)
    retry_count = state.get("mention_retry_count", 0)
    if retry_count >= max_retries:
        return "failure_handler"

    return "repair_mentions"


def _route_after_proposition_validation(state: ExtractionState) -> str:
    validation = state.get("latest_proposition_validation", {})
    verdict = validation.get("verdict", "invalid")

    if verdict == "valid":
        return "persist_artifacts"

    max_retries = state.get("max_retries", 3)
    retry_count = state.get("proposition_retry_count", 0)
    if retry_count >= max_retries:
        return "failure_handler"

    return "repair_propositions"


def _failure_handler(state: ExtractionState) -> dict[str, Any]:
    """Mark the workflow as failed when max retries are exhausted."""
    event = make_audit_event(
        "failure_handler",
        "failed",
        reason="max retries exhausted",
        mention_retry_count=state.get("mention_retry_count", 0),
        proposition_retry_count=state.get("proposition_retry_count", 0),
        max_retries=state.get("max_retries", 3),
    )
    return {
        "status": WorkflowStatus.FAILED.value,
        "audit_events": list(state.get("audit_events", [])) + [event],
    }


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

    # Add nodes — class instances with RetryPolicy for transient errors
    graph.add_node("extract_mentions", ExtractMentions(llm_client), retry_policy=LLM_RETRY)
    graph.add_node("validate_mentions", ValidateMentions(mcp_client), retry_policy=MCP_RETRY)
    graph.add_node("repair_mentions", RepairMentions(llm_client), retry_policy=LLM_RETRY)
    graph.add_node("extract_propositions", ExtractPropositions(llm_client), retry_policy=LLM_RETRY)
    graph.add_node("validate_propositions", ValidatePropositions(mcp_client), retry_policy=MCP_RETRY)
    graph.add_node("repair_propositions", RepairPropositions(llm_client), retry_policy=LLM_RETRY)
    graph.add_node("persist_artifacts", PersistArtifacts(repository))
    graph.add_node("failure_handler", _failure_handler)

    # Set entry point
    graph.set_entry_point("extract_mentions")

    # Linear edges
    graph.add_edge("extract_mentions", "validate_mentions")
    graph.add_edge("repair_mentions", "validate_mentions")
    graph.add_edge("extract_propositions", "validate_propositions")
    graph.add_edge("repair_propositions", "validate_propositions")
    graph.add_edge("persist_artifacts", END)
    graph.add_edge("failure_handler", END)

    # Conditional edges
    graph.add_conditional_edges(
        "validate_mentions",
        _route_after_mention_validation,
        {
            "extract_propositions": "extract_propositions",
            "repair_mentions": "repair_mentions",
            "failure_handler": "failure_handler",
        },
    )
    graph.add_conditional_edges(
        "validate_propositions",
        _route_after_proposition_validation,
        {
            "persist_artifacts": "persist_artifacts",
            "repair_propositions": "repair_propositions",
            "failure_handler": "failure_handler",
        },
    )

    return graph.compile()

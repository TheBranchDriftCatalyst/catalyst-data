"""Async LangGraph orchestration for LLM extraction with MCP contract validation.

.. deprecated::
    This package is superseded by ``catalyst-exgraph`` which provides generic
    composable extraction graphs. Set ``EXGRAPH_ENABLED=true`` to use the new
    pipeline, or migrate to ``ExtractionResource`` for the Dagster interface.
    See ``libs/catalyst-exgraph/docs/MIGRATION.md`` for details.
"""

import warnings

from catalyst_langgraph.graph import build_extraction_graph
from catalyst_langgraph.state import ExtractionState, WorkflowStatus

warnings.warn(
    "catalyst-langgraph-aio is deprecated. Use catalyst-exgraph instead. "
    "Set EXGRAPH_ENABLED=true or migrate to ExtractionResource. "
    "See libs/catalyst-exgraph/docs/MIGRATION.md",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "ExtractionState",
    "WorkflowStatus",
    "build_extraction_graph",
]

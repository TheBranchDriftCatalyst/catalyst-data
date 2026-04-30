"""catalyst-exgraph: Generic composable extraction graphs.

Replaces the hardcoded NER→SPO pipeline in catalyst-langgraph-aio with
configurable extract→validate→repair stages that compose into pipelines.

Key features:
- Generic stage graph: one extract→validate→repair loop, parameterized by StageConfig
- Pipeline composition: chain stages (NER→SPO), per-stage model selection
- Ensemble extraction: N models → consensus voting
- Full provenance: every extraction tracks which model produced it
- ExtractionResource: Dagster ConfigurableResource with extract_mentions/extract_assertions
"""

from catalyst_exgraph.config import PipelineConfig, StageConfig, chunk_stage_config
from catalyst_exgraph.nodes.chunk import ChunkNode
from catalyst_exgraph.pipeline import build_pipeline
from catalyst_exgraph.protocol import ExtractionClient, ExtractionResult, StageResult
from catalyst_exgraph.resource import ExtractionResource
from catalyst_exgraph.stage import build_stage_graph
from catalyst_exgraph.state import ExGraphState, ExGraphStatus

__all__ = [
    "ChunkNode",
    "ExGraphState",
    "ExGraphStatus",
    "ExtractionClient",
    "ExtractionResource",
    "ExtractionResult",
    "PipelineConfig",
    "StageConfig",
    "StageResult",
    "build_pipeline",
    "build_stage_graph",
    "chunk_stage_config",
]

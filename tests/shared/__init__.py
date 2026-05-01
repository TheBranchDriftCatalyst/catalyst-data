"""Cross-domain test utilities. Domain-specific tests live in
``packages/<domain>/tests/`` and write asset outputs to
``.test-output/<domain>/`` via ``LocalJsonIOManager`` (re-exported from
``dagster_io``). The medallion reader here merges chunks across all
domains so the extraction harness has a single read path.
"""

from .extraction_scoring import (
    compute_model_scores,
    print_benchmark_report,
    print_comparison_table,
    print_scores,
    score_mentions,
    score_propositions,
    validate_ground_truth,
)
from .ground_truth import generate_ensemble_ground_truth
from .medallion import load_chunks
from .report import build_report_json
from .store import BenchmarkStore, RunStore

__all__ = [
    "BenchmarkStore",
    "RunStore",
    "build_report_json",
    "compute_model_scores",
    "generate_ensemble_ground_truth",
    "load_chunks",
    "print_benchmark_report",
    "print_comparison_table",
    "print_scores",
    "score_mentions",
    "score_propositions",
    "validate_ground_truth",
]

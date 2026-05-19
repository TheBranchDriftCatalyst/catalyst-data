"""DELETED in Wave 1 / Step 3 (bead llm-g0b).

Originally exercised the chunks→docs grouping + SPO fan-out internals
of the legacy ``extract_validated`` LangGraph driver (``_Doc``,
``_group_chunks_into_docs``, ``_process_doc``). Those internals were
retired when ``extract_validated`` became a thin wrapper around
``ExtractionResource.extract_assertions()`` — the resource handles
concurrency + chunk-level fan-out internally, and the doc-level
batching is no longer a public-shape concern of dagster-io.

Coverage for the new behaviour lives in catalyst-exgraph's own tests
of ``ExtractionResource`` and the AMR pipeline.

Kept as a placeholder so ``git`` history is intact. No assertions.
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="legacy LangGraph-driver internals retired in AMR-as-spine refactor (bead llm-g0b)"
)

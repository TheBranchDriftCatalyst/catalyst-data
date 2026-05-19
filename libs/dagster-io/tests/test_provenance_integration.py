"""DELETED in Wave 1 / Step 3 (bead llm-g0b).

Originally exercised ``extract_validated`` end-to-end with mocked
``_build_pipelines`` to verify that Provenance fields (temporal,
speaker, code_location, span_start/end) were assembled correctly for
media / congress / open-leaks chunks.

The provenance-assembly code that was being tested lived inside the
legacy LangGraph driver. After Wave 1 / Step 3, ``extract_validated``
is a thin wrapper around ``ExtractionResource.extract_assertions``
which owns all Mention/Assertion construction internally — including
provenance assembly. Coverage for that lives in catalyst-exgraph's own
tests of ``ExtractionResource``.

Kept as a placeholder so ``git`` history is intact. No assertions.
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="provenance assembly moved into ExtractionResource (catalyst-exgraph) under bead llm-g0b"
)

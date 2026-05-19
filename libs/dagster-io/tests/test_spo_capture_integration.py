"""DELETED in Wave 1 / Step 3 (bead llm-g0b).

Originally exercised ``catalyst_exgraph.nodes.extract.ExtractNode`` with
a stub ``ExtractionClient`` to verify the SPO prompt/response capture
flow (Gap #5). That flow lived in the legacy SPO LLM stage which was
retired when the AMR-as-spine refactor landed. ``consume_spo_capture``
no longer exists in ``catalyst-exgraph`` — there is no SPO LLM stage to
capture from. AMR projection emits assertions deterministically; no
prompt/response pair to archive.

Kept as a placeholder so ``git`` history is intact. No assertions.
"""

import pytest

pytestmark = pytest.mark.skip(reason="legacy SPO LLM capture path retired in AMR-as-spine refactor (bead llm-g0b)")

"""Validated extraction — thin wrapper around ``ExtractionResource``.

Wave 1 (bead ``llm-g0b``, Step 3) retired the legacy LangGraph-driver
wrapper that used to live here. The single extraction path now goes
through ``catalyst_exgraph.resource.ExtractionResource.extract_assertions()``
which routes chunks through the AMR-as-spine pipeline:
``NER ensemble → consensus → cluster → pack → AMR parse → AMR projection``.

The function signature ``extract_validated(chunks, code_location, ...)``
is preserved so existing callers in ``asset_factory.py`` and the
per-domain assets keep working. Internally we now construct an
``ExtractionResource`` per call, picking the appropriate prompt
directory + label pack from ``code_location``.

Code-location → label pack mapping:
    - ``"congress"`` / ``"congress_data"``  → ``label_pack_id="congress"``
    - ``"media_ingest"`` / ``"media"``      → ``label_pack_id="media"``
    - anything else (incl. ``"open_leaks"``) → ``label_pack_id="generic"``
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from dagster_io.logging import get_logger

if TYPE_CHECKING:
    from catalyst_exgraph.protocol import ExtractionResult

logger = get_logger(__name__)


# ── code_location → (prompt_dir, label_pack_id) lookup ────────────────────
# The bench harness, integration tests, and ad-hoc scripts all need the
# same label-pack mapping that extract_validated uses internally. Exporting
# the dict + the resolver as public symbols means a new code location is
# a one-line addition here — not an N-place hunt across the codebase.

LABEL_PACK_BY_LOCATION: dict[str, str] = {
    "congress": "congress",
    "congress_data": "congress",
    "media": "media",
    "media_ingest": "media",
}

# Legacy alias — keep until the next refactor removes all underscore-prefixed
# callers. The dict itself is what matters; the alias just preserves the
# import path that ``from dagster_io.extraction import _LABEL_PACK_BY_LOCATION``
# would have hit.
_LABEL_PACK_BY_LOCATION = LABEL_PACK_BY_LOCATION


def resolve_label_pack(code_location: str) -> str:
    """Pick the AMR label pack id for a given code location.

    Unknown / empty locations fall back to ``"generic"`` (which has an
    empty ``amr_frames`` table — ``extract_assertions`` will warn and
    emit zero or all-novel assertions). Set ``code_location`` to one of
    the keys in ``LABEL_PACK_BY_LOCATION`` for real output.
    """
    return LABEL_PACK_BY_LOCATION.get(code_location, "generic")


# Legacy alias.
_resolve_label_pack = resolve_label_pack


def extract_validated(
    chunks: list,
    code_location: str = "",
    *,
    max_concurrency: int = 5,
) -> ExtractionResult:
    """Run AMR-as-spine extraction on a list of TextChunk objects.

    Thin wrapper around ``ExtractionResource.extract_assertions()``.
    The resource handles concurrency, NER ensembling, AMR projection,
    and Mention/Assertion construction internally.

    Args:
        chunks: List of TextChunk objects (must carry ``.text``,
            ``.document_id``, ``.chunk_id``). Empty list short-circuits
            to an empty ``ExtractionResult``.
        code_location: For metrics labelling AND label-pack selection.
            See ``_LABEL_PACK_BY_LOCATION`` for the mapping.
        max_concurrency: Max parallel chunk processing inside the
            resource (chunk-level fan-out, NOT doc-level).

    Returns:
        ``ExtractionResult`` with ``.mentions``, ``.assertions``,
        ``.stats``, and ``.audit_events`` populated. The legacy
        ``(mentions, assertions)`` tuple shape is no longer returned —
        callers should switch to ``result.mentions`` / ``result.assertions``.
    """
    # Lazy import: catalyst-exgraph pulls in langgraph + pydantic graph
    # machinery, and during catalyst-data test collection the resource
    # module may not be importable yet (the AMR-spine wiring is still
    # consolidating in catalyst-llm). Importing inside the function
    # keeps ``dagster_io.extraction`` cheap-to-import for callers that
    # only need the module path (e.g. unit tests mocking this symbol).
    from catalyst_exgraph.protocol import ExtractionResult

    if not chunks:
        return ExtractionResult()

    from catalyst_exgraph.resource import ExtractionResource

    from dagster_io.prompts import resolve_prompt_dir

    # code_location uses snake_case (``media_ingest``) but the k8s
    # ConfigMap dirs are kebab-cased (``media-ingest``) — translate so
    # the per-domain fallback in resolve_prompt_dir can find them.
    prompt_dir = resolve_prompt_dir(domain=code_location.replace("_", "-"))
    label_pack_id = resolve_label_pack(code_location)

    # NER controls are deliberately decoupled from LLM_MODEL. Earlier
    # versions defaulted ner_model to LLM_MODEL, which silently routed
    # mention extraction through ChatGPT whenever LLM_MODEL was set for
    # the bill_claims synthesis asset (the symptom: ~1,700 completions
    # on the IRA reconciliation bill). Mention extraction belongs on the
    # local NER ensemble — never on a language model.
    ner_model = os.environ.get("NER_MODEL", "gliner")
    ensemble_raw = os.environ.get("NER_ENSEMBLE", "gliner,nuextract,universalner,regex")
    ner_ensemble = [m.strip() for m in ensemble_raw.split(",") if m.strip()] or None

    logger.info(
        "extract_validated: %d chunks, code_location=%s, label_pack=%s, ner_model=%s, ner_ensemble=%s, concurrency=%d",
        len(chunks),
        code_location,
        label_pack_id,
        ner_model,
        ner_ensemble,
        max_concurrency,
    )

    resource = ExtractionResource(
        ner_model=ner_model,
        ner_ensemble=ner_ensemble,
        prompt_dir=prompt_dir,
        label_pack_id=label_pack_id,
        max_concurrency=max_concurrency,
    )

    return resource.extract_assertions(chunks=chunks, code_location=code_location)

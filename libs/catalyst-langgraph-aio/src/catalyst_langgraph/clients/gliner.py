"""GLiNER adapter — wraps the GLiNER encoder model behind the LLMClient interface.

GLiNER is a 300M bidirectional transformer for zero-shot NER. It's NOT an LLM —
it runs locally via Python, no serving endpoint needed. ~0.1s per extraction on CPU.

pip install gliner

Usage:
    client = GLiNERClient()
    result = await client.structured_output(MentionExtractionResult, messages)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Map our MentionType enum to GLiNER label strings.
# GLiNER works best with lowercase natural-language labels.
MENTION_TYPE_TO_GLINER_LABEL = {
    "PERSON": "person",
    "ORG": "organization",
    "GPE": "country or city",
    "LOC": "location",
    "DATE": "date",
    "LAW": "law or legislation",
    "EVENT": "event",
    "MONEY": "money or financial amount",
    "NORP": "political or national group",
    "FACILITY": "facility or building",
    "DOCUMENT": "document or report",
    "BOOK": "book",
    "ROLE": "role or job title",
    "STRATEGIC_ASSET": "strategic asset",
    "FINANCIAL_INSTRUMENT": "financial instrument",
}

GLINER_LABEL_TO_MENTION_TYPE = {v: k for k, v in MENTION_TYPE_TO_GLINER_LABEL.items()}


class GLiNERClient:
    """Adapter that runs GLiNER for entity extraction and wraps results
    in the same Pydantic schemas as LLMClient.structured_output().

    Config from environment:
    - GLINER_MODEL: HuggingFace model ID (default: urchade/gliner_medium-v2.1)
    - GLINER_THRESHOLD: minimum confidence score (default: 0.5)
    """

    def __init__(
        self,
        *,
        model_name: str | None = None,
        threshold: float | None = None,
    ) -> None:
        self.model_name = model_name or os.environ.get("GLINER_MODEL", "urchade/gliner_medium-v2.1")
        self.threshold = threshold or float(os.environ.get("GLINER_THRESHOLD", "0.5"))
        self._model = None
        # Expose for compatibility with code that reads these
        self.model = self.model_name
        self.structured_method = "gliner"
        self.temperature = 0.0

    def _get_model(self):
        """Lazy-load the GLiNER model on first use."""
        if self._model is None:
            from gliner import GLiNER

            logger.info("gliner: loading model %s", self.model_name)
            t0 = time.perf_counter()
            self._model = GLiNER.from_pretrained(self.model_name)
            logger.info("gliner: model loaded in %.1fs", time.perf_counter() - t0)
        return self._model

    async def complete(self, prompt: str, *, system: str = "") -> str:
        """Not supported — GLiNER is an encoder, not a generative model."""
        raise NotImplementedError("GLiNER is an encoder model, use structured_output() for extraction")

    async def structured_output(self, schema: type[BaseModel], messages: list[Any]) -> BaseModel:
        """Run GLiNER extraction and return results as the expected Pydantic schema.

        Handles MentionExtractionResult. For PropositionExtractionResult, returns
        empty propositions (GLiNER doesn't do relation extraction).
        """
        # Extract raw text from messages
        raw_text = ""
        for m in messages:
            content = getattr(m, "content", str(m))
            if hasattr(m, "type") and m.type == "human":
                raw_text = content
                break
        if not raw_text:
            raw_text = str(messages[-1].content) if messages else ""

        schema_name = schema.__name__

        if "Mention" in schema_name:
            return await self._extract_mentions(raw_text, schema)
        elif "Proposition" in schema_name:
            # GLiNER doesn't do relation extraction — return empty
            from catalyst_contracts.models.extraction_output import PropositionExtractionResult

            return PropositionExtractionResult(propositions=[])
        else:
            raise ValueError(f"GLiNERClient doesn't support schema: {schema_name}")

    async def _extract_mentions(self, raw_text: str, schema: type[BaseModel]) -> BaseModel:
        """Run GLiNER NER on the text and convert to MentionExtractionResult."""
        from catalyst_contracts.models.extraction_output import MentionCandidate

        model = self._get_model()
        labels = list(MENTION_TYPE_TO_GLINER_LABEL.values())

        logger.info("gliner: extracting from %d chars with %d labels", len(raw_text), len(labels))
        t0 = time.perf_counter()
        entities = model.predict_entities(raw_text, labels, threshold=self.threshold)
        elapsed = time.perf_counter() - t0
        logger.info("gliner: extracted %d entities in %.3fs", len(entities), elapsed)

        mentions = []
        seen_spans: set[tuple[int, int]] = set()
        for e in entities:
            span_key = (e["start"], e["end"])
            if span_key in seen_spans:
                continue
            seen_spans.add(span_key)

            mention_type = GLINER_LABEL_TO_MENTION_TYPE.get(e["label"], "OTHER")
            mentions.append(
                MentionCandidate(
                    text=e["text"],
                    mention_type=mention_type,
                    span_start=e["start"],
                    span_end=e["end"],
                    confidence=round(e["score"], 3),
                )
            )

        return schema(mentions=mentions)

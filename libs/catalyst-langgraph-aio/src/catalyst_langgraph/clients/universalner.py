"""UniversalNER adapter — wraps UniNER-7B behind the LLMClient interface.

UniversalNER is a 7B model (Llama-based) fine-tuned on 43 NER datasets.
It uses a specific conversational template where you ask about one entity
type at a time:

    USER: Text: {text}
    ASSISTANT: I've read this text.
    USER: What describes {entity_type} in the text?
    ASSISTANT: ["entity1", "entity2"]

This adapter makes one call per entity type and merges the results.

Usage:
    client = UniversalNERClient()
    result = await client.structured_output(MentionExtractionResult, messages)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel

from catalyst_langgraph.clients._retry import retry_llm_call

logger = logging.getLogger(__name__)

# Map our MentionType to natural-language entity type descriptions
# that UniversalNER was trained on
MENTION_TYPE_TO_QUERY = {
    "PERSON": "person",
    "ORG": "organization",
    "GPE": "country, state, or city",
    "LOC": "location",
    "DATE": "date",
    "LAW": "law or legislation",
    "EVENT": "event",
    "MONEY": "monetary value",
    "NORP": "political group, nationality, or religion",
    "FACILITY": "facility or building",
}


def _compute_spans(text: str, entity_text: str) -> list[tuple[int, int]]:
    """Find all occurrences of entity_text in text.

    NOTE: Duplicate of catalyst_exgraph.nodes.spans.find_all_spans.
    Cannot import directly because catalyst-langgraph-aio does not depend on
    catalyst-exgraph (the dependency runs the other direction).  If the
    dependency is ever added, replace this with:
        from catalyst_exgraph.nodes.spans import find_all_spans as _compute_spans
    """
    spans = []
    start = 0
    while True:
        idx = text.find(entity_text, start)
        if idx == -1:
            break
        spans.append((idx, idx + len(entity_text)))
        start = idx + 1
    return spans


class UniversalNERClient:
    """Adapter that calls UniNER via Ollama and returns MentionExtractionResult.

    Makes one Ollama call per entity type, merges results.

    Config from environment:
    - LLM_BASE_URL: Ollama base URL (default http://localhost:11434)
    - LLM_MODEL: model name (default universalner:latest)
    - LLM_TIMEOUT: request timeout in seconds (default 300)
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        raw_url = base_url or os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
        self.base_url = raw_url.rstrip("/").removesuffix("/v1")
        self.model = model or os.environ.get("LLM_MODEL", "universalner:latest")
        self.timeout = timeout or int(os.environ.get("LLM_TIMEOUT", "300"))
        self.structured_method = "universalner"
        self.temperature = 0.0

    @retry_llm_call(name="universalner")
    async def _call_ollama(self, messages: list[dict]) -> str:
        """Send a multi-turn chat to Ollama and return the assistant response.

        Wrapped in retry_llm_call (CD-58ry): transient Ollama 5xx /
        connection-reset errors retry up to 3 times with exponential
        backoff + full jitter.
        """
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "stream": False,
            "messages": messages,
            "options": {"temperature": 0.0},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"]

    async def _extract_entity_type(self, text: str, entity_type_query: str) -> list[str]:
        """Ask UniNER about one entity type and parse the response."""
        messages = [
            {
                "role": "system",
                "content": "A virtual assistant answers questions from a user based on the provided text.",
            },
            {"role": "user", "content": f"Text: {text}"},
            {"role": "assistant", "content": "I've read this text."},
            {"role": "user", "content": f"What describes {entity_type_query} in the text?"},
        ]

        try:
            response = await self._call_ollama(messages)
            response = response.strip()
            # Parse JSON array from response
            # UniNER returns ["entity1", "entity2"] or sometimes just text
            if response.startswith("["):
                return json.loads(response)
            # Try to find a JSON array in the response
            match = re.search(r"\[.*?\]", response, re.DOTALL)
            if match:
                return json.loads(match.group())
            return []
        except Exception as e:
            logger.warning("universalner: failed to extract %s: %s", entity_type_query, e)
            return []

    async def complete(self, prompt: str, *, system: str = "") -> str:
        """Not the intended use — UniNER is NER-specific."""
        raise NotImplementedError("UniversalNER is NER-specific, use structured_output()")

    async def structured_output(self, schema: type[BaseModel], messages: list[Any]) -> BaseModel:
        """Run NER extraction across all entity types and return results.

        On repair calls (validation feedback), re-uses the cached original text
        since UniversalNER's conversational template can't handle arbitrary
        repair prompts.
        """
        raw_text = ""
        for m in messages:
            content = getattr(m, "content", str(m))
            if hasattr(m, "type") and m.type == "human":
                raw_text = content
                break
        if not raw_text:
            raw_text = str(messages[-1].content) if messages else ""

        # Detect repair calls: repair prompts contain validation feedback
        # like "Fix the following" or JSON error details. In that case, re-use
        # the original source text since UniversalNER can't parse repair prompts.
        is_repair = any(
            marker in raw_text.lower()
            for marker in ["fix the following", "validation error", "span_mismatch", "mentions["]
        )
        if is_repair and hasattr(self, "_last_source_text"):
            logger.info("universalner: repair call detected, re-using original source text")
            raw_text = self._last_source_text
        else:
            self._last_source_text = raw_text

        schema_name = schema.__name__

        if "Mention" in schema_name:
            return await self._extract_mentions(raw_text, schema)
        elif "Proposition" in schema_name:
            from catalyst_contracts.models.extraction_output import PropositionExtractionResult

            return PropositionExtractionResult(propositions=[])
        else:
            raise ValueError(f"UniversalNERClient doesn't support schema: {schema_name}")

    async def _extract_mentions(self, raw_text: str, schema: type[BaseModel]) -> BaseModel:
        """Extract mentions by querying each entity type separately."""
        from catalyst_contracts.models.extraction_output import MentionCandidate

        logger.info(
            "universalner: extracting from %d chars, %d entity types",
            len(raw_text),
            len(MENTION_TYPE_TO_QUERY),
        )
        t0 = time.perf_counter()

        all_mentions: dict[tuple[str, str], dict] = {}

        for mention_type, query in MENTION_TYPE_TO_QUERY.items():
            entities = await self._extract_entity_type(raw_text, query)
            for entity_text in entities:
                if not isinstance(entity_text, str) or not entity_text.strip():
                    continue
                entity_text = entity_text.strip()

                spans = _compute_spans(raw_text, entity_text)
                if spans:
                    span_start, span_end = spans[0]
                else:
                    lower_spans = _compute_spans(raw_text.lower(), entity_text.lower())
                    if lower_spans:
                        span_start, span_end = lower_spans[0]
                        entity_text = raw_text[span_start:span_end]
                    else:
                        span_start, span_end = 0, 0

                key = (entity_text.lower(), mention_type)
                if key not in all_mentions:
                    all_mentions[key] = {
                        "text": entity_text,
                        "mention_type": mention_type,
                        "span_start": span_start,
                        "span_end": span_end,
                        "confidence": 0.9,
                    }

        elapsed = time.perf_counter() - t0
        logger.info(
            "universalner: extracted %d mentions in %.3fs (%d calls)",
            len(all_mentions),
            elapsed,
            len(MENTION_TYPE_TO_QUERY),
        )

        return schema(mentions=[MentionCandidate(**m) for m in all_mentions.values()])

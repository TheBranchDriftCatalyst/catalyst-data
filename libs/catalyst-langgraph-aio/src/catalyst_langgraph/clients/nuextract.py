"""NuExtract adapter — wraps the nuextract model's <|input|>/<|output|> format
behind the same interface as LLMClient.

NuExtract is a 3.8B extraction-specialist model that uses a category-based
schema template instead of tool calling or json_mode. This adapter translates
between our Pydantic extraction schemas and nuextract's native format.

Usage:
    client = NuExtractClient()  # reads LLM_BASE_URL etc from env
    result = await client.structured_output(MentionExtractionResult, messages)
    # result is a MentionExtractionResult with computed spans
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

logger = logging.getLogger(__name__)

# Map our MentionType enum values to nuextract-friendly category names and back
MENTION_TYPE_TO_CATEGORY = {
    "PERSON": "Person",
    "ORG": "Organization",
    "GPE": "Country_State_City",
    "LOC": "Location",
    "DATE": "Date",
    "LAW": "Law_Legislation",
    "EVENT": "Event",
    "MONEY": "Money_Amount",
    "NORP": "Political_National_Group",
    "FACILITY": "Facility_Building",
    "DOCUMENT": "Document_Report",
    "BOOK": "Book",
    "ROLE": "Role_Title",
    "STRATEGIC_ASSET": "Strategic_Asset",
    "FINANCIAL_INSTRUMENT": "Financial_Instrument",
    "OTHER": "Other",
}

CATEGORY_TO_MENTION_TYPE = {v: k for k, v in MENTION_TYPE_TO_CATEGORY.items()}


def _build_nuextract_template(categories: list[str] | None = None) -> str:
    """Build the JSON template that nuextract uses to extract entities."""
    if categories is None:
        categories = list(MENTION_TYPE_TO_CATEGORY.values())
    return json.dumps({cat: [""] for cat in categories})


def _compute_spans(text: str, entity_text: str) -> list[tuple[int, int]]:
    """Find all occurrences of entity_text in text, return (start, end) pairs."""
    spans = []
    start = 0
    while True:
        idx = text.find(entity_text, start)
        if idx == -1:
            break
        spans.append((idx, idx + len(entity_text)))
        start = idx + 1
    return spans


class NuExtractClient:
    """Adapter that calls nuextract via Ollama's /api/chat endpoint and
    returns results matching our Pydantic extraction schemas.

    Config from environment (same vars as LLMClient):
    - LLM_BASE_URL: Ollama base (default http://localhost:11434)
    - LLM_MODEL: model name (default nuextract:latest)
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
        # Strip /v1 suffix — we call /api/chat directly
        self.base_url = raw_url.rstrip("/").removesuffix("/v1")
        self.model = model or os.environ.get("LLM_MODEL", "nuextract:latest")
        self.timeout = timeout or int(os.environ.get("LLM_TIMEOUT", "300"))
        # Expose for compatibility with code that reads these
        self.structured_method = "nuextract"
        self.temperature = 0.0

    async def _call_ollama(self, prompt: str) -> str:
        """Send a chat request to Ollama and return the response text."""
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.0},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["message"]["content"]

    def _parse_nuextract_output(self, raw: str) -> dict:
        """Parse nuextract's output, stripping template markers."""
        text = raw.strip()
        # Remove <|end-output|> and any trailing content
        text = text.split("<|end-output|>")[0].strip()
        # Remove <|output|> tags (may appear with varying whitespace)
        text = re.sub(r"<\|output\|>\s*", "", text).strip()
        # Find the JSON object in the remaining text
        start = text.find("{")
        if start == -1:
            logger.warning("nuextract: no JSON object found in output: %s", text[:200])
            return {}
        # Find matching closing brace
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
        # Fallback: try parsing from the first brace
        return json.loads(text[start:])

    async def complete(self, prompt: str, *, system: str = "") -> str:
        """Simple completion — just wraps the Ollama call."""
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        return await self._call_ollama(full_prompt)

    async def structured_output(self, schema: type[BaseModel], messages: list[Any]) -> BaseModel:
        """Extract structured data using nuextract's native template format.

        Translates MentionExtractionResult / PropositionExtractionResult schemas
        into nuextract's category-based template, calls the model, and converts
        the response back into the expected Pydantic model.
        """
        # Get the raw text from messages (HumanMessage content)
        raw_text = ""
        for m in messages:
            content = getattr(m, "content", str(m))
            if hasattr(m, "type") and m.type == "human":
                raw_text = content
                break
        if not raw_text:
            raw_text = str(messages[-1].content) if messages else ""

        schema_name = schema.__name__
        logger.info(
            "nuextract.structured_output: model=%s, schema=%s, input_len=%d",
            self.model,
            schema_name,
            len(raw_text),
        )
        t0 = time.perf_counter()

        if "Mention" in schema_name:
            result = await self._extract_mentions(raw_text, schema)
        elif "Proposition" in schema_name:
            result = await self._extract_propositions(raw_text, messages, schema)
        else:
            raise ValueError(f"NuExtractClient doesn't support schema: {schema_name}")

        elapsed = time.perf_counter() - t0
        logger.info("nuextract.structured_output: done, schema=%s, duration=%.3fs", schema_name, elapsed)
        return result

    async def _extract_mentions(self, raw_text: str, schema: type[BaseModel]) -> BaseModel:
        """Extract entity mentions using nuextract's category template.

        For texts longer than MAX_WINDOW_CHARS, uses a sliding window approach:
        split into overlapping windows, extract from each, then deduplicate.
        The 3.8B nuextract model degenerates on inputs >~600 chars.
        """
        MAX_WINDOW_CHARS = 500
        OVERLAP_CHARS = 50

        if len(raw_text) <= MAX_WINDOW_CHARS:
            windows = [(0, raw_text)]
        else:
            windows = []
            start = 0
            while start < len(raw_text):
                end = min(start + MAX_WINDOW_CHARS, len(raw_text))
                windows.append((start, raw_text[start:end]))
                start += MAX_WINDOW_CHARS - OVERLAP_CHARS
            logger.info("nuextract: splitting %d chars into %d windows", len(raw_text), len(windows))

        template = _build_nuextract_template()
        all_mentions: dict[tuple[str, str], dict] = {}  # (text, type) -> mention dict

        for window_offset, window_text in windows:
            prompt = f"<|input|>\n{window_text}\n<|output|>\n{template}"
            try:
                response = await self._call_ollama(prompt)
                parsed = self._parse_nuextract_output(response)
            except Exception as e:
                logger.warning("nuextract: window at offset %d failed: %s", window_offset, e)
                continue

            for category, entities in parsed.items():
                mention_type = CATEGORY_TO_MENTION_TYPE.get(category, "OTHER")
                for entity_text in entities:
                    if not entity_text or not entity_text.strip():
                        continue
                    entity_text = entity_text.strip()

                    # Compute span against the FULL source text (not the window)
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

        from catalyst_contracts.models.extraction_output import MentionCandidate

        return schema(mentions=[MentionCandidate(**m) for m in all_mentions.values()])

    async def _extract_propositions(self, raw_text: str, messages: list[Any], schema: type[BaseModel]) -> BaseModel:
        """Extract propositions using nuextract.

        Propositions are harder for nuextract since it's designed for entity
        extraction. We use a simple template with subject/predicate/object slots.
        """
        template = json.dumps(
            {
                "propositions": [
                    {
                        "subject": "",
                        "predicate": "",
                        "object": "",
                    }
                ]
            }
        )
        prompt = f"<|input|>\n{raw_text}\n<|output|>\n{template}"

        response = await self._call_ollama(prompt)
        parsed = self._parse_nuextract_output(response)

        propositions = []
        for p in parsed.get("propositions", []):
            subj = p.get("subject", "").strip()
            pred = p.get("predicate", "").strip()
            obj = p.get("object", "").strip()
            if subj and pred and obj:
                propositions.append(
                    {
                        "subject": subj,
                        "predicate": pred,
                        "object": obj,
                        "confidence": 0.8,
                        "evidence": "",
                    }
                )

        from catalyst_contracts.models.extraction_output import PropositionCandidate

        return schema(propositions=[PropositionCandidate(**p) for p in propositions])

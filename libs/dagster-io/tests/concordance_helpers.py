"""Shared factory functions and embedding helpers for concordance tests."""

from __future__ import annotations

import hashlib
import math

from dagster_io.models import EntityCandidate, Mention, MentionType


def make_mention(
    text: str,
    mention_type: MentionType = MentionType.PERSON,
    document_id: str = "doc-1",
    chunk_id: str = "chunk-0",
) -> Mention:
    return Mention(
        document_id=document_id,
        chunk_id=chunk_id,
        text=text,
        mention_type=mention_type,
    )


def make_candidate(
    name: str,
    candidate_type: MentionType = MentionType.PERSON,
    code_location: str = "source_a",
    *,
    aliases: list[str] | None = None,
    embedding: list[float] | None = None,
    mention_count: int = 5,
    cid: str | None = None,
) -> EntityCandidate:
    return EntityCandidate(
        candidate_id=cid or "",
        canonical_name=name,
        candidate_type=candidate_type,
        aliases=aliases or [],
        mention_ids=[f"m-{name.lower().replace(' ', '-')}"],
        mention_count=mention_count,
        source_documents=[f"doc-{code_location}"],
        code_location=code_location,
        embedding=embedding,
    )


def make_embedding(seed: int, dim: int = 64) -> list[float]:
    raw = hashlib.sha256(str(seed).encode()).digest() * (dim // 32 + 1)
    vec = [((b % 200) - 100) / 100.0 for b in raw[:dim]]
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm > 0 else vec


def make_similar_embedding(base: list[float], similarity: float) -> list[float]:
    dim = len(base)
    raw = [(i + 1.0) / dim for i in range(dim)]
    dot = sum(a * b for a, b in zip(raw, base, strict=False))
    ortho = [r - dot * b for r, b in zip(raw, base, strict=False)]
    norm_o = math.sqrt(sum(x * x for x in ortho))
    ortho = [x / norm_o for x in ortho] if norm_o > 0 else ortho
    beta = math.sqrt(max(0, 1 - similarity * similarity))
    result = [similarity * b + beta * o for b, o in zip(base, ortho, strict=False)]
    norm_r = math.sqrt(sum(x * x for x in result))
    return [x / norm_r for x in result] if norm_r > 0 else result

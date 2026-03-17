"""Artifact persistence for extraction outputs."""

from catalyst_langgraph.repository.base import ArtifactRepository
from catalyst_langgraph.repository.jsonl import JsonlRepository

__all__ = ["ArtifactRepository", "JsonlRepository"]

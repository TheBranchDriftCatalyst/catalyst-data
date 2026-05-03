"""Media-domain-specific API routes.

Generic list+detail endpoints for media documents now live in
``routes/documents_factory.py`` (mounted at ``/viewer/api/media/documents``).
This module hosts the media-only extras (transcription, diarization,
chunks, mentions, assertions) under the same ``/viewer/api/media`` prefix
so the frontend has a single namespace per domain.

GET /viewer/api/media/documents/{document_id}/transcription
GET /viewer/api/media/documents/{document_id}/diarization
GET /viewer/api/media/documents/{document_id}/chunks
GET /viewer/api/media/documents/{document_id}/mentions
GET /viewer/api/media/documents/{document_id}/assertions
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from dagster_io.logging import get_logger
from media_ingest.viewer.services.s3_data import S3DataService

logger = get_logger(__name__)

router = APIRouter(prefix="/viewer/api/media", tags=["media-api"])

# Singleton data service — created once, reused across requests
_data_service: S3DataService | None = None


def _svc() -> S3DataService:
    global _data_service
    if _data_service is None:
        _data_service = S3DataService()
    return _data_service


# ── Transcription ────────────────────────────────────────────────────────────


@router.get("/documents/{document_id}/transcription")
def get_transcription(document_id: str) -> dict:
    """Get the transcription for a document (segments + words + language)."""
    svc = _svc()
    data = svc.load_transcription(document_id)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Transcription not found for document '{document_id}'",
        )
    return data


# ── Diarization ──────────────────────────────────────────────────────────────


@router.get("/documents/{document_id}/diarization")
def get_diarization(document_id: str) -> dict:
    """Get speaker diarization for a document (speaker-annotated segments)."""
    svc = _svc()
    data = svc.load_diarization(document_id)
    if data is None:
        raise HTTPException(
            status_code=404,
            detail=f"Diarization not found for document '{document_id}'",
        )
    return data


# ── Chunks ───────────────────────────────────────────────────────────────────


@router.get("/documents/{document_id}/chunks")
def get_chunks(document_id: str) -> list[dict]:
    """Get text chunks with strategy metadata for a document."""
    svc = _svc()
    return svc.load_chunks(document_id)


# ── Mentions ─────────────────────────────────────────────────────────────────


@router.get("/documents/{document_id}/mentions")
def get_mentions(document_id: str) -> list[dict]:
    """Get NER entity mentions for a document."""
    svc = _svc()
    return svc.load_mentions(document_id)


# ── Assertions ───────────────────────────────────────────────────────────────


@router.get("/documents/{document_id}/assertions")
def get_assertions(document_id: str) -> list[dict]:
    """Get qualified S-P-O assertions for a document."""
    svc = _svc()
    return svc.load_assertions(document_id)

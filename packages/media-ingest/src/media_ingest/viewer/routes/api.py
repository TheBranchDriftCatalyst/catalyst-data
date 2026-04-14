"""REST API routes for the media viewer.

GET /viewer/api/documents                          — list all media documents
GET /viewer/api/documents/{document_id}            — single document metadata
GET /viewer/api/documents/{document_id}/transcription — transcription data
GET /viewer/api/documents/{document_id}/diarization   — speaker-attributed segments
GET /viewer/api/documents/{document_id}/mentions       — NER entity mentions
GET /viewer/api/documents/{document_id}/assertions     — S-P-O triples
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from dagster_io.logging import get_logger
from media_ingest.viewer.services.s3_data import S3DataService

logger = get_logger(__name__)

router = APIRouter(prefix="/viewer/api", tags=["viewer-api"])

# Singleton data service — created once, reused across requests
_data_service: S3DataService | None = None


def _svc() -> S3DataService:
    global _data_service
    if _data_service is None:
        _data_service = S3DataService()
    return _data_service


# ── Document list ────────────────────────────────────────────────────────────


@router.get("/documents")
def list_documents() -> list[dict]:
    """List all media documents from the silver layer."""
    svc = _svc()
    docs = svc.list_documents()

    # Enrich each document with a media URL + thumbnail URL if resolvable
    for doc in docs:
        source_path = doc.get("source_path", "")
        media_url = svc.resolve_media_url(source_path)
        if media_url:
            doc["media_url"] = media_url
            # Add thumbnail URL for video files
            if doc.get("metadata", {}).get("has_video"):
                doc["thumbnail_url"] = media_url.replace("/viewer/media/", "/viewer/media/thumbnail/")

    return docs


# ── Single document ──────────────────────────────────────────────────────────


@router.get("/documents/{document_id}")
def get_document(document_id: str) -> dict:
    """Get a single media document by ID."""
    svc = _svc()
    doc = svc.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found")

    source_path = doc.get("source_path", "")
    media_url = svc.resolve_media_url(source_path)
    if media_url:
        doc["media_url"] = media_url

    return doc


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

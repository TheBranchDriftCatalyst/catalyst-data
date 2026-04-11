"""Annotation API routes — human feedback on pipeline outputs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dagster_io.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/viewer/api", tags=["annotations"])

# Singleton store — initialized in app.py
_store = None


def set_store(store) -> None:
    global _store
    _store = store


def _get_store():
    if _store is None:
        raise HTTPException(503, "Annotation store not initialized")
    return _store


# ── Request/Response Models ──────────────────────────────────────────────────


class AnnotationCreate(BaseModel):
    target_type: str = Field(..., pattern="^(mention|assertion|segment|speaker)$")
    target_id: str
    action: str = Field(..., pattern="^(approve|reject|edit|flag)$")
    edits: dict[str, Any] = Field(default_factory=dict)
    reviewer: str = ""
    notes: str = ""


class AnnotationUpdate(BaseModel):
    action: str | None = None
    edits: dict[str, Any] | None = None
    reviewer: str | None = None
    notes: str | None = None


class BulkAnnotationCreate(BaseModel):
    annotations: list[AnnotationCreate]


class SpeakerMappingUpdate(BaseModel):
    mappings: dict[str, str]  # { "SPEAKER_00": "John Smith", ... }


# ── Annotation Endpoints ─────────────────────────────────────────────────────


@router.get("/documents/{document_id}/annotations")
async def list_annotations(document_id: str) -> list[dict]:
    return _get_store().list_annotations(document_id)


@router.post("/documents/{document_id}/annotations")
async def create_annotation(document_id: str, body: AnnotationCreate) -> dict:
    result = _get_store().create_annotation(
        {
            "document_id": document_id,
            **body.model_dump(),
        }
    )
    if result is None:
        raise HTTPException(500, "Failed to create annotation")
    return result


@router.patch("/annotations/{annotation_id}")
async def update_annotation(annotation_id: str, body: AnnotationUpdate) -> dict:
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    result = _get_store().update_annotation(annotation_id, data)
    if result is None:
        raise HTTPException(404, "Annotation not found or no changes")
    return result


@router.delete("/annotations/{annotation_id}")
async def delete_annotation(annotation_id: str) -> dict:
    if _get_store().delete_annotation(annotation_id):
        return {"deleted": True}
    raise HTTPException(404, "Annotation not found")


@router.post("/documents/{document_id}/annotations/bulk")
async def bulk_create_annotations(document_id: str, body: BulkAnnotationCreate) -> dict:
    annotations = [{"document_id": document_id, **a.model_dump()} for a in body.annotations]
    count = _get_store().bulk_create_annotations(annotations)
    return {"created": count}


# ── Speaker Mapping Endpoints ────────────────────────────────────────────────


@router.get("/documents/{document_id}/speakers")
async def get_speaker_mappings(document_id: str) -> dict:
    return _get_store().get_speaker_mappings(document_id)


@router.patch("/documents/{document_id}/speakers")
async def update_speaker_mappings(document_id: str, body: SpeakerMappingUpdate) -> dict:
    if _get_store().save_speaker_mappings(document_id, body.mappings):
        return {"updated": True, "mappings": body.mappings}
    raise HTTPException(500, "Failed to save speaker mappings")

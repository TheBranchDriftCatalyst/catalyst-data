"""Router factory + per-domain registry for the generic Documents API.

Each registered domain mounts at ``/viewer/api/<domain>/documents`` with
the same shape (list + single-doc detail). Domain-specific extras —
media's transcription/diarization/etc. — stay in their own routers; this
factory is *only* for the generic list+detail surface.

Adding a new domain is one entry in ``DOMAINS`` below.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException

from dagster_io.logging import get_logger
from media_ingest.viewer.services.documents_service import DocumentsService

logger = get_logger(__name__)


@dataclass(frozen=True)
class DomainConfig:
    """One row in the domain registry. Keep this lean — domain-specific
    enrichment (e.g. media's media_url + thumbnail_url) plugs in via the
    optional ``enrich`` callback so we don't pollute the generic shape."""

    slug: str  # URL slug used in /viewer/api/<slug>/documents
    code_location: str
    group: str
    asset: str = "documents"
    label: str = ""  # human-readable; defaults to slug when empty
    enrich: Callable[[dict], dict] | None = None


# Per-process service cache so each domain's S3DataService is built once.
_services: dict[str, DocumentsService] = {}


def _service(cfg: DomainConfig) -> DocumentsService:
    svc = _services.get(cfg.slug)
    if svc is None:
        svc = DocumentsService(cfg.code_location, cfg.group, cfg.asset)
        _services[cfg.slug] = svc
    return svc


def make_documents_router(cfg: DomainConfig) -> APIRouter:
    """Build a FastAPI router for one domain's generic document endpoints."""
    router = APIRouter(prefix=f"/viewer/api/{cfg.slug}", tags=[f"{cfg.slug}-documents"])

    @router.get("/documents")
    def list_documents() -> list[dict]:
        svc = _service(cfg)
        docs = svc.list_documents()
        if cfg.enrich:
            docs = [cfg.enrich(d) for d in docs]
        return docs

    @router.get("/documents/{document_id}")
    def get_document(document_id: str) -> dict:
        svc = _service(cfg)
        doc = svc.get_document(document_id)
        if doc is None:
            raise HTTPException(
                status_code=404,
                detail=f"Document '{document_id}' not found in domain '{cfg.slug}'",
            )
        if cfg.enrich:
            doc = cfg.enrich(doc)
        return doc

    return router


domains_registry_router = APIRouter(prefix="/viewer/api/domains", tags=["domains"])


@domains_registry_router.get("")
def list_domains() -> list[dict]:
    """Domain registry endpoint — frontend uses this to populate the
    Documents sub-tab list and discover backend availability."""
    return [
        {
            "slug": d.slug,
            "label": d.label or d.slug,
            "code_location": d.code_location,
            "group": d.group,
            "asset": d.asset,
        }
        for d in DOMAINS
    ]


# ── Domain registry ─────────────────────────────────────────────────────────


import os as _os

from media_ingest.viewer.services.s3_data import S3DataService as _S3DataService

# Lazy singleton — only built once `_media_enrich` is first called, which
# in turn only happens after media-domain requests start arriving.
_media_legacy_svc: _S3DataService | None = None


def _media_enrich(doc: dict) -> dict:
    """Resolve media_url + thumbnail_url for media-ingest documents.

    Mirrors the existing logic in ``routes/api.py:list_documents`` so the
    generic /viewer/api/media/documents endpoint produces the same shape
    the frontend already consumes.
    """
    global _media_legacy_svc
    if _media_legacy_svc is None:
        _media_legacy_svc = _S3DataService()

    source_path = doc.get("source_path", "")
    media_url = _media_legacy_svc.resolve_media_url(source_path)
    if media_url:
        doc["media_url"] = media_url
        meta = doc.get("metadata", {}) or {}
        ext = (meta.get("extension") or _os.path.splitext(source_path)[1] or "").lower()
        is_video = bool(meta.get("has_video")) or ext in {
            ".mp4",
            ".mkv",
            ".webm",
            ".avi",
            ".mov",
            ".m4v",
        }
        if is_video:
            doc["thumbnail_url"] = media_url.replace("/viewer/media/", "/viewer/media/thumbnail/")
    return doc


DOMAINS: list[DomainConfig] = [
    DomainConfig(
        slug="media",
        code_location="media_ingest",
        group="media",
        asset="media_documents",
        label="media-ingest",
        enrich=_media_enrich,
    ),
    DomainConfig(
        slug="congress",
        code_location="congress_data",
        group="congress",
        asset="congress_documents",
        label="congress-wtf",
    ),
    DomainConfig(
        slug="leaks",
        code_location="open_leaks",
        group="leaks",
        asset="leak_documents",
        label="open-leaks",
    ),
]


def all_routers() -> list[APIRouter]:
    """Return one router per domain plus the registry endpoint. The viewer
    app calls this from its factory and `include_router`s each."""
    return [make_documents_router(d) for d in DOMAINS] + [domains_registry_router]

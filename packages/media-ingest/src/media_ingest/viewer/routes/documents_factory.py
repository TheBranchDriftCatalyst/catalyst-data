"""Router factory + per-domain registry for the generic Documents API.

Each registered domain mounts at ``/viewer/api/<domain>/documents`` with
the same shape (list + single-doc detail). Domain-specific extras —
media's transcription/diarization/etc. — stay in their own routers; this
factory is *only* for the generic list+detail surface.

Adding a new domain is one entry in ``DOMAINS`` below.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException

from dagster_io.logging import get_logger
from media_ingest.viewer.services.documents_service import DocumentsService
from media_ingest.viewer.services.partitioned_assets import PartitionedAssetService

logger = get_logger(__name__)


@dataclass(frozen=True)
class PartitionedAssetSpec:
    """One partitioned asset exposed under the per-domain partitioned-resource
    route (e.g. ``/viewer/api/congress/bills/<partition>/<name>``).

    ``name`` is both the URL segment and the JSON key on the combined detail
    response. ``layer`` × ``asset`` × ``group`` × ``code_location`` resolve
    to the S3 prefix. ``format`` picks the on-disk reader (jsonl | json |
    events). One spec per domain should be marked ``is_primary`` — the
    factory uses that one to enumerate partitions for the list endpoint.
    """

    name: str
    layer: str  # bronze | silver | gold | platinum
    asset: str
    group: str | None = None  # falls back to DomainConfig.group when None
    code_location: str | None = None  # falls back to DomainConfig.code_location
    format: str = "jsonl"  # jsonl | json | events
    is_primary: bool = False


@dataclass(frozen=True)
class DomainConfig:
    """One row in the domain registry. Keep this lean — domain-specific
    enrichment (e.g. media's media_url + thumbnail_url) plugs in via the
    optional ``enrich`` callback so we don't pollute the generic shape.

    ``partitioned`` enables the richer per-partition viewer surface for
    domains where one "document" really means one Dagster partition (e.g.
    a congress bill keyed by ``{congress}-{bill_type}-{number}``). The
    factory mounts ``/viewer/api/<slug>/<partitioned_resource>`` for the
    partition list and ``/viewer/api/<slug>/<partitioned_resource>/<key>/<spec.name>``
    for each individual asset payload.
    """

    slug: str  # URL slug used in /viewer/api/<slug>/documents
    code_location: str
    group: str
    asset: str = "documents"
    label: str = ""  # human-readable; defaults to slug when empty
    enrich: Callable[[dict], dict] | None = None
    partitioned: tuple[PartitionedAssetSpec, ...] = field(default_factory=tuple)
    # URL segment for the partitioned-resource route ("bills", "videos",
    # etc.). Empty string disables the partitioned surface for this domain.
    partitioned_resource: str = ""


# Per-process service cache so each domain's S3DataService is built once.
_services: dict[str, DocumentsService] = {}

# Cache of PartitionedAssetService instances, keyed by (slug, spec.name).
# Each spec maps to one service that knows how to list partitions + load
# a single partition's payload for that asset.
_partitioned_services: dict[tuple[str, str], PartitionedAssetService] = {}


def _service(cfg: DomainConfig) -> DocumentsService:
    svc = _services.get(cfg.slug)
    if svc is None:
        svc = DocumentsService(cfg.code_location, cfg.group, cfg.asset)
        _services[cfg.slug] = svc
    return svc


def _partitioned_service(cfg: DomainConfig, spec: PartitionedAssetSpec) -> PartitionedAssetService:
    key = (cfg.slug, spec.name)
    svc = _partitioned_services.get(key)
    if svc is None:
        svc = PartitionedAssetService(
            layer=spec.layer,
            code_location=spec.code_location or cfg.code_location,
            group=spec.group or cfg.group,
            asset=spec.asset,
            format=spec.format,
        )
        _partitioned_services[key] = svc
    return svc


def _spec_by_name(cfg: DomainConfig, name: str) -> PartitionedAssetSpec | None:
    for spec in cfg.partitioned:
        if spec.name == name:
            return spec
    return None


def _primary_spec(cfg: DomainConfig) -> PartitionedAssetSpec | None:
    for spec in cfg.partitioned:
        if spec.is_primary:
            return spec
    return cfg.partitioned[0] if cfg.partitioned else None


def make_documents_router(cfg: DomainConfig) -> APIRouter:
    """Build a FastAPI router for one domain's document endpoints.

    Always mounts the generic ``/documents`` list+detail surface. When
    ``cfg.partitioned`` is non-empty, additionally mounts the
    partitioned-resource surface at ``/<cfg.partitioned_resource>``.
    """
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

    # ── Partitioned-resource surface (e.g. /viewer/api/congress/bills) ──
    if cfg.partitioned and cfg.partitioned_resource:
        _mount_partitioned_routes(router, cfg)

    return router


def _mount_partitioned_routes(router: APIRouter, cfg: DomainConfig) -> None:
    """Mount the bill-list + per-partition asset routes for one domain.

    Endpoints (with ``resource = cfg.partitioned_resource``):

    - ``GET /<resource>`` — partition keys + a summary dict per partition,
      sourced from the primary spec's ``data.json`` (silver bill_document
      for congress). Light-touch — fetches one ``data.json`` per partition
      but the underlying service caches for 60s.
    - ``GET /<resource>/{partition}`` — full detail = the primary spec's
      payload (the silver document itself).
    - ``GET /<resource>/{partition}/<spec.name>`` — that spec's payload.
    """
    resource = cfg.partitioned_resource
    primary = _primary_spec(cfg)
    if primary is None:
        return

    @router.get(f"/{resource}")
    def list_partitions() -> list[dict]:
        svc = _partitioned_service(cfg, primary)
        partitions = svc.list_partitions()
        summaries: list[dict] = []
        for pkey in partitions:
            row: dict = {"partition": pkey}
            if primary.format == "json":
                # Fold the primary doc directly into the row so consumers
                # see title/metadata without a second roundtrip.
                payload = svc.load(pkey)
                if isinstance(payload, dict):
                    row.update(
                        {
                            "title": payload.get("title"),
                            "metadata": payload.get("metadata", {}),
                            "domain": payload.get("domain"),
                            "document_type": payload.get("document_type"),
                            "source": payload.get("source"),
                        }
                    )
            summaries.append(row)
        return summaries

    @router.get(f"/{resource}/{{partition}}")
    def get_partition_detail(partition: str) -> dict:
        svc = _partitioned_service(cfg, primary)
        payload = svc.load(partition)
        if payload is None:
            raise HTTPException(
                status_code=404,
                detail=(f"Partition '{partition}' not found in {cfg.slug}/{resource} ({primary.asset})"),
            )
        if isinstance(payload, dict):
            return {"partition": partition, **payload}
        # Primary should be a single dict (json). Fall back to wrapping.
        return {"partition": partition, "rows": payload}

    @router.get(f"/{resource}/{{partition}}/{{name}}")
    def get_partition_asset(partition: str, name: str) -> dict:
        spec = _spec_by_name(cfg, name)
        if spec is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Unknown asset '{name}' for {cfg.slug}/{resource}. Available: {[s.name for s in cfg.partitioned]}"
                ),
            )
        svc = _partitioned_service(cfg, spec)
        payload = svc.load(partition)
        # Always wrap so the response shape is predictable per asset format.
        if spec.format == "json":
            if not isinstance(payload, dict):
                raise HTTPException(
                    status_code=404,
                    detail=f"No {name} data for partition '{partition}'",
                )
            return {"partition": partition, "asset": name, "data": payload}
        rows = payload if isinstance(payload, list) else []
        return {
            "partition": partition,
            "asset": name,
            "count": len(rows),
            "rows": rows,
        }


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
        partitioned_resource="bills",
        partitioned=(
            # Primary: the silver per-bill document drives partition
            # enumeration + provides title/metadata for the list view.
            PartitionedAssetSpec(
                name="detail",
                layer="silver",
                asset="bill_document",
                group="bill",
                format="json",
                is_primary=True,
            ),
            PartitionedAssetSpec(
                name="chunks",
                layer="silver",
                asset="bill_chunks",
                group="bill",
                format="jsonl",
            ),
            PartitionedAssetSpec(
                name="assertions",
                layer="gold",
                asset="bill_assertions",
                group="bill",
                format="jsonl",
            ),
            PartitionedAssetSpec(
                name="mentions",
                layer="gold",
                asset="bill_mentions",
                group="bill",
                format="jsonl",
            ),
            # Structured assertions (cosponsor dates, public-law signed
            # dates) live under the ``congress`` group, NOT ``bill`` —
            # the override on ``group`` keeps the prefix correct.
            PartitionedAssetSpec(
                name="structured",
                layer="gold",
                asset="congress_structured_assertions",
                group="congress",
                format="events",
            ),
        ),
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

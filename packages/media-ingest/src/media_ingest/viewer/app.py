"""FastAPI application factory for the media viewer.

Creates the viewer app with:
- REST API routes mounted at /viewer/api/
- Media streaming routes mounted at /viewer/media/
- Static file serving from /app/viewer-static at /viewer/ (production React build)
- CORS middleware for development mode
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from dagster_io.logging import get_logger
from media_ingest.viewer.routes.annotations import router as annotations_router
from media_ingest.viewer.routes.annotations import set_store
from media_ingest.viewer.routes.api import router as api_router
from media_ingest.viewer.routes.bench import router as bench_router
from media_ingest.viewer.routes.bench_runner import router as bench_runner_router
from media_ingest.viewer.routes.docs import router as docs_router
from media_ingest.viewer.routes.documents_factory import all_routers as documents_routers
from media_ingest.viewer.routes.media import router as media_router
from media_ingest.viewer.routes.s3_explorer import router as s3_router
from media_ingest.viewer.services.annotation_store import AnnotationStore

logger = get_logger(__name__)

# Path where the production React build is mounted in the container
_STATIC_DIR = "/app/viewer-static"


class SPAStaticFiles(StaticFiles):
    """StaticFiles with SPA fallback to index.html for any 404.

    React Router client-side routes like ``/viewer/player/<doc_id>`` don't
    correspond to real files on disk. A vanilla StaticFiles mount returns
    404 for them, which produces the FastAPI ``{"detail":"Not Found"}``
    response. This subclass catches the 404 and serves index.html instead,
    letting React Router handle routing in the browser.

    API routes (``/viewer/api/...``, ``/viewer/media/...``,
    ``/viewer/health``, ``/viewer/openapi.json``, ``/viewer/docs``) are
    registered on the FastAPI app itself and match BEFORE this mount, so
    they never reach this fallback.
    """

    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404:
                index = os.path.join(self.directory, "index.html")
                if os.path.isfile(index):
                    return FileResponse(index)
            raise


def create_viewer_app() -> FastAPI:
    """Create and configure the media viewer FastAPI application."""
    app = FastAPI(
        title="Media Viewer",
        description="REST API and media streaming for the media-ingest pipeline",
        docs_url="/viewer/docs",
        openapi_url="/viewer/openapi.json",
    )

    # CORS for dev mode (Vite dev server on a different port)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",  # Vite dev server
            "http://localhost:3000",
            "http://media.talos00",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API, annotation, and media routes.
    # The factory's per-domain document routers (`/viewer/api/<domain>/documents`)
    # go FIRST so list+detail are served by the generic loader; api_router then
    # contributes the media-only extras (transcription/diarization/etc.) on the
    # same `/viewer/api/media` prefix.
    for r in documents_routers():
        app.include_router(r)
    app.include_router(api_router)
    app.include_router(annotations_router)
    app.include_router(media_router)
    app.include_router(s3_router)
    app.include_router(bench_router)
    app.include_router(bench_runner_router)
    app.include_router(docs_router)

    # Initialize annotation store
    store = AnnotationStore()
    set_store(store)

    @app.on_event("shutdown")
    def shutdown_store():
        store.close()

    # Health check
    @app.get("/viewer/health")
    def health() -> dict:
        return {"status": "ok"}

    # Serve React static build with SPA fallback if the directory exists
    if os.path.isdir(_STATIC_DIR):
        logger.info("Serving static files (SPA) from %s at /viewer/", _STATIC_DIR)
        app.mount(
            "/viewer/",
            SPAStaticFiles(directory=_STATIC_DIR, html=True),
            name="viewer-static",
        )
    else:
        logger.info(
            "Static directory %s not found — skipping SPA mount (dev mode?)",
            _STATIC_DIR,
        )

    logger.info("Media viewer app created")
    return app

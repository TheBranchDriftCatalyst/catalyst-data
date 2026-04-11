"""FastAPI application factory for the media viewer.

Creates the viewer app with:
- REST API routes mounted at /viewer/api/
- Media streaming routes mounted at /viewer/media/
- Static file serving from /app/viewer-static at /viewer/ (production React build)
- CORS middleware for development mode
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dagster_io.logging import get_logger

from media_ingest.viewer.routes.api import router as api_router
from media_ingest.viewer.routes.annotations import router as annotations_router, set_store
from media_ingest.viewer.routes.media import router as media_router
from media_ingest.viewer.services.annotation_store import AnnotationStore

logger = get_logger(__name__)

# Path where the production React build is mounted in the container
_STATIC_DIR = "/app/viewer-static"


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
            "http://localhost:5173",   # Vite dev server
            "http://localhost:3000",
            "http://media.talos00",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount API, annotation, and media routes
    app.include_router(api_router)
    app.include_router(annotations_router)
    app.include_router(media_router)

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

    # Serve React static build if the directory exists (production)
    if os.path.isdir(_STATIC_DIR):
        logger.info("Serving static files from %s at /viewer/", _STATIC_DIR)
        app.mount(
            "/viewer/",
            StaticFiles(directory=_STATIC_DIR, html=True),
            name="viewer-static",
        )

        # SPA catch-all: React Router client-side routes like
        # `/viewer/player/<document_id>` need to fall back to index.html
        # rather than 404. The StaticFiles mount only serves exact files.
        # This catch-all matches anything under /viewer/ that isn't an API
        # route (/viewer/api/..., /viewer/media/..., /viewer/health,
        # /viewer/openapi.json, /viewer/docs) or an existing static file,
        # and returns the SPA shell so React Router can handle routing.
        _INDEX = os.path.join(_STATIC_DIR, "index.html")

        @app.get("/viewer/{full_path:path}")
        def spa_fallback(full_path: str, request: Request):
            # API/docs/health routes are matched earlier by their specific
            # handlers; this handler only fires for unmatched paths. But
            # double-check in case of an ordering quirk.
            if full_path.startswith(("api/", "media/", "health", "openapi.json", "docs")):
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail="Not Found")
            # If the requested path maps to a real static file, let the
            # StaticFiles mount handle it (won't reach here in practice
            # because StaticFiles is mounted first).
            candidate = os.path.join(_STATIC_DIR, full_path)
            if os.path.isfile(candidate):
                return FileResponse(candidate)
            # Otherwise serve the SPA shell.
            return FileResponse(_INDEX)
    else:
        logger.info(
            "Static directory %s not found — skipping SPA mount (dev mode?)",
            _STATIC_DIR,
        )

    logger.info("Media viewer app created")
    return app

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
from fastapi.staticfiles import StaticFiles

from dagster_io.logging import get_logger

from media_ingest.viewer.routes.api import router as api_router
from media_ingest.viewer.routes.media import router as media_router

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

    # Mount API and media routes
    app.include_router(api_router)
    app.include_router(media_router)

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
    else:
        logger.info(
            "Static directory %s not found — skipping SPA mount (dev mode?)",
            _STATIC_DIR,
        )

    logger.info("Media viewer app created")
    return app

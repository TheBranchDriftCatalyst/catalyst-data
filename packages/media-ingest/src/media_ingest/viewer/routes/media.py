"""Media file streaming routes with HTTP Range request support.

GET /viewer/media/{source}/{path:path}

Streams files from NFS mounts (/data/metube, /data/tubesync) with:
- HTTP Range requests (206 Partial Content) for seeking
- 256KB chunk generator for memory-efficient streaming
- MIME type detection from file extension
"""

from __future__ import annotations

import mimetypes
import os
import stat
from collections.abc import Generator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from dagster_io.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/viewer/media", tags=["viewer-media"])

# Allowed NFS source roots
_MEDIA_ROOTS: dict[str, str] = {
    "metube": "/data/metube",
    "tubesync": "/data/tubesync",
}

# Supported media extensions
_ALLOWED_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mkv",
        ".webm",  # video
        ".mp3",
        ".m4a",
        ".wav",
        ".flac",  # audio
    }
)

# Chunk size for streaming (256KB)
_CHUNK_SIZE = 256 * 1024

# Additional MIME types not always in the system registry
_EXTRA_MIMES: dict[str, str] = {
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
}


def _detect_mime(file_path: str) -> str:
    """Detect MIME type from extension, with fallback."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in _EXTRA_MIMES:
        return _EXTRA_MIMES[ext]
    mime, _ = mimetypes.guess_type(file_path)
    return mime or "application/octet-stream"


def _resolve_path(source: str, path: str) -> str:
    """Resolve and validate a media file path.

    Raises HTTPException on invalid source, path traversal, missing file,
    or unsupported extension.
    """
    root = _MEDIA_ROOTS.get(source)
    if root is None:
        raise HTTPException(status_code=400, detail=f"Unknown media source: {source}")

    # Normalize and prevent path traversal
    full_path = os.path.normpath(os.path.join(root, path))
    if not full_path.startswith(root):
        raise HTTPException(status_code=403, detail="Path traversal not allowed")

    # Check extension
    ext = os.path.splitext(full_path)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file extension: {ext}")

    # Check file exists and is a regular file
    try:
        st = os.stat(full_path)
        if not stat.S_ISREG(st.st_mode):
            raise HTTPException(status_code=404, detail="Not a regular file")
    except FileNotFoundError as err:
        raise HTTPException(status_code=404, detail="File not found") from err

    return full_path


def _range_stream(file_path: str, start: int, end: int) -> Generator[bytes, None, None]:
    """Yield chunks of a file from start to end (inclusive)."""
    remaining = end - start + 1
    with open(file_path, "rb") as f:
        f.seek(start)
        while remaining > 0:
            chunk_size = min(_CHUNK_SIZE, remaining)
            data = f.read(chunk_size)
            if not data:
                break
            remaining -= len(data)
            yield data


@router.get("/{source}/{path:path}")
def stream_media(source: str, path: str, request: Request) -> Response:
    """Stream a media file with optional HTTP Range support."""
    file_path = _resolve_path(source, path)
    file_size = os.path.getsize(file_path)
    content_type = _detect_mime(file_path)

    range_header = request.headers.get("range")

    if range_header:
        # Parse Range header: "bytes=START-END" or "bytes=START-"
        try:
            range_spec = range_header.strip().removeprefix("bytes=")
            parts = range_spec.split("-", 1)
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else file_size - 1
        except (ValueError, IndexError) as err:
            raise HTTPException(status_code=416, detail="Invalid Range header") from err

        # Clamp values
        start = max(0, start)
        end = min(end, file_size - 1)

        if start > end or start >= file_size:
            raise HTTPException(
                status_code=416,
                detail="Range not satisfiable",
                headers={"Content-Range": f"bytes */{file_size}"},
            )

        content_length = end - start + 1
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Type": content_type,
        }

        return StreamingResponse(
            _range_stream(file_path, start, end),
            status_code=206,
            headers=headers,
            media_type=content_type,
        )

    # Full file response (no Range header)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Content-Type": content_type,
    }

    return StreamingResponse(
        _range_stream(file_path, 0, file_size - 1),
        status_code=200,
        headers=headers,
        media_type=content_type,
    )

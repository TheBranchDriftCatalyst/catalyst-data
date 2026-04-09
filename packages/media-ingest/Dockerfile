# syntax=docker/dockerfile:1
# ---------- stage 0: build React SPA ----------
FROM node:22-slim AS ui-builder
WORKDIR /ui
COPY packages/media-ingest/viewer-ui/package.json packages/media-ingest/viewer-ui/package-lock.json ./
RUN npm ci
COPY packages/media-ingest/viewer-ui/ .
RUN npm run build

# ---------- stage 1: build Python ----------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# 1. Install shared libs — changes infrequently, cached aggressively
COPY libs/catalyst-contracts-core/ /tmp/catalyst-contracts-core/
COPY libs/dagster-io/              /tmp/dagster-io/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system /tmp/catalyst-contracts-core/ && \
    uv pip install --system /tmp/dagster-io/

# 2. Install package dependencies — only re-runs when pyproject.toml changes
#    Use CPU-only PyTorch wheels to avoid pulling ~2GB CUDA runtime
COPY packages/media-ingest/pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv \
    mkdir -p src/media_ingest && \
    touch src/media_ingest/__init__.py && \
    uv pip install --system \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        . && \
    rm -rf src/media_ingest

# 3. Copy real source — changes most often, deps already cached
COPY packages/media-ingest/src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-deps --reinstall-package media-ingest .

# ---------- stage 2: runtime ----------
FROM python:3.12-slim

# Runtime system dependencies:
#   ffmpeg       - media processing / audio extraction
#   libsndfile1  - soundfile (required by pyannote.audio)
#   libgomp1     - OpenMP (required by ctranslate2/faster-whisper)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libsndfile1 libgomp1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

ENV DAGSTER_HOME=/opt/dagster/dagster_home
RUN mkdir -p $DAGSTER_HOME

# Copy React SPA build
COPY --from=ui-builder /ui/dist /app/viewer-static

EXPOSE 4000 8080

CMD ["dagster", "code-server", "start", \
     "--host", "0.0.0.0", \
     "--port", "4000", \
     "--module-name", "media_ingest"]

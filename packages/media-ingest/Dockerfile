# syntax=docker/dockerfile:1
# ---------- media-ingest: app layer on pre-built base ----------
# Build: docker build --platform linux/amd64 -f packages/media-ingest/Dockerfile -t media-ingest .
#
# Uses the pre-built base image (Dockerfile.base) which contains all system
# packages, Intel GPU drivers, PyTorch XPU, OpenVINO, and pip dependencies.
# This Dockerfile only layers shared libs and source code on top (~2 min).
#
# To rebuild the base image (rarely needed):
#   docker build --platform linux/amd64 -f packages/media-ingest/Dockerfile.base \
#       -t ghcr.io/thebranchdriftcatalyst/media-ingest-base:latest .

# ---------- stage 0: build React SPA ----------
FROM node:22-slim AS ui-builder
WORKDIR /ui
COPY packages/media-ingest/viewer-ui/package.json packages/media-ingest/viewer-ui/package-lock.json ./
RUN npm ci
COPY packages/media-ingest/viewer-ui/ .
RUN npm run build

# ---------- stage 1: install libs + source ----------
FROM ghcr.io/thebranchdriftcatalyst/media-ingest-base:latest

WORKDIR /app

# Remove EXTERNALLY-MANAGED marker (base image runtime stage has it)
RUN rm -f /usr/lib/python3.13/EXTERNALLY-MANAGED

# 1. Shared libs
COPY libs/ /app/libs/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --python /usr/bin/python3.13 \
        /app/libs/catalyst-contracts-core/ \
        /app/libs/catalyst-llm-contract-mcp/ \
        /app/libs/catalyst-langgraph-aio/ \
        /app/libs/dagster-io/

# 2. Source code
COPY packages/media-ingest/src/ src/
COPY packages/media-ingest/pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --python /usr/bin/python3.13 \
        --no-deps --reinstall-package media-ingest .

# Copy React SPA build
COPY --from=ui-builder /ui/dist /app/viewer-static

# Prompts: shared first, then domain-specific overrides
COPY k8s/shared/prompts/ /app/prompts/
COPY k8s/media-ingest/prompts/ /app/prompts/
ENV PROMPT_REGISTRY_DIR=/app/prompts

# All other ENV vars (DAGSTER_HOME, PYTHONPATH, LD_LIBRARY_PATH, SYCL_CACHE_DIR)
# are inherited from the base image.

EXPOSE 4000 8080

CMD ["python3", "-m", "dagster", "code-server", "start", \
     "--host", "0.0.0.0", \
     "--port", "4000", \
     "--module-name", "media_ingest"]

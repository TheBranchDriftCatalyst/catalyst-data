# syntax=docker/dockerfile:1
# ---------- media-ingest: OpenVINO + Intel XPU (Arc/Flex) ----------
# Build: docker build --platform linux/amd64 -f packages/media-ingest/Dockerfile -t media-ingest .
#
# Unified image — runs on both GPU and CPU:
# - With gpu.intel.com/i915: diarization uses torch.xpu (5x faster)
# - Without GPU: falls back to CPU transparently
#
# Uses Ubuntu 24.04 (Intel GPU packages require Ubuntu repos).
# Includes OpenVINO GenAI (transcription) + torch XPU (diarization).
#
# Key insight: torch 2.7+xpu wheels auto-install all Intel runtime deps via pip
# (intel-sycl-rt, intel-pti, intel-cmplr-lib-rt, etc). The .so files land in
# {prefix}/lib/ which needs LD_LIBRARY_PATH. No IPEX needed — native XPU since 2.5.
# See: https://docs.pytorch.org/docs/stable/notes/get_start_xpu.html

# ---------- stage 0: build React SPA ----------
FROM node:22-slim AS ui-builder
WORKDIR /ui
COPY packages/media-ingest/viewer-ui/package.json packages/media-ingest/viewer-ui/package-lock.json ./
RUN npm ci
COPY packages/media-ingest/viewer-ui/ .
RUN npm run build

# ---------- stage 1: build Python (Ubuntu + Python 3.12) ----------
FROM ubuntu:25.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.13 python3.13-dev python3.13-venv \
        python3-pip curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Allow uv to install into system Python (Ubuntu marks it EXTERNALLY-MANAGED)
RUN rm -f /usr/lib/python3.13/EXTERNALLY-MANAGED
ENV UV_PYTHON=/usr/bin/python3.13

WORKDIR /app

# 1. Install ML-heavy deps FIRST — these change ~never and take 3-5 min.
#    Layer ordering: heaviest+most-stable deps first so they stay cached
#    when shared libs or source code change.
#
# Step A: Install torch+torchaudio from the XPU index. This auto-pulls
# intel-sycl-rt (libsycl.so), intel-pti (libpti_view.so), and other
# Intel runtime pip packages as declared dependencies of the XPU wheel.
# Uses --mount=type=cache for pip downloads (~2GB torch wheel).
#
# Step B: Install the package with OpenVINO extras from PyPI.
COPY packages/media-ingest/pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cache/uv \
    mkdir -p src/media_ingest && \
    touch src/media_ingest/__init__.py && \
    python3.13 -m pip install \
        "torch>=2.6" "torchaudio>=2.6" \
        --index-url https://download.pytorch.org/whl/xpu && \
    uv pip install --system --python /usr/bin/python3.13 \
        ".[openvino]" && \
    rm -rf src/media_ingest

# 2. Install shared libs — changes more often than ML deps but less than src.
#    Placed AFTER torch so libs/ changes don't bust the 2GB torch cache.
COPY libs/ /app/libs/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --python /usr/bin/python3.13 \
        /app/libs/catalyst-contracts-core/ \
        /app/libs/catalyst-llm-contract-mcp/ \
        /app/libs/catalyst-langgraph-aio/ \
        /app/libs/dagster-io/

# 3. Copy real source — only the src/ dir changes on code updates.
#    Dependencies are already installed, so this layer only reinstalls
#    the local package (fast, no network).
COPY packages/media-ingest/src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --python /usr/bin/python3.13 \
        --no-deps --reinstall-package media-ingest .

# ---------- stage 2: runtime (Ubuntu + Intel GPU) ----------
FROM ubuntu:25.04

ENV DEBIAN_FRONTEND=noninteractive

# System deps + Intel GPU kernel-mode driver userspace (OpenCL + Level Zero)
# The heavy Intel runtime libs (SYCL, PTI, compiler-rt) come from pip packages
# auto-installed as torch XPU dependencies — no apt oneAPI packages needed.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.13 python3.13-venv python3.13-dev libpython3.13 \
        ffmpeg libsndfile1 libgomp1 \
        gnupg ca-certificates wget && \
    wget -qO- https://repositories.intel.com/gpu/intel-graphics.key | \
        gpg --dearmor -o /usr/share/keyrings/intel-graphics.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/intel-graphics.gpg] https://repositories.intel.com/gpu/ubuntu noble unified" \
        > /etc/apt/sources.list.d/intel-gpu.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        ocl-icd-libopencl1 intel-opencl-icd \
        libze-intel-gpu1 libze1 && \
    apt-get purge -y gnupg wget && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Python site-packages + binaries + Intel runtime libs from builder.
# Intel pip packages (intel-pti, intel-sycl-rt, intel-cmplr-lib-rt, etc.)
# install .so files into {prefix}/lib/ via pip's data-files mechanism, so
# we copy the entire /usr/local/lib/ tree to get them all (libpti_view.so,
# libsycl.so, libsvml.so, libumf.so, libtcm.so, etc.).
COPY --from=builder /usr/lib/python3/dist-packages/ /usr/lib/python3/dist-packages/
COPY --from=builder /usr/local/lib/ /usr/local/lib/
COPY --from=builder /usr/local/bin/ /usr/local/bin/
COPY --from=builder /app/libs/ /app/libs/

# CRITICAL: uv copies a standalone Python to /usr/local/bin/python3.13 which
# uses site-packages (source-build convention). The apt-installed Python at
# /usr/bin/python3.13 uses dist-packages (Debian convention). pip/uv install
# packages to dist-packages, so we MUST use the apt Python. Remove the uv
# shadow so PATH resolves to /usr/bin/python3.13.
RUN rm -f /usr/local/bin/python3.13 /usr/local/bin/python3 /usr/local/bin/python
RUN ln -sf /usr/bin/python3.13 /usr/bin/python3 && \
    ln -sf /usr/bin/python3.13 /usr/bin/python

ENV DAGSTER_HOME=/opt/dagster/dagster_home
ENV PYTHONPATH=/app/libs/catalyst-contracts-core/src:/app/libs/catalyst-llm-contract-mcp/src:/app/libs/catalyst-langgraph-aio/src:/app/libs/dagster-io/src:/usr/local/lib/python3.13/dist-packages:/usr/lib/python3/dist-packages
# CRITICAL: Intel pip runtime libs (.so files) install to {prefix}/lib/
# which is NOT on LD_LIBRARY_PATH by default. Without this, torch fails
# with "libpti_view.so.0.10: cannot open shared object file".
ENV LD_LIBRARY_PATH=/usr/local/lib:${LD_LIBRARY_PATH}
ENV SYCL_CACHE_DIR=/tmp/sycl_cache
RUN mkdir -p $DAGSTER_HOME

# Copy React SPA build
COPY --from=ui-builder /ui/dist /app/viewer-static

EXPOSE 4000 8080

CMD ["python3", "-m", "dagster", "code-server", "start", \
     "--host", "0.0.0.0", \
     "--port", "4000", \
     "--module-name", "media_ingest"]

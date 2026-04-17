# syntax=docker/dockerfile:1
# ---------- stage 1: build ----------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY libs/ /app/libs/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system \
        /app/libs/catalyst-contracts-core/ \
        /app/libs/catalyst-llm-contract-mcp/ \
        /app/libs/catalyst-langgraph-aio/ \
        /app/libs/dagster-io/

COPY packages/data-explorer/pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv \
    mkdir -p src/data_explorer && \
    touch src/data_explorer/__init__.py && \
    uv pip install --system . && \
    rm -rf src/data_explorer
COPY packages/data-explorer/src/ src/
COPY packages/data-explorer/.streamlit/ .streamlit/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-deps --reinstall-package data-explorer .

# ---------- stage 2: runtime ----------
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/
COPY --from=builder /app/libs/ /app/libs/

COPY --from=builder /app/src/ src/
COPY --from=builder /app/.streamlit/ .streamlit/

ENV PYTHONPATH=/app/libs/catalyst-contracts-core/src:/app/libs/catalyst-llm-contract-mcp/src:/app/libs/catalyst-langgraph-aio/src:/app/libs/dagster-io/src

EXPOSE 8501

HEALTHCHECK CMD curl --fail http://localhost:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "src/data_explorer/streamlit/app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]

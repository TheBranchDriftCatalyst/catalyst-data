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

COPY packages/knowledge-graph/pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv \
    mkdir -p src/knowledge_graph && \
    touch src/knowledge_graph/__init__.py && \
    uv pip install --system . && \
    rm -rf src/knowledge_graph
COPY packages/knowledge-graph/src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-deps --reinstall-package knowledge-graph .

# ---------- stage 2: runtime ----------
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/
COPY --from=builder /app/libs/ /app/libs/

ENV DAGSTER_HOME=/opt/dagster/dagster_home
ENV PYTHONPATH=/app/libs/catalyst-contracts-core/src:/app/libs/catalyst-llm-contract-mcp/src:/app/libs/catalyst-langgraph-aio/src:/app/libs/dagster-io/src
RUN mkdir -p $DAGSTER_HOME

EXPOSE 4000

CMD ["dagster", "code-server", "start", \
     "--host", "0.0.0.0", \
     "--port", "4000", \
     "--module-name", "knowledge_graph"]

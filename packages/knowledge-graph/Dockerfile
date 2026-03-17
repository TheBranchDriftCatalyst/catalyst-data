# syntax=docker/dockerfile:1
# ---------- stage 1: build ----------
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
COPY packages/knowledge-graph/pyproject.toml .
RUN --mount=type=cache,target=/root/.cache/uv \
    mkdir -p src/knowledge_graph && \
    touch src/knowledge_graph/__init__.py && \
    uv pip install --system . && \
    rm -rf src/knowledge_graph

# 3. Copy real source — changes most often, deps already cached
COPY packages/knowledge-graph/src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system --no-deps --reinstall-package knowledge-graph .

# ---------- stage 2: runtime ----------
FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

ENV DAGSTER_HOME=/opt/dagster/dagster_home
RUN mkdir -p $DAGSTER_HOME

EXPOSE 4000

CMD ["dagster", "code-server", "start", \
     "--host", "0.0.0.0", \
     "--port", "4000", \
     "--module-name", "knowledge_graph"]

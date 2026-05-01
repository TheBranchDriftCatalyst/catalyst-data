# syntax=docker/dockerfile:1
# Multi-stage Node 24 build for the knowledge-graph-api service.
# Build context is the repo root (see .github/workflows/build-image.yaml).

# ---------- stage 1: build ----------
FROM node:24-slim AS builder

WORKDIR /app

# Install deps — cached on lockfile hash
COPY packages/knowledge-graph-api/package.json packages/knowledge-graph-api/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

# Compile TS -> dist/
COPY packages/knowledge-graph-api/tsconfig.json ./
COPY packages/knowledge-graph-api/src/ src/
RUN npm run build

# ---------- stage 2: runtime ----------
FROM node:24-slim

ENV NODE_ENV=production

WORKDIR /app

# Prod deps only — no tsx, no typescript compiler
COPY packages/knowledge-graph-api/package.json packages/knowledge-graph-api/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --omit=dev && npm cache clean --force

COPY --from=builder /app/dist ./dist

EXPOSE 4000

CMD ["node", "dist/index.js"]

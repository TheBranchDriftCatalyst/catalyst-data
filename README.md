# catalyst-data

Dagster data platform for the catalyst homelab. Three code locations processing Congress data, media files, and leaked documents through bronze/silver/gold medallion architecture.

## Code Locations

| Location | Assets | Status |
|----------|--------|--------|
| **congress-data** | 8 (bills, members, committees + transforms) | Stages 1-2 working, 3-6 stubbed |
| **media-ingest** | 5 (discovery, metadata, documents, transcription, embeddings) | Stages 1-3 working, 4-5 stubbed |
| **open-leaks** | 9 (WikiLeaks, ICIJ, Epstein + transforms) | Stage 1 working, 2-6 stubbed |

## Quick Start

```bash
# Install all packages
pip install -e libs/dagster-io -e packages/congress-data -e packages/media-ingest -e packages/open-leaks

# Run tests
pip install pytest
pytest

# Run Dagster dev UI (pick a code location)
cd packages/congress-data && dagster dev -m congress_data
```

## Docker Build

```bash
# Build context is repo root (Dockerfiles reference sibling dirs)
docker build -f packages/congress-data/Dockerfile -t congress-data:latest .
docker build -f packages/media-ingest/Dockerfile -t media-ingest:latest .
docker build -f packages/open-leaks/Dockerfile -t open-leaks:latest .
```

## K8s Deployment

ArgoCD syncs `k8s/` directory to the `catalyst-data` namespace. Everything is consolidated:

- Single namespace (was 4 separate namespaces)
- Deduplicated ExternalSecrets
- Deduplicated NFS volumes

```bash
# Dry run
kubectl apply -k k8s/ --dry-run=client

# Access Dagster UI
open http://dagster.talos00
```

## CI/CD

Push to `main` triggers:
1. `pytest` across all code locations
2. Matrix Docker build for congress-data, media-ingest, open-leaks
3. Push images to `ghcr.io/thebranchdriftcatalyst/<app>:latest`
4. ArgoCD image updater detects new images and rolls out

# catalyst-data

Dagster data platform for the catalyst homelab. Three code locations processing Congress data, media files, and leaked documents through bronze/silver/gold medallion architecture.

## Code Locations

| Location | Assets | Status |
|----------|--------|--------|
| **congress-data** | 8 (bills, members, committees + transforms) | Stages 1-2 working, 3-6 stubbed |
| **media-ingest** | 11 (discovery → transcode → transcription → diarization → segment-merge → chunks → mentions/assertions/embeddings → speakers → entity candidates) | End-to-end working; multi-video benchmark wired (per-video GT/scoring deferred — see CD-vfiq) |
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

## Testing

See **[TESTING.md](TESTING.md)** for the full testing guide, including:
- Unit tests per code location
- Integration pipeline tests (media-ingest, congress-data)
- Extraction benchmark framework (multi-model comparison)
- Multi-video workflow (`audio_manifest.yaml` + per-doc-id pipeline cache)

## Quick start: benchmark workflow

```bash
# One-time: compress raw fixture videos to keep the working tree small
python scripts/fixtures/compress_fixtures.py packages/media-ingest/tests/fixtures/

# Pre-warm the per-doc-id audio cache for every manifest video
HF_TOKEN=hf_xxx WHISPER_BACKEND=mlx-whisper task bench:fixtures:regen

# Materialize the *_chunks Dagster assets for all 3 domains via LocalJsonIOManager
# (writes to .test-output/<domain>/<layer>/.../*_chunks/[<partition>/]data.jsonl —
# same medallion paths MinIO uses in prod, swapped via DAGSTER_IO_BACKEND env)
task bench:chunks:regen

# Run the benchmark — single-video flow (default) or multi-video flow
task bench                         # single-video, all models, ensemble GT, F1 report
task bench -- --all-videos --ensemble gliner-medium  # multi-video extraction (per doc_id)

# View results
task bench:view                    # SPA at http://localhost:5173/viewer/benchmarks
```

See **[BENCHMARK.md](BENCHMARK.md)** for the complete benchmark reference.

## CI/CD

Push to `main` triggers:
1. `pytest` across all code locations
2. Matrix Docker build for congress-data, media-ingest, open-leaks
3. Push images to `ghcr.io/thebranchdriftcatalyst/<app>:latest`
4. ArgoCD image updater detects new images and rolls out

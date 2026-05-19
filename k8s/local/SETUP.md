# Local k3d overlay (`k8s/local`)

This overlay targets the shared `k3d-catalyst-dev` cluster (same cluster
catalyst-llm uses for its `k8s/local` overlay). It mirrors the talos00
prod overlay structurally but swaps:

- ExternalSecrets → `secretGenerator` from gitignored `*.env` files in this dir.
- IngressRoute hosts: `*.talos00` → `*.local.lan` (point /etc/hosts at 127.0.0.1).
- Monitoring (ServiceMonitor / PrometheusRule / GrafanaDashboard) → omitted
  (depends on operators not present in k3d).
- Cross-namespace LiteLLM access → ExternalName Service pointing at the
  deployed talos00 LiteLLM proxy (see `litellm-externalname.yaml`).

## One-time setup

```bash
cd k8s/local
# Copy each *.env.example to *.env and fill in dev values.
for f in *.env.example; do cp "$f" "${f%.example}"; done
$EDITOR *.env

# /etc/hosts on your dev workstation:
sudo tee -a /etc/hosts <<'EOF'
127.0.0.1 dagster.local.lan neo4j.local.lan media-explorer.local.lan
127.0.0.1 kg-graphql.local.lan data-hub.local.lan
EOF

# catalyst-llm namespace must exist for the cross-ns litellm ExternalName.
# If you don't already apply catalyst-llm/k8s/local, create it manually:
kubectl create namespace catalyst-llm
```

## Build + apply

```bash
# Dry-run / inspect:
kustomize build k8s/local | less

# Apply:
kustomize build k8s/local | kubectl apply -f -
```

## Known gotchas

- **NFS-backed PVs won't bind.** `base/storage/{nfs-media-volumes,whisper-models-pvc}.yaml`
  reference TrueNAS at `192.168.1.36`, which the k3d cluster can't reach.
  Pods that need media file access (`media-ingest` jobs, transcription)
  will fail to start in k3d. For that workflow, use the `tilt up`
  docker-compose path described in `docs/LOCAL_DEV_RUNBOOK.md` instead.
- **LiteLLM hits prod.** The ExternalName for `litellm.catalyst-llm` points
  at `litellm.talos00`. Your `llm-credentials.env` must hold a real
  LiteLLM master key (same value as catalyst-llm's `litellm-secrets.env`).
- **No monitoring CRDs.** ServiceMonitor/PrometheusRule/GrafanaDashboard
  resources are not in this overlay — they're only in `k8s/talos00`.

## File layout

```
k8s/local/
├── kustomization.yaml              # the overlay entrypoint
├── ingressroute.yaml               # *.local.lan IngressRoutes
├── litellm-externalname.yaml       # cross-ns ExternalName → litellm.talos00
├── SETUP.md                        # this file
├── dagster-s3-credentials.env.example
├── congress-data-secrets.env.example
├── llm-credentials.env.example
└── hf-credentials.env.example
```

`*.env` (no `.example`) is gitignored — kept out of source control by
`k8s/local/*.env` rule in repo `.gitignore`.

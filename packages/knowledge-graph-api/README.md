# knowledge-graph-api

GraphQL read API over the catalyst-data Neo4j knowledge graph.

Read-only, schema auto-derived from the live Neo4j database at startup via [`@neo4j/introspector`](https://neo4j.com/docs/graphql/current/introspector/). Served by [Apollo Server 5](https://www.apollographql.com/docs/apollo-server/) with its embedded **Apollo Sandbox** query explorer.

- **Local dev:** run natively via `npm run dev` or `tilt up -f packages/knowledge-graph-api/Tiltfile`
- **Production:** deployed to k8s as `knowledge-graph-api` in the `catalyst-data` namespace, exposed at **`http://kg-graphql.talos00`** via Traefik

Tracked in beads: `CD-4bj` (MVP), `CD-0mz` (deployment).

## Prerequisites

- Node `>=20` (tested on 24.9).
- Access to the catalyst-data cluster (so Neo4j is reachable on `localhost:7687`).
- Knowledge graph has been materialized at least once (empty DB → empty schema).

## Run it locally

### Option A: Tilt (recommended)

```bash
tilt up -f packages/knowledge-graph-api/Tiltfile
```

The Tiltfile expects the root `Tiltfile` to already be running (it provides the Neo4j port-forwards on 7474/7687). If you want to run this one standalone, flip `RUN_STANDALONE = True` at the top of `packages/knowledge-graph-api/Tiltfile` so it attaches Neo4j itself.

Tilt gives you auto-reload on source edits, a UI at http://localhost:10350 with live logs, and direct links to Apollo Sandbox and Neo4j Browser.

### Option B: plain npm

1. **Port-forward Neo4j to localhost.** Either:
   - `tilt up` at the repo root — the root `Tiltfile` attaches `neo4j` on 7474/7687. Or:
   - `kubectl port-forward -n catalyst-data svc/neo4j 7687:7687 7474:7474`
2. `cd packages/knowledge-graph-api`
3. `cp .env.example .env` (edit if your connection differs from the defaults)
4. `npm install`
5. `npm run dev`

The server prints:

```
[graphql-api] Connecting to Neo4j at neo4j://localhost:7687 ...
[graphql-api] Neo4j connectivity OK.
[graphql-api] Introspecting Neo4j schema ...
[graphql-api] Introspection complete (536 chars of SDL).
[graphql-api] Ready at http://localhost:4000/
```

## Use it

Open **http://localhost:4000/** in a browser — Apollo Sandbox loads with a full query composer, the auto-generated schema browser, and docs explorer.

### Verified example queries

List entities:

```graphql
{
  entities(limit: 3) {
    canonical_id
    name
    entity_type
    mention_count
  }
}
```

Filter + traverse ASSERTS relationships one hop:

```graphql
{
  entities(where: { entity_type_EQ: "PERSON" }, limit: 2) {
    canonical_id
    name
    mention_count
    assertsEntities(limit: 3) {
      name
      entity_type
    }
  }
}
```

Field names are whatever the introspector derived from your live Neo4j — to browse them interactively, open the **Schema** tab in Apollo Sandbox.

## Graph visualization

Apollo Sandbox is for **query composition** — it does not render graphs visually. For interactive pan/zoom/expand-style graph exploration, open **Neo4j Browser** at **http://localhost:7474** (same Tilt port-forward, auth `neo4j` / `neo4j-homelab`).

## How the schema gets derived

On startup, the server:

1. Connects to Neo4j, calls `driver.verifyConnectivity()` (fails loudly if unreachable).
2. Runs `@neo4j/introspector`'s `toGraphQLTypeDefs(sessionFactory, readonly=true)` against the live DB.
3. Feeds the resulting SDL into `@neo4j/graphql` to build an executable schema with Cypher translation.
4. Serves it via Apollo Server 5.

Because this happens once at boot:

- **Neo4j must be reachable** when the server starts. Not reachable → hard crash. Intentional.
- **Schema does not live-update on data changes.** If new node labels or relationship types appear in Neo4j after boot (e.g., a new `alignment_type` from `packages/knowledge-graph`), restart the server to pick them up. `npm run dev` uses `tsx watch` for file-change reloads but does not retrigger on Neo4j mutations.

## TypeScript note

With `"type": "module"` + `NodeNext` module resolution, TypeScript source files import each other with a `.js` suffix (e.g., `import { loadConfig } from "./env.js"`). The actual file is `env.ts`; TS rewrites the extension at compile time. Normal and expected.

## Production deployment

Deployed to the `catalyst-data` namespace as the `knowledge-graph-api` Deployment + Service + Traefik IngressRoute. Accessible cluster-internally at `knowledge-graph-api.catalyst-data.svc.cluster.local:4000` and externally (on the homelab LAN) at **`http://kg-graphql.talos00`**.

### How it ships

1. **Build**: `.github/workflows/ci.yaml` has a matrix that includes `knowledge-graph-api`. Merges to `main` → `build-image.yaml` workflow builds `packages/knowledge-graph-api/Dockerfile` (multi-stage Node 24, `tsc`-compiled, runs `node dist/index.js`) and pushes to `ghcr.io/thebranchdriftcatalyst/knowledge-graph-api:latest` + a per-SHA tag.
2. **Deploy**: ArgoCD watches this repo's `k8s/` directory. `k8s/kustomization.yaml` references `k8s/knowledge-graph-api/{deployment,service,ingressroute}.yaml`. On sync, ArgoCD creates/updates the resources.

### Known: image-updater not wired up

Like `knowledge-graph` and `data-explorer`, this service is **not** covered by `argocd-image-updater`. A new `:latest` push after CI will not automatically roll the pod. To pick up a new image manually:

```bash
kubectl rollout restart deployment/knowledge-graph-api -n catalyst-data
# or
kubectl delete pod -n catalyst-data -l app=knowledge-graph-api
```

Fixing this requires adding `knowledge-graph-api` to the `argocd-image-updater` annotation on the argocd Application (lives in the GitOps repo, not here).

### No auth

The IngressRoute has no auth middleware, matching the rest of `*.talos00` (LAN-only trust model). If the homelab is ever exposed to a wider network, add a Traefik `basicauth` or `forward-auth` middleware before publishing this hostname.

## Not in scope

- Mutations (the introspector is called with `readonly=true`)
- Unit or integration tests
- Schema hot-reload on Neo4j data changes (restart to pick up new node labels / rel types)
- Built-in graph visualization (use Neo4j Browser)

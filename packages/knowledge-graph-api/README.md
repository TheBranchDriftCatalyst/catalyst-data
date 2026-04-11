# knowledge-graph-api

GraphQL read API over the catalyst-data Neo4j knowledge graph.

MVP: local dev only, read-only, schema auto-derived from the live Neo4j database at startup via [`@neo4j/introspector`](https://neo4j.com/docs/graphql/current/introspector/). Served by [Apollo Server 5](https://www.apollographql.com/docs/apollo-server/) with its embedded **Apollo Sandbox** query explorer.

Tracked in beads: `CD-4bj`.

## Prerequisites

- Node `>=20` (tested on 24.9).
- Access to the catalyst-data cluster (so Neo4j is reachable on `localhost:7687`).
- Knowledge graph has been materialized at least once (empty DB → empty schema).

## Run it

1. **Port-forward Neo4j to localhost.** Either:
   - `tilt up` at the repo root (preferred) — the root `Tiltfile` attaches `neo4j` on ports 7474/7687. Or:
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

## Not in MVP

Explicit out-of-scope list:

- Authentication / authorization (localhost only — do **not** expose this port on a LAN)
- Mutations (the introspector is called with `readonly=true`)
- K8s deployment manifests
- Dockerfile
- Tilt resource for this service (runs natively on your Mac)
- Unit or integration tests
- CI wiring
- Schema hot-reload on Neo4j data changes
- Graph visualization UI (use Neo4j Browser)

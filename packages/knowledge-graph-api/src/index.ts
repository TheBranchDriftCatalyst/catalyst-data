import { ApolloServer, type ApolloServerPlugin, type BaseContext } from "@apollo/server";
import { startStandaloneServer } from "@apollo/server/standalone";
import { Neo4jGraphQL } from "@neo4j/graphql";
import { toGraphQLTypeDefs } from "@neo4j/introspector";
import neo4j, { type Driver, type Session } from "neo4j-driver";

import { loadConfig } from "./env.js";

/**
 * Self-hosted GraphiQL landing page plugin.
 *
 * Apollo Sandbox (the default) loads in an HTTPS iframe from Apollo's CDN,
 * which can't POST to plain-HTTP endpoints (mixed-content block). This plugin
 * serves GraphiQL directly from the server's own origin — same-origin requests,
 * no iframe, no external service dependency for the UI itself.
 */
function graphiqlPlugin(): ApolloServerPlugin<BaseContext> {
  return {
    async serverWillStart() {
      return {
        async renderLandingPage() {
          return {
            html: `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Knowledge Graph — GraphQL</title>
  <style>
    body { height: 100%; margin: 0; width: 100%; overflow: hidden; }
    #graphiql { height: 100vh; }
  </style>
  <link rel="stylesheet" href="https://unpkg.com/graphiql@3/graphiql.min.css" />
</head>
<body>
  <div id="graphiql">Loading GraphiQL…</div>
  <script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script crossorigin src="https://unpkg.com/graphiql@3/graphiql.min.js"></script>
  <script>
    const fetcher = GraphiQL.createFetcher({ url: window.location.origin + '/' });
    ReactDOM.createRoot(document.getElementById('graphiql')).render(
      React.createElement(GraphiQL, { fetcher })
    );
  </script>
</body>
</html>`,
          };
        },
      };
    },
  };
}

async function main(): Promise<void> {
  const config = loadConfig();

  console.log(`[graphql-api] Connecting to Neo4j at ${config.neo4jUri} ...`);
  const driver: Driver = neo4j.driver(
    config.neo4jUri,
    neo4j.auth.basic(config.neo4jUser, config.neo4jPassword),
  );

  // Fail fast if Neo4j is unreachable.
  await driver.verifyConnectivity();
  console.log("[graphql-api] Neo4j connectivity OK.");

  const sessionFactory = (): Session =>
    driver.session({
      defaultAccessMode: neo4j.session.READ,
      database: config.neo4jDatabase,
    });

  console.log("[graphql-api] Introspecting Neo4j schema ...");
  const readonly = true;
  const typeDefs = await toGraphQLTypeDefs(sessionFactory, readonly);
  console.log(
    `[graphql-api] Introspection complete (${typeDefs.length} chars of SDL).`,
  );

  const neoSchema = new Neo4jGraphQL({ typeDefs, driver });
  const schema = await neoSchema.getSchema();

  const server = new ApolloServer({
    schema,
    plugins: [graphiqlPlugin()],
  });

  const { url } = await startStandaloneServer(server, {
    listen: { port: config.port },
  });

  console.log(`[graphql-api] Ready at ${url}`);
  console.log(
    "[graphql-api] Graph visualization: http://localhost:7474 (Neo4j Browser)",
  );

  // Graceful shutdown — so tsx watch restarts cleanly and we don't leak driver sessions.
  const shutdown = async (signal: string): Promise<void> => {
    console.log(`[graphql-api] ${signal} received, shutting down ...`);
    await server.stop();
    await driver.close();
    process.exit(0);
  };
  process.on("SIGINT", () => {
    void shutdown("SIGINT");
  });
  process.on("SIGTERM", () => {
    void shutdown("SIGTERM");
  });
}

main().catch((err: unknown) => {
  console.error("[graphql-api] Startup failed:", err);
  process.exit(1);
});

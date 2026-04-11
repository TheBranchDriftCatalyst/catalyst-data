import { ApolloServer } from "@apollo/server";
import { startStandaloneServer } from "@apollo/server/standalone";
import { Neo4jGraphQL } from "@neo4j/graphql";
import { toGraphQLTypeDefs } from "@neo4j/introspector";
import neo4j, { type Driver, type Session } from "neo4j-driver";

import { loadConfig } from "./env.js";

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
    // Apollo Sandbox is the default landing page in non-production.
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

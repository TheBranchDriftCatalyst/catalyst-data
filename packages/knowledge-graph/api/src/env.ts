// Centralized, typed env config.
// Loaded once at startup. Throws loud errors for missing required values.

import "dotenv/config";

export interface AppConfig {
  neo4jUri: string;
  neo4jUser: string;
  neo4jPassword: string;
  neo4jDatabase: string | undefined;
  port: number;
}

function required(name: string, fallback?: string): string {
  const value = process.env[name] ?? fallback;
  if (value === undefined || value === "") {
    throw new Error(`Missing required env var: ${name}`);
  }
  return value;
}

export function loadConfig(): AppConfig {
  return {
    neo4jUri: required("NEO4J_URI", "neo4j://localhost:7687"),
    neo4jUser: required("NEO4J_USER", "neo4j"),
    neo4jPassword: required("NEO4J_PASSWORD", "neo4j-homelab"),
    neo4jDatabase: process.env.NEO4J_DATABASE,
    port: Number.parseInt(process.env.PORT ?? "4000", 10),
  };
}

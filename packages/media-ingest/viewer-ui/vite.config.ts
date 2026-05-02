import fs from "fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

// The bench harness writes its live run-bus port file under the S3-store's
// local cache root (see libs/dagster-io/src/dagster_io/bench_store.py:
// `_default_local_cache_root`). Vite reads it at dev-server startup so the
// `/viewer/bus` proxy can forward WebSocket traffic to the bus.
const BUS_PORT_FILE = path.resolve(
  __dirname,
  "../../../.test-output/media-ingest/bench-cache/.bus-port",
);

function readBusPort(): number | null {
  try {
    const raw = fs.readFileSync(BUS_PORT_FILE, "utf-8").trim();
    const port = parseInt(raw, 10);
    return Number.isFinite(port) && port > 0 ? port : null;
  } catch {
    return null;
  }
}

const BUS_PORT = readBusPort();
if (BUS_PORT) {
  // eslint-disable-next-line no-console
  console.log(`[vite] bus proxy → http://127.0.0.1:${BUS_PORT} (from .bus-port)`);
} else {
  // eslint-disable-next-line no-console
  console.log("[vite] bus proxy disabled (no .bus-port found — start a bench run to enable live tail)");
}

export default defineConfig({
  base: "/viewer/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      // Workaround: catalyst-ui ThemeProvider doesn't load CSS in consumer builds.
      // TODO: Fix in catalyst-ui 2.2.0 — make ThemeProvider CSS imports work cross-package.
      "catalyst-theme": path.resolve(
        __dirname,
        "node_modules/@thebranchdriftcatalyst/catalyst-ui/dist/contexts/Theme/styles/catalyst.css",
      ),
    },
  },
  server: {
    // Force IPv4 so the bus proxy (target 127.0.0.1) doesn't fail with
    // EADDRNOTAVAIL when Node tries to bind a dual-stack source socket.
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/viewer/api": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
      "/viewer/media": {
        target: "http://localhost:8080",
        changeOrigin: true,
      },
      // Same-origin proxy for the harness run-bus. The bus port is
      // read once from `.bus-port` at vite startup — restart the dev
      // server when starting a new benchmark run. WebSocket upgrade is
      // enabled so the viewer's LiveGantt can stream live events
      // without a cross-origin handshake.
      ...(BUS_PORT
        ? {
            // Forward to the bus with the /viewer/bus prefix intact.
            // The bus mounts duplicate routes under both /<path> and
            // /viewer/bus/<path> so neither side needs path-rewriting,
            // which Vite's WebSocket-upgrade proxy doesn't apply.
            "/viewer/bus": {
              target: `http://127.0.0.1:${BUS_PORT}`,
              changeOrigin: true,
              ws: true,
            },
          }
        : {}),
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});

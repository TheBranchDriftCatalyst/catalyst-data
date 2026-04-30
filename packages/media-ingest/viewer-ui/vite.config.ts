import fs from "fs";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

const GT_DIR = path.resolve(__dirname, "../../../.test-output/media-ingest/ground-truth");

/** Tiny Vite plugin: handles PUT /viewer/ground-truth/*.json to save GT files during dev. */
function gtSavePlugin(): Plugin {
  return {
    name: "gt-save",
    configureServer(server) {
      server.middlewares.use("/viewer/ground-truth", (req, res, next) => {
        if (req.method !== "PUT") return next();
        const filename = req.url?.replace(/^\//, "") || "";
        if (!filename.endsWith(".json") || filename.includes("..")) {
          res.statusCode = 400;
          res.end("Bad filename");
          return;
        }
        let body = "";
        req.on("data", (chunk: Buffer) => (body += chunk.toString()));
        req.on("end", () => {
          const target = path.join(GT_DIR, filename);
          fs.mkdirSync(path.dirname(target), { recursive: true });
          fs.writeFileSync(target, body, "utf-8");
          res.statusCode = 200;
          res.end(JSON.stringify({ saved: target }));
        });
      });
    },
  };
}

export default defineConfig({
  base: "/viewer/",
  plugins: [react(), tailwindcss(), gtSavePlugin()],
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
    },
    // Allow serving benchmark report JSON from .test-output/
    fs: {
      allow: [
        path.resolve(__dirname),
        path.resolve(__dirname, "../../../.test-output/media-ingest"),
      ],
    },
  },
  // Serve benchmark-report.json (and other static files) from .test-output/
  publicDir: path.resolve(__dirname, "../../../.test-output/media-ingest"),
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});

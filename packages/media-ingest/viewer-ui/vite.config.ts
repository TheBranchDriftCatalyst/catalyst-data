import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

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
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});

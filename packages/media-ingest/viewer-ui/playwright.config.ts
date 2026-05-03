import { defineConfig } from "@playwright/test";

const isCI = !!process.env.CI;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  workers: 1, // serial — shared live data, write ops must not conflict

  /**
   * Multi-reporter setup:
   *  - `list`     line-by-line stdout (local dev default)
   *  - `html`     interactive HTML report with traces / screenshots / videos
   *  - `junit`    XML for CI parsers (GitLab, Jenkins, GitHub Actions)
   *  - `json`     machine-readable results for dashboards / scripts
   *  - `github`   PR annotations (only on CI)
   *  - `monocart` V8 code coverage + richer report; collected by the
   *               `coverage` fixture in `e2e/fixtures/coverage.ts`.
   */
  reporter: [
    ["list"],
    ["html", { outputFolder: "test-results/html", open: "never" }],
    ["junit", { outputFile: "test-results/junit.xml" }],
    ["json", { outputFile: "test-results/results.json" }],
    ...(isCI ? ([["github"]] as const) : []),
    [
      "monocart-reporter",
      {
        name: "S3 Explorer E2E + Coverage",
        outputFile: "test-results/monocart/index.html",
        coverage: {
          // Accept every script. `sourceFilter` then narrows the post-
          // sourcemap-resolution view to OUR src/ files. Vite dev serves
          // modules with various URL shapes (localhost-relative, query-
          // strung, /@id/...) so a URL-based entryFilter is fragile —
          // we'd rather over-collect cheaply and filter at the source map
          // layer where real .tsx paths emerge.
          entryFilter: () => true,
          // Match any source under src/ — Vite production sourcemaps emit
          // paths relative to the dist/ directory, e.g.
          //   ../../src/pages/S3Explorer.tsx
          //   ../../src/api/client.ts
          // Reject node_modules so coverage % stays attributable to our code.
          sourceFilter: (sourcePath: string) => {
            if (!sourcePath) return false;
            if (sourcePath.includes("node_modules")) return false;
            return /(^|\/)src\/.*\.(tsx?|jsx?)$/.test(sourcePath);
          },
          reports: [
            ["v8"], // monocart's native HTML + JSON
            ["lcovonly", { file: "lcov.info" }], // for Codecov / SonarQube
            ["console-summary"], // prints %% to stdout at end of run
          ],
          outputDir: "test-results/coverage",
        },
      },
    ],
  ],

  use: {
    baseURL: process.env.VIEWER_URL ?? "http://media-explorer.talos00",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    viewport: { width: 1920, height: 1080 },
  },

  projects: [
    {
      name: "chromium",
      use: {
        browserName: "chromium",
        // Headless for CI, headed for local debugging
        headless: isCI ? true : !process.env.HEADED,
      },
    },
  ],

  // No webServer — tests run against the live deployed instance
});

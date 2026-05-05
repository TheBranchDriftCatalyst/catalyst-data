import { defineConfig } from "@playwright/test";

const isCI = !!process.env.CI;

export default defineConfig({
  testDir: "./e2e",
  // CD-1qqy: globalSetup fails loud when corpora are missing — fixture
  // mode is the only mode, so the corpora dir must be populated before
  // any spec runs.
  globalSetup: "./playwright-global-setup.ts",
  fullyParallel: false,
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  workers: 1, // serial — shared live data, write ops must not conflict
  // 150s per-test cap. State Inspector specs deep-link into the SPA,
  // wait for poll-driven events (3s polling against a multi-MB events
  // response that takes 30–60s on the FastAPI side from a cold parquet
  // cache) to populate, and assert on rendered testids. Discovery alone
  // can take 20s; the SPA's first poll for limit=50000 takes 40s+; then
  // we need ~10s of margin for panel-render. 150s is generous but the
  // dev-server cache amortizes across the suite so the practical cost
  // stays modest.
  timeout: 150_000,
  // Global default for `expect(...)` waits. Specs override per-call when
  // they need a tighter or looser bound — but the default of 5s is too
  // tight for the SPA's poll-driven testids that depend on a 50000-event
  // ndjson response landing.
  expect: { timeout: 60_000 },

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
    // ENV BLEED PREVENTION
    // ────────────────────
    // The fallback here is `localhost:5173` — local Vite dev server,
    // full stop. Deployed (talos / prod) hosts return SPA-fallback HTML
    // for `/viewer/api/*` which silently breaks JSON-parsing fixtures
    // and makes regression specs skip-by-default ("0 passed, 0 failed,
    // N skipped"). NO production hostname is allowed in this fallback
    // chain.
    //
    // ``localhost`` (not ``127.0.0.1``) because Vite's dev server binds
    // exclusively to the ``localhost`` host header by default and
    // returns a blank page when hit on the IPv4 literal — the Gap #4
    // verifier hit this and had to override the script's URL manually.
    // The env-guard in ``e2e/fixtures/coverage.ts`` still accepts
    // 127.0.0.1 / ::1 if explicitly set, so dev configs that pin the
    // IPv4 literal continue to work.
    //
    // If `PLAYWRIGHT_BASE_URL` or `VIEWER_URL` is set in the agent's
    // shell, the env-guard in `e2e/fixtures/coverage.ts` will assert it
    // resolves to a localhost address and fail the run loud if it does
    // not. That's the active defense — this default is the passive one.
    baseURL:
      process.env.PLAYWRIGHT_BASE_URL ??
      process.env.VIEWER_URL ??
      "http://localhost:5173",
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

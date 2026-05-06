import { defineConfig } from "@playwright/test";
import os from "node:os";

const isCI = !!process.env.CI;

// Coverage collection (V8 + monocart) adds 1-3s/test of overhead and is
// only useful when you actually want a coverage report. Off by default;
// flip on with `COLLECT_COVERAGE=1` for the report run.
const COLLECT_COVERAGE = process.env.COLLECT_COVERAGE === "1";

// Workers: file-level parallelism (each worker runs one spec file at a
// time, tests within a file stay serial). With `workers: 1` the previous
// full run took 53 minutes; bumping to ~half the available cores gets it
// to ~10-15 minutes. Override via `PW_WORKERS` if a slow machine needs
// throttling.
const WORKERS = process.env.PW_WORKERS
  ? Math.max(1, parseInt(process.env.PW_WORKERS, 10))
  : Math.max(2, Math.floor(os.cpus().length / 2));

export default defineConfig({
  testDir: "./e2e",
  // CD-1qqy: globalSetup fails loud when corpora are missing — fixture
  // mode is the only mode, so the corpora dir must be populated before
  // any spec runs.
  globalSetup: "./playwright-global-setup.ts",
  // Tests within a file stay serial (avoids any in-file ordering surprises
  // around shared module state); files run in parallel across workers.
  fullyParallel: false,
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  workers: WORKERS,
  // Per-test cap — fixture-mode serves all data from disk, so a passing
  // test takes ≤5s; 30s leaves margin for Vite cold-compile on the first
  // navigation. (When tests fail, this just affects how long they take
  // to fail.)
  timeout: 30_000,
  // Global default for `expect(...)` waits. Fixture-mode is instant; 8s
  // is enough for any synchronous render assertion. Specs that genuinely
  // need to wait longer (e.g. for a poll cycle) override per-call.
  expect: { timeout: 8_000 },

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
    // monocart V8 coverage is gated behind COLLECT_COVERAGE=1 because it
    // adds 1-3s/test of overhead and is only useful when generating a
    // coverage report. Default runs skip it entirely.
    ...(COLLECT_COVERAGE
      ? ([
          [
            "monocart-reporter",
            {
              name: "Viewer UI E2E + Coverage",
              outputFile: "test-results/monocart/index.html",
              coverage: {
                entryFilter: () => true,
                sourceFilter: (sourcePath: string) => {
                  if (!sourcePath) return false;
                  if (sourcePath.includes("node_modules")) return false;
                  return /(^|\/)src\/.*\.(tsx?|jsx?)$/.test(sourcePath);
                },
                reports: [
                  ["v8"],
                  ["lcovonly", { file: "lcov.info" }],
                  ["console-summary"],
                ],
                outputDir: "test-results/coverage",
              },
            },
          ],
        ] as const)
      : []),
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

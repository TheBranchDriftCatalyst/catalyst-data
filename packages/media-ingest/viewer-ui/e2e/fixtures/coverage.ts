/**
 * Per-test V8 coverage fixture for monocart-reporter.
 *
 * Wraps every `test(...)` so Chromium's `coverage.startJSCoverage()` runs
 * before and `stopJSCoverage()` runs after, with the resulting V8 records
 * forwarded to monocart via `attach`. Monocart aggregates across the whole
 * run, source-maps back to .tsx / .ts files, and writes:
 *
 *   - test-results/coverage/index.html   (interactive coverage report)
 *   - test-results/coverage/lcov.info    (Codecov / SonarQube)
 *   - console-summary at end of run
 *
 * Usage: import `test` from this file instead of `@playwright/test`.
 */

import { test as base, expect } from "@playwright/test";
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore — monocart ships its own .d.ts but resolution can be quirky in some setups
import MCR from "monocart-reporter";

// ── ENV BLEED GUARD ───────────────────────────────────────────────────────
// First line of defense against ENV bleed. Every E2E spec imports `test`
// from this file, so the assertion below runs at module-load time —
// BEFORE any test, fixture, or hook executes — for the entire suite.
//
// Why: agent shells (or stale dotfiles) sometimes leak a deployed
// `media-explorer.talos00` URL into VIEWER_URL/PLAYWRIGHT_BASE_URL. That
// returns SPA-fallback HTML for `/viewer/api/*`, every discovery helper
// silently fails, and specs report `0 passed / 0 failed / N skipped`.
// We'd rather the run die loud at startup than pretend to pass.
//
// Allow-list is strict: only loopback hosts (`localhost`, `127.0.0.1`,
// `::1`) pass. If you need to point tests at a non-local sandbox in the
// future, add it explicitly here rather than weakening the regex.
(() => {
  const url =
    process.env.PLAYWRIGHT_BASE_URL ??
    process.env.VIEWER_URL ??
    "http://localhost:5173";
  let host: string;
  try {
    host = new URL(url).hostname;
  } catch {
    throw new Error(
      `E2E baseURL must point at localhost; got ${url} (unparseable URL). ` +
        `Either unset VIEWER_URL/PLAYWRIGHT_BASE_URL or run via \`task test:e2e:local\`.`,
    );
  }
  const localish =
    host === "127.0.0.1" || host === "localhost" || host === "::1";
  if (!localish) {
    throw new Error(
      `E2E baseURL must point at localhost; got ${url}. ` +
        `Either unset VIEWER_URL/PLAYWRIGHT_BASE_URL or run via \`task test:e2e:local\`. ` +
        `(viewer-ui dev server at 127.0.0.1:5173 proxies /viewer/api/* → :8080; ` +
        `deployed talos hosts return SPA-fallback HTML and silently break discovery helpers.)`,
    );
  }
})();

export const test = base.extend({
  // Auto-fixture: runs even when a test doesn't reference `coverage`.
  coverage: [
    async ({ page, browserName }, use) => {
      const supported = browserName === "chromium";
      if (supported) {
        await page.coverage.startJSCoverage({
          resetOnNavigation: false,
        });
      }
      await use(undefined);
      if (supported) {
        const records = await page.coverage.stopJSCoverage();
        await MCR.addCoverageReport(records, test.info());
      }
    },
    { auto: true },
  ],
});

export { expect };

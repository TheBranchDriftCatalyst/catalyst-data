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

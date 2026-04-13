import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1, // serial — shared live data, write ops must not conflict
  reporter: process.env.CI ? "github" : "html",

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
        headless: process.env.CI ? true : !process.env.HEADED,
      },
    },
  ],

  // No webServer — tests run against the live deployed instance
});

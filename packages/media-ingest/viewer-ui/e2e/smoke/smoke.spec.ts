import { test, expect } from "../fixtures/coverage";
import {
  CANONICAL_DOC_ID,
  CANONICAL_DOC_TITLE,
  MIN_DOC_COUNT,
  MIN_SEGMENT_COUNT,
} from "../fixtures/constants";

test.describe("Smoke Tests @smoke", () => {
  test("Documents page loads media-ingest sub-tab and shows documents", async ({ page }) => {
    await page.goto("/viewer/documents/media-ingest");
    await page.waitForLoadState("networkidle");

    // Sub-tab row + active tab indicator
    await expect(page.getByTestId("documents-subtabs")).toBeVisible();
    await expect(page.getByTestId("documents-subtab-media-ingest")).toBeVisible();

    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();

    // Count document cards — link elements that point to /viewer/player/
    const cards = page.locator("a[href^='/viewer/player/']");
    const count = await cards.count();
    // Cards appear in both sidebar and main content, so divide roughly
    expect(count).toBeGreaterThanOrEqual(MIN_DOC_COUNT);
  });

  test("Root URL redirects to /documents/media-ingest", async ({ page }) => {
    await page.goto("/viewer/");
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/documents/media-ingest");
    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();
  });

  test("Documents search filters media-ingest documents", async ({ page }) => {
    await page.goto("/viewer/documents/media-ingest");
    await page.waitForLoadState("networkidle");

    const searchInput = page.getByPlaceholder("Search by title...");
    await expect(searchInput).toBeVisible();

    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();

    await searchInput.fill("Full Show");
    await page.waitForTimeout(500);

    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();
    await expect(page.getByRole("heading", { name: /Full Show/ }).first()).toBeVisible();
  });

  test("Sub-tab navigation: media-ingest → congress-wtf → open-leaks", async ({ page }) => {
    await page.goto("/viewer/documents/media-ingest");
    await page.waitForLoadState("networkidle");

    await page.getByTestId("documents-subtab-congress-wtf").click();
    await expect(page).toHaveURL(/\/documents\/congress-wtf$/);
    await expect(page.getByTestId("documents-congress-wtf-placeholder")).toBeVisible();

    await page.getByTestId("documents-subtab-open-leaks").click();
    await expect(page).toHaveURL(/\/documents\/open-leaks$/);
    await expect(page.getByTestId("documents-open-leaks-placeholder")).toBeVisible();

    await page.getByTestId("documents-subtab-media-ingest").click();
    await expect(page).toHaveURL(/\/documents\/media-ingest$/);
    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();
  });

  test("Unknown domain redirects to default media-ingest", async ({ page }) => {
    await page.goto("/viewer/documents/does-not-exist");
    await page.waitForLoadState("networkidle");
    expect(page.url()).toContain("/documents/media-ingest");
  });

  test("Player page loads for canonical document", async ({ page }) => {
    await page.goto(`/viewer/player/${CANONICAL_DOC_ID}`);

    // Wait for title to appear (may take time to load data)
    const title = page.getByRole("heading", { level: 1 }).filter({ hasText: /Full Show/i });
    await expect(title).toBeVisible({ timeout: 15_000 });

    // Media player should be present
    const mediaElement = page.locator("video, audio");
    await expect(mediaElement.first()).toBeVisible({ timeout: 10_000 });

    // Transcript should have segments
    const segments = page.locator("[data-segment-index]");
    await expect(segments.first()).toBeVisible({ timeout: 15_000 });
    const segmentCount = await segments.count();
    expect(segmentCount).toBeGreaterThanOrEqual(MIN_SEGMENT_COUNT);

    // Entities tab should be visible
    await expect(page.getByRole("tab", { name: /Entities/ })).toBeVisible();
  });

  test("Navigation: media-ingest list -> Player -> Back", async ({ page }) => {
    await page.goto("/viewer/documents/media-ingest");
    await page.waitForLoadState("networkidle");

    const docLink = page.locator(`a[href='/viewer/player/${CANONICAL_DOC_ID}']`).first();
    await expect(docLink).toBeVisible();
    await docLink.click();

    const title = page.getByRole("heading", { level: 1 }).filter({ hasText: /Full Show/i });
    await expect(title).toBeVisible({ timeout: 15_000 });
    expect(page.url()).toContain(`/player/${CANONICAL_DOC_ID}`);

    // Back arrow lives in the player and now points at the documents list.
    const backLink = page
      .locator("a[href='/viewer/documents/media-ingest'], a[href='/viewer/documents']")
      .first();
    await backLink.click();
    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();
  });

  test("API health: /viewer/api/documents returns data", async ({ request, baseURL }) => {
    const resp = await request.get(`${baseURL}/viewer/api/documents`);

    expect(resp.status()).toBe(200);

    const docs = await resp.json();
    expect(Array.isArray(docs)).toBe(true);
    expect(docs.length).toBeGreaterThanOrEqual(MIN_DOC_COUNT);

    for (const doc of docs) {
      expect(doc).toHaveProperty("id");
      expect(doc).toHaveProperty("title");
      expect(doc).toHaveProperty("source");
    }
  });
});

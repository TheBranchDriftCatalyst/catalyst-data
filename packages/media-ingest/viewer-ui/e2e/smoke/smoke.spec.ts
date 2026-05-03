import { test, expect } from "../fixtures/coverage";
import {
  CANONICAL_DOC_ID,
  CANONICAL_DOC_TITLE,
  MIN_DOC_COUNT,
  MIN_SEGMENT_COUNT,
} from "../fixtures/constants";

test.describe("Smoke Tests @smoke", () => {
  test("DocumentList page loads and shows documents", async ({ page }) => {
    await page.goto("/viewer/");
    await page.waitForLoadState("networkidle");

    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();

    // Count document cards — link elements that point to /viewer/player/
    const cards = page.locator("a[href^='/viewer/player/']");
    const count = await cards.count();
    // Cards appear in both sidebar and main content, so divide roughly
    expect(count).toBeGreaterThanOrEqual(MIN_DOC_COUNT);
  });

  test("DocumentList search filters documents", async ({ page }) => {
    await page.goto("/viewer/");
    await page.waitForLoadState("networkidle");

    // Use the main content search (not sidebar)
    const searchInput = page.getByPlaceholder("Search by title...");
    await expect(searchInput).toBeVisible();

    // Count cards before (main content heading area)
    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();

    await searchInput.fill("Full Show");
    // Wait for filter to apply
    await page.waitForTimeout(500);

    // The heading should still be visible
    await expect(page.getByRole("heading", { name: "Media Library" })).toBeVisible();

    // After filtering, there should be fewer documents visible
    // Check that "Full Show" document is visible
    await expect(page.getByRole("heading", { name: /Full Show/ }).first()).toBeVisible();
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

  test("Navigation: DocumentList -> Player -> Back", async ({ page }) => {
    await page.goto("/viewer/");
    await page.waitForLoadState("networkidle");

    // Click the canonical document (find by URL)
    const docLink = page.locator(`a[href='/viewer/player/${CANONICAL_DOC_ID}']`).first();
    await expect(docLink).toBeVisible();
    await docLink.click();

    // Verify player loaded
    const title = page.getByRole("heading", { level: 1 }).filter({ hasText: /Full Show/i });
    await expect(title).toBeVisible({ timeout: 15_000 });
    expect(page.url()).toContain(`/player/${CANONICAL_DOC_ID}`);

    // Go back — find the back arrow link
    const backLink = page.locator("a[href='/viewer']").first();
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

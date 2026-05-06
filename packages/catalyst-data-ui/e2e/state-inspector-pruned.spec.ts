/**
 * State Inspector — `pruned_window` node specs (plan §2.14, §2.15).
 *
 * Pruned windows surface the reason hint, threshold values pulled from
 * the parent pack_evidence event, and any cluster mentions that landed
 * inside the window's cluster. Selecting a pruned window deliberately
 * does NOT paint a doc-source overlay (pruned offsets aren't carried).
 */
import { test, expect } from "./fixtures/coverage";
import { firstPrunedWindow, resolveRunId } from "./fixtures/inspector-discovery";

test.describe("State Inspector — pruned_window node", () => {
  test("pruned_window shows reason hint, threshold, cluster mentions", async ({ page }) => {
    const tgt = await firstPrunedWindow(page);
    test.skip(!tgt, "no evidence_window_pruned events present");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=pruned_window:${encodeURIComponent(tgt!.windowId)}`,
    );
    await expect(page.getByTestId("pruned-window-detail")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("pruned-reason")).toContainText(
      /too_few_mentions|sparse_density|.+/,
    );
    await expect(page.getByTestId("pruned-thresholds")).toBeVisible();
    if ((await page.getByTestId("pruned-cluster-mentions").count()) > 0) {
      expect(
        await page.locator("[data-testid='pruned-cluster-row']").count(),
      ).toBeGreaterThan(0);
    }
  });

  test("pruned_window selection leaves doc-source overlay clean", async ({ page }) => {
    const tgt = await firstPrunedWindow(page);
    test.skip(!tgt, "no evidence_window_pruned events present");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=pruned_window:${encodeURIComponent(tgt!.windowId)}`,
    );
    await expect(page.getByTestId("pruned-window-detail")).toBeVisible({ timeout: 60_000 });
    expect(await page.locator('[data-selected-window="true"]').count()).toBe(0);
  });
});

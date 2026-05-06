/**
 * State Inspector — `document` node specs (plan §2.1, §2.2).
 *
 * Drives selection by URL state (`?run=&doc=&node=document`) — see the
 * window-overlay reference spec for the rationale (ReactFlow's SVG hit
 * boxes are flaky from Playwright). Discovery via `firstDocWithChunks`
 * which only returns docs that emitted at least one input `chunk_loaded`
 * event so the ChunksDetail list is guaranteed non-empty.
 *
 * Pinning ``?run=`` is critical: the SPA's run picker defaults to "follow
 * latest", which usually points at the in-flight live run that has no
 * data for the doc the discovery helper picked from an older run. Passing
 * the helper's run id keeps SPA + helper in lockstep.
 */
import { test, expect } from "./fixtures/coverage";
import { firstDocWithChunks, resolveRunId } from "./fixtures/inspector-discovery";

test.describe("State Inspector — document node", () => {
  test("document node renders Upstream + ChunksDetail @smoke", async ({ page }) => {
    const tgt = await firstDocWithChunks(page);
    test.skip(!tgt, "no doc with chunk_loaded events");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=document`,
    );
    await expect(page.getByTestId("inspector-detail-document")).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByTestId("upstream-panel")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("chunks-detail")).toBeVisible({ timeout: 60_000 });
    expect(await page.locator("[data-testid^='chunk-row-']").count()).toBeGreaterThan(0);
  });

  test("chunk row expands to full text", async ({ page }) => {
    const tgt = await firstDocWithChunks(page);
    test.skip(!tgt, "no doc with chunk_loaded events");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=document`,
    );
    // Wait for chunks-detail to render — it only mounts when ≥1 chunk_loaded
    // event for the doc has landed (per ChunksDetail's guard). The SPA's
    // first poll for the events ndjson takes 30s+ on a cold dev-server
    // boot, so we depend on the global expect-timeout (60s) here.
    await expect(page.getByTestId("chunks-detail")).toBeVisible({
      timeout: 60_000,
    });
    const row = page.locator("[data-testid^='chunk-row-']").first();
    await row.waitFor({ state: "visible", timeout: 60_000 });
    await row.click();
    await expect(row).toHaveAttribute("data-expanded", "true");
    await expect(row.locator("[data-testid='chunk-row-fulltext']")).toBeVisible();
  });
});

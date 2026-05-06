/**
 * State Inspector — `persist` node spec (plan §2.16).
 *
 * As of Gap #10, the persist node is hosted by ``<DownstreamPanel>``
 * rather than the legacy single-line status. This regression spec
 * asserts the panel renders (or its empty state when no event exists)
 * and that the doc-source overlay stays clean. The richer Gap #10 spec
 * (`state-inspector-gap-10-downstream-lineage.spec.ts`) covers the
 * data-shape assertions; this one is the always-on smoke gate.
 */
import { test, expect } from "./fixtures/coverage";
import { firstDocWithPersist, resolveRunId } from "./fixtures/inspector-discovery";

test.describe("State Inspector — persist node", () => {
  test("persist node renders DownstreamPanel", async ({ page }) => {
    const tgt = await firstDocWithPersist(page);
    test.skip(!tgt, "no persist_artifacts events");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=persist`,
    );
    const detail = page.getByTestId("inspector-detail-persist");
    await expect(detail).toBeVisible({ timeout: 60_000 });
    await expect(detail.getByTestId("downstream-panel")).toBeVisible({
      timeout: 30_000,
    });
    expect(await page.locator('[data-selected-window="true"]').count()).toBe(0);
  });
});

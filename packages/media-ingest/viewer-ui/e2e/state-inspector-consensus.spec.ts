/**
 * State Inspector — `consensus` node specs (plan §2.5, §2.6, §2.7, §2.17).
 *
 * Sort by type uses the inner ``consensus-row-type`` testid the
 * MentionTable emits when a consensus row's parent rowTestId ends with
 * ``-row``. Rejected disclosure tests open/close round-trip on
 * ``consensus-rejected-list`` which is the MentionTable container.
 *
 * Pinning ``?run=`` keeps the SPA on the run the helper found the shape
 * in — otherwise the SPA defaults to "follow latest" which is usually
 * the in-flight run with no consensus events for the chosen doc.
 */
import { test, expect } from "./fixtures/coverage";
import { firstDocWithConsensus, resolveRunId } from "./fixtures/inspector-discovery";

test.describe("State Inspector — consensus node", () => {
  test("consensus shows accepted+rejected counts and lists @smoke", async ({ page }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with mention_decision events");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=consensus`,
    );
    await expect(page.getByTestId("consensus-detail")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("consensus-accepted-count")).toContainText(/^\d+$/);
    await expect(page.getByTestId("consensus-rejected-count")).toContainText(/^\d+$/);
    await expect(
      page.locator("[data-testid='consensus-accepted-row']").first(),
    ).toBeVisible({ timeout: 60_000 });
    expect(
      await page.locator("[data-testid='consensus-accepted-row']").count(),
    ).toBeGreaterThan(0);
  });

  test("consensus sort by type re-orders rows alphabetically", async ({ page }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with mention_decision events");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=consensus`,
    );
    await expect(page.getByTestId("consensus-detail")).toBeVisible({ timeout: 60_000 });
    await page
      .locator("[data-testid='consensus-accepted-row']")
      .first()
      .waitFor({ state: "visible", timeout: 60_000 });
    await page.getByTestId("consensus-sort-type").click();
    const types = await page
      .locator("[data-testid='consensus-accepted-row-type']")
      .allTextContents();
    test.skip(types.length < 2, "need ≥2 accepted rows to test sort order");
    expect([...types]).toEqual([...types].sort());
  });

  test("consensus rejected disclosure toggles open", async ({ page }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with mention_decision events");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=consensus`,
    );
    const toggle = page.getByTestId("consensus-rejected-toggle");
    await toggle.waitFor({ state: "visible", timeout: 60_000 });
    await toggle.click();
    await expect(page.getByTestId("consensus-rejected-list")).toBeVisible();
    await toggle.click();
    await expect(page.getByTestId("consensus-rejected-list")).toHaveCount(0);
  });

  test("consensus selection paints inline accepted-mention spans", async ({ page }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with mention_decision events");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=consensus`,
    );
    await expect(page.getByTestId("consensus-detail")).toBeVisible({ timeout: 60_000 });
    // Wait until at least one consensus mention span paints — the doc
    // payload + chunk_extracted:_consensus event have to both arrive.
    await expect(
      page.locator("[data-mention-source='consensus']").first(),
    ).toBeVisible({ timeout: 60_000 });
    expect(
      await page.locator("[data-mention-source='consensus']").count(),
    ).toBeGreaterThan(0);
  });
});

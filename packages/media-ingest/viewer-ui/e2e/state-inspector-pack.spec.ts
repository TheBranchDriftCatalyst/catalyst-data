/**
 * State Inspector — `pack` node specs (plan §2.8, §2.9, §2.18).
 *
 * Pack detail shows the kept-window table rendered from
 * pack_evidence.kept_windows[]. Selecting the pack node also paints
 * emerald kept-window bands on the doc-source panel via the
 * `data-pack-window-status="kept"` attribute the panel sets per
 * segment.
 *
 * Pinning ``?run=`` keeps the SPA on the same run the discovery helper
 * resolved against — otherwise the SPA defaults to "follow latest"
 * (typically a live run with no pack_evidence yet).
 */
import { test, expect } from "./fixtures/coverage";
import { firstDocWithPackEvidence, resolveRunId } from "./fixtures/inspector-discovery";

test.describe("State Inspector — pack node", () => {
  test("pack node renders kept window table + summary stats @smoke", async ({ page }) => {
    const tgt = await firstDocWithPackEvidence(page);
    test.skip(!tgt, "no doc with pack_evidence completed");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=pack`,
    );
    await expect(page.getByTestId("pack-detail")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("pack-kept-count")).toContainText(/^\d+$/);
    await expect(
      page.locator("[data-testid^='pack-kept-row-']").first(),
    ).toBeVisible({ timeout: 60_000 });
    expect(
      await page.locator("[data-testid^='pack-kept-row-']").count(),
    ).toBeGreaterThan(0);
  });

  test("pack selection paints emerald kept bands on doc source", async ({ page }) => {
    const tgt = await firstDocWithPackEvidence(page);
    test.skip(!tgt, "no doc with pack_evidence completed");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=pack`,
    );
    await expect(
      page.locator("[data-pack-window-status='kept']").first(),
    ).toBeVisible({ timeout: 60_000 });
    expect(
      await page.locator("[data-pack-window-status='kept']").count(),
    ).toBeGreaterThan(0);
  });

  // §2.18 — kept-row click drills into spo_window. PackDetail's <tr> rows
  // currently render without an onClick handler, so this interaction
  // doesn't exist in the UI yet. Skip with a TODO; wiring it requires a
  // frontend change (pass onSelectNode into PackDetail, attach onClick to
  // the kept rows) which is out of scope for this wave.
  test.skip("pack kept-row click selects matching spo_window", async () => {
    // TODO: wire onSelectNode into PackDetail keep rows — see data-scientist gap doc.
  });
});

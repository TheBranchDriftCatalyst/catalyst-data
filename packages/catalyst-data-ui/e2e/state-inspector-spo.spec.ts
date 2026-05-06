/**
 * State Inspector — SPO node specs (plan §2.10, §2.11, §2.12, §2.13).
 *
 * Covers the spo_windows_collapsed matrix (drill-into + zero-prop
 * pathology banner), spo_window proposition list, and spo_model
 * per-window matrix. Each deep-link pins ``?run=`` to the helper's
 * resolved run so the SPA reads the same audit log.
 */
import { test, expect } from "./fixtures/coverage";
import {
  firstDocWithSpoWindows,
  firstDocWithZeroPropPathology,
  firstWindowWithPropositions,
  firstSpoModelWithWindows,
  resolveRunId,
} from "./fixtures/inspector-discovery";

test.describe("State Inspector — SPO nodes", () => {
  test("collapsed matrix → row click drills into spo_window", async ({ page }) => {
    const tgt = await firstDocWithSpoWindows(page);
    test.skip(!tgt, "no doc with :win- chunk_extracted events");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=spo_windows_collapsed`,
    );
    await expect(page.getByTestId("spo-collapsed-matrix")).toBeVisible({ timeout: 60_000 });
    const row = page.locator("[data-testid^='spo-collapsed-row-']").first();
    await row.waitFor({ state: "visible", timeout: 60_000 });
    await row.click();
    // The deep-link encodes ``:`` as ``%3A`` because writeQuery() runs the
    // URLSearchParams toString() — so accept either literal or escaped.
    await expect(page).toHaveURL(/node=spo_window(?::|%3A)/);
    // Detail panel: ChunkTextPanel only renders its testid when a
    // matching ``chunk_loaded`` event lives in the SPA's events list
    // (see StateInspector.DetailRouter for ``spo_window``). Backends
    // don't emit ``chunk_loaded`` for derived ``:win-*`` windows, so
    // assert on the role-tagged inspector wrapper that DOES render
    // unconditionally instead. (chunk-text-panel only renders on docs
    // whose loader replayed window-level chunks — out of scope for v4.)
    await expect(page.getByTestId("inspector-detail-spo_window")).toBeVisible({
      timeout: 60_000,
    });
  });

  test("collapsed shows pathology banner when no model produced props", async ({ page }) => {
    const tgt = await firstDocWithZeroPropPathology(page);
    test.skip(!tgt, "no run exhibits the all-zero-props pathology");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=spo_windows_collapsed`,
    );
    await expect(page.getByTestId("spo-collapsed-pathology")).toBeVisible({
      timeout: 60_000,
    });
  });

  test("spo_window detail lists propositions when extracted", async ({ page }) => {
    const tgt = await firstWindowWithPropositions(page);
    test.skip(!tgt, "no spo_window with extracted propositions");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=spo_window:${encodeURIComponent(tgt!.chunkId)}`,
    );
    await expect(page.getByTestId("chunk-text-panel")).toBeVisible({ timeout: 60_000 });
    await expect(
      page.locator("[data-testid='proposition-row']").first(),
    ).toBeVisible({ timeout: 60_000 });
    expect(
      await page.locator("[data-testid='proposition-row']").count(),
    ).toBeGreaterThan(0);
  });

  test("spo_model detail shows per-window mention/prop table", async ({ page }) => {
    const tgt = await firstSpoModelWithWindows(page);
    test.skip(!tgt, "no spo_model with :win- extractions");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=spo_model:${encodeURIComponent(tgt!.model)}`,
    );
    await expect(page.getByTestId("spo-model-table")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("spo-model-total-mentions")).toContainText(/\d+/);
    await expect(
      page.locator("[data-testid^='spo-model-row-']").first(),
    ).toBeVisible({ timeout: 60_000 });
    expect(
      await page.locator("[data-testid^='spo-model-row-']").count(),
    ).toBeGreaterThan(0);
  });
});

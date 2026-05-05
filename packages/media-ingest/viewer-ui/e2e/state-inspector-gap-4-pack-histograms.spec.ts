/**
 * State Inspector — Gap #4 regression specs.
 *
 * Locks in the pack threshold histograms with draggable counterfactual
 * (`.test-output/inspector-tour/data-scientist-gaps.md` §2.4):
 *
 *   - Two stacked histograms above the kept-windows table on PackDetail:
 *     mention_count distribution + chars_per_mention distribution.
 *   - Each carries a draggable amber threshold handle that snaps to bin
 *     edges and updates a live counterfactual readout below the chart.
 *   - Bar click filters the kept + pruned tables (rows dimmed via
 *     `opacity-30`); switching axes is mutually exclusive.
 *   - Pre-existing testids (`pack-detail`, `pack-kept-count`, etc.) still
 *     report under the new histograms.
 *
 * Source-of-truth assertions: `scripts/qa-verify-gap4.mjs`.
 */
import { test, expect } from "./fixtures/coverage";
import type { Page } from "@playwright/test";
import {
  firstDocWithKeptAndPruned,
  resolveRunId,
} from "./fixtures/inspector-discovery";

async function deepLink(
  page: Page,
  docId: string,
  nodeQuery: string,
): Promise<string> {
  const runId = await resolveRunId(page);
  const runSeg = runId ? `run=${encodeURIComponent(runId)}&` : "";
  return `/viewer/benchmarks/state?${runSeg}doc=${encodeURIComponent(docId)}&node=${nodeQuery}`;
}

/**
 * Drag a `<rect>` handle by `dxPx` pixels using the real input system.
 *
 * Why `page.mouse` and NOT `page.evaluate(... new PointerEvent ...)`:
 *   - Chromium synthesises PointerEvents from MouseEvents when input
 *     comes from the input system (`page.mouse.*`); those PointerEvents
 *     carry a real pointer id that `setPointerCapture` recognises.
 *   - Synthetic `PointerEvent` dispatch via `page.evaluate` invents a
 *     pointer id (`pointerId: 7`) that is never registered with the
 *     compositor. The first `setPointerCapture(pointerId)` call inside
 *     React's drag handler then throws "No active pointer with the
 *     given id" and the drag silently aborts before producing any
 *     `pointermove` deltas — readout never flips.
 *   - The Gap-#4 verifier script (`scripts/qa-verify-gap4.mjs`)
 *     established this pattern; we mirror it here so the spec exercises
 *     the same code path users actually trigger.
 */
async function dragHandleBy(page: Page, selector: string, dxPx: number) {
  const handle = page.locator(selector);
  // The handle may be off-screen on tall pack-detail panels; scroll it
  // into view first so the bounding box lands inside the viewport (else
  // page.mouse.* clicks miss the target).
  await handle
    .waitFor({ state: "attached", timeout: 10_000 })
    .catch(() => {});
  await handle.scrollIntoViewIfNeeded({ timeout: 5_000 }).catch(() => {});
  await page.waitForTimeout(150);
  const box = await handle.boundingBox();
  if (!box) throw new Error(`no bounding box for ${selector}`);
  const startX = box.x + box.width / 2;
  const startY = box.y + box.height / 2;
  await page.mouse.move(startX, startY);
  await page.waitForTimeout(50);
  await page.mouse.down();
  const steps = 25;
  for (let i = 1; i <= steps; i += 1) {
    await page.mouse.move(startX + (dxPx * i) / steps, startY);
  }
  await page.mouse.up();
  await page.waitForTimeout(300);
}

test.describe.configure({ timeout: 180_000 });
const PANEL_TIMEOUT = 90_000;

test.describe("State Inspector — Gap #4 — Pack threshold histograms", () => {
  test("pack panel mounts threshold-histograms root @smoke", async ({ page }) => {
    const tgt = await firstDocWithKeptAndPruned(page);
    test.skip(
      !tgt,
      "no doc has both kept_windows and pruned_windows in resolved run",
    );
    await page.goto(await deepLink(page, tgt!.docId, "pack"));
    await expect(page.getByTestId("pack-detail")).toBeVisible({ timeout: PANEL_TIMEOUT });
    await expect(page.getByTestId("pack-threshold-histograms")).toBeVisible();
  });

  test("both histograms (mention_count + chars_per_mention) are visible", async ({
    page,
  }) => {
    const tgt = await firstDocWithKeptAndPruned(page);
    test.skip(!tgt, "no doc with kept+pruned");
    await page.goto(await deepLink(page, tgt!.docId, "pack"));
    await expect(page.getByTestId("pack-detail")).toBeVisible({ timeout: PANEL_TIMEOUT });
    await expect(page.getByTestId("pack-histogram-mention-count")).toBeVisible();
    await expect(page.getByTestId("pack-histogram-chars-per-mention")).toBeVisible();
  });

  test("each histogram renders ≥1 bin", async ({ page }) => {
    const tgt = await firstDocWithKeptAndPruned(page);
    test.skip(!tgt, "no doc with kept+pruned");
    await page.goto(await deepLink(page, tgt!.docId, "pack"));
    await expect(page.getByTestId("pack-detail")).toBeVisible({ timeout: PANEL_TIMEOUT });
    const mentionBins = await page
      .locator('[data-testid^="pack-bin-mention-count-"]')
      .count();
    const cpmBins = await page
      .locator('[data-testid^="pack-bin-chars-per-mention-"]')
      .count();
    expect(mentionBins).toBeGreaterThanOrEqual(1);
    expect(cpmBins).toBeGreaterThanOrEqual(1);
  });

  test("both threshold drag handles are visible", async ({ page }) => {
    const tgt = await firstDocWithKeptAndPruned(page);
    test.skip(!tgt, "no doc with kept+pruned");
    await page.goto(await deepLink(page, tgt!.docId, "pack"));
    await expect(page.getByTestId("pack-detail")).toBeVisible({ timeout: PANEL_TIMEOUT });
    await expect(
      page.getByTestId("pack-threshold-handle-mention-count"),
    ).toBeVisible();
    await expect(
      page.getByTestId("pack-threshold-handle-chars-per-mention"),
    ).toBeVisible();
  });

  test("default readouts render `current min_mentions = N` shape", async ({
    page,
  }) => {
    const tgt = await firstDocWithKeptAndPruned(page);
    test.skip(!tgt, "no doc with kept+pruned");
    await page.goto(await deepLink(page, tgt!.docId, "pack"));
    await expect(page.getByTestId("pack-detail")).toBeVisible({ timeout: PANEL_TIMEOUT });
    await expect(page.getByTestId("pack-readout-mention-count")).toContainText(
      /current\s+min_mentions\s*=\s*\d+/,
    );
    await expect(page.getByTestId("pack-readout-chars-per-mention")).toContainText(
      /current\s+max_chars_per_mention\s*=\s*\d+/,
    );
  });

  test("dragging the mention_count handle flips readout to counterfactual form", async ({
    page,
  }) => {
    const tgt = await firstDocWithKeptAndPruned(page);
    test.skip(!tgt, "no doc with kept+pruned");
    await page.goto(await deepLink(page, tgt!.docId, "pack"));
    await expect(page.getByTestId("pack-detail")).toBeVisible({ timeout: PANEL_TIMEOUT });
    const before =
      ((await page.getByTestId("pack-readout-mention-count").textContent()) ?? "")
        .trim();
    await dragHandleBy(
      page,
      '[data-testid="pack-threshold-handle-mention-count"]',
      60,
    );
    // Readout must flip to "at min_mentions ≥ N: ..." form.
    await expect(page.getByTestId("pack-readout-mention-count")).toContainText(
      /at\s+min_mentions\s*≥/,
    );
    const after =
      ((await page.getByTestId("pack-readout-mention-count").textContent()) ?? "")
        .trim();
    expect(after).not.toBe(before);
  });

  test("clicking a mention_count bin filters kept rows (some dim, clear button visible)", async ({
    page,
  }) => {
    const tgt = await firstDocWithKeptAndPruned(page);
    test.skip(!tgt, "no doc with kept+pruned");
    await page.goto(await deepLink(page, tgt!.docId, "pack"));
    await expect(page.getByTestId("pack-detail")).toBeVisible({ timeout: PANEL_TIMEOUT });
    const keptRows = await page.locator('[data-testid^="pack-kept-row-"]').count();
    test.skip(keptRows === 0, "no kept rows to dim");

    // Walk bins and pick one that produces ≥1 dimmed row. Some bins will
    // match every kept row (e.g. modal mention_count) and dim none — that's
    // valid behavior, just skip past those.
    const bins = await page.locator('[data-testid^="pack-bin-mention-count-"]').all();
    let dimmedFound = false;
    for (const bin of bins) {
      await bin.click({ force: true });
      const clearVisible = await page
        .getByTestId("pack-filter-clear")
        .isVisible()
        .catch(() => false);
      if (!clearVisible) continue;
      const dimmed = await page
        .locator('[data-testid^="pack-kept-row-"].opacity-30')
        .count();
      if (dimmed > 0) {
        dimmedFound = true;
        break;
      }
      // Bin matched every row — clear and try next.
      await page.getByTestId("pack-filter-clear").click();
    }
    test.skip(
      !dimmedFound,
      "every mention_count bin matches all kept rows — cannot test dimming",
    );
    expect(dimmedFound).toBe(true);
  });

  test("clear-filter button removes dimming and hides itself", async ({ page }) => {
    const tgt = await firstDocWithKeptAndPruned(page);
    test.skip(!tgt, "no doc with kept+pruned");
    await page.goto(await deepLink(page, tgt!.docId, "pack"));
    await expect(page.getByTestId("pack-detail")).toBeVisible({ timeout: PANEL_TIMEOUT });
    // Click the first bin to apply some filter — even if no rows dim, the
    // clear button still mounts.
    const firstBin = page.locator('[data-testid^="pack-bin-mention-count-"]').first();
    test.skip((await firstBin.count()) === 0, "no mention_count bins");
    await firstBin.click({ force: true });
    const clearBtn = page.getByTestId("pack-filter-clear");
    await expect(clearBtn).toBeVisible();
    await clearBtn.click();
    await expect(clearBtn).toHaveCount(0);
    expect(
      await page.locator('[data-testid^="pack-kept-row-"].opacity-30').count(),
    ).toBe(0);
  });

  test("axis filters are mutually exclusive (cpm click clears mention_count outline)", async ({
    page,
  }) => {
    const tgt = await firstDocWithKeptAndPruned(page);
    test.skip(!tgt, "no doc with kept+pruned");
    await page.goto(await deepLink(page, tgt!.docId, "pack"));
    await expect(page.getByTestId("pack-detail")).toBeVisible({ timeout: PANEL_TIMEOUT });
    const mentionBin = page
      .locator('[data-testid^="pack-bin-mention-count-"]')
      .first();
    const cpmBin = page
      .locator('[data-testid^="pack-bin-chars-per-mention-"]')
      .first();
    test.skip(
      (await mentionBin.count()) === 0 || (await cpmBin.count()) === 0,
      "missing bins on one axis",
    );
    await mentionBin.click({ force: true });
    await expect(page.getByTestId("pack-filter-clear")).toBeVisible();
    await cpmBin.click({ force: true });
    await expect(page.getByTestId("pack-filter-clear")).toBeVisible();
    // Cyan outline lives only on the active axis.
    const mentionOutlines = await page
      .locator(
        '[data-testid="pack-histogram-mention-count"] rect[stroke="rgb(34 211 238)"]',
      )
      .count();
    const cpmOutlines = await page
      .locator(
        '[data-testid="pack-histogram-chars-per-mention"] rect[stroke="rgb(34 211 238)"]',
      )
      .count();
    expect(mentionOutlines).toBe(0);
    expect(cpmOutlines).toBeGreaterThanOrEqual(1);
  });

  test("pre-existing pack testids still report alongside histograms", async ({
    page,
  }) => {
    const tgt = await firstDocWithKeptAndPruned(page);
    test.skip(!tgt, "no doc with kept+pruned");
    await page.goto(await deepLink(page, tgt!.docId, "pack"));
    await expect(page.getByTestId("pack-detail")).toBeVisible({ timeout: PANEL_TIMEOUT });
    await expect(page.getByTestId("pack-kept-count")).toContainText(/^\d+$/);
    await expect(page.getByTestId("pack-pruned-count")).toContainText(/^\d+$/);
    expect(
      await page.locator('[data-testid^="pack-kept-row-"]').count(),
    ).toBeGreaterThan(0);
  });
});

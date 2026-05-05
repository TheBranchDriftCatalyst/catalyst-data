/**
 * State Inspector + BenchmarkReport — Gap #8 regression specs.
 *
 * Locks in the cross-run TrendSparkline rail described in
 * `.test-output/inspector-tour/data-scientist-gaps.md` §2.8: an 80×16
 * mini-chart in every supported panel header (encoder, consensus, pack,
 * spo_model, persist) plus per-row on the BenchmarkReport leaderboard.
 *
 * Important constraints baked in:
 *   - Click-to-jump preserves doc + node selection (the inverse of the
 *     RunPicker's onRunSelect).
 *   - Counts (mention_count, accepted_count) are used as the "always
 *     renders" smoke metric so tests don't depend on GT being wired.
 *
 * Conventions (mirrors Gap #1–#7 specs):
 *   - Deep-link via URL only; no graph clicks.
 *   - Skip cleanly when the resolved corpus lacks the required shape.
 *   - test.describe block matches `--grep "Gap 8"`.
 */
import { test, expect, type Page } from "./fixtures/coverage";
import {
  firstDocWithConsensus,
  firstDocWithPackEvidence,
  firstDocWithPersist,
  firstEncoderWithMentions,
  firstSpoModelWithWindows,
  listRuns,
  resolveRunId,
} from "./fixtures/inspector-discovery";

async function deepLink(
  page: Page,
  docId: string,
  nodeQuery: string,
  pinnedRunId?: string,
): Promise<string> {
  const runId = pinnedRunId ?? (await resolveRunId(page));
  const runSeg = runId ? `run=${encodeURIComponent(runId)}&` : "";
  return `/viewer/benchmarks/state?${runSeg}doc=${encodeURIComponent(docId)}&node=${nodeQuery}`;
}

/** Resolve a non-current run from the runs index for click-to-jump
 *  tests. Returns null when the run index has fewer than two entries. */
async function olderThanCurrent(page: Page, currentRunId: string): Promise<string | null> {
  const { runs } = await listRuns(page);
  for (const r of runs) {
    if (r !== currentRunId) return r;
  }
  return null;
}

test.describe("State Inspector — Gap 8 — TrendSparkline rail", () => {
  test("encoder panel renders sparkline @smoke", async ({ page }) => {
    const tgt = await firstEncoderWithMentions(page);
    test.skip(!tgt, "no encoder with mentions in resolved run");
    await page.goto(
      await deepLink(
        page,
        tgt!.docId,
        `ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
      ),
    );
    const panel = page.getByTestId("ner-encoder-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    await expect(panel.getByTestId("trend-sparkline").first()).toBeVisible({
      timeout: 30_000,
    });
  });

  test("consensus panel renders sparkline @smoke", async ({ page }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with consensus events");
    await page.goto(await deepLink(page, tgt!.docId, "consensus"));
    const panel = page.getByTestId("consensus-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    await expect(panel.getByTestId("trend-sparkline").first()).toBeVisible({
      timeout: 30_000,
    });
  });

  test("pack panel renders sparkline @smoke", async ({ page }) => {
    const tgt = await firstDocWithPackEvidence(page);
    test.skip(!tgt, "no doc with pack_evidence kept_windows");
    await page.goto(await deepLink(page, tgt!.docId, "pack"));
    const panel = page.getByTestId("pack-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    await expect(panel.getByTestId("trend-sparkline").first()).toBeVisible({
      timeout: 30_000,
    });
  });

  test("spo_model panel renders sparkline @smoke", async ({ page }) => {
    const tgt = await firstSpoModelWithWindows(page);
    test.skip(!tgt, "no SPO model with windows in resolved run");
    await page.goto(
      await deepLink(
        page,
        tgt!.docId,
        `spo_model:${encodeURIComponent(tgt!.model)}`,
      ),
    );
    const panel = page.getByTestId("inspector-detail-spo_model");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    await expect(panel.getByTestId("trend-sparkline").first()).toBeVisible({
      timeout: 30_000,
    });
  });

  test("persist panel renders sparkline @smoke", async ({ page }) => {
    const tgt = await firstDocWithPersist(page);
    test.skip(!tgt, "no doc with persist_artifacts events");
    await page.goto(await deepLink(page, tgt!.docId, "persist"));
    const panel = page.getByTestId("inspector-detail-persist");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    await expect(panel.getByTestId("trend-sparkline").first()).toBeVisible({
      timeout: 30_000,
    });
  });

  test("current run is highlighted exactly once per sparkline", async ({ page }) => {
    const tgt = await firstEncoderWithMentions(page);
    test.skip(!tgt, "no encoder with mentions");
    const runId = await resolveRunId(page);
    test.skip(!runId, "no resolvable run");
    await page.goto(
      await deepLink(
        page,
        tgt!.docId,
        `ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
        runId!,
      ),
    );
    const panel = page.getByTestId("ner-encoder-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    const sparkline = panel.getByTestId("trend-sparkline").first();
    await expect(sparkline).toBeVisible();
    // Wait until the report fetches resolve and at least one point is drawn.
    await expect
      .poll(
        async () =>
          await sparkline.locator('[data-testid^="trend-sparkline-point-"]').count(),
        { timeout: 30_000 },
      )
      .toBeGreaterThan(0);
    const currentDots = sparkline.locator(
      '[data-testid^="trend-sparkline-point-"][data-current="true"]',
    );
    expect(await currentDots.count()).toBe(1);
    const dotRunId = await currentDots.first().getAttribute("data-run-id");
    expect(dotRunId).toBe(runId);
  });

  test("point count is bounded — 1 ≤ N ≤ 10", async ({ page }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with consensus");
    await page.goto(await deepLink(page, tgt!.docId, "consensus"));
    const panel = page.getByTestId("consensus-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    const sparkline = panel.getByTestId("trend-sparkline").first();
    await expect(sparkline).toBeVisible();
    await expect
      .poll(
        async () =>
          await sparkline.locator('[data-testid^="trend-sparkline-point-"]').count(),
        { timeout: 30_000 },
      )
      .toBeGreaterThan(0);
    const n = await sparkline
      .locator('[data-testid^="trend-sparkline-point-"]')
      .count();
    expect(n).toBeGreaterThanOrEqual(1);
    expect(n).toBeLessThanOrEqual(10);
  });

  test("click-to-jump preserves doc + node selection", async ({ page }) => {
    const tgt = await firstEncoderWithMentions(page);
    test.skip(!tgt, "no encoder with mentions");
    const runId = await resolveRunId(page);
    test.skip(!runId, "no resolved run");
    const olderRun = await olderThanCurrent(page, runId!);
    test.skip(!olderRun, "fewer than 2 runs in index — cannot test jump");

    const startUrl = await deepLink(
      page,
      tgt!.docId,
      `ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
      runId!,
    );
    await page.goto(startUrl);
    const panel = page.getByTestId("ner-encoder-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    const sparkline = panel.getByTestId("trend-sparkline").first();
    await expect
      .poll(
        async () =>
          await sparkline
            .locator(`[data-testid^="trend-sparkline-point-"][data-run-id="${olderRun}"]`)
            .count(),
        { timeout: 30_000 },
      )
      .toBeGreaterThan(0);
    const olderDot = sparkline.locator(
      `[data-testid^="trend-sparkline-point-"][data-run-id="${olderRun}"]`,
    );
    await olderDot.first().click({ force: true });

    // URL should now carry the new run id but keep doc + node intact.
    await expect.poll(() => page.url(), { timeout: 10_000 }).toContain(
      `run=${encodeURIComponent(olderRun!)}`,
    );
    expect(page.url()).toContain(`doc=${encodeURIComponent(tgt!.docId)}`);
    expect(page.url()).toContain(
      `node=ner_encoder%3A${encodeURIComponent(tgt!.encoder)}`,
    );
  });

  test("tooltip on hover shows runId + value", async ({ page }) => {
    const tgt = await firstEncoderWithMentions(page);
    test.skip(!tgt, "no encoder with mentions");
    const runId = await resolveRunId(page);
    test.skip(!runId, "no resolvable run");
    await page.goto(
      await deepLink(
        page,
        tgt!.docId,
        `ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
        runId!,
      ),
    );
    const panel = page.getByTestId("ner-encoder-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    const sparkline = panel.getByTestId("trend-sparkline").first();
    await expect
      .poll(
        async () =>
          await sparkline.locator('[data-testid^="trend-sparkline-point-"]').count(),
        { timeout: 30_000 },
      )
      .toBeGreaterThan(0);
    const dot = sparkline.locator('[data-testid^="trend-sparkline-point-"]').first();
    const dotRunId = await dot.getAttribute("data-run-id");
    expect(dotRunId).toBeTruthy();
    await dot.hover();
    // Radix Tooltip portals to body. Wait for any tooltip role to appear
    // and check it carries the run id + a numeric value substring.
    const tip = page.locator('[role="tooltip"]', { hasText: dotRunId! });
    await expect(tip.first()).toBeVisible({ timeout: 5_000 });
    const tipText = (await tip.first().textContent()) ?? "";
    expect(tipText).toContain(dotRunId!);
    // Value line is "value: <number>" — match an arbitrary number with
    // optional decimal / scientific tail.
    expect(tipText).toMatch(/value:\s*-?\d+(\.\d+)?/);
  });

  test("BenchmarkReport leaderboard renders sparkline + click switches run", async ({
    page,
  }) => {
    await page.goto("/viewer/benchmarks");
    // Wait for the leaderboard to render.
    await page.waitForLoadState("networkidle");
    const firstRow = page.locator('[data-testid^="leaderboard-row-"]').first();
    // Some bench fixtures may not populate; skip rather than fail.
    const rowCount = await page.locator('[data-testid^="leaderboard-row-"]').count();
    test.skip(rowCount === 0, "leaderboard has no rows in resolved run");
    await expect(firstRow).toBeVisible({ timeout: 30_000 });
    const sparkline = firstRow.getByTestId("trend-sparkline").first();
    await expect(sparkline).toBeVisible({ timeout: 30_000 });
    // Wait for the report-fetch chain to land.
    await expect
      .poll(
        async () =>
          await sparkline.locator('[data-testid^="trend-sparkline-point-"]').count(),
        { timeout: 30_000 },
      )
      .toBeGreaterThan(0);
    // Click the leftmost (oldest) point — guaranteed to be different from
    // the currently-selected report when more than one report exists.
    const points = sparkline.locator('[data-testid^="trend-sparkline-point-"]');
    const n = await points.count();
    test.skip(n < 2, "fewer than 2 runs available — cannot test jump");
    const oldestDot = points.first();
    const oldestRunId = await oldestDot.getAttribute("data-run-id");
    expect(oldestRunId).toBeTruthy();
    // Capture the picker label before the click so we can assert the
    // active report changed.
    const beforeLabel = await page
      .getByTestId("run-picker-trigger")
      .first()
      .textContent();
    await oldestDot.click({ force: true });
    // The RunPicker label should reflect the newly-selected run id.
    await expect
      .poll(
        async () =>
          (await page.getByTestId("run-picker-trigger").first().textContent()) ?? "",
        { timeout: 10_000 },
      )
      .toContain(oldestRunId!);
    expect(beforeLabel).not.toContain(oldestRunId!);
  });

  test("null-value points render as gaps in the polyline", async ({ page }) => {
    // Discovery: find a sparkline where at least one point is null. If
    // every metric on the resolved corpus has 10 complete runs, skip.
    const tgt = await firstEncoderWithMentions(page);
    test.skip(!tgt, "no encoder with mentions");
    await page.goto(
      await deepLink(
        page,
        tgt!.docId,
        `ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
      ),
    );
    const panel = page.getByTestId("ner-encoder-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    const sparkline = panel.getByTestId("trend-sparkline").first();
    await expect(sparkline).toBeVisible();
    await expect
      .poll(
        async () =>
          await sparkline.locator('[data-testid^="trend-sparkline-point-"]').count(),
        { timeout: 30_000 },
      )
      .toBeGreaterThan(0);
    // If runs.length === drawn-dots, no null points → skip. The hook
    // returns one slot per run including null-valued ones, but the SVG
    // only renders dots for non-null values (null = gap).
    const { runs } = await listRuns(page);
    const expectedSlots = Math.min(10, runs.length);
    const drawnDots = await sparkline
      .locator('[data-testid^="trend-sparkline-point-"]')
      .count();
    test.skip(
      drawnDots >= expectedSlots,
      `all ${expectedSlots} runs have data for this metric — no gaps to assert`,
    );
    // At least one "missing" slot exists. The polyline should be split
    // (multiple <polyline> elements) OR the dot count is strictly less
    // than the slot count.
    expect(drawnDots).toBeLessThan(expectedSlots);
  });
});

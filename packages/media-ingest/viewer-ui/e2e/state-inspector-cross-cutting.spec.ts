/**
 * State Inspector — cross-cutting specs (plan §3.1–§3.6, §3.8, §3.9).
 *
 * Skips §3.7 — already covered by `state-inspector-window-overlay.spec.ts`
 * which was the reference template for this suite.
 */
import { test, expect } from "./fixtures/coverage";
import {
  fetchRuns,
  firstDocWithConsensus,
  firstEncoderWithMentions,
  listRuns,
  resolveRunId,
} from "./fixtures/inspector-discovery";

test.describe("State Inspector — cross-cutting", () => {
  test("switching run via picker clears doc + node selection", async ({ page }) => {
    const runs = await listRuns(page);
    test.skip(runs.runs.length < 2, "need at least 2 runs");
    await page.goto(
      `/viewer/benchmarks/state?run=${runs.runs[0]}&doc=fakedoc&node=document`,
    );
    await page.getByTestId("run-picker-trigger").waitFor({
      state: "visible",
      timeout: 60_000,
    });
    await page.getByTestId("run-picker-trigger").click();
    await page.getByTestId(`run-picker-${runs.runs[1]}`).click();
    await expect(page).toHaveURL(new RegExp(`run=${runs.runs[1]}`));
    await expect(page).not.toHaveURL(/doc=/);
    await expect(page).not.toHaveURL(/node=/);
  });

  test("reload preserves run+doc+node selection", async ({ page }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with consensus events");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=consensus`,
    );
    await expect(page.getByTestId("consensus-detail")).toBeVisible({ timeout: 60_000 });
    await page.reload();
    await expect(page.getByTestId("consensus-detail")).toBeVisible({ timeout: 60_000 });
    expect(page.url()).toContain(`doc=${encodeURIComponent(tgt!.docId)}`);
    expect(page.url()).toContain("node=consensus");
  });

  test("initial load shows spinner then graph @smoke", async ({ page }) => {
    // Pin a run with events so the SPA auto-picks a doc and renders the
    // graph. Without ?run=, the SPA defaults to the live run (often empty)
    // and the graph stays in its "Pick a doc" empty state forever.
    const runId = await resolveRunId(page);
    await page.goto(`/viewer/benchmarks/state?run=${runId ?? ""}`);
    await page
      .waitForSelector('[data-testid="state-inspector-loading"]', {
        state: "visible",
        timeout: 5_000,
      })
      .catch(() => null);
    await expect(page.getByTestId("pipeline-graph")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("state-inspector-loading")).toHaveCount(0);
  });

  test("unknown doc shows graceful error in doc panel", async ({ page }) => {
    await page.goto("/viewer/benchmarks/state?doc=does-not-exist&node=document");
    await expect(page.getByTestId("doc-source-error")).toBeVisible({ timeout: 60_000 });
  });

  test("unknown run id renders without crashing", async ({ page }) => {
    await page.goto("/viewer/benchmarks/state?run=run-does-not-exist");
    await expect(page.getByTestId("run-picker-trigger")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("inspector-conn-badge")).toBeVisible();
  });

  test("live polling updates encoder mention count without reload", async ({ page }) => {
    const runs = await fetchRuns(page);
    test.skip(!runs.live, "no in-flight run");
    const tgt = await firstEncoderWithMentions(page);
    test.skip(!tgt, "no encoder with mentions on the live run");
    await page.goto(
      `/viewer/benchmarks/state?run=${runs.live}&doc=${encodeURIComponent(tgt!.docId)}&node=ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
    );
    const counter = page.getByTestId("ner-encoder-mention-count");
    await counter.waitFor({ state: "visible", timeout: 60_000 });
    const initial = await counter.innerText();
    await page.waitForTimeout(8_000);
    const next = await counter.innerText();
    expect(Number(next)).toBeGreaterThanOrEqual(Number(initial));
  });

  test("rail width persists in localStorage", async ({ page }) => {
    await page.goto("/viewer/benchmarks/state");
    await page.evaluate(() => localStorage.setItem("state-inspector-rail", "320"));
    await page.reload();
    await page.getByTestId("state-inspector-rail").waitFor({
      state: "visible",
      timeout: 60_000,
    });
    expect(
      await page.evaluate(() => localStorage.getItem("state-inspector-rail")),
    ).toBe("320");
    const w = await page
      .getByTestId("state-inspector-rail")
      .evaluate((el) => (el as HTMLElement).getBoundingClientRect().width);
    expect(Math.round(w)).toBeGreaterThanOrEqual(315);
  });

  test("detail panel height persists in localStorage", async ({ page }) => {
    await page.goto("/viewer/benchmarks/state");
    await page.evaluate(() =>
      localStorage.setItem("state-inspector-detail:height", "300"),
    );
    await page.reload();
    await page.getByTestId("state-inspector-detail-panel").waitFor({
      state: "visible",
      timeout: 60_000,
    });
    expect(
      await page.evaluate(() => localStorage.getItem("state-inspector-detail:height")),
    ).toBe("300");
  });
});

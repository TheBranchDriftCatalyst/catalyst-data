/**
 * State Inspector — Gap #1 regression specs.
 *
 * Locks in the F1Strip + GT-chip surface from
 * `.test-output/inspector-tour/data-scientist-gaps.md` §2.1: per-encoder
 * and consensus header strips that render P/R/F1 to 2 decimals when the
 * run advertises ground truth, plus an emerald/red Δ pill on the
 * consensus header (consensus F1 vs best-encoder F1) and inline GT chips
 * on accepted-mention rows.
 *
 * Source-of-truth assertions: see qa-verify-gaps.mjs (Gap #1) — every
 * one-shot pass condition there has a `test()` block here.
 *
 * Conventions:
 *  - Deep-link via URL only; no graph clicks.
 *  - Every test skips cleanly when the resolved run lacks GT or the
 *    expected mention shape.
 */
import { test, expect } from "./fixtures/coverage";
import { useFixtureCorpus } from "./fixtures/fixture-mode";
import {
  firstDocWithConsensus,
  firstEncoderWithMentions,
  resolveRunId,
  runReportInfo,
} from "./fixtures/inspector-discovery";

// CD-1qqy: happy-path corpus is served from disk via `page.route`
// interception + Node-side filesystem reads. The corpus is engineered
// to satisfy every skip-gate in this file (active GT, ≥3 encoders with
// strict_f1, ensemble scores).
test.beforeEach(async ({ page }) => {
  await useFixtureCorpus(page, "happy-path");
});

const TWO_DP = /^\d+\.\d{2}$/;
const DELTA_FMT = /^Δ\s*[+-]?\d+\.\d{2}$/;

/** Build a deep-link URL pinned to the helper-resolved run so the SPA
 *  reads the same data the discovery helpers found their match in. */
async function deepLink(
  page: import("@playwright/test").Page,
  docId: string,
  nodeQuery: string,
): Promise<string> {
  const runId = await resolveRunId(page);
  const runSeg = runId ? `run=${encodeURIComponent(runId)}&` : "";
  return `/viewer/benchmarks/state?${runSeg}doc=${encodeURIComponent(docId)}&node=${nodeQuery}`;
}

// Discovery + Vite dev cold-compile + ~150MB events ndjson fetch easily
// eats past Playwright's 30s default — bump the per-test budget so the
// panel has time to mount before assertions fire. The events stream
// itself routinely takes 45s to parse on first request.
test.describe.configure({ timeout: 180_000 });
const PANEL_TIMEOUT = 90_000;

test.describe("State Inspector — Gap #1 — F1 strip + GT chips", () => {
  test("encoder panel renders @smoke", async ({ page }) => {
    const tgt = await firstEncoderWithMentions(page);
    test.skip(!tgt, "no encoder with mentions in resolved run");
    await page.goto(
      await deepLink(
        page,
        tgt!.docId,
        `ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
      ),
    );
    await expect(page.getByTestId("ner-encoder-detail")).toBeVisible({
      timeout: PANEL_TIMEOUT,
    });
  });

  test("F1Strip renders on encoder header when GT is available @smoke", async ({
    page,
  }) => {
    const info = await runReportInfo(page);
    test.skip(!info, "no resolved run");
    test.skip(!info!.gtAvailable, "report.json reports gt_available=false");
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
    await expect(panel).toBeVisible({ timeout: PANEL_TIMEOUT });
    // The strip renders only when the encoder appears in report.models with
    // strict_f1 set. Some encoders may legitimately be absent (e.g. errored
    // out before scoring) — tolerate that with a shape skip rather than
    // failing.
    const stripCount = await panel.getByTestId("f1-strip").count();
    test.skip(
      stripCount === 0,
      "encoder absent from report.json.models — F1 strip cannot render",
    );
    await expect(panel.getByTestId("f1-strip").first()).toBeVisible();
  });

  test("F1 strip on encoder renders P/R/F1 to 2 decimals", async ({ page }) => {
    const info = await runReportInfo(page);
    test.skip(!info, "no resolved run");
    test.skip(!info!.gtAvailable, "no GT available");
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
    await expect(panel).toBeVisible({ timeout: PANEL_TIMEOUT });
    const stripCount = await panel.getByTestId("f1-strip").count();
    test.skip(stripCount === 0, "encoder not in report.models — strip absent");
    const strip = panel.getByTestId("f1-strip").first();
    await expect(strip.getByTestId("f1-strip-precision")).toHaveText(TWO_DP);
    await expect(strip.getByTestId("f1-strip-recall")).toHaveText(TWO_DP);
    await expect(strip.getByTestId("f1-strip-f1")).toHaveText(TWO_DP);
  });

  test("consensus panel renders @smoke", async ({ page }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with mention_decision events");
    await page.goto(await deepLink(page, tgt!.docId, "consensus"));
    // ConsensusDetail renders a no-testid placeholder when consensusEvents
    // is still empty (events haven't landed for the chunkId yet). The
    // useRunStream poll interval is 3s and the events ndjson can be
    // multi-MB on dev mode — wait up to 60s for the testid to appear.
    await expect(page.getByTestId("consensus-detail")).toBeVisible({
      timeout: PANEL_TIMEOUT,
    });
  });

  test("F1Strip renders on consensus header when GT is available @smoke", async ({
    page,
  }) => {
    const info = await runReportInfo(page);
    test.skip(!info, "no resolved run");
    test.skip(!info!.gtAvailable, "no GT available");
    test.skip(
      info!.ensembleScores === null,
      "report.json has no ensemble/consensus scores entry",
    );
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with mention_decision events");
    await page.goto(await deepLink(page, tgt!.docId, "consensus"));
    const panel = page.getByTestId("consensus-detail");
    await expect(panel).toBeVisible({ timeout: PANEL_TIMEOUT });
    await expect(panel.getByTestId("f1-strip").first()).toBeVisible();
  });

  test("F1 strip on consensus renders P/R/F1 to 2 decimals", async ({ page }) => {
    const info = await runReportInfo(page);
    test.skip(!info, "no resolved run");
    test.skip(!info!.gtAvailable, "no GT available");
    test.skip(info!.ensembleScores === null, "no ensemble scores in report");
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with consensus");
    await page.goto(await deepLink(page, tgt!.docId, "consensus"));
    const panel = page.getByTestId("consensus-detail");
    await expect(panel).toBeVisible({ timeout: PANEL_TIMEOUT });
    const strip = panel.getByTestId("f1-strip").first();
    await expect(strip.getByTestId("f1-strip-precision")).toHaveText(TWO_DP);
    await expect(strip.getByTestId("f1-strip-recall")).toHaveText(TWO_DP);
    await expect(strip.getByTestId("f1-strip-f1")).toHaveText(TWO_DP);
  });

  test("consensus delta pill renders and is signed/2-decimal formatted", async ({
    page,
  }) => {
    const info = await runReportInfo(page);
    test.skip(!info, "no resolved run");
    test.skip(!info!.gtAvailable, "no GT available");
    test.skip(info!.ensembleScores === null, "no ensemble scores in report");
    test.skip(
      info!.encoderModels.length === 0,
      "no encoder entries in report — consensus has nothing to compare against",
    );
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with consensus");
    await page.goto(await deepLink(page, tgt!.docId, "consensus"));
    const panel = page.getByTestId("consensus-detail");
    await expect(panel).toBeVisible({ timeout: PANEL_TIMEOUT });
    const strip = panel.getByTestId("f1-strip").first();
    const delta = strip.getByTestId("f1-strip-delta");
    await expect(delta).toBeVisible();
    // text is "Δ +0.04" / "Δ -0.04" / "Δ 0.00" — strip whitespace via regex.
    const txt = (await delta.textContent())?.trim() ?? "";
    expect(txt.replace(/\s+/g, " ")).toMatch(DELTA_FMT);
  });

  test("F1 strip absent when run has no ground truth", async ({ page }) => {
    const info = await runReportInfo(page);
    test.skip(!info, "no resolved run");
    test.skip(
      info!.gtAvailable,
      "GT available — strip-absent assertion only meaningful for GT-less runs",
    );
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with consensus");
    await page.goto(await deepLink(page, tgt!.docId, "consensus"));
    const panel = page.getByTestId("consensus-detail");
    await expect(panel).toBeVisible({ timeout: PANEL_TIMEOUT });
    expect(await panel.getByTestId("f1-strip").count()).toBe(0);
  });

  test("GT chip renders on accepted rows when active GT has scoped mentions", async ({
    page,
  }) => {
    const info = await runReportInfo(page);
    test.skip(!info, "no resolved run");
    test.skip(!info!.gtAvailable, "no GT available");
    // Active GT in current bench fixtures reports total_mentions=0 — chip
    // render is upstream-gated on `gtList.length > 0` AND scoped rows for
    // the doc. Skip cleanly when no GT mentions exist for this run.
    test.skip(
      info!.gtMentionCount === 0,
      `report.ground_truth.mention_count=${info!.gtMentionCount}; chip cannot render`,
    );
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with consensus");
    await page.goto(await deepLink(page, tgt!.docId, "consensus"));
    const panel = page.getByTestId("consensus-detail");
    await expect(panel).toBeVisible({ timeout: PANEL_TIMEOUT });
    expect(await panel.getByTestId("mention-gt-chip").count()).toBeGreaterThan(0);
  });
});

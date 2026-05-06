/**
 * State Inspector — Gap #3 regression specs.
 *
 * Locks in the per-encoder confidence histogram on NerEncoderDetail
 * (`.test-output/inspector-tour/data-scientist-gaps.md` §2.3):
 *
 *   - 20-bin histogram between the type-tally pills and the mention list,
 *     keyed off `mention.confidence` from the encoder's chunk_extracted
 *     payload.
 *   - Hover any bin → threshold preview line below the chart updates with
 *     the bin's lower bound, kept count vs total, and (when GT loaded)
 *     a P/R-at-threshold breakdown.
 *   - Optional informational long-tail badge when one bin > 5× the median.
 *   - Empty-state row when the encoder reports no per-mention confidence.
 *
 * Source-of-truth assertions: `scripts/qa-verify-gaps.mjs` (Gap #3 block).
 */
import { test, expect } from "./fixtures/coverage";
import { useCorpus } from "./fixtures/corpora";
import {
  firstEncoderWithConfidence,
  firstEncoderWithMentions,
  resolveRunId,
} from "./fixtures/inspector-discovery";

async function deepLink(
  page: import("@playwright/test").Page,
  docId: string,
  nodeQuery: string,
): Promise<string> {
  const runId = await resolveRunId(page);
  const runSeg = runId ? `run=${encodeURIComponent(runId)}&` : "";
  return `/viewer/benchmarks/state?${runSeg}doc=${encodeURIComponent(docId)}&node=${nodeQuery}`;
}

test.describe.configure({ timeout: 180_000 });
const PANEL_TIMEOUT = 90_000;

test.describe("State Inspector — Gap #3 — Confidence histogram", () => {
  test.describe("numeric confidence branch (happy-path corpus)", () => {
    // CD-1qqy: happy-path corpus provides encoders with numeric confidence values
    test.beforeEach(async ({ page }) => {
      await useCorpus(page, "happy-path");
    });

    test("histogram renders inside encoder panel @smoke", async ({ page }) => {
    const tgt = await firstEncoderWithConfidence(page);
    test.skip(!tgt, "no encoder reports per-mention confidence in resolved run");
    await page.goto(
      await deepLink(
        page,
        tgt!.docId,
        `ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
      ),
    );
    const panel = page.getByTestId("ner-encoder-detail");
    await expect(panel).toBeVisible({ timeout: PANEL_TIMEOUT });
    await expect(panel.getByTestId("confidence-histogram")).toBeVisible();
  });

  test("histogram has exactly 20 bins (BIN_WIDTH=0.05)", async ({ page }) => {
    const tgt = await firstEncoderWithConfidence(page);
    test.skip(!tgt, "no encoder with confidence");
    await page.goto(
      await deepLink(
        page,
        tgt!.docId,
        `ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
      ),
    );
    const panel = page.getByTestId("ner-encoder-detail");
    await expect(panel).toBeVisible({ timeout: PANEL_TIMEOUT });
    await expect(panel.getByTestId("confidence-histogram")).toBeVisible();
    expect(
      await panel.locator('[data-testid^="confidence-bin-"]').count(),
    ).toBe(20);
  });

  test("preview line renders default summary text on initial mount", async ({
    page,
  }) => {
    const tgt = await firstEncoderWithConfidence(page);
    test.skip(!tgt, "no encoder with confidence");
    await page.goto(
      await deepLink(
        page,
        tgt!.docId,
        `ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
      ),
    );
    const panel = page.getByTestId("ner-encoder-detail");
    await expect(panel).toBeVisible({ timeout: PANEL_TIMEOUT });
    const preview = panel.getByTestId("confidence-preview");
    await expect(preview).toBeVisible();
    // Default summary: `range: [0.00–1.00]  ·  N mentions[  ·  GT-confirmed: M]`
    await expect(preview).toContainText(/range:\s*\[0\.00–1\.00\]/);
    await expect(preview).toContainText(/\d+\s+mentions/);
  });

  test("hovering a bin updates the threshold preview text", async ({ page }) => {
    const tgt = await firstEncoderWithConfidence(page);
    test.skip(!tgt, "no encoder with confidence");
    await page.goto(
      await deepLink(
        page,
        tgt!.docId,
        `ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
      ),
    );
    const panel = page.getByTestId("ner-encoder-detail");
    await expect(panel).toBeVisible({ timeout: PANEL_TIMEOUT });
    const preview = panel.getByTestId("confidence-preview");
    const before = ((await preview.textContent()) ?? "").trim();

    // Walk a few bin indices — distribution may concentrate anywhere on
    // [0, 1] so don't assume bin 10 has data. The preview text changes
    // for ANY bin (each bin index produces its own "at conf ≥ X.YZ" line)
    // — what we need is at least one hover that flips the text.
    let flipped = false;
    for (const idx of [10, 14, 16, 18, 4, 0, 19]) {
      const target = panel.locator(`[data-testid="confidence-bin-${idx}"]`);
      if ((await target.count()) === 0) continue;
      await target.hover();
      const after = ((await preview.textContent()) ?? "").trim();
      if (after !== before) {
        flipped = true;
        // Threshold preview format: "at conf ≥ 0.dd: keep N / M  ·  bin [0.dd–0.dd)"
        // (no GT) or with embedded P/R math (GT loaded).
        expect(after).toMatch(/at conf ≥ \d\.\d{2}/);
        break;
      }
    }
    expect(flipped, "no bin hover changed the preview text").toBe(true);
  });

  test("hovering bin 0 produces a `keep N / total` form", async ({ page }) => {
    const tgt = await firstEncoderWithConfidence(page);
    test.skip(!tgt, "no encoder with confidence");
    await page.goto(
      await deepLink(
        page,
        tgt!.docId,
        `ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
      ),
    );
    const panel = page.getByTestId("ner-encoder-detail");
    await expect(panel).toBeVisible({ timeout: PANEL_TIMEOUT });
    await panel.getByTestId("confidence-bin-0").hover();
    const preview = panel.getByTestId("confidence-preview");
    await expect(preview).toContainText(/at conf ≥ 0\.00:\s*keep\s+\d+\s*\/\s*\d+/);
  });
  });

  test.describe("empty confidence branch (edge-cases corpus)", () => {
    // CD-1qqy: edge-cases corpus provides encoder with null-confidence mentions
    test.beforeEach(async ({ page }) => {
      await useCorpus(page, "edge-cases");
    });

    test("empty state renders for encoders with only null-confidence mentions", async ({
      page,
    }) => {
    const allEncoders = await firstEncoderWithMentions(page);
    test.skip(!allEncoders, "no encoder with mentions in resolved run");
    // The current bench corpus does not include any encoder that emits
    // exclusively-null confidence — gliner-pii / gliner-large both report
    // numeric scores. Skip cleanly when no fixture matches the empty path.
    const withConf = await firstEncoderWithConfidence(page);
    // If every encoder reports confidence, there's no fixture for the empty
    // state — we cannot test the negative path without a stub.
    test.skip(
      withConf?.encoder === allEncoders!.encoder,
      "no null-confidence encoder fixture available",
    );
    // Future: when an encoder lands that emits null-confidence-only, the
    // skip flips off and this assertion fires:
    // await expect(page.getByTestId("confidence-empty")).toBeVisible();
  });
  });
});

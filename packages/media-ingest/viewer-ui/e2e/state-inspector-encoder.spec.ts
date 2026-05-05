/**
 * State Inspector — `ner_encoder` node specs (plan §2.3, §2.4).
 *
 * Uses the discovery helpers to find an encoder with mentions (and one
 * with an error) so these tests skip cleanly when the run lacks the
 * shape rather than red-flagging. The deep-link pins `?run=` to keep
 * the SPA on the same run the helper found the shape in.
 */
import { test, expect } from "./fixtures/coverage";
import {
  firstEncoderWithMentions,
  firstEncoderWithError,
  resolveRunId,
} from "./fixtures/inspector-discovery";

test.describe("State Inspector — ner_encoder node", () => {
  test("encoder selection renders mention list + doc underlines", async ({ page }) => {
    const tgt = await firstEncoderWithMentions(page);
    test.skip(!tgt, "no encoder with span_start/end mentions");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
    );
    await expect(page.getByTestId("ner-encoder-detail")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("ner-encoder-status")).toContainText(/ok|error|unknown/);
    await expect(
      page.locator("[data-testid='ner-encoder-mention-row']").first(),
    ).toBeVisible({ timeout: 60_000 });
    expect(
      await page.locator("[data-testid='ner-encoder-mention-row']").count(),
    ).toBeGreaterThan(0);
    expect(
      await page.locator("[data-mention-source='encoder']").count(),
    ).toBeGreaterThan(0);
  });

  test("encoder with error surfaces error message + status=error", async ({ page }) => {
    const tgt = await firstEncoderWithError(page);
    test.skip(!tgt, "no encoder with error in any run");
    const runId = await resolveRunId(page);
    await page.goto(
      `/viewer/benchmarks/state?run=${runId}&doc=${encodeURIComponent(tgt!.docId)}&node=ner_encoder:${encodeURIComponent(tgt!.encoder)}`,
    );
    await expect(page.getByTestId("ner-encoder-detail")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByTestId("ner-encoder-status")).toContainText("error", { timeout: 60_000 });
    await expect(page.getByTestId("ner-encoder-error")).toBeVisible();
  });
});

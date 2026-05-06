/**
 * State Inspector — Gap #9 regression specs.
 *
 * Locks in the `source_models` chip on the consensus rejected-row variant
 * (`.test-output/inspector-tour/data-scientist-gaps.md` §2.9):
 *
 *   - Backend: `mention_rejected.details.source_models` is now emitted by
 *     ConsensusNode (and mirrored on cache-replay in benchmark_harness).
 *   - Frontend: ConsensusDetail's rejected-row MentionTable opts into a
 *     `"source"` column. MentionTable renders:
 *       * `sourceModels.length === 1` → cyan "from: <encoder>" chip with
 *         data-source-count="1".
 *       * `sourceModels.length > 1`   → zinc "from: N encoders" chip with
 *         data-source-count="<N>" and a hover tooltip listing every
 *         encoder name (no truncation, no dedupe, original ordering).
 *       * empty / missing             → no chip (legacy events).
 *
 *   - Cross-cut: Gap #2's encoder co-vote matrix `accepted+rejected` mode
 *     used to produce identical Jaccards to `accepted` because rejected
 *     events lacked `source_models`. With Gap #9 landed, runs that emit
 *     the new field should produce divergent values.
 *
 * Discovery: every test resolves the latest viable run via
 * `firstDocWithConsensus` and probes its `mention_rejected` events
 * directly. When the latest run was produced before Gap #9 (its rejected
 * events have no `source_models`), the relevant test skips cleanly with a
 * structured `test.skip(reason)` — that's the spec template's contract.
 */
import { request as plRequest } from "@playwright/test";
import { test, expect } from "./fixtures/coverage";
import { useCorpus } from "./fixtures/corpora";
import {
  firstDocWithConsensus,
  resolveRunId,
} from "./fixtures/inspector-discovery";

interface RejectedShape {
  text: string;
  source_models: string[];
}

interface RejectedSurvey {
  docId: string;
  /** All rejected events for the resolved run+doc. */
  all: RejectedShape[];
  /** Subset whose source_models is non-empty (Gap #9-era events). */
  withSource: RejectedShape[];
  /** Subset whose source_models is missing or empty (legacy events). */
  withoutSource: RejectedShape[];
}

/** Probe `mention_rejected` events for the resolved run+doc and bucket
 *  them by whether `source_models` is present and non-empty. Used by every
 *  test below to skip cleanly when the latest run predates Gap #9.
 *
 *  Uses Playwright's APIRequestContext (Node-side fetch) rather than
 *  `page.evaluate(fetch(...))` because the latter fails with "Failed to
 *  parse URL" before the page navigates to a real origin — every test
 *  here calls surveyRejected BEFORE goto to drive the skip decision. */
async function surveyRejected(
  page: import("@playwright/test").Page,
  docId: string,
): Promise<RejectedSurvey> {
  const runId = await resolveRunId(page);
  if (!runId) return { docId, all: [], withSource: [], withoutSource: [] };
  const baseURL =
    process.env.PLAYWRIGHT_BASE_URL ??
    process.env.VIEWER_URL ??
    "http://127.0.0.1:5173";
  const ctx = await plRequest.newContext({ baseURL });
  // Cap the scan at 5000 events (matches inspector-discovery's getEvents
  // ceiling) — exhaustive scans of 20k events time out at 60s on slower
  // hosts, and rejected mentions are dense enough per doc that the first
  // 5k usually contains every shape we need.
  const limit = process.env.PLAYWRIGHT_DISCOVERY_LIMIT ?? "5000";
  const path = `/viewer/api/bench/runs/${runId}/events?limit=${limit}`;
  let text = "";
  try {
    const resp = await ctx.get(path, { timeout: 60_000 });
    text = await resp.text();
  } finally {
    await ctx.dispose();
  }
  const all: RejectedShape[] = [];
  for (const ln of text.split("\n")) {
    if (!ln) continue;
    let ev: Record<string, unknown>;
    try {
      ev = JSON.parse(ln);
    } catch {
      continue;
    }
    if (ev.node_name !== "mention_rejected") continue;
    const evDocId =
      (ev.doc_id as string | undefined) ??
      ((ev.chunk_id as string | undefined) ?? "").split(":")[0];
    if (evDocId !== docId) continue;
    const d = (ev.details ?? {}) as Record<string, unknown>;
    const sm = Array.isArray(d.source_models)
      ? (d.source_models as string[])
      : [];
    all.push({ text: String(d.text ?? ""), source_models: sm });
  }
  const withSource = all.filter((r) => r.source_models.length > 0);
  const withoutSource = all.filter((r) => r.source_models.length === 0);
  return { docId, all, withSource, withoutSource };
}

async function deepLink(
  page: import("@playwright/test").Page,
  docId: string,
): Promise<string> {
  const runId = await resolveRunId(page);
  const runSeg = runId ? `run=${encodeURIComponent(runId)}&` : "";
  return `/viewer/benchmarks/state?${runSeg}doc=${encodeURIComponent(docId)}&node=consensus`;
}

async function expandRejected(page: import("@playwright/test").Page) {
  const toggle = page.getByTestId("consensus-rejected-toggle");
  await toggle.waitFor({ state: "visible", timeout: 30_000 });
  // The rejected list is collapsed by default; click iff it's not already
  // open. Probe via consensus-rejected-list visibility.
  const list = page.getByTestId("consensus-rejected-list");
  if ((await list.count()) === 0) {
    await toggle.click();
  }
  await list.waitFor({ state: "visible", timeout: 15_000 });
}

async function openCovoteDetails(page: import("@playwright/test").Page) {
  await page.evaluate(() => {
    const root = document.querySelector('[data-testid="consensus-detail"]');
    if (!root) return;
    root.querySelectorAll("details").forEach((d) => {
      (d as HTMLDetailsElement).open = true;
    });
  });
}

test.describe.configure({ timeout: 120_000 });

test.describe("State Inspector — Gap 9 — rejected source_models chip", () => {
  // CD-1qqy: happy-path corpus provides rejected mentions with source_models
  test.beforeEach(async ({ page }) => {
    await useCorpus(page, "happy-path");
  });

  test("rejected mentions render source-models chip when field present @smoke", async ({
    page,
  }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with consensus events in resolved run");
    const survey = await surveyRejected(page, tgt!.docId);
    test.skip(
      survey.all.length === 0,
      "no rejected mentions on this doc",
    );
    test.skip(
      survey.withSource.length === 0,
      "no rejected mentions with source_models in latest run (predates Gap #9 — re-run bench)",
    );

    await page.goto(await deepLink(page, tgt!.docId));
    const panel = page.getByTestId("consensus-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    await expandRejected(page);

    // At least one chip must have rendered. Count must match the survey's
    // withSource bucket size — that's the "no off-by-one, no truncation,
    // no dropped names" guarantee the data-scientist critic verifies.
    const chips = panel.locator(
      '[data-testid="consensus-rejected-row"] [data-testid="mention-source-chip"]',
    );
    await expect(chips.first()).toBeVisible({ timeout: 15_000 });
    const renderedCount = await chips.count();
    expect(renderedCount).toBe(survey.withSource.length);
  });

  test("lone-voter chip is cyan with single encoder name", async ({ page }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with consensus events");
    const survey = await surveyRejected(page, tgt!.docId);
    const lone = survey.withSource.filter((r) => r.source_models.length === 1);
    test.skip(
      lone.length === 0,
      "no lone-voter rejected mentions on this doc (need source_models.length === 1)",
    );

    await page.goto(await deepLink(page, tgt!.docId));
    const panel = page.getByTestId("consensus-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    await expandRejected(page);

    // Find a chip whose data-source-count is "1". Assert the chip's text
    // content matches `from: <non-whitespace>` AND the encoder name in the
    // text matches one of the lone-voter source_models on the survey side
    // (rules out off-by-one / dropped-name corruption).
    const loneChip = panel
      .locator(
        '[data-testid="consensus-rejected-row"] [data-testid="mention-source-chip"][data-source-count="1"]',
      )
      .first();
    await expect(loneChip).toBeVisible({ timeout: 15_000 });
    const text = ((await loneChip.textContent()) ?? "").trim();
    expect(text).toMatch(/^from: \S+$/);
    const renderedEncoder = text.replace(/^from:\s*/, "").trim();
    const allLoneEncoders = new Set(lone.map((r) => r.source_models[0]!));
    expect(
      allLoneEncoders.has(renderedEncoder),
      `rendered encoder '${renderedEncoder}' not in survey lone-voter set ${[...allLoneEncoders].join(",")}`,
    ).toBe(true);
    // Cyan styling — the class string carries the cyan-500/15 marker we
    // committed to in the SourceChip implementation.
    const className = (await loneChip.getAttribute("class")) ?? "";
    expect(className).toContain("cyan-500/15");
  });

  test("multi-voter chip is zinc with count", async ({ page }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with consensus events");
    const survey = await surveyRejected(page, tgt!.docId);
    const multi = survey.withSource.filter((r) => r.source_models.length > 1);
    test.skip(
      multi.length === 0,
      "no multi-voter rejected mentions on this doc (need source_models.length > 1)",
    );

    await page.goto(await deepLink(page, tgt!.docId));
    const panel = page.getByTestId("consensus-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    await expandRejected(page);

    // Iterate every multi-chip and assert each one's data-source-count is
    // ≥2 AND the text matches the literal "from: N encoders" template.
    // Encoder count from the chip must equal the survey row's source_models
    // length for the SAME mention text — otherwise the chip is showing a
    // count that disagrees with the underlying audit-event payload.
    const multiChips = panel.locator(
      '[data-testid="consensus-rejected-row"] [data-testid="mention-source-chip"]',
    );
    const total = await multiChips.count();
    let multiSeen = 0;
    for (let i = 0; i < total; i += 1) {
      const c = multiChips.nth(i);
      const cnt = Number(await c.getAttribute("data-source-count"));
      if (cnt < 2) continue;
      multiSeen += 1;
      expect(cnt).toBeGreaterThanOrEqual(2);
      const text = ((await c.textContent()) ?? "").trim();
      expect(text).toMatch(/^from: \d+ encoders$/);
      const stated = Number(text.match(/^from: (\d+) encoders$/)?.[1] ?? -1);
      expect(stated).toBe(cnt);
    }
    expect(multiSeen).toBeGreaterThanOrEqual(1);
    // Spot-check styling on the first multi-chip — zinc, not cyan.
    const firstMulti = panel
      .locator(
        '[data-testid="consensus-rejected-row"] [data-testid="mention-source-chip"][data-source-count="2"], [data-testid="consensus-rejected-row"] [data-testid="mention-source-chip"][data-source-count="3"], [data-testid="consensus-rejected-row"] [data-testid="mention-source-chip"][data-source-count="4"]',
      )
      .first();
    if ((await firstMulti.count()) > 0) {
      const className = (await firstMulti.getAttribute("class")) ?? "";
      expect(className).toContain("zinc-700/40");
      expect(className).not.toContain("cyan-500/15");
    }
  });

  test("tooltip on multi-voter chip lists all encoder names", async ({
    page,
  }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with consensus events");
    const survey = await surveyRejected(page, tgt!.docId);
    const multi = survey.withSource.filter((r) => r.source_models.length > 1);
    test.skip(
      multi.length === 0,
      "no multi-voter rejected mentions on this doc",
    );

    await page.goto(await deepLink(page, tgt!.docId));
    const panel = page.getByTestId("consensus-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    await expandRejected(page);

    // Find the first rejected row whose chip is multi (data-source-count >= 2).
    // We cross-reference its row text against the survey to know exactly
    // which encoder names should appear in the tooltip body — that's the
    // hard "no truncation / no dropped names" check the data-scientist
    // critic runs.
    const rows = panel.locator('[data-testid="consensus-rejected-row"]');
    const rowCount = await rows.count();
    let pickedEncoders: string[] | null = null;
    let pickedRow: import("@playwright/test").Locator | null = null;
    for (let i = 0; i < rowCount; i += 1) {
      const row = rows.nth(i);
      const chip = row
        .locator('[data-testid="mention-source-chip"]')
        .first();
      if ((await chip.count()) === 0) continue;
      const cnt = Number((await chip.getAttribute("data-source-count")) ?? 0);
      if (cnt < 2) continue;
      // Match this DOM row to a survey row by text prefix. The row's first
      // span is `row.text` from the audit event so .textContent() should
      // contain it.
      const rowText = ((await row.textContent()) ?? "").trim();
      const match = multi.find((r) => rowText.includes(r.text));
      if (!match) continue;
      pickedEncoders = match.source_models;
      pickedRow = row;
      break;
    }
    test.skip(
      !pickedRow || !pickedEncoders,
      "could not match a multi-voter DOM row to a survey row by text",
    );

    const chip = pickedRow!.locator('[data-testid="mention-source-chip"]').first();
    await chip.hover();
    // The catalyst-ui Tooltip renders into a Radix portal. Wait briefly
    // for it to appear, then probe its full text. We don't bind to a
    // specific testid (Radix doesn't expose one); we look up the live
    // tooltip role + match its text against every encoder name.
    const tipLocator = page.locator('[role="tooltip"]:visible');
    await tipLocator.first().waitFor({ state: "visible", timeout: 5_000 });
    const tipText = ((await tipLocator.first().textContent()) ?? "").trim();
    // Every encoder name from the audit event must appear verbatim. No
    // truncation / no abbreviation / no "+ N more" — that's the contract.
    for (const enc of pickedEncoders!) {
      expect(
        tipText.includes(enc),
        `tooltip body '${tipText}' missing encoder '${enc}' from source_models ${pickedEncoders!.join(",")}`,
      ).toBe(true);
    }
  });

  test("legacy rejected events without source_models render no chip", async ({
    page,
  }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with consensus events");
    const survey = await surveyRejected(page, tgt!.docId);
    test.skip(
      survey.withoutSource.length === 0,
      "no legacy (pre-Gap #9) rejected mentions on this doc",
    );
    test.skip(
      survey.withSource.length === 0,
      "doc has only legacy events — chip suppression is also true when the entire feature isn't wired (need both old + new style on same doc to assert suppression specifically)",
    );

    await page.goto(await deepLink(page, tgt!.docId));
    const panel = page.getByTestId("consensus-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    await expandRejected(page);

    // Total chip count must equal the withSource bucket size — no chips
    // for legacy rows. This is the inverse assertion of test #1.
    const chips = panel.locator(
      '[data-testid="consensus-rejected-row"] [data-testid="mention-source-chip"]',
    );
    expect(await chips.count()).toBe(survey.withSource.length);
  });

  test("co-vote matrix accepted+rejected mode produces different Jaccards from accepted-only", async ({
    page,
  }) => {
    const tgt = await firstDocWithConsensus(page);
    test.skip(!tgt, "no doc with consensus events");
    const survey = await surveyRejected(page, tgt!.docId);
    test.skip(
      survey.withSource.length < 5,
      `not enough rejected mentions with source_models (have ${survey.withSource.length}, need >=5)`,
    );

    await page.goto(await deepLink(page, tgt!.docId));
    const panel = page.getByTestId("consensus-detail");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    await openCovoteDetails(page);

    // Need the full N×N matrix variant (≥3 encoders) for cell snapshots —
    // the inline 2-encoder summary doesn't have a mode toggle.
    const modeAll = panel.getByTestId("encoder-covote-mode-all");
    if ((await modeAll.count()) === 0) {
      test.skip(true, "co-vote matrix is the inline 2-encoder variant (no mode toggle)");
    }

    // Snapshot the off-diagonal cells in `accepted` mode.
    const readOff = async () => {
      const cells = panel.locator(
        '[data-testid^="encoder-covote-cell-"]',
      );
      const total = await cells.count();
      const out: string[] = [];
      for (let i = 0; i < total; i += 1) {
        const t = ((await cells.nth(i).textContent()) ?? "").trim();
        // Off-diagonal Jaccards have shape "0.NN" — diagonals are "[N]".
        if (/^\d\.\d{2}$/.test(t)) out.push(t);
      }
      return out;
    };
    // Force accepted mode first to get a clean baseline.
    await panel.getByTestId("encoder-covote-mode-accepted").click();
    const acceptedSig = (await readOff()).join(",");

    // Toggle to all and re-read.
    await modeAll.click();
    await expect(modeAll).toHaveClass(/text-cyan-300/);
    const allSig = (await readOff()).join(",");

    // With Gap #9 landed AND ≥5 rejected with source_models, at least one
    // cell value MUST change — otherwise either the chip-data isn't being
    // consumed by the matrix (regression) or the rejected mentions all
    // come from encoder pairs already saturated on accepted.
    expect(allSig).not.toBe(acceptedSig);
  });
});

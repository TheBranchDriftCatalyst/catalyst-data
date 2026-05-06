#!/usr/bin/env node
/**
 * Gap #4 QA verifier — pack threshold histograms with draggable
 * counterfactual on PackDetail.
 *
 * Walks the latest bench run, finds a doc with both kept and pruned
 * windows so the bar stacks have both colors, deep-links to the pack
 * panel and runs the ten assertions from the verifier prompt.
 *
 * Output:
 *   .test-output/inspector-tour/qa-verify/gap-4-*.png   (4 screenshots)
 *   .test-output/inspector-tour/qa-verify/REPORT.md     (Gap #4 section appended)
 */

import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { resolveLocalViewerURL, resolveLocalApiURL } from "./_local-only.mjs";
import { safeFetchJson, safeFetchText } from "./_fetch.mjs";

// Force `localhost` because some Vite dev-server configs gate access to
// `127.0.0.1` (host check), and Playwright's headless Chromium then
// receives a blank page. localhost works on this stack.
const VIEWER = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";
const API = process.env.VIEWER_API_URL ?? "http://localhost:8080";
// Touch the imports so they remain (silences linters that strip unused).
void resolveLocalViewerURL;
void resolveLocalApiURL;
const REPO_ROOT = "/Users/panda/catalyst-devspace/workspace/catalyst-data";
const OUT_DIR = path.join(REPO_ROOT, ".test-output/inspector-tour/qa-verify");
const REPORT_PATH = path.join(OUT_DIR, "REPORT.md");

fs.mkdirSync(OUT_DIR, { recursive: true });

// ---- Discovery ----------------------------------------------------------

const idx = await safeFetchJson(`${API}/viewer/api/bench/runs`);
const runId = idx.latest;
if (!runId) {
  console.error("no runs available");
  process.exit(1);
}
console.log(`run: ${runId}`);

const evText = await safeFetchText(`${API}/viewer/api/bench/runs/${runId}/events?limit=30000`);
const events = evText
  .split("\n")
  .filter(Boolean)
  .map((l) => {
    try {
      return JSON.parse(l);
    } catch {
      return null;
    }
  })
  .filter(Boolean);
console.log(`events: ${events.length}`);

const packs = events.filter((e) => e.node_name === "pack_evidence" && e.status === "completed");
console.log(`pack_evidence completed events: ${packs.length}`);

let target = packs.find(
  (e) =>
    (e.details?.kept_windows?.length || 0) >= 1 &&
    (e.details?.pruned_windows?.length || 0) >= 1,
);
let bothColors = true;
if (!target) {
  bothColors = false;
  target = packs.find((e) => (e.details?.kept_windows?.length || 0) >= 1);
  if (!target) {
    console.error("no pack with kept_windows; aborting");
    process.exit(1);
  }
  console.log("⚠ no doc with both kept and pruned — falling back to kept-only");
}
const docId = target.doc_id || target.chunk_id?.split(":")[0];
if (!docId) {
  console.error("could not determine doc_id from target event");
  process.exit(1);
}
console.log(`doc: ${docId} (bothColors=${bothColors})`);
console.log(
  `kept=${target.details?.kept_windows?.length || 0} pruned_windows=${target.details?.pruned_windows?.length || 0}`,
);

// Assert tracker
const results = [];
const notes = [];
let suiteError = null;
const recordPass = (id, msg) => {
  results.push({ id, status: "PASS", msg });
  console.log(`  ✅ ${id} ${msg}`);
};
const recordFail = (id, msg) => {
  results.push({ id, status: "FAIL", msg });
  console.log(`  ❌ ${id} ${msg}`);
};
const recordSkip = (id, msg) => {
  results.push({ id, status: "SKIP", msg });
  console.log(`  ⚠ ${id} ${msg}`);
};

// ---- Browser setup ------------------------------------------------------

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
const page = await context.newPage();

const consoleErrors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});
page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));

const url = `${VIEWER}/viewer/benchmarks/state?run=${encodeURIComponent(runId)}&doc=${encodeURIComponent(docId)}&node=pack`;
console.log(`→ ${url}`);

await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 }).catch((e) => {
  console.warn(`navigate timeout: ${e.message}`);
});
await page.waitForLoadState("load", { timeout: 60_000 }).catch(() => {});

// Wait for the pack-detail to render (allow dev-server cold-compile slack)
try {
  // PackDetail renders <div data-testid="pack-detail"> only once events
  // have arrived AND a pack_evidence event for the doc was found. While
  // events are loading it returns a placeholder text node. Wait for the
  // kept-count chip which only appears in the loaded state.
  await page.waitForSelector('[data-testid="pack-kept-count"]', { timeout: 60_000 });
} catch (e) {
  // Diagnostic dump on timeout
  const html = await page.content().catch(() => "");
  console.error(`page-html-length=${html.length}`);
  const testids = Array.from(html.matchAll(/data-testid="([^"]+)"/g)).map((m) => m[1]);
  const unique = [...new Set(testids)];
  console.error(`distinct-testids: ${unique.slice(0, 100).join(", ")}`);
  console.error(`current-url: ${page.url()}`);
  // Show whatever is in the inspector-detail-pack container.
  const packContent = await page
    .locator('[data-testid="inspector-detail-pack"]')
    .innerText()
    .catch(() => "(no inspector-detail-pack)");
  console.error(`inspector-detail-pack content: ${packContent.slice(0, 500)}`);
  recordFail("setup", `pack-detail did not render: ${e.message}`);
  await page.screenshot({ path: path.join(OUT_DIR, "gap-4-pack-default.png"), fullPage: true });
  await browser.close();
  writeReport();
  process.exit(1);
}

// Screenshot: default state
await page.screenshot({ path: path.join(OUT_DIR, "gap-4-pack-default.png"), fullPage: false });
console.log("saved gap-4-pack-default.png");

try {
// ---- Assertion 1: roots visible ----------------------------------------
// The pack-detail can be tall enough that the cpm chart lands below the
// initial viewport. We use `count() > 0` (= mounted in DOM) rather than
// `isVisible()` (= in viewport AND non-zero box). The dragHandleBy
// helper scrolls into view before interacting.
{
  const ids = [
    "pack-threshold-histograms",
    "pack-histogram-mention-count",
    "pack-histogram-chars-per-mention",
  ];
  let ok = true;
  for (const id of ids) {
    const c = await page.locator(`[data-testid="${id}"]`).count().catch(() => 0);
    if (c === 0) {
      ok = false;
      recordFail("A1", `[data-testid="${id}"] not in DOM`);
    }
  }
  if (ok) recordPass("A1", "roots present in DOM (histograms + both charts)");
}

// ---- Assertion 2: bin counts -------------------------------------------
{
  const mentionBins = await page.locator('[data-testid^="pack-bin-mention-count-"]').count();
  const cpmBins = await page.locator('[data-testid^="pack-bin-chars-per-mention-"]').count();
  if (mentionBins >= 1 && cpmBins >= 1) {
    recordPass("A2", `bin counts: mention=${mentionBins} cpm=${cpmBins}`);
  } else {
    recordFail("A2", `bin counts insufficient: mention=${mentionBins} cpm=${cpmBins}`);
  }
}

// ---- Assertion 3: threshold lines render -------------------------------
{
  const mentionHandle = page.locator('[data-testid="pack-threshold-handle-mention-count"]');
  const cpmHandle = page.locator('[data-testid="pack-threshold-handle-chars-per-mention"]');
  const a = (await mentionHandle.count().catch(() => 0)) > 0;
  const b = (await cpmHandle.count().catch(() => 0)) > 0;
  if (a && b) recordPass("A3", "both threshold handles present");
  else recordFail("A3", `mentionHandle=${a} cpmHandle=${b}`);
}

// ---- Assertion 4: live readout default ---------------------------------
{
  // Wait for readouts to populate (React does an extra render when histograms mount)
  await page
    .locator('[data-testid="pack-readout-mention-count"]')
    .filter({ hasText: /current\s+min_mentions\s*=/ })
    .waitFor({ timeout: 10_000 })
    .catch(() => {});
  const mentionTxt = await page
    .locator('[data-testid="pack-readout-mention-count"]')
    .textContent()
    .catch(() => "");
  const cpmTxt = await page
    .locator('[data-testid="pack-readout-chars-per-mention"]')
    .textContent()
    .catch(() => "");
  const mOk = /current\s+min_mentions\s*=\s*/i.test(mentionTxt || "");
  const cOk = /current\s+max_chars_per_mention\s*=\s*/i.test(cpmTxt || "");
  if (mOk && cOk) {
    recordPass(
      "A4",
      `default readouts present (mention="${(mentionTxt || "").slice(0, 60)}…")`,
    );
  } else {
    recordFail("A4", `mentionOk=${mOk} cpmOk=${cOk}; mention="${mentionTxt}" cpm="${cpmTxt}"`);
  }
}

// ---- Helper: drag handle by px delta -----------------------------------
// Use page.mouse — Chromium synthesises PointerEvents from MouseEvents,
// and `setPointerCapture` only works with real pointer ids. Synthetic
// dispatch via page.evaluate breaks `setPointerCapture` ("No active
// pointer with the given id"), so we must drive the input system.
async function dragHandleBy(handleSel, dxPx) {
  const handle = page.locator(handleSel);
  // Wait for the handle to be attached (state changes during drag may
  // trigger React re-renders that briefly detach the rect).
  await handle.waitFor({ state: "attached", timeout: 10_000 }).catch(() => {});
  // Scroll the handle into view first — the pack-detail panel can place
  // the cpm chart below the initial viewport (1100px tall) so the bbox
  // ends up at y > 1100 and `page.mouse` clicks land off-screen.
  await handle.scrollIntoViewIfNeeded({ timeout: 5_000 }).catch(() => {});
  await page.waitForTimeout(200);
  let box = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    box = await handle.boundingBox().catch(() => null);
    if (box) break;
    await page.waitForTimeout(300);
  }
  if (!box) throw new Error(`no bounding box for ${handleSel}`);
  const startX = box.x + box.width / 2;
  const startY = box.y + box.height / 2;
  await page.mouse.move(startX, startY);
  await page.waitForTimeout(50);
  await page.mouse.down();
  const steps = 25;
  for (let i = 1; i <= steps; i++) {
    await page.mouse.move(startX + (dxPx * i) / steps, startY);
  }
  await page.mouse.up();
  await page.waitForTimeout(400);
}

// ---- Assertion 5: drag mention-count handle ----------------------------
{
  const before = await page
    .locator('[data-testid="pack-readout-mention-count"]')
    .textContent()
    .catch(() => "");
  try {
    // Drag right by 60px (should advance threshold by ≥ 1 bin)
    await dragHandleBy('[data-testid="pack-threshold-handle-mention-count"]', 60);
    const after = await page
      .locator('[data-testid="pack-readout-mention-count"]')
      .textContent()
      .catch(() => "");
    const flipped = /at\s+min_mentions\s*≥/.test(after || "");
    const loseDeltaMatch = /lose\s+(\d+)\s+to\s+too_few/.exec(after || "");
    const loseDelta = loseDeltaMatch ? parseInt(loseDeltaMatch[1], 10) : null;
    if (flipped && (loseDelta === null || loseDelta >= 0)) {
      recordPass(
        "A5",
        `readout flipped to counterfactual form (lose Δ=${loseDelta}); before="${(before || "").slice(0, 50)}" after="${(after || "").slice(0, 80)}"`,
      );
    } else {
      recordFail(
        "A5",
        `readout did not flip; before="${before}" after="${after}"`,
      );
    }
  } catch (e) {
    recordFail("A5", `drag failed: ${e.message}`);
  }
  await page.screenshot({
    path: path.join(OUT_DIR, "gap-4-pack-dragged-min.png"),
    fullPage: false,
  });
  console.log("saved gap-4-pack-dragged-min.png");
}

// ---- Assertion 6: drag chars_per_mention handle ------------------------
{
  // First reload to reset previewMin (so the chars drag screenshot is clean of mention-count drag state)
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 }).catch(() => {});
  await page.waitForLoadState("load", { timeout: 60_000 }).catch(() => {});
  await page
    .waitForSelector('[data-testid="pack-kept-count"]', { state: "attached", timeout: 60_000 })
    .catch(() => {});
  await page
    .waitForSelector('[data-testid="pack-threshold-handle-chars-per-mention"]', {
      state: "attached",
      timeout: 30_000,
    })
    .catch(() => {});
  await page.waitForTimeout(800);

  const before = await page
    .locator('[data-testid="pack-readout-chars-per-mention"]')
    .textContent()
    .catch(() => "");
  try {
    // Try drags in both directions, choosing whichever moves the readout.
    // The CPM handle snap-targets are bin edges (0/50/100/200/400/800/1600/observedMax)
    // in log-space; a -200px drag from 800 lands well below 50 so it
    // snaps to 0; a +200px drag pushes past 1600 and snaps to observedMax.
    let after = before;
    let flipped = false;
    for (const delta of [-200, 200, -100, 100]) {
      await dragHandleBy('[data-testid="pack-threshold-handle-chars-per-mention"]', delta);
      after = await page
        .locator('[data-testid="pack-readout-chars-per-mention"]')
        .textContent()
        .catch(() => "");
      flipped = /at\s+max_chars_per_mention\s*≤/.test(after || "");
      if (flipped) break;
    }
    if (flipped) {
      recordPass(
        "A6",
        `cpm readout flipped (after="${(after || "").slice(0, 100)}…")`,
      );
    } else {
      recordFail("A6", `cpm readout did not flip; before="${before}" after="${after}"`);
    }
  } catch (e) {
    recordFail("A6", `drag failed: ${e.message}`);
  }
  await page.screenshot({
    path: path.join(OUT_DIR, "gap-4-pack-dragged-cpm.png"),
    fullPage: false,
  });
  console.log("saved gap-4-pack-dragged-cpm.png");
}

// ---- Assertion 7: bar click filters tables -----------------------------
{
  // Reset
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 }).catch(() => {});
  await page.waitForLoadState("load", { timeout: 60_000 }).catch(() => {});
  // PackDetail only renders <div data-testid="pack-detail"> once events
  // have arrived and pack_evidence is found. While loading it shows a
  // placeholder. Wait for the kept-count chip which only appears in the
  // loaded state.
  await page
    .waitForSelector('[data-testid="pack-kept-count"]', {
      state: "attached",
      timeout: 60_000,
    })
    .catch(() => {});
  await page.waitForTimeout(500);
  await page.waitForTimeout(800);

  // Pick a mention-count bin that has some data — try indices and find the first that exists
  const allBins = await page.locator('[data-testid^="pack-bin-mention-count-"]').all();
  if (allBins.length === 0) {
    recordSkip("A7", "no mention-count bins available");
  } else {
    // Find a bin whose corresponding kept rows exist & match — pick a low-index bin like bin idx of first kept row
    // Prefer the bin matching the FIRST kept row's mention count.
    const keptRows = await page.locator('[data-testid^="pack-kept-row-"]').count();
    if (keptRows === 0) {
      recordSkip("A7", "no kept rows in the table to dim");
    } else {
      // Try each bin click and check if pack-filter-clear becomes visible
      let success = false;
      let dimmedCount = 0;
      // Strategy: click the LAST bin (highest mention_count) since it's most likely to be a unique kept-row class
      // Actually, a simpler approach — just click each in order and verify clear-pill shows + at least one row dims.
      for (let i = 0; i < allBins.length; i++) {
        const bin = allBins[i];
        await bin.click({ force: true });
        await page.waitForTimeout(250);
        const clearVisible = await page
          .locator('[data-testid="pack-filter-clear"]')
          .isVisible()
          .catch(() => false);
        if (clearVisible) {
          // Count opacity-30 rows
          const dimmed = await page
            .locator('[data-testid^="pack-kept-row-"].opacity-30')
            .count();
          dimmedCount = dimmed;
          if (dimmed > 0) {
            success = true;
            break;
          } else {
            // bin matched all rows — clear and try the next
            const clear = page.locator('[data-testid="pack-filter-clear"]');
            await clear.click();
            await page.waitForTimeout(150);
          }
        }
      }
      if (success) {
        // Take screenshot of filtered state
        await page.screenshot({
          path: path.join(OUT_DIR, "gap-4-pack-bar-filtered.png"),
          fullPage: false,
        });
        console.log("saved gap-4-pack-bar-filtered.png");
        recordPass("A7", `bar click → ${dimmedCount} kept rows dimmed; clear-pill visible`);

        // Click clear → confirm dimming clears
        const clear = page.locator('[data-testid="pack-filter-clear"]');
        await clear.click();
        await page.waitForTimeout(250);
        const dimmedAfter = await page
          .locator('[data-testid^="pack-kept-row-"].opacity-30')
          .count();
        const clearVisibleAfter = await page
          .locator('[data-testid="pack-filter-clear"]')
          .isVisible()
          .catch(() => false);
        if (dimmedAfter === 0 && !clearVisibleAfter) {
          recordPass("A7b", "clear-pill clicked → all dimming gone, pill hidden");
        } else {
          recordFail(
            "A7b",
            `after clear: dimmed=${dimmedAfter} clearVisible=${clearVisibleAfter}`,
          );
        }
      } else {
        // Take a screenshot anyway
        await page.screenshot({
          path: path.join(OUT_DIR, "gap-4-pack-bar-filtered.png"),
          fullPage: false,
        });
        recordFail(
          "A7",
          "no bin click produced any dimmed rows (filter chip never made any row mismatch)",
        );
      }
    }
  }
}

// ---- Assertion 8: switching axes mutually exclusive --------------------
{
  // Click a mention-count bin, then a chars-per-mention bin → only one filter active
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 }).catch(() => {});
  await page.waitForLoadState("load", { timeout: 60_000 }).catch(() => {});
  // PackDetail only renders <div data-testid="pack-detail"> once events
  // have arrived and pack_evidence is found. While loading it shows a
  // placeholder. Wait for the kept-count chip which only appears in the
  // loaded state.
  await page
    .waitForSelector('[data-testid="pack-kept-count"]', {
      state: "attached",
      timeout: 60_000,
    })
    .catch(() => {});
  await page.waitForTimeout(500);
  await page.waitForTimeout(800);

  const mentionBins = await page.locator('[data-testid^="pack-bin-mention-count-"]').all();
  const cpmBins = await page.locator('[data-testid^="pack-bin-chars-per-mention-"]').all();
  if (mentionBins.length === 0 || cpmBins.length === 0) {
    recordSkip("A8", "missing bins on one axis");
  } else {
    // Click first mention bin
    await mentionBins[0].click({ force: true });
    await page.waitForTimeout(200);
    // Find a "filtered" mention bin (has rect outline). The active filter applies an outline rect — easier to verify behavioral form: swap to cpm bin and confirm the previous mention-bin is no longer outlined.
    const clearVisibleAfterFirst = await page
      .locator('[data-testid="pack-filter-clear"]')
      .isVisible()
      .catch(() => false);

    // Click first cpm bin
    await cpmBins[0].click({ force: true });
    await page.waitForTimeout(200);
    const clearVisibleAfterSecond = await page
      .locator('[data-testid="pack-filter-clear"]')
      .isVisible()
      .catch(() => false);

    // Inspect: count outlined rects (cyan stroke) inside mention vs cpm SVGs.
    // Implementation outlines the filtered bin with stroke="rgb(34 211 238)".
    const mentionOutlineCount = await page
      .locator(
        '[data-testid="pack-histogram-mention-count"] rect[stroke="rgb(34 211 238)"]',
      )
      .count();
    const cpmOutlineCount = await page
      .locator(
        '[data-testid="pack-histogram-chars-per-mention"] rect[stroke="rgb(34 211 238)"]',
      )
      .count();

    if (
      clearVisibleAfterFirst &&
      clearVisibleAfterSecond &&
      cpmOutlineCount >= 1 &&
      mentionOutlineCount === 0
    ) {
      recordPass(
        "A8",
        `mutually exclusive: after cpm click mentionOutlines=${mentionOutlineCount} cpmOutlines=${cpmOutlineCount}`,
      );
    } else {
      recordFail(
        "A8",
        `mut-excl failed: clearAfterFirst=${clearVisibleAfterFirst} clearAfterSecond=${clearVisibleAfterSecond} mentionOutlines=${mentionOutlineCount} cpmOutlines=${cpmOutlineCount}`,
      );
    }
  }
}

// ---- Assertion 9: existing testids still work --------------------------
{
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 }).catch(() => {});
  await page.waitForLoadState("load", { timeout: 60_000 }).catch(() => {});
  // PackDetail only renders <div data-testid="pack-detail"> once events
  // have arrived and pack_evidence is found. While loading it shows a
  // placeholder. Wait for the kept-count chip which only appears in the
  // loaded state.
  await page
    .waitForSelector('[data-testid="pack-kept-count"]', {
      state: "attached",
      timeout: 60_000,
    })
    .catch(() => {});
  await page.waitForTimeout(500);
  await page.waitForTimeout(500);

  const ids = ["pack-detail", "pack-kept-count", "pack-pruned-count"];
  let allOk = true;
  for (const id of ids) {
    const c = await page.locator(`[data-testid="${id}"]`).count().catch(() => 0);
    if (c === 0) {
      allOk = false;
      recordFail("A9", `[data-testid="${id}"] not in DOM`);
    }
  }
  const keptRowCount = await page.locator('[data-testid^="pack-kept-row-"]').count();
  if (allOk && keptRowCount >= 1) {
    recordPass("A9", `regression check OK; kept-rows=${keptRowCount}`);
  } else if (allOk) {
    recordFail("A9", `kept rows missing (count=${keptRowCount})`);
  }
}

// ---- Assertion 10: pointer capture survives leaving SVG ----------------
{
  // Drag the mention-count handle, move outside SVG, back inside, then up.
  const handle = page.locator('[data-testid="pack-threshold-handle-mention-count"]');
  const handleBox = await handle.boundingBox();
  const svg = page.locator('[data-testid="pack-histogram-mention-count"]');
  const svgBox = await svg.boundingBox();
  if (!handleBox || !svgBox) {
    recordFail("A10", "no bounding box for handle or svg");
  } else {
    const readoutBefore = await page
      .locator('[data-testid="pack-readout-mention-count"]')
      .textContent()
      .catch(() => "");
    const startX = handleBox.x + handleBox.width / 2;
    const startY = handleBox.y + handleBox.height / 2;
    const outsideX = svgBox.x + svgBox.width + 200;
    const outsideY = svgBox.y - 200;

    await page.mouse.move(startX, startY);
    await page.mouse.down();
    // Phase 1: still inside SVG
    for (let i = 1; i <= 8; i++) await page.mouse.move(startX + (30 * i) / 8, startY);
    await page.waitForTimeout(150);
    const readoutInside1 = await page
      .locator('[data-testid="pack-readout-mention-count"]')
      .textContent()
      .catch(() => "");

    // Phase 2: well outside SVG
    for (let i = 1; i <= 10; i++) {
      const fr = i / 10;
      await page.mouse.move(
        startX + 30 + (outsideX - (startX + 30)) * fr,
        startY + (outsideY - startY) * fr,
      );
    }
    await page.waitForTimeout(150);
    const readoutOutside = await page
      .locator('[data-testid="pack-readout-mention-count"]')
      .textContent()
      .catch(() => "");

    // Phase 3: back inside, different X
    for (let i = 1; i <= 10; i++) {
      const fr = i / 10;
      await page.mouse.move(
        outsideX + (startX + 80 - outsideX) * fr,
        outsideY + (startY - outsideY) * fr,
      );
    }
    await page.waitForTimeout(150);
    const readoutInside2 = await page
      .locator('[data-testid="pack-readout-mention-count"]')
      .textContent()
      .catch(() => "");

    await page.mouse.up();
    await page.waitForTimeout(200);

    // Validity criterion: readouts changed at least once during/after the
    // outside trip — meaning pointer-capture kept the drag alive.
    const flippedInside1 = /at\s+min_mentions\s*≥/.test(readoutInside1 || "");
    const flippedInside2 = /at\s+min_mentions\s*≥/.test(readoutInside2 || "");
    const counterfactualEverActive = flippedInside1 || flippedInside2;

    // Distinct readout while outside vs inside1: not strictly required (it
    // depends on bar-width vs distance) — what we really need is that
    // the drag did NOT silently snap back to default after going outside.
    // We assert: readout still in counterfactual form after re-entering.
    if (flippedInside2) {
      recordPass(
        "A10",
        `pointer capture survived; readoutAfterReentry="${(readoutInside2 || "").slice(0, 80)}…"`,
      );
    } else if (counterfactualEverActive) {
      recordFail(
        "A10",
        `drag started but readout reverted after leaving SVG; in1="${readoutInside1}" out="${readoutOutside}" in2="${readoutInside2}"`,
      );
    } else {
      recordFail(
        "A10",
        `drag never engaged the counterfactual form; readouts: before="${readoutBefore}" in1="${readoutInside1}" out="${readoutOutside}" in2="${readoutInside2}"`,
      );
    }
  }
}

// ---- Cross-cutting: console errors -------------------------------------
{
  if (consoleErrors.length === 0) {
    recordPass("CC1", "no console errors during navigation + interactions");
  } else {
    recordFail(
      "CC1",
      `${consoleErrors.length} console error(s); first: ${consoleErrors[0].slice(0, 200)}`,
    );
  }
}
} catch (e) {
  suiteError = e;
  console.error(`suite error: ${e.message}`);
  results.push({ id: "SUITE", status: "FAIL", msg: `unexpected throw: ${e.message}` });
}

// ---- Cleanup ------------------------------------------------------------
await browser.close();

// ---- Visual anomaly notes ----------------------------------------------
{
  // Bar visibility heuristic: too few mention bins (<= 2) means bars stretch wide
  if ((target.details?.kept_windows?.length || 0) <= 1 && (target.details?.pruned_windows?.length || 0) === 0) {
    notes.push("only one kept window — bars are coarse, but the threshold UI still rendered");
  }
  if (suiteError) {
    notes.push(`suite threw early: ${suiteError.message}`);
  }
}

// ---- Report writing -----------------------------------------------------
function writeReport() {
  const total = results.length;
  const pass = results.filter((r) => r.status === "PASS").length;
  const fail = results.filter((r) => r.status === "FAIL").length;
  const skip = results.filter((r) => r.status === "SKIP").length;

  const lines = [];
  lines.push("");
  lines.push("---");
  lines.push("");
  lines.push("## Gap #4 — Pack threshold histograms with draggable counterfactual");
  lines.push("");
  lines.push(`- Run: \`${runId}\``);
  lines.push(`- Doc: \`${docId}\``);
  lines.push(`- Doc has both kept + pruned windows: **${bothColors ? "yes" : "no (kept-only fallback)"}**`);
  lines.push(`- Total assertions: ${total} (✅ ${pass} · ❌ ${fail} · ⚠ ${skip})`);
  lines.push("");
  lines.push("### Assertion results");
  lines.push("");
  lines.push("| ID | Status | Notes |");
  lines.push("|----|--------|-------|");
  for (const r of results) {
    const icon = r.status === "PASS" ? "✅" : r.status === "FAIL" ? "❌" : "⚠";
    const safeMsg = r.msg.replace(/\|/g, "\\|").replace(/\n/g, " ");
    lines.push(`| ${r.id} | ${icon} ${r.status} | ${safeMsg} |`);
  }
  lines.push("");
  lines.push("### Screenshots");
  lines.push("");
  for (const f of [
    "gap-4-pack-default.png",
    "gap-4-pack-dragged-min.png",
    "gap-4-pack-dragged-cpm.png",
    "gap-4-pack-bar-filtered.png",
  ]) {
    const p = path.join(OUT_DIR, f);
    const exists = fs.existsSync(p);
    lines.push(`- \`${p}\` ${exists ? "" : "(missing)"}`);
  }
  if (notes.length > 0) {
    lines.push("");
    lines.push("### Notes / anomalies");
    lines.push("");
    for (const n of notes) lines.push(`- ${n}`);
  }
  if (consoleErrors.length > 0) {
    lines.push("");
    lines.push("### Console errors");
    lines.push("");
    for (const e of consoleErrors.slice(0, 10)) lines.push(`- \`${e.slice(0, 300)}\``);
  }
  lines.push("");

  // Idempotently replace any prior Gap #4 section so re-runs don't stack.
  let existing = "";
  try {
    existing = fs.readFileSync(REPORT_PATH, "utf8");
  } catch {
    /* fresh file */
  }
  // Strip a previous Gap #4 section (everything from the "---" separator
  // immediately before "## Gap #4" through end-of-file).
  const gapHeader = "## Gap #4 — Pack threshold histograms";
  const gapIdx = existing.indexOf(gapHeader);
  if (gapIdx !== -1) {
    // Walk back to the preceding "---\n\n" if present, else to the gap header.
    const before = existing.slice(0, gapIdx);
    const sepIdx = before.lastIndexOf("\n---\n");
    const cutAt = sepIdx !== -1 ? sepIdx : gapIdx;
    existing = existing.slice(0, cutAt).replace(/\s*$/, "") + "\n";
  }
  fs.writeFileSync(REPORT_PATH, existing + lines.join("\n"));
  console.log(`\nwrote Gap #4 section to ${REPORT_PATH}`);
}

writeReport();

const fail = results.filter((r) => r.status === "FAIL").length;
process.exit(fail > 0 ? 1 : 0);

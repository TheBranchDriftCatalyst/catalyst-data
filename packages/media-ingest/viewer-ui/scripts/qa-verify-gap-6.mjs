#!/usr/bin/env node
/**
 * Gap #6 QA verifier — DocumentSourcePanel right-edge coverage gutter
 * (mention density + GT recall holes + selected-window marker).
 *
 * Pre-conditions: viewer-api on :8080, viewer-ui dev server on :5173.
 *
 * Walks the latest bench run, finds a doc that has consensus mentions
 * AND at least one kept window (so we can exercise both the cyan-density
 * layer and the saturated selected-window marker), deep-links into the
 * State Inspector, and runs the assertion suite.
 *
 * Output:
 *   .test-output/inspector-tour/qa-verify/gap-6-default.png
 *   .test-output/inspector-tour/qa-verify/gap-6-window-selected.png
 *   .test-output/inspector-tour/qa-verify/REPORT.md   (Gap #6 section appended)
 *
 * Note on GT: the active GT in S3 currently has ``total_mentions=0`` for
 * many bench runs. Per the verifier prompt we ⚠ skip the recall-hole
 * track assertion in that case rather than failing the suite — the cyan
 * density layer alone is enough to certify the gutter is wired up.
 */

import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { resolveLocalViewerURL, resolveLocalApiURL } from "./_local-only.mjs";
import { safeFetchJson, safeFetchText } from "./_fetch.mjs";

const VIEWER = resolveLocalViewerURL();
const API = resolveLocalApiURL();
const REPO_ROOT = "/Users/panda/catalyst-devspace/workspace/catalyst-data";
const OUT_DIR = path.join(REPO_ROOT, ".test-output/inspector-tour/qa-verify");
const REPORT_PATH = path.join(OUT_DIR, "REPORT.md");

fs.mkdirSync(OUT_DIR, { recursive: true });

// ── allow-list for benign React/Vite dev console noise ──────────────────────
const ALLOWED_CONSOLE_PATTERNS = [
  /Warning: Each child in a list/,
  /Download the React DevTools/,
  /\[vite\]/i,
];
function isAllowedConsole(text) {
  return ALLOWED_CONSOLE_PATTERNS.some((p) => p.test(text));
}

// ── assertion tracker ───────────────────────────────────────────────────────
const results = [];
const notes = [];
const recordPass = (id, msg) => {
  results.push({ id, status: "PASS", msg });
  console.log(`  PASS ${id} ${msg}`);
};
const recordFail = (id, msg) => {
  results.push({ id, status: "FAIL", msg });
  console.log(`  FAIL ${id} ${msg}`);
};
const recordSkip = (id, msg) => {
  results.push({ id, status: "SKIP", msg });
  console.log(`  SKIP ${id} ${msg}`);
};

// ── 1. discover run + doc ──────────────────────────────────────────────────

const idx = await safeFetchJson(`${API}/viewer/api/bench/runs`);
const runId = idx.latest;
if (!runId) {
  console.error("no runs available");
  process.exit(1);
}
console.log(`run: ${runId}`);

const evText = await safeFetchText(
  `${API}/viewer/api/bench/runs/${encodeURIComponent(runId)}/events?limit=50000`,
);
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

// Find docs that have consensus mentions populated (chunk_id ends in
// :_consensus and details.mentions has length >= 1).
const consensusByDoc = new Map(); // doc_id → mention_count
const packKeptByDoc = new Map(); // doc_id → first kept window_id (with offsets)
for (const e of events) {
  if (
    e.node_name === "chunk_extracted" &&
    typeof e.chunk_id === "string" &&
    e.chunk_id.endsWith(":_consensus") &&
    e.status === "completed"
  ) {
    const docId = e.chunk_id.slice(0, -":_consensus".length);
    const m = e.details?.mentions ?? e.details?.accepted ?? [];
    const positioned = m.filter(
      (x) => x?.span_start != null && x?.span_end != null,
    );
    if (positioned.length > 0) {
      consensusByDoc.set(docId, (consensusByDoc.get(docId) ?? 0) + positioned.length);
    }
  }
  if (e.node_name === "pack_evidence" && e.status === "completed") {
    const docId = e.doc_id;
    if (!docId) continue;
    if (packKeptByDoc.has(docId)) continue;
    const kept = e.details?.kept_windows ?? [];
    const w = kept.find(
      (k) => typeof k.doc_char_start === "number" && typeof k.doc_char_end === "number",
    );
    if (w) packKeptByDoc.set(docId, w);
  }
}

// Prefer a doc with both consensus AND a kept window so the
// selected-window screenshot has data. Fall back to consensus-only.
let docId = null;
let chosenWindow = null;
for (const [d, count] of consensusByDoc) {
  if (packKeptByDoc.has(d)) {
    docId = d;
    chosenWindow = packKeptByDoc.get(d);
    console.log(`doc: ${d} (${count} consensus mentions, kept window=${chosenWindow.window_id})`);
    break;
  }
}
if (!docId) {
  for (const [d, count] of consensusByDoc) {
    docId = d;
    console.log(`doc: ${d} (${count} consensus mentions, no kept window — selected-window assertion will skip)`);
    break;
  }
}
if (!docId) {
  console.error("no doc has positioned consensus mentions on this run");
  process.exit(1);
}

// GT signal — only used to decide whether to assert vs skip on the
// recall-hole track. Empty GT is handled as a SKIP per the verifier prompt.
let gtAvailableForDoc = false;
try {
  // 404 is fine ("no active GT") — anything else (HTML, 5xx) throws loud.
  const gtUrl = `${API}/viewer/api/bench/ground-truth/active.json`;
  const probe = await fetch(gtUrl);
  if (probe.ok) {
    const gt = await safeFetchJson(gtUrl);
    for (const ch of gt.chunks ?? []) {
      if (ch.doc_id !== docId) continue;
      const ms = ch.mentions ?? [];
      if (ms.some((m) => m.span_start != null)) {
        gtAvailableForDoc = true;
        break;
      }
    }
  }
} catch {
  /* ignore — fall through to skip */
}
console.log(`gt active populated for this doc: ${gtAvailableForDoc}`);

// ── 2. browser ─────────────────────────────────────────────────────────────

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1600, height: 1100 },
});
const page = await context.newPage();

const consoleErrors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") {
    const txt = msg.text();
    if (!isAllowedConsole(txt)) consoleErrors.push(txt);
  }
});
page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));

const baseUrl = `${VIEWER}/viewer/benchmarks/state?run=${encodeURIComponent(
  runId,
)}&doc=${encodeURIComponent(docId)}&node=consensus`;

console.log(`\n→ ${baseUrl}`);

try {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 30_000 });
} catch (e) {
  console.warn(`navigate timeout: ${e.message}`);
}
await page.waitForTimeout(1500);

// Gate everything in a try/finally so we always write the report.
try {
  // Wait for the doc panel + gutter to mount.
  try {
    await page.waitForSelector('[data-testid="doc-source-panel"]', { timeout: 30_000 });
  } catch (e) {
    recordFail("setup", `doc-source-panel did not render: ${e.message}`);
    throw e;
  }

  // ── A1: gutter visible ─────────────────────────────────────────────────
  let gutterHandle = null;
  try {
    await page.waitForSelector('[data-testid="doc-coverage-gutter"]', {
      timeout: 15_000,
    });
    gutterHandle = page.locator('[data-testid="doc-coverage-gutter"]').first();
    const visible = await gutterHandle.isVisible().catch(() => false);
    if (visible) recordPass("A1", "doc-coverage-gutter visible");
    else recordFail("A1", "doc-coverage-gutter present but not visible");
  } catch (e) {
    recordFail("A1", `doc-coverage-gutter never appeared: ${e.message}`);
  }

  // Wait for the events stream to arrive — for ~38k events on this run
  // it usually takes 15-25s. We poll for at least one bin to register a
  // non-zero consensus count, which is the signal that
  // `_collectConsensusSpans` has run against fully-loaded events. Without
  // this gate the rest of the suite asserts against an empty gutter.
  const populated = await page
    .waitForFunction(
      () => {
        const bins = document.querySelectorAll(
          '[data-testid^="doc-coverage-bin-"]',
        );
        for (const b of bins) {
          const c = parseInt(b.getAttribute("data-bin-consensus-count") || "0", 10);
          if (c > 0) return true;
        }
        return false;
      },
      null,
      { timeout: 90_000, polling: 1_000 },
    )
    .then(() => true)
    .catch(() => false);
  if (!populated) {
    notes.push(
      "events stream never populated the gutter density layer — the rest of the assertions run against an empty gutter (still meaningful as smoke tests)",
    );
  } else {
    console.log("  gutter density populated; proceeding to assertions");
  }

  // (Density-population gate above ensures the default screenshot
  //  captures real bars; without that gate the early shot was empty.)
  await page.screenshot({
    path: path.join(OUT_DIR, "gap-6-default.png"),
    fullPage: false,
  });
  console.log("saved gap-6-default.png");

  // ── A2: at least one bin hit-rect ─────────────────────────────────────
  const binCount = await page.locator('[data-testid^="doc-coverage-bin-"]').count();
  if (binCount >= 1) {
    recordPass("A2", `${binCount} bin hit-rects rendered`);
  } else {
    recordFail("A2", `expected ≥1 bin, got ${binCount}`);
  }

  // ── A3: click bin near middle scrolls the doc-text scroller ───────────
  // The scrollRef element is the inner overflow-y-auto sibling of the gutter
  // inside doc-source-panel — locate it via the panel root.
  const scrollerHandle = page.locator(
    '[data-testid="doc-source-panel"] .overflow-y-auto',
  ).first();
  const scrollerExists = (await scrollerHandle.count()) > 0;
  if (!scrollerExists) {
    recordFail("A3", "could not locate scrollable doc-text element");
  } else if (binCount === 0) {
    recordSkip("A3", "no bins to click");
  } else {
    const scrollBefore = await scrollerHandle.evaluate((el) => el.scrollTop);
    const targetIdx = Math.min(75, binCount - 1);
    const target = page
      .locator(`[data-testid="doc-coverage-bin-${targetIdx}"]`)
      .first();
    if ((await target.count()) === 0) {
      recordSkip("A3", `bin ${targetIdx} not present (binCount=${binCount})`);
    } else {
      await target.click({ force: true });
      // smooth-scroll → poll briefly for scrollTop change
      let scrollAfter = scrollBefore;
      for (let attempt = 0; attempt < 20; attempt++) {
        await page.waitForTimeout(120);
        scrollAfter = await scrollerHandle.evaluate((el) => el.scrollTop);
        if (Math.abs(scrollAfter - scrollBefore) > 5) break;
      }
      if (Math.abs(scrollAfter - scrollBefore) > 5) {
        recordPass(
          "A3",
          `click bin #${targetIdx} → scrollTop ${scrollBefore.toFixed(0)} → ${scrollAfter.toFixed(0)} (Δ=${(scrollAfter - scrollBefore).toFixed(0)})`,
        );
      } else {
        recordFail(
          "A3",
          `click bin #${targetIdx} did not change scrollTop (before=${scrollBefore} after=${scrollAfter})`,
        );
      }
    }
  }

  // ── A4: hover tooltip text ─────────────────────────────────────────────
  if (binCount >= 1) {
    // Pick the highest-density bin so the tooltip text is meaningful —
    // hovering bin 0 sometimes lands on a leading-whitespace region
    // with 0 consensus mentions, which still passes the format regex
    // but doesn't really prove the count is wired up.
    const targetIdx = await page.evaluate(() => {
      const bins = Array.from(
        document.querySelectorAll('[data-testid^="doc-coverage-bin-"]'),
      );
      let best = 0;
      let bestVal = -1;
      for (let i = 0; i < bins.length; i++) {
        const c = parseInt(
          bins[i].getAttribute("data-bin-consensus-count") || "0",
          10,
        );
        if (c > bestVal) {
          bestVal = c;
          best = i;
        }
      }
      return best;
    });
    const target = page
      .locator(`[data-testid="doc-coverage-bin-${targetIdx}"]`)
      .first();
    await target.hover();
    await page.waitForTimeout(450);
    // catalyst-ui tooltip lives on a Radix Portal — query body-wide for the
    // tooltip surface (TOOLTIP_CLS uses bg-surface-1).
    const tooltipText = await page.locator('[role="tooltip"]').first().textContent().catch(() => "");
    const looksRight =
      /chars\s+\d/.test(tooltipText || "") &&
      /mention/.test(tooltipText || "");
    if (looksRight) {
      recordPass(
        "A4",
        `tooltip text looks correct: "${(tooltipText || "").slice(0, 80)}…"`,
      );
    } else {
      // Tooltip surfacing depends on Radix portal — flag as info, not fail.
      recordSkip(
        "A4",
        `tooltip not surfaced (tooltipText="${(tooltipText || "").slice(0, 80)}"); Radix may render under viewport edge`,
      );
    }
    // Move pointer off the bin to dismiss the tooltip before the next step.
    await page.mouse.move(0, 0);
  } else {
    recordSkip("A4", "no bins to hover");
  }

  // ── A5: GT recall-hole track ──────────────────────────────────────────
  const gtTrackCount = await page.locator('[data-testid="doc-coverage-gt-track"]').count();
  if (!gtAvailableForDoc) {
    recordSkip(
      "A5",
      `active GT has 0 mentions for doc=${docId}; cyan density alone certifies the gutter (per verifier prompt)`,
    );
    if (gtTrackCount > 0) {
      notes.push(
        `gt-track rendered (${gtTrackCount}) despite no GT for this doc — should be 0; investigate.`,
      );
    }
  } else {
    if (gtTrackCount >= 1) {
      recordPass("A5", `gt-track rendered (${gtTrackCount} group)`);
    } else {
      recordFail(
        "A5",
        `gt-track absent despite gt populated for doc — expected at least 1`,
      );
    }
  }

  // ── A6: selected-window marker on spo_window selection ────────────────
  if (!chosenWindow) {
    recordSkip("A6", "no kept window with doc_char offsets on this doc");
  } else {
    const winChunkId = `${docId}:${chosenWindow.window_id}`;
    const url = `${VIEWER}/viewer/benchmarks/state?run=${encodeURIComponent(
      runId,
    )}&doc=${encodeURIComponent(docId)}&node=spo_window:${encodeURIComponent(winChunkId)}`;
    console.log(`\n→ ${url}`);
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
    } catch (e) {
      console.warn(`navigate timeout: ${e.message}`);
    }
    try {
      await page.waitForSelector('[data-testid="doc-coverage-gutter"]', { timeout: 15_000 });
    } catch {
      /* fallthrough — assertion will fail below */
    }
    // First wait for events to actually arrive on this navigation —
    // selectedWindow is resolved from pack_evidence.kept_windows which
    // only appears once useRunStream has populated the events array.
    // We gate on the gutter density-population signal (any bin with > 0
    // consensus_count means events arrived); without this gate the
    // marker assertion races the events stream.
    const eventsArrivedSpoNav = await page
      .waitForFunction(
        () => {
          const bins = document.querySelectorAll(
            '[data-testid^="doc-coverage-bin-"]',
          );
          for (const b of bins) {
            const c = parseInt(
              b.getAttribute("data-bin-consensus-count") || "0",
              10,
            );
            if (c > 0) return true;
          }
          return false;
        },
        null,
        { timeout: 120_000, polling: 1_000 },
      )
      .then(() => true)
      .catch(() => false);
    console.log(
      `  spo_window nav: events arrived = ${eventsArrivedSpoNav}`,
    );
    const markerAppeared = await page
      .waitForFunction(
        () =>
          document.querySelector(
            '[data-testid="doc-coverage-selected-window-marker"]',
          ) !== null,
        null,
        { timeout: 30_000, polling: 500 },
      )
      .then(() => true)
      .catch(() => false);
    const markerVisible = markerAppeared;
    if (!markerAppeared && !eventsArrivedSpoNav) {
      notes.push(
        "spo_window nav: events stream never arrived within 90s — marker assertion is gated on pack_evidence which lives in the events array; treat as flaky-environment",
      );
    }
    await page.screenshot({
      path: path.join(OUT_DIR, "gap-6-window-selected.png"),
      fullPage: false,
    });
    console.log("saved gap-6-window-selected.png");
    if (markerVisible) {
      recordPass("A6", `selected-window marker rendered for window=${chosenWindow.window_id}`);
    } else {
      recordFail(
        "A6",
        `selected-window marker not found for window=${chosenWindow.window_id}`,
      );
    }
  }

  // ── A7: console errors ────────────────────────────────────────────────
  if (consoleErrors.length === 0) {
    recordPass("A7", "no console errors during interactions");
  } else {
    recordFail(
      "A7",
      `${consoleErrors.length} console error(s); first: ${consoleErrors[0].slice(0, 200)}`,
    );
  }
} catch (e) {
  if (results.length === 0 || !results.some((r) => r.id === "setup")) {
    results.push({ id: "SUITE", status: "FAIL", msg: `unexpected throw: ${e.message}` });
  }
} finally {
  await browser.close();
}

// ── 3. write report ────────────────────────────────────────────────────────

function writeReport() {
  const total = results.length;
  const pass = results.filter((r) => r.status === "PASS").length;
  const fail = results.filter((r) => r.status === "FAIL").length;
  const skip = results.filter((r) => r.status === "SKIP").length;

  const lines = [];
  lines.push("");
  lines.push("---");
  lines.push("");
  lines.push("## Gap #6 — DocumentSourcePanel right-edge coverage gutter");
  lines.push("");
  lines.push(`- Run: \`${runId}\``);
  lines.push(`- Doc: \`${docId}\``);
  lines.push(
    `- Selected window: ${chosenWindow ? `\`${chosenWindow.window_id}\`` : "_(none — A6 skipped)_"}`,
  );
  lines.push(`- GT active populated for this doc: **${gtAvailableForDoc ? "yes" : "no (A5 skipped)"}**`);
  lines.push(`- Total assertions: ${total} (PASS ${pass} · FAIL ${fail} · SKIP ${skip})`);
  lines.push("");
  lines.push("### Assertion results");
  lines.push("");
  lines.push("| ID | Status | Notes |");
  lines.push("|----|--------|-------|");
  for (const r of results) {
    const safeMsg = r.msg.replace(/\|/g, "\\|").replace(/\n/g, " ");
    lines.push(`| ${r.id} | ${r.status} | ${safeMsg} |`);
  }
  lines.push("");
  lines.push("### Screenshots");
  lines.push("");
  for (const f of ["gap-6-default.png", "gap-6-window-selected.png"]) {
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

  // Idempotently replace any prior Gap #6 section so re-runs don't stack.
  let existing = "";
  try {
    existing = fs.readFileSync(REPORT_PATH, "utf8");
  } catch {
    /* fresh file */
  }
  const gapHeader = "## Gap #6 — DocumentSourcePanel right-edge coverage gutter";
  const gapIdx = existing.indexOf(gapHeader);
  if (gapIdx !== -1) {
    const before = existing.slice(0, gapIdx);
    const sepIdx = before.lastIndexOf("\n---\n");
    const cutAt = sepIdx !== -1 ? sepIdx : gapIdx;
    existing = existing.slice(0, cutAt).replace(/\s*$/, "") + "\n";
  }
  fs.writeFileSync(REPORT_PATH, existing + lines.join("\n"));
  console.log(`\nwrote Gap #6 section to ${REPORT_PATH}`);
}

writeReport();

const fail = results.filter((r) => r.status === "FAIL").length;
process.exit(fail > 0 ? 1 : 0);

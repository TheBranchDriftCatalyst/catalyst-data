#!/usr/bin/env node
/**
 * Gap #5 QA verifier — SPO prompt + raw-response inspection panes on
 * spo_window detail, plus tokens/cost rollup on spo_model header.
 *
 * Discovery picks the most-recent run that already has at least one
 * `chunk_extracted` event whose `details.prompt_hash` is non-null
 * (Gap #5 backend shape). If no such run exists, the suite skips
 * cleanly with a ⚠ — we deliberately don't trigger a fresh bench
 * here; the data-scientist-gaps spec is locked, so absence of fresh
 * data is a fixture issue, not a UI failure.
 *
 * Output:
 *   .test-output/inspector-tour/qa-verify/gap-5-*.png
 *   .test-output/inspector-tour/qa-verify/REPORT.md  (Gap #5 section appended/replaced)
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

// ── helpers ────────────────────────────────────────────────────────────────

const results = [];
const notes = [];
const consoleErrors = [];
let sampleChunkId = null;
let sampleModel = null;
let winWithErrors = null;
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

async function fetchEvents(runId) {
  // safeFetchText throws loud on SPA-fallback HTML / non-2xx, so an
  // unreachable proxy fails this verifier instead of silently returning [].
  const txt = await safeFetchText(
    `${API}/viewer/api/bench/runs/${encodeURIComponent(runId)}/events?limit=50000`,
  );
  return txt
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
}

// ── 1. discover a run with the new SPO event shape ─────────────────────────

const idx = await safeFetchJson(`${API}/viewer/api/bench/runs`);
const runIds = idx.runs ?? [];
if (runIds.length === 0) {
  console.error("no runs available — cannot verify Gap #5");
  recordSkip("setup", "no bench runs in /viewer/api/bench/runs");
  writeReport(null, null);
  process.exit(0);
}

let chosen = null;
let chosenEvents = null;
for (const runId of runIds) {
  console.log(`scanning ${runId}…`);
  const events = await fetchEvents(runId);
  // /events leaks across runs — filter strictly to this run_id so the
  // chosen sample chunk actually surfaces in the URL-pinned stream.
  const ownEvents = events.filter((e) => e.run_id === runId);
  const win = ownEvents.find(
    (e) =>
      e.node_name === "chunk_extracted" &&
      typeof e.chunk_id === "string" &&
      e.chunk_id.includes(":win-") &&
      e.details &&
      typeof e.details.prompt_hash === "string" &&
      e.details.prompt_hash.length > 0,
  );
  if (win) {
    chosen = { runId, sampleWin: win };
    chosenEvents = ownEvents;
    console.log(`✓ run ${runId} has prompt_hash on ${win.chunk_id}`);
    break;
  }
}

if (!chosen) {
  console.warn("⚠ no run has chunk_extracted events with details.prompt_hash");
  console.warn("  (Gap #5 backend may not have re-run the bench since landing)");
  recordSkip("setup", "no run with details.prompt_hash — Gap #5 backend hasn't been exercised yet");

  // Smoke-test: the new SpoCallInspect block must NOT render on legacy
  // spo_window events, and must NOT break the existing chunk-text-panel.
  // We deep-link to a real spo_window in any run that owns :win- events
  // (the /events endpoint leaks across runs, so we have to scan to find
  // a run that actually has its own win events). Assert legacy testids
  // resolve while the new ones are absent (prompt_hash missing → empty-
  // state guard returns null).
  console.log("\n→ regression smoke-test: legacy spo_window must not break");
  let fallbackRunId = null;
  let fallbackWin = null;
  for (const candidate of runIds) {
    const ev = await fetchEvents(candidate);
    const own = ev.filter((e) => e.run_id === candidate);
    const win = own.find(
      (e) =>
        e.node_name === "chunk_extracted" &&
        typeof e.chunk_id === "string" &&
        e.chunk_id.includes(":win-"),
    );
    if (win) {
      fallbackRunId = candidate;
      fallbackWin = win;
      break;
    }
  }
  if (fallbackRunId && fallbackWin) {
    {
      console.log(`  using run ${fallbackRunId} · win ${fallbackWin.chunk_id}`);
      const fallbackDoc = fallbackWin.doc_id || fallbackWin.chunk_id.split(":")[0];
      const browser = await chromium.launch({ headless: true });
      const ctx = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
      const p = await ctx.newPage();
      const errs = [];
      p.on("pageerror", (e) => errs.push(`pageerror: ${e.message}`));
      p.on("console", (m) => {
        if (m.type() === "error") errs.push(m.text());
      });
      const u = `${VIEWER}/viewer/benchmarks/state?run=${encodeURIComponent(fallbackRunId)}&doc=${encodeURIComponent(fallbackDoc)}&node=spo_window:${encodeURIComponent(fallbackWin.chunk_id)}`;
      try {
        await p.goto(u, { waitUntil: "domcontentloaded", timeout: 30_000 });
        await p.waitForTimeout(1500);
        await p.waitForSelector('[data-testid="chunk-text-panel"]', { timeout: 20_000 });
        const legacyTestidsOk =
          (await p.locator('[data-testid="chunk-text-panel"]').count()) === 1;
        const newPaneCount = await p.locator('[data-testid="spo-prompt-pane"]').count();
        if (legacyTestidsOk && newPaneCount === 0) {
          recordPass(
            "smoke",
            `legacy spo_window renders without prompt_hash; new SpoCallInspect correctly absent`,
          );
        } else {
          recordFail(
            "smoke",
            `legacy=${legacyTestidsOk} newPaneCount=${newPaneCount} (expected pane count=0)`,
          );
        }
        const real = errs.filter(
          (t) =>
            !/Warning: Each child in a list/.test(t) &&
            !/Download the React DevTools/.test(t) &&
            !/\[vite\]/i.test(t),
        );
        if (real.length === 0) {
          recordPass("smoke-console", "no console errors on legacy spo_window");
        } else {
          recordFail("smoke-console", `console errors: ${real[0].slice(0, 200)}`);
        }
      } catch (e) {
        recordFail("smoke", `page load failed: ${e.message}`);
      } finally {
        await browser.close();
      }
    }
  } else {
    recordSkip("smoke", "no run owns any :win- chunk_extracted events — cannot run regression smoke-test");
  }

  writeReport(null, null);
  const fail = results.filter((r) => r.status === "FAIL").length;
  process.exit(fail > 0 ? 1 : 0);
}

const { runId, sampleWin } = chosen;
const events = chosenEvents;
const docId = sampleWin.doc_id || sampleWin.chunk_id.split(":")[0];
sampleChunkId = sampleWin.chunk_id;
console.log(`run: ${runId} · doc: ${docId} · sample chunkId: ${sampleChunkId}`);

// Pick the spo_model: any model attached to a :win- chunk_extracted on
// this doc with the new shape.
const modelEvents = events.filter(
  (e) =>
    e.node_name === "chunk_extracted" &&
    typeof e.chunk_id === "string" &&
    e.chunk_id.includes(":win-") &&
    e.doc_id === docId,
);
const modelSet = new Set(modelEvents.map((e) => e.model).filter(Boolean));
sampleModel = modelEvents.find(
  (e) => e.details?.usage || typeof e.details?.cost_usd !== "undefined",
)?.model ?? [...modelSet][0];
console.log(`sampleModel: ${sampleModel}`);

// Pick a window with parse_errors !== [] for the optional callout test.
winWithErrors = events.find(
  (e) =>
    e.node_name === "chunk_extracted" &&
    typeof e.chunk_id === "string" &&
    e.chunk_id.includes(":win-") &&
    e.doc_id === docId &&
    Array.isArray(e.details?.parse_errors) &&
    e.details.parse_errors.length > 0,
);
console.log(`winWithErrors: ${winWithErrors ? winWithErrors.chunk_id : "(none)"}`);

// ── 2. drive the browser ───────────────────────────────────────────────────

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
const page = await context.newPage();

page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});
page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));

function urlFor(role, ref) {
  const refSeg = ref ? `:${encodeURIComponent(ref)}` : "";
  return `${VIEWER}/viewer/benchmarks/state?run=${encodeURIComponent(runId)}&doc=${encodeURIComponent(docId)}&node=${role}${refSeg}`;
}

async function navigate(url) {
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30_000 });
  } catch (e) {
    console.warn(`  navigate timeout — continuing: ${e.message}`);
  }
  await page.waitForTimeout(1500);
}

// ── 3. spo_window: prompt/response/usage panes ─────────────────────────────

{
  const url = urlFor("spo_window", sampleChunkId);
  console.log(`\n→ spo_window: ${url}`);
  await navigate(url);
  // Wait for the chunk-text-panel to settle.
  try {
    await page.waitForSelector('[data-testid="chunk-text-panel"]', { timeout: 30_000 });
  } catch (e) {
    recordFail("setup-window", `chunk-text-panel never rendered: ${e.message}`);
    await page.screenshot({ path: path.join(OUT_DIR, "gap-5-spo-window.png"), fullPage: false });
    await browser.close();
    writeReport(runId, docId);
    process.exit(1);
  }

  await page.screenshot({ path: path.join(OUT_DIR, "gap-5-spo-window.png"), fullPage: false });

  // A1: prompt pane visible
  {
    const visible = await page.locator('[data-testid="spo-prompt-pane"]').isVisible().catch(() => false);
    if (visible) recordPass("A1", "spo-prompt-pane visible");
    else recordFail("A1", "spo-prompt-pane not visible");
  }
  // A2: response pane visible
  {
    const visible = await page.locator('[data-testid="spo-response-pane"]').isVisible().catch(() => false);
    if (visible) recordPass("A2", "spo-response-pane visible");
    else recordFail("A2", "spo-response-pane not visible");
  }
  // A3: usage strip visible
  {
    const visible = await page.locator('[data-testid="spo-usage-strip"]').isVisible().catch(() => false);
    if (visible) {
      const txt = (await page.locator('[data-testid="spo-usage-strip"]').textContent()) ?? "";
      const hasIn = txt.includes(" in");
      const hasOut = txt.includes(" out");
      const hasTotal = txt.includes("total");
      if (hasIn && hasOut && hasTotal) recordPass("A3", `usage strip OK: "${txt.replace(/\s+/g, " ").trim()}"`);
      else recordFail("A3", `usage strip text malformed: "${txt}"`);
    } else {
      recordFail("A3", "spo-usage-strip not visible");
    }
  }
  // A4: hash chip
  {
    const txt = (await page.locator('[data-testid="spo-prompt-hash"]').textContent().catch(() => "")) ?? "";
    const expectedPrefix = sampleWin.details.prompt_hash.slice(0, 8);
    if (txt.trim() === expectedPrefix) {
      recordPass("A4", `hash chip = "${txt.trim()}"`);
    } else {
      recordFail("A4", `hash chip text "${txt}" != expected prefix "${expectedPrefix}"`);
    }
  }
  // A5: expand prompt → fetch resolves to non-empty
  {
    const pane = page.locator('[data-testid="spo-prompt-pane"]');
    // Expand the <details> first by clicking the summary
    await pane.locator("summary").click();
    await page.waitForTimeout(150);
    const expandBtn = page.locator('[data-testid="spo-prompt-expand"]');
    const btnVisible = await expandBtn.isVisible().catch(() => false);
    if (!btnVisible) {
      recordFail("A5", "spo-prompt-expand button not visible after summary click");
    } else {
      // Set up a response listener for the prompts endpoint.
      const respPromise = page.waitForResponse(
        (r) => r.url().includes("/viewer/api/bench/prompts/") && r.request().method() === "GET",
        { timeout: 10_000 },
      ).catch(() => null);
      await expandBtn.click();
      const resp = await respPromise;
      if (!resp) {
        recordFail("A5", "no /viewer/api/bench/prompts/ response observed");
      } else {
        const status = resp.status();
        if (status === 200) {
          const body = await resp.text();
          if (body && body.length > 0) {
            recordPass("A5", `prompts endpoint 200, body len=${body.length}`);
          } else {
            recordFail("A5", `prompts endpoint 200 but empty body`);
          }
        } else if (status === 404) {
          // Tolerated — the prompt may not be archived in S3 (older runs).
          recordSkip("A5", `prompts endpoint 404 — full prompt not archived; UI shows missing-message`);
        } else {
          recordFail("A5", `prompts endpoint returned ${status}`);
        }
      }
      await page.waitForTimeout(250);
      await page.screenshot({
        path: path.join(OUT_DIR, "gap-5-prompt-expanded.png"),
        fullPage: false,
      });
    }
  }
}

// ── 4. spo_model: tokens/cost rollup badge ─────────────────────────────────

{
  if (!sampleModel) {
    recordSkip("A6", "no spo_model identified for this doc");
  } else {
    const url = urlFor("spo_model", sampleModel);
    console.log(`\n→ spo_model: ${url}`);
    await navigate(url);
    try {
      await page.waitForSelector('[data-testid="spo-model-table"]', { timeout: 20_000 });
    } catch {
      recordFail("A6", "spo-model-table did not render");
    }
    await page.screenshot({
      path: path.join(OUT_DIR, "gap-5-spo-model-header.png"),
      fullPage: false,
    });
    const badge = page.locator('[data-testid="spo-model-tokens-cost"]');
    const badgeCount = await badge.count();
    if (badgeCount === 0) {
      // If no event for this model carries `usage`, the badge is
      // intentionally suppressed. Skip rather than fail.
      const modelHasTokens = modelEvents.some(
        (e) => e.model === sampleModel && e.details?.usage,
      );
      if (!modelHasTokens) {
        recordSkip("A6", `spo-model-tokens-cost absent — no usage on any window for ${sampleModel}`);
      } else {
        recordFail("A6", `spo-model-tokens-cost absent but usage events exist`);
      }
    } else {
      const txt = (await badge.textContent()) ?? "";
      if (txt.includes("tok in")) {
        recordPass("A6", `spo-model-tokens-cost = "${txt.replace(/\s+/g, " ").trim()}"`);
      } else {
        recordFail("A6", `spo-model-tokens-cost text malformed: "${txt}"`);
      }
    }
  }
}

// ── 5. parse_errors callout (optional) ─────────────────────────────────────

{
  if (!winWithErrors) {
    recordSkip("A7", "no window in this run has details.parse_errors with entries");
  } else {
    const url = urlFor("spo_window", winWithErrors.chunk_id);
    console.log(`\n→ spo_window with parse_errors: ${url}`);
    await navigate(url);
    try {
      await page.waitForSelector('[data-testid="chunk-text-panel"]', { timeout: 20_000 });
    } catch (e) {
      recordFail("A7", `chunk-text-panel did not render: ${e.message}`);
    }
    const callout = page.locator('[data-testid="spo-parse-errors"]');
    const visible = await callout.isVisible().catch(() => false);
    if (visible) {
      const rowCount = await page.locator('[data-testid="spo-parse-error-row"]').count();
      const expected = winWithErrors.details.parse_errors.length;
      if (rowCount === expected) {
        recordPass("A7", `${rowCount}/${expected} parse_error rows rendered`);
      } else {
        recordFail("A7", `parse_error rows: rendered=${rowCount} expected=${expected}`);
      }
      await page.screenshot({
        path: path.join(OUT_DIR, "gap-5-parse-errors.png"),
        fullPage: false,
      });
    } else {
      recordFail("A7", "spo-parse-errors callout not visible despite parse_errors in event");
    }
  }
}

// ── 6. console errors ──────────────────────────────────────────────────────

if (consoleErrors.length === 0) {
  recordPass("CC1", "no console errors during navigation + interactions");
} else {
  // Filter out known noise — the existing qa-verify-gaps script already
  // tolerates these patterns in production.
  const real = consoleErrors.filter(
    (t) =>
      !/Warning: Each child in a list/.test(t) &&
      !/Download the React DevTools/.test(t) &&
      !/\[vite\]/i.test(t),
  );
  if (real.length === 0) {
    recordPass("CC1", "console errors observed but all in known-noise allow-list");
  } else {
    recordFail("CC1", `${real.length} console error(s); first: ${real[0].slice(0, 240)}`);
  }
}

await browser.close();

// ── 7. write report ────────────────────────────────────────────────────────

writeReport(runId, docId);

const fail = results.filter((r) => r.status === "FAIL").length;
const passCount = results.filter((r) => r.status === "PASS").length;
const skipCount = results.filter((r) => r.status === "SKIP").length;
console.log(`\nGap #5: ${passCount} pass / ${fail} fail / ${skipCount} skip`);
process.exit(fail > 0 ? 1 : 0);

// ── helpers (writeReport) ──────────────────────────────────────────────────

function writeReport(runIdLocal, docIdLocal) {
  const total = results.length;
  const pass = results.filter((r) => r.status === "PASS").length;
  const fail = results.filter((r) => r.status === "FAIL").length;
  const skip = results.filter((r) => r.status === "SKIP").length;

  const lines = [];
  lines.push("");
  lines.push("---");
  lines.push("");
  lines.push("## Gap #5 — SPO prompt + raw-response inspection");
  lines.push("");
  if (runIdLocal && docIdLocal) {
    lines.push(`- Run: \`${runIdLocal}\``);
    lines.push(`- Doc: \`${docIdLocal}\``);
    lines.push(`- Sample window: \`${sampleChunkId ?? "—"}\``);
    lines.push(`- Sample model: \`${sampleModel ?? "—"}\``);
    lines.push(`- Window with parse_errors: \`${winWithErrors?.chunk_id ?? "(none in this run)"}\``);
  } else {
    lines.push("- _No run with the new SPO event shape (details.prompt_hash) found — suite skipped cleanly._");
  }
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
    "gap-5-spo-window.png",
    "gap-5-spo-model-header.png",
    "gap-5-prompt-expanded.png",
    "gap-5-parse-errors.png",
  ]) {
    const p = path.join(OUT_DIR, f);
    const exists = fs.existsSync(p);
    lines.push(`- \`${p}\` ${exists ? "" : "(missing — assertion skipped)"}`);
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

  // Idempotently replace any prior Gap #5 section so re-runs don't stack.
  let existing = "";
  try {
    existing = fs.readFileSync(REPORT_PATH, "utf8");
  } catch {
    /* fresh file */
  }
  const gapHeader = "## Gap #5 — SPO prompt";
  const gapIdx = existing.indexOf(gapHeader);
  if (gapIdx !== -1) {
    const before = existing.slice(0, gapIdx);
    const sepIdx = before.lastIndexOf("\n---\n");
    const cutAt = sepIdx !== -1 ? sepIdx : gapIdx;
    existing = existing.slice(0, cutAt).replace(/\s*$/, "") + "\n";
  }
  fs.writeFileSync(REPORT_PATH, existing + lines.join("\n"));
  console.log(`\nwrote Gap #5 section to ${REPORT_PATH}`);
}

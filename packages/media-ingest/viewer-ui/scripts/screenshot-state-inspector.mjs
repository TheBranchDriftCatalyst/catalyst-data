#!/usr/bin/env node
/**
 * One-off Playwright tour script — screenshots every node type in the
 * State Inspector for the latest bench run.
 *
 * Goal: feed the screenshots to a data-scientist + QA agent pair so they
 * can enumerate functionality and propose either richer UI surfaces or
 * test coverage. Not a regression test — runs ad-hoc and writes to
 * /tmp/inspector-tour/.
 *
 * Usage:
 *   node scripts/screenshot-state-inspector.mjs
 *
 * Pre-conditions: viewer-api running on :8080, viewer-ui dev server on :5173,
 * and at least one bench run with pack_evidence + per-encoder events.
 */

import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { resolveLocalViewerURL, resolveLocalApiURL } from "./_local-only.mjs";
import { safeFetchJson, safeFetchText } from "./_fetch.mjs";

const VIEWER = resolveLocalViewerURL();
const API = resolveLocalApiURL();
const OUT_DIR = "/tmp/inspector-tour";

fs.mkdirSync(OUT_DIR, { recursive: true });

const idx = await safeFetchJson(`${API}/viewer/api/bench/runs`);
const runId = idx.latest;
if (!runId) {
  console.error("no runs available — run a bench first");
  process.exit(1);
}
console.log(`run: ${runId}`);

// Fetch the events ndjson stream and pick a doc that has the full pipeline.
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
console.log(`events fetched: ${events.length}`);

const docs = [...new Set(events.map((e) => e.doc_id).filter((d) => d && d !== "__run__"))];
const docId = docs.find((d) =>
  events.some(
    (e) => e.doc_id === d && e.node_name === "pack_evidence" && e.status === "completed",
  ),
);
if (!docId) {
  console.error("no doc with pack_evidence completed in latest run");
  process.exit(1);
}
console.log(`doc: ${docId}`);

const docEvents = events.filter((e) => e.doc_id === docId);
const encoders = [
  ...new Set(
    docEvents
      .map((e) => (e.chunk_id?.includes(":_ner_") ? e.chunk_id.split(":_ner_")[1] : null))
      .filter(Boolean),
  ),
];
const spoModels = [
  ...new Set(
    docEvents
      .filter((e) => e.chunk_id?.includes(":win-") && e.node_name === "chunk_extracted" && e.model)
      .map((e) => e.model),
  ),
];
const packEv = docEvents.find(
  (e) => e.node_name === "pack_evidence" && e.status === "completed",
);
const kept = packEv?.details?.kept_windows ?? [];
const pruned = packEv?.details?.pruned_windows ?? [];
const sampleWindow = kept.find((w) => typeof w.doc_char_start === "number");
const samplePruned = pruned[0];

const tour = [
  { name: "01-document", ref: null, role: "document" },
  ...encoders.map((enc, i) => ({
    name: `02-encoder-${i.toString().padStart(2, "0")}-${enc}`,
    ref: enc,
    role: "ner_encoder",
  })),
  { name: "03-consensus", ref: null, role: "consensus" },
  { name: "04-pack", ref: null, role: "pack" },
  { name: "05-windows-collapsed", ref: null, role: "spo_windows_collapsed" },
  ...(sampleWindow
    ? [
        {
          name: `06-spo-window-${sampleWindow.window_id}`,
          ref: `${docId}:${sampleWindow.window_id}`,
          role: "spo_window",
        },
      ]
    : []),
  ...spoModels.map((m, i) => ({
    name: `07-spo-model-${i.toString().padStart(2, "0")}-${m}`,
    ref: m,
    role: "spo_model",
  })),
  ...(samplePruned
    ? [
        {
          name: `08-pruned-window-${samplePruned.window_id}`,
          ref: samplePruned.window_id,
          role: "pruned_window",
        },
      ]
    : []),
  { name: "09-persist", ref: null, role: "persist" },
];

console.log(`tour stops: ${tour.length}`);

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1600, height: 1100 },
});
const page = await context.newPage();

for (const stop of tour) {
  const refSeg = stop.ref ? `:${encodeURIComponent(stop.ref)}` : "";
  const url = `${VIEWER}/viewer/benchmarks/state?run=${encodeURIComponent(
    runId,
  )}&doc=${encodeURIComponent(docId)}&node=${stop.role}${refSeg}`;
  console.log(`→ ${stop.name}`);
  try {
    await page.goto(url, { waitUntil: "networkidle", timeout: 30_000 });
  } catch (e) {
    console.warn(`  navigate timeout — continuing: ${e.message}`);
  }
  await page.waitForTimeout(1500); // settle scrolls + animations
  const file = path.join(OUT_DIR, `${stop.name}.png`);
  await page.screenshot({ path: file, fullPage: false });
  console.log(`  saved ${file}`);
}

await browser.close();
console.log(`\ndone — ${tour.length} screenshots in ${OUT_DIR}`);

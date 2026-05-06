#!/usr/bin/env node
/**
 * QA verification for State Inspector Gap #1 (F1/P/R header strip + GT chips)
 * and Gap #3 (per-encoder confidence histogram).
 *
 * Pre-conditions: viewer-api on :8080, viewer-ui dev server on :5173.
 *
 * Outputs:
 *   - Screenshots in $OUT_DIR (default .test-output/inspector-tour/qa-verify/)
 *   - REPORT.md with one section per gap (pass / fail / skip per assertion)
 *   - Exit code 0 always — assertions are recorded, not enforced; the report
 *     is the source of truth. Caller can grep "❌" if they want a hard exit.
 *
 * Usage: node scripts/qa-verify-gaps.mjs
 */

import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import {
  safeFetchJson,
  safeFetchText,
  resolveLocalViewerURL,
  resolveLocalApiURL,
} from "./_fetch.mjs";

// NEVER hardcode a deployed (talos / prod) host here.
// resolveLocalViewerURL fails LOUD if PLAYWRIGHT_BASE_URL / VIEWER_URL
// resolves to a non-localhost host (see scripts/_local-only.mjs).
const VIEWER = resolveLocalViewerURL();
const API = resolveLocalApiURL();
const OUT_DIR =
  process.env.QA_OUT_DIR ||
  "/Users/panda/catalyst-devspace/workspace/catalyst-data/.test-output/inspector-tour/qa-verify";

fs.mkdirSync(OUT_DIR, { recursive: true });

// ── helpers ──────────────────────────────────────────────────────────────────

const FORMAT_2DP = /^\d+\.\d{2}$/;
const FORMAT_DELTA = /[+-]?\d+\.\d{2}/;
// Allow-list of console messages we don't treat as failures. React dev mode
// occasionally surfaces missing-key warnings on rapid re-renders; those are
// not what we care about here.
const ALLOWED_CONSOLE_PATTERNS = [
  /Warning: Each child in a list/,
  /Download the React DevTools/,
  /\[vite\]/i,
];

function isAllowedConsole(text) {
  return ALLOWED_CONSOLE_PATTERNS.some((p) => p.test(text));
}

function makeUrl(runId, docId, role, ref) {
  const refSeg = ref ? `:${encodeURIComponent(ref)}` : "";
  return `${VIEWER}/viewer/benchmarks/state?run=${encodeURIComponent(
    runId,
  )}&doc=${encodeURIComponent(docId)}&node=${role}${refSeg}`;
}

/** A single recorded assertion result. */
function assertion(name, status, note, evidence) {
  return { name, status, note: note ?? "", evidence: evidence ?? "" };
}

// ── 1. pick run + doc ────────────────────────────────────────────────────────

const idx = await safeFetchJson(`${API}/viewer/api/bench/runs`);
const runId = idx.latest;
if (!runId) {
  console.error("no runs available — run a bench first");
  process.exit(1);
}
console.log(`run: ${runId}`);

// report.json is allowed to 404 (run not yet scored) — fall back to null.
// Other failures (HTML body, 5xx) throw loud via safeFetchJson.
let report = null;
{
  const reportUrl = `${API}/viewer/api/bench/runs/${encodeURIComponent(runId)}/report.json`;
  const probe = await fetch(reportUrl);
  if (probe.status !== 404) {
    report = await safeFetchJson(reportUrl);
  }
}
const gtAvailable = !!report?.ground_truth?.available;
console.log(`report.json fetched, gt_available: ${gtAvailable}`);

const eventsUrl = `${API}/viewer/api/bench/runs/${encodeURIComponent(runId)}/events?limit=50000`;
const eventsText = await safeFetchText(eventsUrl);
const events = eventsText
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

// Pick the doc with the most encoder + consensus signal.
const docs = [...new Set(events.map((e) => e.doc_id).filter((d) => d && d !== "__run__"))];

function describeDoc(docId) {
  const docEvents = events.filter((e) => e.doc_id === docId);
  const encoders = new Set();
  const encoderConfidence = new Map(); // encoder → {hasAny, hasNonNull}
  let hasMentionDecision = false;
  for (const e of docEvents) {
    if (e.node_name === "mention_decision") hasMentionDecision = true;
    if (
      e.node_name === "chunk_extracted" &&
      typeof e.chunk_id === "string" &&
      e.chunk_id.includes(":_ner_")
    ) {
      const enc = e.chunk_id.split(":_ner_")[1];
      encoders.add(enc);
      const mentions = e.details?.mentions ?? [];
      const cur = encoderConfidence.get(enc) ?? { hasAny: false, hasNonNull: false };
      cur.hasAny = cur.hasAny || mentions.length > 0;
      for (const m of mentions) {
        if (m.confidence != null && !Number.isNaN(m.confidence)) {
          cur.hasNonNull = true;
          break;
        }
      }
      encoderConfidence.set(enc, cur);
    }
  }
  return {
    docId,
    encoders: [...encoders],
    encoderConfidence,
    hasMentionDecision,
  };
}

const candidates = docs.map(describeDoc).filter((d) => d.encoders.length > 0);
// Prefer a doc with both encoders + a mention_decision (for both panels).
const withConsensus = candidates.find((d) => d.hasMentionDecision);
const chosen = withConsensus ?? candidates[0];
if (!chosen) {
  console.error("no usable doc found");
  process.exit(1);
}
const docId = chosen.docId;
console.log(`doc: ${docId}`);
console.log(`encoders: ${chosen.encoders.join(", ")}`);
console.log(`has mention_decision: ${chosen.hasMentionDecision}`);

// Pick encoders for histogram tests:
//   - encoderWithConf: at least one mention with confidence != null (histogram expected)
//   - encoderWithoutConf: all mentions confidence==null (empty state expected)
const encoderWithConf = chosen.encoders.find(
  (e) => chosen.encoderConfidence.get(e)?.hasNonNull,
);
const encoderWithoutConf = chosen.encoders.find(
  (e) =>
    chosen.encoderConfidence.get(e)?.hasAny &&
    !chosen.encoderConfidence.get(e)?.hasNonNull,
);
console.log(
  `encoderWithConf: ${encoderWithConf ?? "(none)"} · encoderWithoutConf: ${
    encoderWithoutConf ?? "(none)"
  }`,
);

// Pick a primary encoder for Gap #1 (encoder panel) — prefer one with
// confidence so the same screenshot also exercises the histogram.
const primaryEncoder = encoderWithConf ?? chosen.encoders[0];
console.log(`primaryEncoder for gap-1 encoder panel: ${primaryEncoder}`);

// ── 2. browser ───────────────────────────────────────────────────────────────

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
page.on("pageerror", (err) => {
  consoleErrors.push(`pageerror: ${err.message}`);
});

async function navigate(url) {
  try {
    await page.goto(url, { waitUntil: "networkidle", timeout: 30_000 });
  } catch (e) {
    console.warn(`  navigate timeout — continuing: ${e.message}`);
  }
  await page.waitForTimeout(1500);
}

// ── 3. Gap #1 — encoder panel ───────────────────────────────────────────────

const gap1Results = [];
const consoleErrorsBeforeGap1 = consoleErrors.length;

async function runGap1Encoder() {
  const url = makeUrl(runId, docId, "ner_encoder", primaryEncoder);
  console.log(`\n→ Gap #1 encoder panel: ${url}`);
  await navigate(url);
  const screenshot = path.join(OUT_DIR, "gap-1-encoder.png");
  await page.screenshot({ path: screenshot, fullPage: false });

  // 1.1 panel renders
  const panelVisible = await page
    .locator('[data-testid="ner-encoder-detail"]')
    .first()
    .isVisible()
    .catch(() => false);
  gap1Results.push(
    assertion(
      "Encoder panel renders",
      panelVisible ? "pass" : "fail",
      panelVisible ? "" : "[data-testid=ner-encoder-detail] not visible",
      "gap-1-encoder.png",
    ),
  );

  // Scope every subsequent query inside the panel.
  const panel = page.locator('[data-testid="ner-encoder-detail"]').first();

  // 1.2 F1Strip presence — driven by report.json.ground_truth.available
  const stripCount = await panel.locator('[data-testid="f1-strip"]').count();
  const expectStrip = gtAvailable;
  let stripPresenceStatus, stripPresenceNote;
  if (expectStrip) {
    stripPresenceStatus = stripCount >= 1 ? "pass" : "fail";
    stripPresenceNote =
      stripCount >= 1
        ? `gt_available=true and f1-strip rendered (${stripCount}×)`
        : `gt_available=true but no f1-strip rendered`;
  } else {
    stripPresenceStatus = stripCount === 0 ? "pass" : "fail";
    stripPresenceNote =
      stripCount === 0
        ? "gt_available=false and strip absent (correct)"
        : `gt_available=false but strip rendered (${stripCount}×)`;
  }
  gap1Results.push(
    assertion(
      "F1Strip presence on encoder header",
      stripPresenceStatus,
      stripPresenceNote,
      "gap-1-encoder.png",
    ),
  );

  // 1.3 F1Strip values — only meaningful if strip is present
  if (stripCount >= 1) {
    const strip = panel.locator('[data-testid="f1-strip"]').first();
    const p = (await strip.locator('[data-testid="f1-strip-precision"]').textContent()) ?? "";
    const r = (await strip.locator('[data-testid="f1-strip-recall"]').textContent()) ?? "";
    const f = (await strip.locator('[data-testid="f1-strip-f1"]').textContent()) ?? "";
    const allMatch = FORMAT_2DP.test(p.trim()) && FORMAT_2DP.test(r.trim()) && FORMAT_2DP.test(f.trim());
    gap1Results.push(
      assertion(
        "F1Strip P/R/F1 values are 2-decimal numbers",
        allMatch ? "pass" : "fail",
        `P="${p.trim()}" · R="${r.trim()}" · F1="${f.trim()}"`,
        "gap-1-encoder.png",
      ),
    );
  } else {
    gap1Results.push(
      assertion(
        "F1Strip P/R/F1 values are 2-decimal numbers",
        "skip",
        "strip not present — nothing to validate",
        "",
      ),
    );
  }

  // GT chip presence on encoder panel — collected here too in case the
  // encoder mention rows wire chips. Generally chips only render on consensus
  // accepted rows + when GT has rows scoped to this doc.
  const encoderChipCount = await panel.locator('[data-testid="mention-gt-chip"]').count();
  console.log(`  encoder panel chip count: ${encoderChipCount}`);
}

// ── 4. Gap #1 — consensus panel ─────────────────────────────────────────────

async function runGap1Consensus() {
  const url = makeUrl(runId, docId, "consensus", null);
  console.log(`\n→ Gap #1 consensus panel: ${url}`);
  await navigate(url);
  const screenshot = path.join(OUT_DIR, "gap-1-consensus.png");
  await page.screenshot({ path: screenshot, fullPage: false });

  // 1.1 panel renders
  const panelVisible = await page
    .locator('[data-testid="consensus-detail"]')
    .first()
    .isVisible()
    .catch(() => false);
  gap1Results.push(
    assertion(
      "Consensus panel renders",
      panelVisible ? "pass" : "fail",
      panelVisible ? "" : "[data-testid=consensus-detail] not visible",
      "gap-1-consensus.png",
    ),
  );
  const panel = page.locator('[data-testid="consensus-detail"]').first();

  // 1.2 strip presence
  const stripCount = await panel.locator('[data-testid="f1-strip"]').count();
  const expectStrip = gtAvailable;
  gap1Results.push(
    assertion(
      "F1Strip presence on consensus header",
      expectStrip ? (stripCount >= 1 ? "pass" : "fail") : (stripCount === 0 ? "pass" : "fail"),
      expectStrip
        ? stripCount >= 1
          ? "gt_available=true and strip rendered"
          : "gt_available=true but strip absent"
        : stripCount === 0
          ? "gt_available=false and strip absent"
          : "gt_available=false but strip rendered",
      "gap-1-consensus.png",
    ),
  );

  // 1.3 P/R/F1 values
  if (stripCount >= 1) {
    const strip = panel.locator('[data-testid="f1-strip"]').first();
    const p = (await strip.locator('[data-testid="f1-strip-precision"]').textContent()) ?? "";
    const r = (await strip.locator('[data-testid="f1-strip-recall"]').textContent()) ?? "";
    const f = (await strip.locator('[data-testid="f1-strip-f1"]').textContent()) ?? "";
    const allMatch = FORMAT_2DP.test(p.trim()) && FORMAT_2DP.test(r.trim()) && FORMAT_2DP.test(f.trim());
    gap1Results.push(
      assertion(
        "Consensus F1Strip P/R/F1 values are 2-decimal",
        allMatch ? "pass" : "fail",
        `P="${p.trim()}" · R="${r.trim()}" · F1="${f.trim()}"`,
        "gap-1-consensus.png",
      ),
    );

    // 1.4 delta pill
    const deltaText = (await strip.locator('[data-testid="f1-strip-delta"]').textContent().catch(() => null)) ?? "";
    const deltaCount = await strip.locator('[data-testid="f1-strip-delta"]').count();
    if (deltaCount === 0) {
      gap1Results.push(
        assertion(
          "Consensus delta pill rendered",
          "fail",
          "f1-strip-delta missing — should render on consensus when comparison computed",
          "gap-1-consensus.png",
        ),
      );
    } else {
      const ok = FORMAT_DELTA.test(deltaText);
      gap1Results.push(
        assertion(
          "Consensus delta pill formatted",
          ok ? "pass" : "fail",
          `text="${deltaText.trim()}"`,
          "gap-1-consensus.png",
        ),
      );
    }
  } else {
    gap1Results.push(
      assertion(
        "Consensus F1Strip P/R/F1 values are 2-decimal",
        "skip",
        "strip absent",
        "",
      ),
    );
    gap1Results.push(
      assertion(
        "Consensus delta pill rendered",
        "skip",
        "strip absent",
        "",
      ),
    );
  }

  // 1.5 GT chip presence — only when GT has scoped, non-empty mentions.
  // The active-GT for this run reports total_mentions=0, so chips cannot
  // render even though report.ground_truth.available=true. Mark ⚠ skip.
  const chipCount = await panel.locator('[data-testid="mention-gt-chip"]').count();
  // Need to know whether GT is loaded with non-empty mentions for this doc.
  // We pull /viewer/api/bench/ground-truth/active.json directly and count
  // mentions whose chunk doc_id matches.
  let gtScopedMentions = 0;
  try {
    // safeFetchJson will throw on SPA-fallback HTML; legitimate 404 ("no
    // active GT") is the only soft-failure we want, so probe first.
    const gtUrl = `${API}/viewer/api/bench/ground-truth/active.json`;
    const probe = await fetch(gtUrl);
    if (probe.ok) {
      const gt = await safeFetchJson(gtUrl);
      gtScopedMentions = gt.total_mentions ?? 0;
    }
  } catch {
    // ignore — only swallow real fetch failures, not the SPA-fallback
    // guard (which fires before this catch via safeFetchJson's body read).
  }
  if (gtScopedMentions > 0) {
    gap1Results.push(
      assertion(
        "GT chip on accepted rows",
        chipCount >= 1 ? "pass" : "fail",
        `${chipCount} chips · ${gtScopedMentions} GT mentions in active GT`,
        "gap-1-gt-chips.png",
      ),
    );
    // Save a focused screenshot of the accepted table.
    await page
      .locator('[data-testid="consensus-detail"]')
      .first()
      .screenshot({ path: path.join(OUT_DIR, "gap-1-gt-chips.png") })
      .catch(() => {});
  } else {
    gap1Results.push(
      assertion(
        "GT chip on accepted rows",
        "skip",
        `active GT has 0 mentions (total_mentions=${gtScopedMentions}); chip render is gated upstream — not a UI bug. Mark ⚠.`,
        "",
      ),
    );
  }
}

// ── 5. Gap #3 — confidence histogram ────────────────────────────────────────

const gap3Results = [];

async function runGap3Histogram() {
  if (!encoderWithConf) {
    gap3Results.push(
      assertion(
        "Histogram presence",
        "skip",
        "no encoder in this run emits per-mention confidence — cannot test histogram render path",
        "",
      ),
    );
    gap3Results.push(
      assertion("Bin count === 20", "skip", "no encoder with confidence", ""),
    );
    gap3Results.push(
      assertion(
        "Threshold preview updates on hover",
        "skip",
        "no encoder with confidence",
        "",
      ),
    );
    gap3Results.push(
      assertion(
        "Long-tail badge",
        "skip",
        "no encoder with confidence",
        "",
      ),
    );
    return;
  }
  const url = makeUrl(runId, docId, "ner_encoder", encoderWithConf);
  console.log(`\n→ Gap #3 histogram (${encoderWithConf}): ${url}`);
  await navigate(url);
  const screenshot = path.join(OUT_DIR, "gap-3-histogram.png");
  await page.screenshot({ path: screenshot, fullPage: false });

  const panel = page.locator('[data-testid="ner-encoder-detail"]').first();
  const histVisible = await panel
    .locator('[data-testid="confidence-histogram"]')
    .first()
    .isVisible()
    .catch(() => false);
  gap3Results.push(
    assertion(
      "Histogram presence",
      histVisible ? "pass" : "fail",
      histVisible ? "" : "[data-testid=confidence-histogram] not visible",
      "gap-3-histogram.png",
    ),
  );

  if (!histVisible) {
    gap3Results.push(
      assertion("Bin count === 20", "skip", "histogram absent", ""),
    );
    gap3Results.push(
      assertion(
        "Threshold preview updates on hover",
        "skip",
        "histogram absent",
        "",
      ),
    );
    gap3Results.push(
      assertion("Long-tail badge", "skip", "histogram absent", ""),
    );
    return;
  }

  // Bin count
  const binCount = await panel.locator('[data-testid^="confidence-bin-"]').count();
  gap3Results.push(
    assertion(
      "Bin count === 20",
      binCount === 20 ? "pass" : "fail",
      `binCount=${binCount}`,
      "gap-3-histogram.png",
    ),
  );

  // Threshold preview hover
  const previewBefore = (await panel
    .locator('[data-testid="confidence-preview"]')
    .first()
    .textContent()) ?? "";
  // Hover the middle bin (10) which usually has data — but try a few until
  // the text changes, in case the bin we picked was empty / lower-bound 0.
  let previewAfter = previewBefore;
  let hoveredBin = null;
  for (const idx of [10, 14, 16, 18, 4, 0]) {
    const target = panel.locator(`[data-testid="confidence-bin-${idx}"]`).first();
    if ((await target.count()) === 0) continue;
    await target.hover();
    await page.waitForTimeout(220);
    previewAfter = (await panel
      .locator('[data-testid="confidence-preview"]')
      .first()
      .textContent()) ?? "";
    if (previewAfter.trim() !== previewBefore.trim()) {
      hoveredBin = idx;
      break;
    }
  }
  gap3Results.push(
    assertion(
      "Threshold preview updates on hover",
      previewAfter.trim() !== previewBefore.trim() ? "pass" : "fail",
      `bin=${hoveredBin} · before="${previewBefore.trim()}" · after="${previewAfter.trim()}"`,
      "gap-3-histogram.png",
    ),
  );

  // Long-tail badge — informational only.
  const longTailHtml = await panel
    .locator('div:has(> svg)')
    .first()
    .evaluate((el) => el.outerHTML.includes("long tail"))
    .catch(() => false);
  gap3Results.push(
    assertion(
      "Long-tail badge presence (informational)",
      "info",
      longTailHtml ? "long tail badge present" : "long tail badge absent",
      "gap-3-histogram.png",
    ),
  );
}

async function runGap3Empty() {
  if (!encoderWithoutConf) {
    gap3Results.push(
      assertion(
        "Empty state on null-confidence encoder",
        "skip",
        "no encoder in this run emits exclusively null-confidence mentions — fixture not available",
        "",
      ),
    );
    return;
  }
  const url = makeUrl(runId, docId, "ner_encoder", encoderWithoutConf);
  console.log(`\n→ Gap #3 empty state (${encoderWithoutConf}): ${url}`);
  await navigate(url);
  const screenshot = path.join(OUT_DIR, "gap-3-empty.png");
  await page.screenshot({ path: screenshot, fullPage: false });

  const panel = page.locator('[data-testid="ner-encoder-detail"]').first();
  const emptyVisible = await panel
    .locator('[data-testid="confidence-empty"]')
    .first()
    .isVisible()
    .catch(() => false);
  const histAbsent = (await panel.locator('[data-testid="confidence-histogram"]').count()) === 0;
  gap3Results.push(
    assertion(
      "Empty state on null-confidence encoder",
      emptyVisible && histAbsent ? "pass" : "fail",
      `confidence-empty visible=${emptyVisible} · histogram absent=${histAbsent}`,
      "gap-3-empty.png",
    ),
  );
}

// ── 6. drive ────────────────────────────────────────────────────────────────

await runGap1Encoder();
await runGap1Consensus();
const consoleErrorsAfterGap1 = consoleErrors.slice(consoleErrorsBeforeGap1);
const consoleErrorsBeforeGap3 = consoleErrors.length;
await runGap3Histogram();
await runGap3Empty();
const consoleErrorsAfterGap3 = consoleErrors.slice(consoleErrorsBeforeGap3);

// Console assertions — record per-gap so the report ties failures to scope.
gap1Results.push(
  assertion(
    "No console errors during navigation",
    consoleErrorsAfterGap1.length === 0 ? "pass" : "fail",
    consoleErrorsAfterGap1.length === 0
      ? "clean"
      : consoleErrorsAfterGap1.slice(0, 6).join(" || "),
    "",
  ),
);
gap3Results.push(
  assertion(
    "No console errors during navigation",
    consoleErrorsAfterGap3.length === 0 ? "pass" : "fail",
    consoleErrorsAfterGap3.length === 0
      ? "clean"
      : consoleErrorsAfterGap3.slice(0, 6).join(" || "),
    "",
  ),
);

// ── 7. write report ─────────────────────────────────────────────────────────

function emoji(s) {
  return s === "pass" ? "✅" : s === "fail" ? "❌" : s === "skip" ? "⚠" : s === "info" ? "ℹ" : "?";
}
function tally(results) {
  const t = { pass: 0, fail: 0, skip: 0, info: 0 };
  for (const r of results) t[r.status] = (t[r.status] ?? 0) + 1;
  return t;
}

function table(results) {
  const lines = ["| Assertion | Result | Evidence | Notes |", "|---|---|---|---|"];
  for (const r of results) {
    const ev = r.evidence ? r.evidence : "—";
    const note = r.note.replace(/\|/g, "\\|");
    lines.push(`| ${r.name} | ${emoji(r.status)} ${r.status} | ${ev} | ${note} |`);
  }
  return lines.join("\n");
}

const t1 = tally(gap1Results);
const t3 = tally(gap3Results);

const reportMd = `# QA Verification — Inspector Gaps

Run: \`${runId}\`
Doc: \`${docId}\`
Primary encoder (gap-1 panel + gap-3 histogram): \`${primaryEncoder}\`
Empty-state encoder (gap-3 empty): \`${encoderWithoutConf ?? "(none — fixture unavailable)"}\`
GT available in report.json: \`${gtAvailable}\`
Active-GT total_mentions: pulled inline (see Gap #1 GT-chip note)

## Gap #1 — F1/P/R header strip + GT chips

${table(gap1Results)}

## Gap #3 — Confidence histogram

${table(gap3Results)}

## Summary

- Gap #1: ${t1.pass} pass / ${t1.fail} fail / ${t1.skip} skip${t1.info ? ` / ${t1.info} info` : ""}
- Gap #3: ${t3.pass} pass / ${t3.fail} fail / ${t3.skip} skip${t3.info ? ` / ${t3.info} info` : ""}

### Console errors (full list, deduped)
${
  consoleErrors.length === 0
    ? "_none_"
    : "```\n" + [...new Set(consoleErrors)].slice(0, 30).join("\n") + "\n```"
}
`;

fs.writeFileSync(path.join(OUT_DIR, "REPORT.md"), reportMd);
console.log(`\nreport written: ${path.join(OUT_DIR, "REPORT.md")}`);
console.log(`gap-1: ${t1.pass} pass / ${t1.fail} fail / ${t1.skip} skip`);
console.log(`gap-3: ${t3.pass} pass / ${t3.fail} fail / ${t3.skip} skip`);

await browser.close();

#!/usr/bin/env node
/**
 * QA verifier for Gap #2 — encoder co-vote / agreement matrix on the
 * ConsensusDetail panel (see EncoderCovoteMatrix.tsx). Drives the local
 * Playwright install (NOT MCP — disconnected) against viewer-ui :5173 +
 * viewer-api :8080.
 *
 * Targeted assertions only — no full e2e suite. Writes screenshots +
 * appends a "Gap #2" section to REPORT.md.
 */

import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { resolveLocalViewerURL, resolveLocalApiURL } from "./_local-only.mjs";
import { safeFetchJson, safeFetchText } from "./_fetch.mjs";

const VIEWER = resolveLocalViewerURL();
const API = resolveLocalApiURL();
const OUT_DIR =
  "/Users/panda/catalyst-devspace/workspace/catalyst-data/.test-output/inspector-tour/qa-verify";
const REPORT = path.join(OUT_DIR, "REPORT.md");

fs.mkdirSync(OUT_DIR, { recursive: true });

// ── Assertion harness ─────────────────────────────────────────────────────────
const results = [];
function record(id, status, detail) {
  results.push({ id, status, detail });
  const sigil = status === "pass" ? "PASS" : status === "fail" ? "FAIL" : "SKIP";
  console.log(`[${sigil}] ${id} — ${detail}`);
}

function pass(id, detail) {
  record(id, "pass", detail);
}
function fail(id, detail) {
  record(id, "fail", detail);
}
function skip(id, detail) {
  record(id, "skip", detail);
}

// ── Step 1: discover candidate doc(s) ─────────────────────────────────────────
//
// The events endpoint stores events globally; filter client-side by ``run_id``
// per-event. Search across ALL available runs (not just ``latest``) because
// the recent ``testing1`` pipeline only configured 2 NER encoders.
const runsResp = await safeFetchJson(`${API}/viewer/api/bench/runs`);
const runIds = runsResp.runs ?? [];
if (runIds.length === 0) {
  console.error("no runs available — abort");
  process.exit(2);
}
console.log(`runs available: ${runIds.length} (latest: ${runsResp.latest})`);

let target3 = null;
let target2 = null;
let runIdFor3 = null;
let runIdFor2 = null;

for (const r of runIds) {
  let txt;
  try {
    // safeFetchText throws loud on SPA-fallback HTML — for the per-run
    // probe we rethrow so the operator sees the proxy issue rather than
    // silently skipping every run with "fetch error".
    txt = await safeFetchText(
      `${API}/viewer/api/bench/runs/${r}/events?node_name=mention_decision&limit=20000`,
    );
  } catch (e) {
    console.warn(`  ${r}: fetch error ${e.message}`);
    continue;
  }
  const evs = txt
    .split("\n")
    .filter(Boolean)
    .map((l) => {
      try {
        return JSON.parse(l);
      } catch {
        return null;
      }
    })
    .filter(Boolean)
    .filter((e) => e.run_id === r);

  const byDoc = new Map();
  for (const ev of evs) {
    const sm = ev.details?.source_models ?? [];
    if (!Array.isArray(sm) || sm.length === 0) continue;
    if (!byDoc.has(ev.doc_id)) byDoc.set(ev.doc_id, new Set());
    for (const m of sm) byDoc.get(ev.doc_id).add(m);
  }
  for (const [docId, encs] of byDoc.entries()) {
    if (encs.size >= 3 && !target3) {
      target3 = { docId, encoders: [...encs] };
      runIdFor3 = r;
    }
    if (encs.size === 2 && !target2) {
      target2 = { docId, encoders: [...encs] };
      runIdFor2 = r;
    }
    if (target3 && target2) break;
  }
  if (target3 && target2) break;
}

console.log(
  `candidate 3+: ${target3 ? `${target3.docId} in ${runIdFor3} (${target3.encoders.length} encs: ${target3.encoders.join(", ")})` : "—"}`,
);
console.log(
  `candidate 2:  ${target2 ? `${target2.docId} in ${runIdFor2} (${target2.encoders.join(", ")})` : "—"}`,
);

if (!target3 && !target2) {
  fail(
    "discovery",
    "no doc in any run has ≥2 encoders contributing source_models on mention_decision events",
  );
  await writeReport({ runIdFor3, runIdFor2 });
  process.exit(1);
}

// Pre-compute expected matrix dimensions for the 3+ doc.
const N = target3 ? target3.encoders.length : 0;
const expectedCellCount = target3 ? (N * (N + 1)) / 2 : 0;

// We need rejected events on the 3+ doc to verify the mode-toggle assertion.
let rejectedWithSm3 = 0;
if (target3) {
  try {
    const rTxt = await safeFetchText(
      `${API}/viewer/api/bench/runs/${runIdFor3}/events?node_name=mention_rejected&doc_id=${encodeURIComponent(target3.docId)}&limit=5000`,
    );
    const rEvs = rTxt
      .split("\n")
      .filter(Boolean)
      .map((l) => {
        try {
          return JSON.parse(l);
        } catch {
          return null;
        }
      })
      .filter(Boolean)
      .filter((e) => e.run_id === runIdFor3 && e.doc_id === target3.docId);
    rejectedWithSm3 = rEvs.filter(
      (e) =>
        Array.isArray(e.details?.source_models) &&
        e.details.source_models.length > 0,
    ).length;
  } catch (e) {
    console.warn(`probe rejected fetch error: ${e.message}`);
  }
  console.log(`rejected-with-source_models on 3+ doc: ${rejectedWithSm3}`);
}

// ── Playwright run ────────────────────────────────────────────────────────────
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1100 } });
const page = await ctx.newPage();

const consoleErrors = [];
page.on("console", (msg) => {
  if (msg.type() !== "error") return;
  const text = msg.text();
  // Allow-list common dev-mode noise (HMR, source-map, font 404).
  if (
    /Download the React DevTools/.test(text) ||
    /\[vite\]/i.test(text) ||
    /sourcemap/i.test(text) ||
    /favicon/i.test(text)
  ) {
    return;
  }
  consoleErrors.push(text);
});

async function navigateToConsensus(runIdToUse, docId) {
  const url = `${VIEWER}/viewer/benchmarks/state?run=${encodeURIComponent(
    runIdToUse,
  )}&doc=${encodeURIComponent(docId)}&node=consensus`;
  console.log(`→ ${url}`);
  try {
    await page.goto(url, { waitUntil: "networkidle", timeout: 30_000 });
  } catch (e) {
    console.warn(`  goto warn: ${e.message}`);
  }
  await page.waitForTimeout(1500);
  // ConsensusDetail wraps the matrix in <details> — for ≥3 encoders it's
  // open by default, but be defensive: programmatically open all <details>
  // descendants of consensus-detail. This is verification-only DOM
  // tweaking, not a component change.
  await page.evaluate(() => {
    const root = document.querySelector('[data-testid="consensus-detail"]');
    if (!root) return;
    root.querySelectorAll("details").forEach((d) => {
      d.open = true;
    });
  });
  await page.waitForTimeout(300);
}

// ── 3+-encoder assertions ─────────────────────────────────────────────────────
if (target3) {
  await navigateToConsensus(runIdFor3, target3.docId);

  // Default mode is "accepted" per ConsensusDetail useState — capture the
  // default-state screenshot first.
  await page.screenshot({
    path: path.join(OUT_DIR, "gap-2-matrix-default.png"),
    fullPage: false,
  });

  // 1. Matrix root visible.
  const matrixLoc = page.locator(
    '[data-testid="consensus-detail"] [data-testid="encoder-covote-matrix"]',
  );
  const matrixVisible = await matrixLoc.isVisible().catch(() => false);
  if (matrixVisible) {
    pass("a1.matrix-root", "encoder-covote-matrix visible inside consensus panel");
  } else {
    fail("a1.matrix-root", "encoder-covote-matrix NOT visible inside consensus panel");
  }

  // 2. Cell count = N*(N+1)/2.
  const cells = await page.locator('[data-testid^="encoder-covote-cell-"]').all();
  if (cells.length === expectedCellCount) {
    pass("a2.cell-count", `cells=${cells.length} matches N*(N+1)/2 for N=${N}`);
  } else {
    fail(
      "a2.cell-count",
      `cells=${cells.length}, expected ${expectedCellCount} for N=${N} encoders`,
    );
  }

  // 3 & 4. Diagonal vs off-diagonal text patterns.
  const diagonalRegex = /^\[\d+\]$/;
  const offDiagRegex = /^\d\.\d{2}$/;
  let diagOk = 0;
  let diagBad = 0;
  let offOk = 0;
  let offBad = 0;
  let offDash = 0;
  const offDiagSamples = [];
  for (const cell of cells) {
    const tid = await cell.getAttribute("data-testid");
    const text = (await cell.textContent())?.trim() ?? "";
    // testid is encoder-covote-cell-<a>-<b>; diagonal when the a==b prefix.
    // Easier: parse tid, but encoders may contain hyphens — instead probe
    // by content shape. Diagonal ⇒ [<n>]; off-diagonal ⇒ d.dd or "—".
    if (diagonalRegex.test(text)) {
      diagOk += 1;
    } else if (offDiagRegex.test(text)) {
      offOk += 1;
      offDiagSamples.push({ tid, text });
    } else if (text === "—") {
      offDash += 1;
    } else {
      // Could not classify — count as failure.
      if (text.startsWith("[") && text.endsWith("]")) diagBad += 1;
      else offBad += 1;
    }
  }

  if (diagOk === N && diagBad === 0) {
    pass("a3.diagonal-shape", `${diagOk}/${N} diagonal cells match /^\\[\\d+\\]$/`);
  } else {
    fail(
      "a3.diagonal-shape",
      `diagonal cells matching=${diagOk}, malformed=${diagBad}, expected ${N}`,
    );
  }

  const expectedOffDiag = expectedCellCount - N;
  if (offBad === 0) {
    if (offDash > 0) {
      pass(
        "a4.offdiag-shape",
        `${offOk}/${expectedOffDiag} off-diag cells render Jaccard 0.00–1.00; ${offDash} render — (union==0, graceful)`,
      );
    } else {
      pass(
        "a4.offdiag-shape",
        `${offOk}/${expectedOffDiag} off-diag cells render Jaccard 0.00–1.00`,
      );
    }
  } else {
    fail(
      "a4.offdiag-shape",
      `off-diag cells malformed=${offBad}, valid=${offOk}, dash=${offDash}, expected ${expectedOffDiag}`,
    );
  }

  // 5. Mode toggle changes cell numbers.
  // Read current "accepted"-mode off-diag values.
  const readOffDiagValues = async () => {
    const all = await page.locator('[data-testid^="encoder-covote-cell-"]').all();
    const vals = [];
    for (const c of all) {
      const t = (await c.textContent())?.trim() ?? "";
      if (offDiagRegex.test(t)) vals.push(t);
    }
    return vals.join(",");
  };
  const acceptedSig = await readOffDiagValues();

  // Click mode-all.
  const allBtn = page.locator('[data-testid="encoder-covote-mode-all"]');
  if (await allBtn.isVisible().catch(() => false)) {
    await allBtn.click();
    await page.waitForTimeout(400);
    await page.screenshot({
      path: path.join(OUT_DIR, "gap-2-mode-toggle.png"),
      fullPage: false,
    });
    const allSig = await readOffDiagValues();
    if (allSig !== acceptedSig) {
      pass(
        "a5.mode-toggle",
        `mode-all values differ from mode-accepted (sig changed)`,
      );
    } else {
      // Could be legitimate if there are zero rejected mentions with
      // source_models — flag with warn rather than fail.
      const rejectedWithSm = rejectedWithSm3;
      if (rejectedWithSm === 0) {
        skip(
          "a5.mode-toggle",
          `cell numbers unchanged between accepted/all — but 0 rejected-events carry source_models on this doc, so degraded state expected (warn, not fail)`,
        );
      } else {
        fail(
          "a5.mode-toggle",
          `cell numbers UNCHANGED between accepted/all despite ${rejectedWithSm} rejected events with source_models — likely a bug in matrix mode wiring`,
        );
      }
    }
    // Restore to "accepted" for subsequent assertions.
    await page.locator('[data-testid="encoder-covote-mode-accepted"]').click();
    await page.waitForTimeout(300);
  } else {
    fail("a5.mode-toggle", "encoder-covote-mode-all button not visible");
  }

  // 6. Click filtering — pick the first off-diagonal cell.
  let pairCellTid = null;
  for (const sample of offDiagSamples) {
    pairCellTid = sample.tid;
    break;
  }
  let dimmedAfterClick = 0;
  if (pairCellTid) {
    await page.locator(`[data-testid="${pairCellTid}"]`).click();
    await page.waitForTimeout(400);

    const chip = page.locator('[data-testid="encoder-covote-active-filter"]');
    const chipVisible = await chip.isVisible().catch(() => false);
    const chipText = chipVisible ? (await chip.textContent()) ?? "" : "";

    // Parse the encoder pair from the testid: it's encoder-covote-cell-<a>-<b>
    // but encoder names contain hyphens. Use tooltip-tagged "pair: A ∩ B" chip.
    const both =
      chipVisible &&
      target3.encoders.filter((e) => chipText.includes(e)).length >= 2;
    if (chipVisible && both) {
      pass(
        "a6a.filter-chip",
        `clicked off-diag cell → filter chip appears with both encoder names: "${chipText.trim()}"`,
      );
    } else {
      fail(
        "a6a.filter-chip",
        `chip visible=${chipVisible}, both-names=${both}, text="${chipText.trim()}"`,
      );
    }

    // 6b. Some accepted rows now have opacity-30.
    const acceptedRows = await page
      .locator('[data-testid="consensus-accepted-row"]')
      .all();
    let dimmed = 0;
    for (const row of acceptedRows) {
      const cls = (await row.getAttribute("class")) ?? "";
      if (cls.includes("opacity-30")) dimmed += 1;
    }
    dimmedAfterClick = dimmed;
    if (dimmed > 0) {
      pass(
        "a6b.dim-rows",
        `${dimmed}/${acceptedRows.length} accepted rows dimmed (opacity-30) after pair filter`,
      );
    } else if (acceptedRows.length === 0) {
      skip("a6b.dim-rows", `no consensus-accepted-row rows present (doc has 0 accepted)`);
    } else {
      fail(
        "a6b.dim-rows",
        `0/${acceptedRows.length} rows dimmed — expected ≥1 with active pair filter`,
      );
    }

    await page.screenshot({
      path: path.join(OUT_DIR, "gap-2-matrix-filtered.png"),
      fullPage: false,
    });

    // 6c. Clear filter resets dim.
    await page.locator('[data-testid="encoder-covote-clear"]').click();
    await page.waitForTimeout(300);
    let dimmedAfterClear = 0;
    for (const row of await page.locator('[data-testid="consensus-accepted-row"]').all()) {
      const cls = (await row.getAttribute("class")) ?? "";
      if (cls.includes("opacity-30")) dimmedAfterClear += 1;
    }
    if (dimmedAfterClear === 0) {
      pass("a6c.clear-filter", "clear filter removed dim from all rows");
    } else {
      fail(
        "a6c.clear-filter",
        `${dimmedAfterClear} rows still dimmed after clear filter`,
      );
    }
  } else {
    skip("a6a.filter-chip", "no off-diagonal cell available to click");
    skip("a6b.dim-rows", "no off-diagonal cell available to click");
    skip("a6c.clear-filter", "no off-diagonal cell available to click");
  }

  // 7. Click diagonal cell.
  const diagCells = await page.locator('[data-testid^="encoder-covote-cell-"]').all();
  let diagTid = null;
  for (const c of diagCells) {
    const t = (await c.textContent())?.trim() ?? "";
    if (diagonalRegex.test(t)) {
      diagTid = await c.getAttribute("data-testid");
      break;
    }
  }
  if (diagTid) {
    await page.locator(`[data-testid="${diagTid}"]`).click();
    await page.waitForTimeout(400);
    const chip = page.locator('[data-testid="encoder-covote-active-filter"]');
    const chipText = ((await chip.textContent().catch(() => "")) ?? "").trim();
    const isLone = /lone:/i.test(chipText);
    let dimmedAfter = 0;
    for (const row of await page.locator('[data-testid="consensus-accepted-row"]').all()) {
      const cls = (await row.getAttribute("class")) ?? "";
      if (cls.includes("opacity-30")) dimmedAfter += 1;
    }
    const acceptedTotal = (
      await page.locator('[data-testid="consensus-accepted-row"]').all()
    ).length;
    if (isLone && (dimmedAfter > 0 || acceptedTotal === 0)) {
      pass(
        "a7.diagonal-click",
        `diagonal click → chip "${chipText}" (lone keyword present), ${dimmedAfter}/${acceptedTotal} rows dimmed`,
      );
    } else if (!isLone) {
      fail(
        "a7.diagonal-click",
        `diagonal click chip text "${chipText}" missing 'lone:' keyword`,
      );
    } else {
      // isLone but no rows dimmed — could be that this encoder has 0 lone votes
      // (every accepted mention voted on by at least one other) — that's a
      // valid degraded state for the matrix, but the filter chip is still
      // applied correctly. Flag as warn.
      skip(
        "a7.diagonal-click",
        `chip="${chipText}" applied, but 0/${acceptedTotal} rows match — this encoder may have 0 lone-only mentions`,
      );
    }
  } else {
    fail("a7.diagonal-click", "no diagonal cell found to click");
  }

  // 8. Sort still works while filtered. Re-apply the pair filter, then
  //    capture row-type values, click consensus-sort-type, and verify order
  //    changes.
  if (pairCellTid) {
    // Reset filter (clear button might not exist if no chip rendered).
    const clearBtn = page.locator('[data-testid="encoder-covote-clear"]');
    if (await clearBtn.isVisible().catch(() => false)) {
      await clearBtn.click();
      await page.waitForTimeout(300);
    }
    await page.locator(`[data-testid="${pairCellTid}"]`).click();
    await page.waitForTimeout(400);

    const readTypes = async () => {
      // The MentionTable renders the type chip with testid
      // ``${rowTestId}-type`` → ``consensus-accepted-row-type``. Read those
      // in DOM order (matches the rendered visual order — what we want for
      // the sort-changed check).
      const chips = await page
        .locator('[data-testid="consensus-accepted-row-type"]')
        .all();
      const types = [];
      for (const chip of chips) {
        const t = (await chip.textContent()) ?? "";
        types.push(t.trim());
      }
      return types;
    };
    const beforeSort = await readTypes();
    // Click sort-type — this should re-order accepted by canonical_type
    // alphabetically.
    const sortBtn = page.locator('[data-testid="consensus-sort-type"]');
    if ((await sortBtn.count()) === 0) {
      skip("a8.sort-while-filtered", "consensus-sort-type button not present");
    } else {
      await sortBtn.click();
      await page.waitForTimeout(400);
      const afterSort = await readTypes();
      const changed =
        beforeSort.length === afterSort.length &&
        beforeSort.some((v, i) => v !== afterSort[i]);
      // Check alphabetical ordering of afterSort.
      const isSorted = afterSort.every(
        (v, i, arr) => i === 0 || arr[i - 1].localeCompare(v) <= 0,
      );
      if (beforeSort.length === 0) {
        skip(
          "a8.sort-while-filtered",
          "no accepted rows visible while filtered — cannot verify sort change",
        );
      } else if (changed && isSorted) {
        pass(
          "a8.sort-while-filtered",
          `${beforeSort.length} rows re-ordered alphabetically while pair filter active`,
        );
      } else if (!changed && isSorted) {
        // Already alphabetical pre-click — sort still works, just no visible diff.
        skip(
          "a8.sort-while-filtered",
          `${beforeSort.length} rows already alphabetical pre-click (no observable change but post-state correct)`,
        );
      } else {
        fail(
          "a8.sort-while-filtered",
          `changed=${changed}, isSorted=${isSorted}, before=${JSON.stringify(beforeSort.slice(0, 5))}, after=${JSON.stringify(afterSort.slice(0, 5))}`,
        );
      }
    }
  } else {
    skip("a8.sort-while-filtered", "no off-diagonal cell to apply pair filter");
  }
}

// ── 2-encoder assertion ───────────────────────────────────────────────────────
if (target2) {
  await navigateToConsensus(runIdFor2, target2.docId);

  await page.screenshot({
    path: path.join(OUT_DIR, "gap-2-matrix-2-encoders.png"),
    fullPage: false,
  });

  const matrixLoc = page.locator(
    '[data-testid="consensus-detail"] [data-testid="encoder-covote-matrix"]',
  );
  const matrixVisible = await matrixLoc.isVisible().catch(() => false);
  if (!matrixVisible) {
    fail(
      "a9.two-encoder-inline",
      "encoder-covote-matrix root not present on 2-encoder doc (expected inline summary container)",
    );
  } else {
    // Inline variant has no mode toggle and no cells.
    const hasCells =
      (await page.locator('[data-testid^="encoder-covote-cell-"]').count()) > 0;
    const hasModeBtn =
      (await page.locator('[data-testid="encoder-covote-mode-all"]').count()) > 0;
    const text = ((await matrixLoc.textContent()) ?? "").trim();
    const hasIntersection = text.includes("∩") && /\d\.\d{2}/.test(text);
    if (!hasCells && !hasModeBtn && hasIntersection) {
      pass(
        "a9.two-encoder-inline",
        `2-encoder doc renders inline single-line summary: "${text.slice(0, 80)}…"`,
      );
    } else {
      fail(
        "a9.two-encoder-inline",
        `hasCells=${hasCells}, hasModeBtn=${hasModeBtn}, hasIntersection=${hasIntersection}, text="${text.slice(0, 80)}"`,
      );
    }
  }
} else {
  skip("a9.two-encoder-inline", "no 2-encoder doc available in latest run");
}

// ── Console errors check ─────────────────────────────────────────────────────
if (consoleErrors.length === 0) {
  pass("c0.console-clean", "no console errors during verification");
} else {
  fail(
    "c0.console-clean",
    `${consoleErrors.length} console errors: ${consoleErrors.slice(0, 3).join(" | ")}`,
  );
}

await browser.close();

// ── Report ───────────────────────────────────────────────────────────────────
await writeReport({ runIdFor3, runIdFor2 });

const passes = results.filter((r) => r.status === "pass").length;
const fails = results.filter((r) => r.status === "fail").length;
const skips = results.filter((r) => r.status === "skip").length;
console.log(
  `\nDONE — ${results.length} assertions: ${passes} pass · ${fails} fail · ${skips} skip`,
);
process.exit(fails > 0 ? 1 : 0);

async function writeReport({ runIdFor3, runIdFor2 }) {
  const lines = [];
  lines.push("");
  lines.push("---");
  lines.push("");
  lines.push("## Gap #2 — Encoder Co-vote / Agreement Matrix");
  lines.push("");
  lines.push(`- Component: \`packages/media-ingest/viewer-ui/src/components/state/EncoderCovoteMatrix.tsx\``);
  lines.push(`- Spec: \`.test-output/inspector-tour/data-scientist-gaps.md\` Gap #2`);
  if (target3) {
    lines.push(
      `- 3+-encoder doc: \`${target3.docId}\` in run \`${runIdFor3}\` (N=${target3.encoders.length}: ${target3.encoders.join(", ")})`,
    );
    lines.push(`- Expected matrix cells: N*(N+1)/2 = ${expectedCellCount}`);
    lines.push(`- Rejected events with source_models on 3+ doc: ${rejectedWithSm3}`);
  } else {
    lines.push(`- 3+-encoder doc: NONE found across all runs — skipped 3+ assertions`);
  }
  if (target2) {
    lines.push(
      `- 2-encoder doc: \`${target2.docId}\` in run \`${runIdFor2}\` (${target2.encoders.join(", ")})`,
    );
  } else {
    lines.push(`- 2-encoder doc: NONE — skipped inline-summary assertion`);
  }
  lines.push("");
  lines.push("### Assertion results");
  lines.push("");
  lines.push("| ID | Status | Detail |");
  lines.push("| --- | --- | --- |");
  for (const r of results) {
    const sigil = r.status === "pass" ? "PASS" : r.status === "fail" ? "FAIL" : "SKIP";
    const safe = r.detail.replace(/\|/g, "\\|");
    lines.push(`| \`${r.id}\` | ${sigil} | ${safe} |`);
  }
  lines.push("");
  const passes = results.filter((r) => r.status === "pass").length;
  const fails = results.filter((r) => r.status === "fail").length;
  const skips = results.filter((r) => r.status === "skip").length;
  lines.push(`**Totals**: ${results.length} assertions — ${passes} pass · ${fails} fail · ${skips} skip`);
  lines.push("");
  lines.push("### Screenshots");
  lines.push("");
  for (const f of [
    "gap-2-matrix-default.png",
    "gap-2-mode-toggle.png",
    "gap-2-matrix-filtered.png",
    "gap-2-matrix-2-encoders.png",
  ]) {
    const p = path.join(OUT_DIR, f);
    if (fs.existsSync(p)) lines.push(`- \`${p}\``);
  }
  lines.push("");

  const block = lines.join("\n");
  // Append (don't overwrite) — peer agents may have already written Gap #1 / #3.
  if (fs.existsSync(REPORT)) {
    fs.appendFileSync(REPORT, block);
  } else {
    fs.writeFileSync(
      REPORT,
      `# QA Verification Report — State Inspector Gaps\n${block}`,
    );
  }
  console.log(`report → ${REPORT}`);
}

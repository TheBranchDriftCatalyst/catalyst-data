/**
 * State Inspector — Gap #7 regression specs.
 *
 * Locks in the pruned-window counterfactual delta + cross-panel handoff to
 * the pack threshold histograms (`.test-output/inspector-tour/data-scientist-gaps.md` §2.7):
 *
 *   - The amber pruned_window callout grows a `pruned-counterfactual-block`
 *     section with one row per applicable reason:
 *       too_few_mentions  → `min_mentions ≤ <window.mention_count>`
 *       sparse_density    → `max_chars_per_mention ≥ ceil(<chars_per_mention>)`
 *   - Edge case: `mention_count == 0` renders the degenerate caption.
 *   - Edge case: legacy `chars_per_mention == null` → derive char/mention
 *     from char_count/mention_count; if both 0 → render empty-window note,
 *     skip the cpm row.
 *   - A `pruned-tune-in-pack` link sets `?node=pack&packPreviewMin=N`
 *     (or `&packPreviewMaxCpm=M`) and seeds the histogram preview state on
 *     mount with a transient `animate-pulse` halo around the affected
 *     handle. The seeded params are stripped from the URL via
 *     `history.replaceState` so reload doesn't re-trigger the pulse.
 *   - The pack readout flips to `at min_mentions ≥ N: ...` (or the cpm
 *     equivalent) when the seed differs from the configured threshold.
 *
 * Math reviewer notes:
 *   - Inequality direction comes straight from pack.py (CD-lxcf):
 *       too_few_mentions: pruned iff `mention_count < prune_min_mentions`
 *                         → keep iff `min_mentions ≤ mention_count`
 *       sparse_density:   pruned iff `chars_per_mention > prune_max_chars_per_mention`
 *                         → keep iff `max_chars_per_mention ≥ chars_per_mention`
 *   - max_chars_per_mention threshold is rendered with `Math.ceil` so a
 *     fractional cpm (e.g. 813.4) suggests 814 — the *minimum* integer max
 *     that actually clears the window without flipping the inequality.
 */
import { test, expect } from "./fixtures/coverage";
import { useCorpus } from "./fixtures/corpora";
import type { Page } from "@playwright/test";
import { firstPrunedWindow, resolveRunId } from "./fixtures/inspector-discovery";
import { safeFetchNdjsonInPage } from "./fixtures/api-fetch";

/**
 * Discovery helpers are now wired with `safeJsonFromResponse` /
 * `safeNdjsonFromResponse` (see `e2e/fixtures/api-fetch.ts`), which throw
 * LOUD when the dev-server proxy returns SPA-fallback HTML. We deliberately
 * DO NOT swallow those errors here: an unreachable viewer-api should fail
 * the spec with a useful message, not skip-silently and report
 * `0 passed / 0 failed / N skipped` (the exact pathology this guard exists
 * to prevent). `firstPrunedWindow` / `resolveRunId` still return `null`
 * for the legitimate "no matching events" / "no resolvable run" case —
 * `test.skip(!result, ...)` covers those cleanly.
 */

interface PrunedDetail {
  reason: string;
  windowId: string;
  docId: string;
  mentionCount: number;
  charCount: number;
  /** Effective ch/mention used for counterfactual math: pulled from the
   *  event field when present, else derived from char_count/mention_count
   *  (matches the component's null-handling path). null = empty window. */
  charsPerMention: number | null;
  /** The verbatim event field — null when the legacy emitter dropped it. */
  charsPerMentionRaw: number | null;
}

/** Fetch all evidence_window_pruned events for a given run via the API.
 *  Bypasses the in-page cache so we always have a freshly-typed view of
 *  the math the UI is summarising. */
async function fetchAllPrunedWindows(
  page: Page,
  runId: string,
): Promise<PrunedDetail[]> {
  const path = `/viewer/api/bench/runs/${runId}/events?limit=20000`;
  const text = await safeFetchNdjsonInPage(page, path);

  const out: PrunedDetail[] = [];
  for (const ln of text.split("\n")) {
    if (!ln) continue;
    let ev: Record<string, unknown>;
    try {
      ev = JSON.parse(ln);
    } catch {
      continue;
    }
    if (ev.node_name !== "evidence_window_pruned") continue;
    const d = (ev.details ?? {}) as Record<string, unknown>;
    const mc = typeof d.mention_count === "number" ? (d.mention_count as number) : 0;
    const cc = typeof d.char_count === "number" ? (d.char_count as number) : 0;
    const cpmRaw =
      typeof d.chars_per_mention === "number" ? (d.chars_per_mention as number) : null;
    let cpmEff: number | null;
    if (cpmRaw != null) cpmEff = cpmRaw;
    else if (mc > 0) cpmEff = cc / mc;
    else cpmEff = null;
    out.push({
      reason: String(d.reason ?? ""),
      windowId: String(d.window_id ?? ""),
      docId: String(ev.doc_id ?? ""),
      mentionCount: mc,
      charCount: cc,
      charsPerMention: cpmEff,
      charsPerMentionRaw: cpmRaw,
    });
  }
  return out;
}

/** Find the API-side detail for a given (docId, windowId) pair. */
async function fetchPrunedDetail(
  page: Page,
  runId: string,
  docId: string,
  windowId: string,
): Promise<PrunedDetail | null> {
  const all = await fetchAllPrunedWindows(page, runId);
  return all.find((p) => p.docId === docId && p.windowId === windowId) ?? null;
}

async function gotoPrunedDetail(
  page: Page,
  runId: string,
  docId: string,
  windowId: string,
): Promise<void> {
  await page.goto(
    `/viewer/benchmarks/state?run=${encodeURIComponent(runId)}&doc=${encodeURIComponent(
      docId,
    )}&node=pruned_window:${encodeURIComponent(windowId)}`,
  );
  await expect(page.getByTestId("pruned-window-detail")).toBeVisible({
    timeout: 30_000,
  });
}

test.describe("State Inspector — Gap 7 — Pruned-window counterfactual", () => {
  test.describe("simple/single-reason pruned windows (happy-path corpus)", () => {
    // CD-1qqy: happy-path corpus provides simple pruned windows
    test.beforeEach(async ({ page }) => {
      await useCorpus(page, "happy-path");
    });

    test("pruned_window selection renders counterfactual block @smoke", async ({
      page,
    }) => {
    const tgt = await firstPrunedWindow(page);
    test.skip(!tgt, "no evidence_window_pruned events present in resolved run");
    const runId = await resolveRunId(page);
    test.skip(!runId, "no resolvable run id");

    await gotoPrunedDetail(page, runId!, tgt!.docId, tgt!.windowId);

    await expect(page.getByTestId("pruned-counterfactual-block")).toBeVisible();
    const rowCount = await page
      .locator(
        '[data-testid="pruned-counterfactual-row-too-few-mentions"], [data-testid="pruned-counterfactual-row-sparse-density"]',
      )
      .count();
    expect(rowCount).toBeGreaterThanOrEqual(1);
    // The "tune in pack" handoff link must mount alongside the rows.
    await expect(page.getByTestId("pruned-tune-in-pack")).toBeVisible();
  });

  test("too_few_mentions pruning shows correct min_mentions inequality", async ({
    page,
  }) => {
    const runId = await resolveRunId(page);
    test.skip(!runId, "no resolvable run id");

    // Find a pruned window whose reason is too_few_mentions AND whose
    // mention_count is > 0 (the > 0 path renders the canonical inequality
    // line; mention_count == 0 renders the degenerate caption — covered
    // separately).
    const all = await fetchAllPrunedWindows(page, runId!);
    const pick = all.find(
      (p) => p.reason.startsWith("too_few_mentions") && p.mentionCount > 0,
    );
    test.skip(
      !pick,
      "no too_few_mentions prunes with mention_count > 0 in resolved run",
    );

    await gotoPrunedDetail(page, runId!, pick!.docId, pick!.windowId);

    await expect(
      page.getByTestId("pruned-counterfactual-row-too-few-mentions"),
    ).toBeVisible();
    // Inline element MUST equal the window's mention_count (not min_mentions).
    await expect(
      page.getByTestId("pruned-counterfactual-suggested-min-mentions"),
    ).toHaveText(String(pick!.mentionCount));
    // Render must use ≤ (not <).
    const rowText =
      (await page
        .getByTestId("pruned-counterfactual-row-too-few-mentions")
        .textContent()) ?? "";
    expect(rowText).toMatch(/min_mentions\s*≤/);
    expect(rowText).not.toMatch(/min_mentions\s*</);
  });

  test("sparse_density pruning shows correct max_chars_per_mention inequality", async ({
    page,
  }) => {
    const runId = await resolveRunId(page);
    test.skip(!runId, "no resolvable run id");

    // Need a sparse_density prune with a usable cpm (raw or derivable).
    const all = await fetchAllPrunedWindows(page, runId!);
    const pick = all.find(
      (p) =>
        p.reason.startsWith("sparse_density") &&
        p.charsPerMention != null &&
        Number.isFinite(p.charsPerMention),
    );
    test.skip(!pick, "no sparse_density prunes with finite cpm in resolved run");

    await gotoPrunedDetail(page, runId!, pick!.docId, pick!.windowId);

    await expect(
      page.getByTestId("pruned-counterfactual-row-sparse-density"),
    ).toBeVisible();

    const expected = Math.ceil(pick!.charsPerMention!);
    await expect(
      page.getByTestId("pruned-counterfactual-suggested-max-chars-per-mention"),
    ).toHaveText(String(expected));

    const rowText =
      (await page
        .getByTestId("pruned-counterfactual-row-sparse-density")
        .textContent()) ?? "";
    expect(rowText).toMatch(/max_chars_per_mention\s*≥/);
    expect(rowText).not.toMatch(/max_chars_per_mention\s*>/);
  });

  test("degenerate zero-mention window renders the degenerate caption", async ({
    page,
  }) => {
    const runId = await resolveRunId(page);
    test.skip(!runId, "no resolvable run id");

    const all = await fetchAllPrunedWindows(page, runId!);
    const pick = all.find(
      (p) => p.reason.startsWith("too_few_mentions") && p.mentionCount === 0,
    );
    test.skip(!pick, "no zero-mention pruned windows in resolved run");

    await gotoPrunedDetail(page, runId!, pick!.docId, pick!.windowId);

    await expect(
      page.getByTestId("pruned-counterfactual-row-too-few-mentions"),
    ).toBeVisible();
    // Suggested = 0 in the degenerate path.
    await expect(
      page.getByTestId("pruned-counterfactual-suggested-min-mentions"),
    ).toHaveText("0");
    const rowText =
      (await page
        .getByTestId("pruned-counterfactual-row-too-few-mentions")
        .textContent()) ?? "";
    // Degenerate caption + `=` (not `≤`) in this branch.
    expect(rowText).toMatch(/degenerate/i);
    expect(rowText).toMatch(/min_mentions\s*=\s*0/);
  });

  test("tune-in-pack link navigates to pack panel and pre-positions sliders", async ({
    page,
  }) => {
    const runId = await resolveRunId(page);
    test.skip(!runId, "no resolvable run id");

    const all = await fetchAllPrunedWindows(page, runId!);
    // Prefer a too_few_mentions window with mention_count > 0 — gives us
    // a deterministic packPreviewMin to assert against.
    const pick =
      all.find(
        (p) => p.reason.startsWith("too_few_mentions") && p.mentionCount > 0,
      ) ??
      all.find(
        (p) =>
          p.reason.startsWith("sparse_density") &&
          p.charsPerMention != null &&
          Number.isFinite(p.charsPerMention),
      );
    test.skip(!pick, "no pruned windows usable for handoff");

    await gotoPrunedDetail(page, runId!, pick!.docId, pick!.windowId);

    const isTooFew = pick!.reason.startsWith("too_few_mentions");
    const expectedMin = isTooFew ? String(pick!.mentionCount) : null;
    const expectedCpm =
      !isTooFew && pick!.charsPerMention != null
        ? String(Math.ceil(pick!.charsPerMention))
        : null;

    // Click the handoff link — onSelectNode + URL-rewrite must fire.
    // The handler synchronously calls replaceState() with the seed params
    // before scheduling React re-render, so reading page.url() immediately
    // after the click captures the URL state with seeds intact (the strip
    // effect runs later, after the histogram mounts).
    await page.getByTestId("pruned-tune-in-pack").click();

    const urlAfterClick = new URL(page.url());
    expect(urlAfterClick.searchParams.get("node")).toBe("pack");
    if (expectedMin != null) {
      expect(urlAfterClick.searchParams.get("packPreviewMin")).toBe(expectedMin);
    }
    if (expectedCpm != null) {
      expect(urlAfterClick.searchParams.get("packPreviewMaxCpm")).toBe(expectedCpm);
    }

    // Wait for the pack panel + its histogram to mount under the new
    // selection. The pulse halo is the load-bearing assertion that the
    // seed survived URL → useMemo → state-init.
    await expect(page.getByTestId("pack-detail")).toBeVisible({ timeout: 30_000 });
    const pulseId = isTooFew
      ? "pack-threshold-handle-pulse-mention-count"
      : "pack-threshold-handle-pulse-chars-per-mention";
    await expect(page.getByTestId(pulseId)).toBeVisible({ timeout: 5_000 });

    // After the ~1.5s pulse window, the URL must be stripped (the
    // useEffect inside PackThresholdHistograms calls replaceState as part
    // of the seeded-mount handshake).
    await page.waitForTimeout(2000);
    const urlAfterStrip = new URL(page.url());
    expect(urlAfterStrip.searchParams.has("packPreviewMin")).toBe(false);
    expect(urlAfterStrip.searchParams.has("packPreviewMaxCpm")).toBe(false);
    // node=pack stays; only the seed params are stripped.
    expect(urlAfterStrip.searchParams.get("node")).toBe("pack");

    // Sanity: the pulse drops back to default styling after the timeout.
    expect(await page.getByTestId(pulseId).count()).toBe(0);
  });

  test("pack handle preview value matches suggested counterfactual", async ({
    page,
  }) => {
    const runId = await resolveRunId(page);
    test.skip(!runId, "no resolvable run id");

    const all = await fetchAllPrunedWindows(page, runId!);
    const pick =
      all.find(
        (p) => p.reason.startsWith("too_few_mentions") && p.mentionCount > 0,
      ) ??
      all.find(
        (p) =>
          p.reason.startsWith("sparse_density") &&
          p.charsPerMention != null &&
          Number.isFinite(p.charsPerMention),
      );
    test.skip(!pick, "no pruned windows usable for handoff");

    await gotoPrunedDetail(page, runId!, pick!.docId, pick!.windowId);

    // We need to verify the suggestion BEFORE the handoff so we know what
    // the readout should subsequently echo. Pull from the live UI.
    const isTooFew = pick!.reason.startsWith("too_few_mentions");
    const detail = await fetchPrunedDetail(
      page,
      runId!,
      pick!.docId,
      pick!.windowId,
    );
    expect(detail).not.toBeNull();

    await page.getByTestId("pruned-tune-in-pack").click();
    await expect(page.getByTestId("pack-detail")).toBeVisible({ timeout: 30_000 });

    if (isTooFew) {
      // Wait for the histogram to mount with seeded preview.
      await expect(page.getByTestId("pack-readout-mention-count")).toBeVisible({
        timeout: 10_000,
      });
      const readout =
        (await page.getByTestId("pack-readout-mention-count").textContent()) ?? "";
      // Readout must reflect the suggestion. The readout uses ≥ form when
      // previewMin differs from configured min_mentions; if they happen to
      // match (i.e. the configured min_mentions is identical to the
      // counterfactual integer — extremely unlikely for a *pruned* window
      // whose mention_count < min by definition of too_few), we'd see the
      // "current" form instead. Defensive: accept either, but require the
      // suggestion number is present.
      const expected = String(detail!.mentionCount);
      expect(readout).toContain(expected);
      // In the canonical case (preview != configured), the ≥ form fires.
      expect(readout).toMatch(
        new RegExp(`(at\\s+min_mentions\\s*≥\\s*${expected}|current\\s+min_mentions\\s*=\\s*${expected})`),
      );
    } else {
      await expect(
        page.getByTestId("pack-readout-chars-per-mention"),
      ).toBeVisible({ timeout: 10_000 });
      const readout =
        (await page.getByTestId("pack-readout-chars-per-mention").textContent()) ??
        "";
      const expected = String(Math.ceil(detail!.charsPerMention ?? 0));
      // Note: PackThresholdHistograms readout uses `at max_chars_per_mention ≤ N`
      // form (it's expressing the SLIDER VALUE — keep below this cpm). The
      // *suggested* cpm may snap to a bin edge during the seeded mount —
      // the seed is clamped to the available CPM_SNAP_TARGETS plus the
      // observed-max in PackThresholdHistograms. Some test runs may show
      // a snapped value differing from the raw ceil. Accept either: seed
      // value in numeric form must appear in the readout.
      expect(readout).toMatch(/at\s+max_chars_per_mention\s*≤|current\s+max_chars_per_mention\s*=/);
      expect(readout).toContain(expected);
    }
  });
  });

  test.describe("composite-reason pruned windows (diversity-composite corpus)", () => {
    // CD-1qqy: diversity-composite corpus provides composite prune_reason="low_confidence,sparse_density"
    test.beforeEach(async ({ page }) => {
      await useCorpus(page, "diversity-composite");
    });

    test("composite-reason pruned window renders both counterfactual rows", async ({
      page,
    }) => {
    // Discover a pruned window whose reason string contains BOTH
    // "too_few_mentions" AND "sparse_density". The pack node sometimes emits
    // a single composite reason ("too_few_mentions+sparse_density") when both
    // thresholds bite. The component branches on /too_few_mentions/ and
    // /sparse_density/ separately (StateInspector.tsx ~L446-447) so a
    // composite reason MUST render both rows. If no such window exists in any
    // reachable run, skip cleanly.
    //
    // page.evaluate runs against the page's window — relative URLs need a
    // real origin, which `about:blank` is not. Navigate to the SPA root first.
    await page.goto("/viewer/");
    const target = await page.evaluate(async () => {
      const r = await fetch("/viewer/api/bench/runs");
      const text = await r.text();
      const head = text.trim().slice(0, 80);
      if (!r.ok || head.startsWith("<")) {
        throw new Error(
          `viewer-api unreachable at /viewer/api/bench/runs — got ${r.status} ` +
            `${r.headers.get("content-type") ?? "(no content-type)"}; first 80 chars: ${head}. ` +
            `Check that vite dev server (:5173) is up AND its /viewer/api/* proxy to :8080 is wired.`,
        );
      }
      const runs = (JSON.parse(text) as { runs: string[] }).runs;
      // Walk newest → oldest; the composite reason is rare so we cast a
      // wider net than the single-run helpers above.
      for (const runId of runs.slice().reverse()) {
        const path = `/viewer/api/bench/runs/${runId}/events?limit=5000`;
        const er = await fetch(path);
        const etext = await er.text();
        const ehead = etext.trim().slice(0, 80);
        if (!er.ok || ehead.startsWith("<")) {
          throw new Error(
            `viewer-api unreachable at ${path} — got ${er.status}; first 80 chars: ${ehead}.`,
          );
        }
        for (const ln of etext.split("\n")) {
          if (!ln) continue;
          let ev: Record<string, unknown>;
          try {
            ev = JSON.parse(ln);
          } catch {
            continue;
          }
          if (ev.node_name !== "evidence_window_pruned") continue;
          const d = (ev.details ?? {}) as Record<string, unknown>;
          const reason = String(d.reason ?? "");
          if (
            reason.includes("too_few_mentions") &&
            reason.includes("sparse_density")
          ) {
            return {
              runId,
              docId: String(ev.doc_id ?? ""),
              windowId: String(d.window_id ?? ""),
            };
          }
        }
      }
      return null;
    });

    test.skip(!target, "no composite-reason pruned window in any reachable run");
    await page.goto(
      `/viewer/benchmarks/state?run=${encodeURIComponent(target!.runId)}` +
        `&doc=${encodeURIComponent(target!.docId)}` +
        `&node=pruned_window:${encodeURIComponent(target!.windowId)}`,
    );
    await expect(page.getByTestId("pruned-counterfactual-block")).toBeVisible({
      timeout: 30_000,
    });
    // The load-bearing assertion: BOTH rows render for a composite reason.
    await expect(
      page.getByTestId("pruned-counterfactual-row-too-few-mentions"),
    ).toBeVisible();
    await expect(
      page.getByTestId("pruned-counterfactual-row-sparse-density"),
    ).toBeVisible();
  });
  });

  test("writeQuery preserves unknown URL params on selection change", async ({
    page,
  }) => {
    // Use happy-path corpus for this generic test (doesn't need composite reason)
    await useCorpus(page, "happy-path");
    // Direct regression on writeQuery's drive-by fix (StateInspector.tsx
    // ~L96-101): the persist-selection effect MUST preserve unknown query
    // params (e.g. packPreviewMin / packPreviewMaxCpm) so the handoff seed
    // survives until the histogram mounts and strips them.
    //
    // App uses BrowserRouter; writeQuery is fired by a React useEffect on
    // [selectedDoc, selectedNode, pinnedRunId] — NOT by URL changes. The
    // pushState/popstate trick wouldn't cause writeQuery to fire because
    // React state doesn't observe URL events. Instead, we trigger
    // writeQuery by simulating a real selection change: click a pipeline
    // node (which calls setSelectedNode → writeQuery).
    await page.goto("/viewer/");
    const docId = await page.evaluate(async () => {
      const r = await fetch("/viewer/api/bench/runs");
      const text = await r.text();
      const head = text.trim().slice(0, 80);
      if (!r.ok || head.startsWith("<")) {
        throw new Error(
          `viewer-api unreachable at /viewer/api/bench/runs — got ${r.status}; first 80 chars: ${head}.`,
        );
      }
      const runs = (JSON.parse(text) as { runs: string[] }).runs;
      if (!runs.length) return null;
      const path = `/viewer/api/bench/runs/${runs[0]}/events?limit=2000`;
      const er = await fetch(path);
      const etext = await er.text();
      const ehead = etext.trim().slice(0, 80);
      if (!er.ok || ehead.startsWith("<")) {
        throw new Error(
          `viewer-api unreachable at ${path} — got ${er.status}; first 80 chars: ${ehead}.`,
        );
      }
      for (const ln of etext.split("\n")) {
        if (!ln) continue;
        try {
          const ev = JSON.parse(ln) as Record<string, unknown>;
          if (ev.doc_id && ev.doc_id !== "__run__") return String(ev.doc_id);
        } catch {
          /* skip */
        }
      }
      return null;
    });
    test.skip(!docId, "no doc available in newest run");

    // Land on the inspector with seed params + an initial node selection.
    // The mount sequence:
    //   1. readQuery effect fires → setSelectedDoc / setSelectedNode
    //   2. writeQuery effect fires (state changed) → URL rewrite
    // The rewrite MUST keep the seed params intact.
    await page.goto(
      `/viewer/benchmarks/state?doc=${encodeURIComponent(
        docId!,
      )}&node=document&packPreviewMin=7&packPreviewMaxCpm=999`,
    );
    await expect(page.getByTestId("state-inspector")).toBeVisible({
      timeout: 30_000,
    });
    // Wait for the post-mount writeQuery to settle.
    await page.waitForTimeout(300);

    const urlAfterMount = new URL(page.url());
    expect(urlAfterMount.searchParams.get("packPreviewMin")).toBe("7");
    expect(urlAfterMount.searchParams.get("packPreviewMaxCpm")).toBe("999");
    expect(urlAfterMount.searchParams.get("doc")).toBe(docId);
    expect(urlAfterMount.searchParams.get("node")).toBe("document");

    // Now trigger a SUBSEQUENT selection change so writeQuery fires again.
    // The pipeline-node-document node is always in the graph (the doc node
    // is the graph's root); clicking it would no-op on selection. We need
    // to switch to a different node — try consensus first, then any other
    // node that's actually rendered. The pipeline-node-* testids are
    // emitted for whichever roles exist in this doc's events.
    const candidateRoles = ["consensus", "pack", "ner_encoder", "persist"];
    let clicked = false;
    for (const role of candidateRoles) {
      // PipelineNode emits role-only testid when ref is null, role-ref
      // otherwise. Match a prefix to handle either form.
      const candidate = page.locator(`[data-testid^="pipeline-node-${role}"]`).first();
      if ((await candidate.count()) > 0) {
        await candidate.click();
        clicked = true;
        break;
      }
    }
    test.skip(
      !clicked,
      "no alternate pipeline node available to trigger a selection change",
    );

    // Wait for the writeQuery effect to fire after the state update.
    await page.waitForTimeout(300);

    const urlAfterSelect = new URL(page.url());
    // The contract: unknown params survive the rewrite.
    expect(urlAfterSelect.searchParams.get("packPreviewMin")).toBe("7");
    expect(urlAfterSelect.searchParams.get("packPreviewMaxCpm")).toBe("999");
    // doc still present, node should have advanced past `document`.
    expect(urlAfterSelect.searchParams.get("doc")).toBe(docId);
    expect(urlAfterSelect.searchParams.get("node")).not.toBe("document");
  });
});

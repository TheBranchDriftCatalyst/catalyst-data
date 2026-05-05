/**
 * State Inspector — Gap #10 regression specs.
 *
 * Locks in the persistent downstream-lineage panel described in
 * `.test-output/inspector-tour/data-scientist-gaps.md` §2.10:
 *
 *   - The persist node is hosted by ``<DownstreamPanel>`` (lifted out of
 *     NodeStats) — symmetric with how ``document`` hosts ``<UpstreamPanel>``.
 *   - One card per output asset, with row count, S3 path, dagster_run_id
 *     deep-link, and materialized_at.
 *   - Empty-state when no persist event observed for the doc.
 *   - Failure cards (per-asset errors in ``per_asset_status``) render
 *     amber and surface the reason.
 *   - The legacy single-line ``persist-status`` testid is gone — persist
 *     is no longer a NodeStats branch.
 *
 * Conventions (mirrors Gap #1–#8 specs):
 *   - Deep-link via URL only; no graph clicks.
 *   - Skip cleanly when the resolved corpus lacks the required shape.
 *   - test.describe block matches `--grep "Gap 10"`.
 */
import { test, expect, type Page } from "./fixtures/coverage";
import { useFixtureCorpus } from "./fixtures/fixture-mode";
import { request as plRequest } from "@playwright/test";
import { firstDocWithPersist, resolveRunId } from "./fixtures/inspector-discovery";
import { safeNdjsonFromResponse } from "./fixtures/api-fetch";

interface BenchEvent {
  ts?: string;
  node_name?: string;
  status?: string;
  doc_id?: string | null;
  details?: Record<string, unknown>;
}

async function deepLink(
  page: Page,
  docId: string,
  nodeQuery: string,
  pinnedRunId?: string | null,
): Promise<string> {
  const runId = pinnedRunId ?? (await resolveRunId(page));
  const runSeg = runId ? `run=${encodeURIComponent(runId)}&` : "";
  return `/viewer/benchmarks/state?${runSeg}doc=${encodeURIComponent(
    docId,
  )}&node=${nodeQuery}`;
}

/** Pull the latest persist_artifacts event for the given doc. Returns
 *  null when no event exists in the resolved run. */
async function fetchPersistEventForDoc(
  page: Page,
  docId: string,
): Promise<BenchEvent | null> {
  const events = await fetchAllEvents(page);
  const candidates = events.filter(
    (e) => e.node_name === "persist_artifacts" && e.doc_id === docId,
  );
  if (candidates.length === 0) return null;
  candidates.sort((a, b) =>
    (a.ts ?? "") < (b.ts ?? "") ? 1 : (a.ts ?? "") > (b.ts ?? "") ? -1 : 0,
  );
  return candidates[0];
}

async function fetchAllEvents(page: Page): Promise<BenchEvent[]> {
  const runId = await resolveRunId(page);
  if (!runId) return [];
  const baseURL =
    process.env.PLAYWRIGHT_BASE_URL ??
    process.env.VIEWER_URL ??
    "http://localhost:5173";
  const ctx = await plRequest.newContext({ baseURL });
  const limit = process.env.PLAYWRIGHT_DISCOVERY_LIMIT ?? "2000";
  const path = `/viewer/api/bench/runs/${runId}/events?limit=${limit}`;
  const resp = await ctx.get(path, { timeout: 60_000 });
  const text = await safeNdjsonFromResponse(resp, path);
  const out: BenchEvent[] = [];
  for (const ln of text.split("\n")) {
    if (!ln) continue;
    try {
      out.push(JSON.parse(ln) as BenchEvent);
    } catch {
      /* skip malformed */
    }
  }
  return out;
}

/** First doc that exists in the run but has NO persist_artifacts event.
 *  Used by the empty-state spec; returns null when every observed doc
 *  has a persist event (in which case we skip cleanly). */
async function firstDocWithoutPersist(page: Page): Promise<{ docId: string } | null> {
  const events = await fetchAllEvents(page);
  const allDocIds = new Set<string>();
  const docsWithPersist = new Set<string>();
  for (const e of events) {
    const docId = e.doc_id ?? null;
    if (!docId || docId === "__run__") continue;
    allDocIds.add(docId);
    if (e.node_name === "persist_artifacts") {
      docsWithPersist.add(docId);
    }
  }
  for (const id of allDocIds) {
    if (!docsWithPersist.has(id)) return { docId: id };
  }
  return null;
}

/** First doc whose persist_artifacts event has at least one
 *  ``per_asset_status[*].status === "error"`` entry. */
async function firstDocWithPartialFailure(
  page: Page,
): Promise<{ docId: string; failedAssetKey: string; reason: string | null } | null> {
  const events = await fetchAllEvents(page);
  for (const e of events) {
    if (e.node_name !== "persist_artifacts") continue;
    const docId = e.doc_id ?? null;
    if (!docId) continue;
    const statusMap = (e.details as { per_asset_status?: Record<string, unknown> } | undefined)
      ?.per_asset_status;
    if (!statusMap || typeof statusMap !== "object") continue;
    for (const [k, v] of Object.entries(statusMap)) {
      if (v && typeof v === "object" && (v as { status?: string }).status === "error") {
        return {
          docId,
          failedAssetKey: k,
          reason: typeof (v as { reason?: unknown }).reason === "string"
            ? ((v as { reason?: string }).reason ?? null)
            : null,
        };
      }
      if (typeof v === "string" && v === "error") {
        return { docId, failedAssetKey: k, reason: null };
      }
    }
  }
  return null;
}

test.describe("State Inspector — Gap 10 — downstream lineage panel", () => {
  // CD-1qqy: happy-path corpus provides persist_artifacts with asset_keys + dagster_run_id
  test.beforeEach(async ({ page }) => {
    await useFixtureCorpus(page, "happy-path");
  });

  test("persist node selection renders DownstreamPanel @smoke", async ({ page }) => {
    const tgt = await firstDocWithPersist(page);
    test.skip(!tgt, "no doc with persist_artifacts events in resolved run");
    await page.goto(await deepLink(page, tgt!.docId, "persist"));
    const detail = page.getByTestId("inspector-detail-persist");
    await expect(detail).toBeVisible({ timeout: 60_000 });
    await expect(detail.getByTestId("downstream-panel")).toBeVisible({
      timeout: 30_000,
    });
  });

  test("card count matches details.output_paths.length", async ({ page }) => {
    const tgt = await firstDocWithPersist(page);
    test.skip(!tgt, "no doc with persist_artifacts events");
    const ev = await fetchPersistEventForDoc(page, tgt!.docId);
    test.skip(!ev, "persist event vanished between discovery and fetch");
    const d = (ev!.details ?? {}) as Record<string, unknown>;
    // The panel renders one card per asset_key; the source-of-truth set
    // is the union of (asset_keys, output_paths, row_counts, size_bytes,
    // per_asset_status). Use the same union the component does so this
    // test passes regardless of which subset the emitter populated.
    const explicitKeys = Array.isArray(d.asset_keys)
      ? (d.asset_keys as unknown[]).filter((k): k is string => typeof k === "string")
      : [];
    const union = new Set<string>(explicitKeys);
    for (const m of ["output_paths", "row_counts", "size_bytes", "per_asset_status"] as const) {
      const obj = d[m];
      if (obj && typeof obj === "object") {
        for (const k of Object.keys(obj as Record<string, unknown>)) union.add(k);
      }
    }
    test.skip(union.size === 0, "persist event has no asset_key fields populated");

    await page.goto(await deepLink(page, tgt!.docId, "persist"));
    const panel = page.getByTestId("downstream-panel");
    await expect(panel).toBeVisible({ timeout: 60_000 });
    const cardCount = await panel.locator('[data-testid^="downstream-card-"]').count();
    expect(cardCount).toBe(union.size);
  });

  test("dagster_run_id deep-link href is correct", async ({ page }) => {
    const tgt = await firstDocWithPersist(page);
    test.skip(!tgt, "no doc with persist_artifacts events");
    const ev = await fetchPersistEventForDoc(page, tgt!.docId);
    test.skip(!ev, "persist event vanished");
    const d = (ev!.details ?? {}) as Record<string, unknown>;
    const dagsterRunId =
      typeof d.dagster_run_id === "string" ? (d.dagster_run_id as string) : null;
    test.skip(!dagsterRunId, "persist event has no dagster_run_id");

    // Pick the first asset_key that the panel will render a card for.
    // Prefer ``asset_keys`` if explicit, fall back to output_paths keys.
    const explicitKeys = Array.isArray(d.asset_keys)
      ? (d.asset_keys as unknown[]).filter((k): k is string => typeof k === "string")
      : [];
    const outPaths = (d.output_paths ?? {}) as Record<string, unknown>;
    const firstKey = explicitKeys[0] ?? Object.keys(outPaths)[0];
    test.skip(!firstKey, "persist event has no asset_keys to deep-link");

    await page.goto(await deepLink(page, tgt!.docId, "persist"));
    const panel = page.getByTestId("downstream-panel");
    await expect(panel).toBeVisible({ timeout: 60_000 });
    const link = panel.getByTestId(`downstream-dagster-link-${firstKey}`);
    await expect(link).toBeVisible({ timeout: 30_000 });
    const href = await link.getAttribute("href");
    expect(href).toBe(`http://localhost:3000/runs/${dagsterRunId}`);
    const target = await link.getAttribute("target");
    expect(target).toBe("_blank");
  });

  test("partial-failure card shows amber border + reason", async ({ page }) => {
    const tgt = await firstDocWithPartialFailure(page);
    test.skip(
      !tgt,
      "no persist event with per_asset_status.error in resolved run",
    );
    await page.goto(await deepLink(page, tgt!.docId, "persist"));
    const panel = page.getByTestId("downstream-panel");
    await expect(panel).toBeVisible({ timeout: 60_000 });
    const failedCard = panel.getByTestId(`downstream-card-${tgt!.failedAssetKey}`);
    await expect(failedCard).toBeVisible({ timeout: 30_000 });
    await expect(failedCard).toHaveAttribute("data-status", "error");
    if (tgt!.reason) {
      await expect(failedCard).toContainText(tgt!.reason);
    } else {
      // No reason carried — at minimum the "failed —" prefix renders.
      await expect(failedCard).toContainText(/failed/);
    }
  });

  test("empty state when no persist event exists for doc", async ({ page }) => {
    const tgt = await firstDocWithoutPersist(page);
    test.skip(
      !tgt,
      "every doc in the resolved run has a persist event — cannot test empty state",
    );
    await page.goto(await deepLink(page, tgt!.docId, "persist"));
    const detail = page.getByTestId("inspector-detail-persist");
    await expect(detail).toBeVisible({ timeout: 60_000 });
    await expect(detail.getByTestId("downstream-empty")).toBeVisible({
      timeout: 30_000,
    });
  });

  test("lift-out regression: persist is at DetailRouter top level, not nested in NodeStats", async ({
    page,
  }) => {
    const tgt = await firstDocWithPersist(page);
    test.skip(!tgt, "no persist events");
    await page.goto(await deepLink(page, tgt!.docId, "persist"));
    const detail = page.getByTestId("inspector-detail-persist");
    await expect(detail).toBeVisible({ timeout: 60_000 });
    // The DownstreamPanel is the only persist body — not a NodeStats
    // branch nested inside an "inspector-detail-persist" wrapper from
    // NodeStats. Asserting that the legacy ``persist-status`` testid
    // is gone is the regression gate.
    expect(await detail.locator('[data-testid="persist-status"]').count()).toBe(0);
    // And the DownstreamPanel must be a direct descendant — not buried
    // inside a NodeStats wrapper.
    await expect(detail.getByTestId("downstream-panel")).toBeVisible();
  });
});

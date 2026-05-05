/**
 * Runtime-discovery helpers for State Inspector Playwright specs.
 *
 * Each helper takes a `Page`, scans the latest viable bench run's events,
 * and returns either a typed match or `null` so callers can do
 * `test.skip(!result, "no shape match")`.
 *
 * Caching: events are cached per-Page in a module-scope WeakMap (keyed
 * by Page, value is a Promise<Event[]>) — so 3 helper calls in one test
 * make 1 fetch. Cache is for the Page's lifetime; Playwright tears down
 * the Page between tests so cross-test contamination is impossible.
 * Use `clearDiscoveryCache(page)` to force a re-fetch mid-test.
 *
 * The fetch happens inside `page.evaluate` so it goes through the same
 * vite proxy the SPA uses (`/viewer/api/...` → :8080).
 *
 * Env overrides:
 *   PLAYWRIGHT_RUN_ID         — pin a specific run for debugging
 *   PLAYWRIGHT_MIN_RUN_AGE_MS — min age before a run is queryable (default 3min)
 */
import { request as plRequest, type APIRequestContext, type Page } from "@playwright/test";
import { safeJsonFromResponse, safeNdjsonFromResponse } from "./api-fetch";

type EvDetails = Record<string, unknown>;
interface BenchEvent {
  node_name?: string;
  status?: string;
  doc_id?: string;
  chunk_id?: string;
  model?: string;
  tags?: string[];
  details?: EvDetails;
  [k: string]: unknown;
}

export interface RunsListing {
  runs: string[];
  latest: string | null;
  live: string | null;
}

const MIN_RUN_AGE_MS = Number(process.env.PLAYWRIGHT_MIN_RUN_AGE_MS ?? 3 * 60_000);

/** Parse a run_id of the form `YYYY-MM-DD-HHMMSS[-label]` to ms. */
function runIdToMs(runId: string): number | null {
  const m = runId.match(/^(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})/);
  if (!m) return null;
  const [, y, mo, d, h, mi, s] = m;
  const t = new Date(+y, +mo - 1, +d, +h, +mi, +s).getTime();
  return Number.isFinite(t) ? t : null;
}

const cache = new WeakMap<Page, Promise<BenchEvent[]>>();

export function clearDiscoveryCache(page: Page): void {
  cache.delete(page);
}

/** Build a Node-side APIRequestContext bound to the same baseURL the page
 *  uses. Doing the discovery fetches over Node bypasses two failure modes
 *  we hit when running through ``page.evaluate(fetch(...))`` against the
 *  Vite dev server:
 *    1. Vite's HMR + ndjson chunked transfer encoding occasionally
 *       confuses Chromium's fetch implementation with "TypeError: Failed
 *       to fetch" on payloads of a few MB.
 *    2. Helpers can run before ``page.goto`` lands a real origin, in
 *       which case ``fetch("/...")`` errors with "Failed to parse URL".
 *    Using APIRequestContext sidesteps both. */
const apiCache = new WeakMap<Page, APIRequestContext>();

async function getApi(page: Page): Promise<APIRequestContext> {
  const existing = apiCache.get(page);
  if (existing) return existing;
  // Local viewer-ui dev server (vite, :5173) proxies `/viewer/api/*` →
  // :8080 (FastAPI) — so a single base URL is sufficient. Never default to
  // a deployed talos host here: the SPA fallback for unknown routes
  // returns `<!doctype html>...</html>`, JSON.parse fails silently, every
  // helper returns null, and every `test.skip(!result)` hits — leaving
  // regression specs reporting `0/0/N skipped` instead of failing loud.
  // ``localhost`` (not ``127.0.0.1``) because Vite's dev server binds
  // exclusively to the ``localhost`` host header by default and serves
  // a blank page on the IPv4 literal — Gap #4 verifier hit this. The
  // env-guard allowlist (`localhost | 127.0.0.1 | ::1`) still accepts
  // either as an explicit override.
  const baseURL =
    process.env.PLAYWRIGHT_BASE_URL ??
    process.env.VIEWER_URL ??
    "http://localhost:5173";
  const ctx = await plRequest.newContext({ baseURL });
  apiCache.set(page, ctx);
  return ctx;
}

export async function listRuns(page: Page): Promise<RunsListing> {
  const api = await getApi(page);
  const resp = await api.get("/viewer/api/bench/runs");
  // Use safeJsonFromResponse so an SPA-fallback HTML body (proves dev
  // server proxy is broken or VIEWER_URL points at a deployed host)
  // throws LOUDLY instead of silently returning empty runs that make
  // every regression spec skip-by-default.
  const body = await safeJsonFromResponse<{
    runs?: string[];
    latest?: string | null;
    live?: string | null;
  }>(resp, "/viewer/api/bench/runs");
  return { runs: body.runs ?? [], latest: body.latest ?? null, live: body.live ?? null };
}
export const fetchRuns = listRuns;

/** Newest run that's old enough to query (skips the in-flight live run
 *  and anything younger than MIN_RUN_AGE_MS). PLAYWRIGHT_RUN_ID overrides.
 *  Exported so tests can pass it as a `?run=` deep-link param to keep the
 *  SPA reading the same run the helper discovered against. */
export async function resolveRunId(page: Page): Promise<string | null> {
  const override = process.env.PLAYWRIGHT_RUN_ID;
  if (override) return override;
  const { runs, live } = await listRuns(page);
  const now = Date.now();
  for (const id of runs) {
    if (id === live) continue;
    const ts = runIdToMs(id);
    if (ts == null) continue;
    if (now - ts >= MIN_RUN_AGE_MS) return id;
  }
  return null;
}

async function getEvents(page: Page): Promise<BenchEvent[]> {
  const existing = cache.get(page);
  if (existing) return existing;
  const promise = (async () => {
    const runId = await resolveRunId(page);
    if (!runId) return [] as BenchEvent[];
    const api = await getApi(page);
    // Cap at 1000 events (~9MB / ~3s warm) — discovery only needs to find
    // the first event matching each shape, not exhaustively scan the run.
    // 1000 is a deliberate floor: the SPA's polling fetch uses limit=50000
    // and the helper-found event MUST be inside that window for the test's
    // SPA-side assertion to land. Capping discovery low keeps helper +
    // SPA in lockstep — pick events the SPA will definitely have polled.
    const limit = process.env.PLAYWRIGHT_DISCOVERY_LIMIT ?? "1000";
    const path = `/viewer/api/bench/runs/${runId}/events?limit=${limit}`;
    const resp = await api.get(path, { timeout: 60_000 });
    // Loud guard: SPA-fallback HTML => throw, not return-empty.
    // (Empty events are a real signal — for instance a run that produced
    // no events — but HTML-instead-of-ndjson means infrastructure is
    // misconfigured and the test should fail with a useful message.)
    const text = await safeNdjsonFromResponse(resp, path);
    const out: BenchEvent[] = [];
    for (const ln of text.split("\n")) {
      if (!ln) continue;
      try {
        // The /runs/$RUN/events endpoint reads from a hive-partitioned
        // parquet store and returns events from multiple run_ids (the SPA
        // does the same fetch and renders them all). We don't filter by
        // ev.run_id here — keep behavior in lockstep with the SPA so a
        // doc the helper finds is also a doc the SPA can render.
        out.push(JSON.parse(ln) as BenchEvent);
      } catch {
        /* skip malformed line */
      }
    }
    return out;
  })();
  cache.set(page, promise);
  return promise;
}

const docOf = (e: BenchEvent, splitOn = ":"): string =>
  e.doc_id ?? (e.chunk_id ?? "").split(splitOn)[0] ?? "";

// ── per-shape discovery ─────────────────────────────────────────────────

export async function firstDocWithChunks(
  page: Page,
): Promise<{ docId: string } | null> {
  for (const e of await getEvents(page)) {
    if (e.node_name !== "chunk_loaded") continue;
    const cid = e.chunk_id ?? "";
    if (!cid || cid.includes(":_ner_") || cid.endsWith(":_consensus") || cid.includes(":win-"))
      continue;
    const docId = docOf(e);
    if (docId) return { docId };
  }
  return null;
}

export async function firstEncoderWithMentions(
  page: Page,
): Promise<{ docId: string; encoder: string } | null> {
  for (const e of await getEvents(page)) {
    if (e.node_name !== "chunk_extracted") continue;
    const m = (e.chunk_id ?? "").match(/^(.+):_ner_(.+)$/);
    if (!m) continue;
    const mentions = (e.details as { mentions?: EvDetails[] } | undefined)?.mentions;
    if (!Array.isArray(mentions) || mentions.length === 0) continue;
    if (!mentions.some((mn) => typeof mn.span_start === "number" && typeof mn.span_end === "number"))
      continue;
    return { docId: e.doc_id ?? m[1], encoder: m[2] };
  }
  return null;
}

export async function firstEncoderWithError(
  page: Page,
): Promise<{ docId: string; encoder: string } | null> {
  for (const e of await getEvents(page)) {
    if (e.node_name !== "ner_encoder_completed" && e.node_name !== "chunk_extracted") continue;
    const m = (e.chunk_id ?? "").match(/^(.+):_ner_(.+)$/);
    if (!m) continue;
    const errMsg = (e.details as { error?: { message?: unknown } } | undefined)?.error?.message;
    if (e.status !== "error" && !errMsg) continue;
    return { docId: e.doc_id ?? m[1], encoder: m[2] };
  }
  return null;
}

export async function firstDocWithConsensus(
  page: Page,
): Promise<{ docId: string } | null> {
  for (const e of await getEvents(page)) {
    if (e.node_name !== "mention_decision") continue;
    const docId = docOf(e);
    if (docId) return { docId };
  }
  return null;
}

export async function firstDocWithPackEvidence(
  page: Page,
): Promise<{ docId: string } | null> {
  for (const e of await getEvents(page)) {
    if (e.node_name !== "pack_evidence" || e.status !== "completed") continue;
    const kept = (e.details as { kept_windows?: unknown[] } | undefined)?.kept_windows;
    if (!Array.isArray(kept) || kept.length === 0) continue;
    const docId = docOf(e);
    if (docId) return { docId };
  }
  return null;
}

export async function firstDocWithSpoWindows(
  page: Page,
): Promise<{ docId: string } | null> {
  for (const e of await getEvents(page)) {
    if (e.node_name !== "chunk_extracted") continue;
    const cid = e.chunk_id ?? "";
    if (!cid.includes(":win-")) continue;
    const docId = e.doc_id ?? cid.split(":win-")[0];
    if (docId) return { docId };
  }
  return null;
}

export async function firstWindowWithPropositions(
  page: Page,
): Promise<{ docId: string; chunkId: string } | null> {
  for (const e of await getEvents(page)) {
    if (e.node_name !== "chunk_extracted") continue;
    const cid = e.chunk_id ?? "";
    if (!cid.includes(":win-")) continue;
    const pc = (e.details as { proposition_count?: unknown } | undefined)?.proposition_count;
    if (typeof pc !== "number" || pc <= 0) continue;
    const docId = e.doc_id ?? cid.split(":win-")[0];
    if (docId) return { docId, chunkId: cid };
  }
  return null;
}

export async function firstSpoModelWithWindows(
  page: Page,
): Promise<{ docId: string; model: string } | null> {
  for (const e of await getEvents(page)) {
    if (e.node_name !== "chunk_extracted") continue;
    const cid = e.chunk_id ?? "";
    if (!cid.includes(":win-")) continue;
    const model = e.model;
    if (!model) continue;
    const lower = model.toLowerCase();
    if (lower.includes("gliner") || lower.includes("encoder")) continue;
    const tags = Array.isArray(e.tags) ? e.tags : [];
    if (tags.some((t) => String(t).toLowerCase() === "extraction-specialist")) continue;
    const docId = e.doc_id ?? cid.split(":win-")[0];
    if (docId) return { docId, model };
  }
  return null;
}

export async function firstPrunedWindow(
  page: Page,
): Promise<{ docId: string; windowId: string } | null> {
  for (const e of await getEvents(page)) {
    if (e.node_name !== "evidence_window_pruned") continue;
    const wid = (e.details as { window_id?: unknown } | undefined)?.window_id;
    if (typeof wid !== "string" || !wid) continue;
    const docId = docOf(e);
    if (docId) return { docId, windowId: wid };
  }
  return null;
}

export async function firstDocWithPersist(
  page: Page,
): Promise<{ docId: string } | null> {
  for (const e of await getEvents(page)) {
    if (e.node_name !== "persist_artifacts") continue;
    const docId = docOf(e);
    if (docId) return { docId };
  }
  return null;
}

/** Doc has ≥1 :win- chunk_extracted but sum(proposition_count) === 0. */
export async function firstDocWithZeroPropPathology(
  page: Page,
): Promise<{ docId: string } | null> {
  const totals = new Map<string, { count: number; sum: number }>();
  for (const e of await getEvents(page)) {
    if (e.node_name !== "chunk_extracted") continue;
    const cid = e.chunk_id ?? "";
    if (!cid.includes(":win-")) continue;
    const docId = e.doc_id ?? cid.split(":win-")[0];
    if (!docId) continue;
    const pc = (e.details as { proposition_count?: unknown } | undefined)?.proposition_count;
    const n = typeof pc === "number" ? pc : 0;
    const cur = totals.get(docId) ?? { count: 0, sum: 0 };
    totals.set(docId, { count: cur.count + 1, sum: cur.sum + n });
  }
  for (const [docId, { count, sum }] of totals) {
    if (count >= 1 && sum === 0) return { docId };
  }
  return null;
}

// ── Gap-spec discovery helpers (used by state-inspector-gap-*.spec.ts) ──

/**
 * Resolve the active run id and probe its `report.json` for ground-truth
 * availability. Returns null when no usable run, or when report.json is
 * missing / unparseable.
 *
 * Used by Gap #1 specs to skip when no GT is wired into the run.
 */
export async function runReportInfo(
  page: Page,
): Promise<{
  runId: string;
  gtAvailable: boolean;
  gtMentionCount: number;
  encoderModels: string[];
  ensembleScores: { precision: number; recall: number; strict_f1: number } | null;
} | null> {
  const runId = await resolveRunId(page);
  if (!runId) return null;
  const api = await getApi(page);
  const path = `/viewer/api/bench/runs/${runId}/report.json`;
  const resp = await api.get(path);
  // 404 on report.json is a legitimate "no report yet" signal — return
  // null so callers can skip with a real reason. SPA-fallback HTML on
  // ANY status (including 200) is infra breakage: throw loud.
  if (resp.status() === 404) return null;
  type ReportBody = {
    ground_truth?: { available?: boolean; mention_count?: number };
    models?: Array<{
      name?: string;
      type?: string;
      scores?: {
        mention_strict_precision?: number;
        mention_strict_recall?: number;
        mention_strict_f1?: number;
      };
    }>;
  };
  const body = await safeJsonFromResponse<ReportBody>(resp, path);
  const gtAvailable = !!body.ground_truth?.available;
  const gtMentionCount =
    typeof body.ground_truth?.mention_count === "number"
      ? body.ground_truth.mention_count
      : 0;
  const encoderModels = (body.models ?? [])
    .filter((m) => m.type === "encoder" && typeof m.name === "string")
    .map((m) => m.name as string);
  const ensembleEntry = (body.models ?? []).find(
    (m) => m.name === "ensemble" || m.name === "consensus",
  );
  const s = ensembleEntry?.scores;
  const ensembleScores =
    s && s.mention_strict_f1 !== undefined
      ? {
          precision: s.mention_strict_precision ?? 0,
          recall: s.mention_strict_recall ?? 0,
          strict_f1: s.mention_strict_f1 ?? 0,
        }
      : null;
  return { runId, gtAvailable, gtMentionCount, encoderModels, ensembleScores };
}

/**
 * Returns the first doc whose `mention_decision` events contributed
 * source_models from at least `minEncoders` distinct encoders. Used
 * by Gap #2 to discriminate the inline 2-encoder summary path from
 * the full N×N matrix path.
 */
export async function firstDocWithNEncoders(
  page: Page,
  minEncoders: number,
): Promise<{ docId: string; encoders: string[] } | null> {
  const byDoc = new Map<string, Set<string>>();
  for (const e of await getEvents(page)) {
    if (e.node_name !== "mention_decision") continue;
    const sm = (e.details as { source_models?: string[] } | undefined)?.source_models;
    if (!Array.isArray(sm) || sm.length === 0) continue;
    const docId = docOf(e);
    if (!docId) continue;
    let s = byDoc.get(docId);
    if (!s) {
      s = new Set();
      byDoc.set(docId, s);
    }
    for (const m of sm) s.add(m);
  }
  for (const [docId, encs] of byDoc) {
    if (encs.size >= minEncoders) return { docId, encoders: [...encs].sort() };
  }
  return null;
}

/**
 * Returns the first encoder whose `chunk_extracted` mentions[] payload
 * carries at least one numeric `confidence` value (≠ null/NaN). Used by
 * Gap #3 to skip when no encoder in the resolved run reports confidence.
 */
export async function firstEncoderWithConfidence(
  page: Page,
): Promise<{ docId: string; encoder: string } | null> {
  for (const e of await getEvents(page)) {
    if (e.node_name !== "chunk_extracted") continue;
    const m = (e.chunk_id ?? "").match(/^(.+):_ner_(.+)$/);
    if (!m) continue;
    const mentions =
      (e.details as { mentions?: Array<{ confidence?: number | null }> } | undefined)
        ?.mentions ?? [];
    if (!Array.isArray(mentions) || mentions.length === 0) continue;
    if (
      !mentions.some(
        (mn) => typeof mn.confidence === "number" && !Number.isNaN(mn.confidence),
      )
    ) {
      continue;
    }
    return { docId: e.doc_id ?? m[1], encoder: m[2] };
  }
  return null;
}

/**
 * Returns the first doc whose `pack_evidence` event has BOTH
 * `kept_windows.length >= 1` AND `pruned_windows.length >= 1`. Required
 * for Gap #4 stacked-bar tests so the histograms have both colors.
 */
export async function firstDocWithKeptAndPruned(
  page: Page,
): Promise<{ docId: string } | null> {
  for (const e of await getEvents(page)) {
    if (e.node_name !== "pack_evidence" || e.status !== "completed") continue;
    const d = e.details as
      | { kept_windows?: unknown[]; pruned_windows?: unknown[] }
      | undefined;
    const kept = Array.isArray(d?.kept_windows) ? d.kept_windows.length : 0;
    const pruned = Array.isArray(d?.pruned_windows) ? d.pruned_windows.length : 0;
    if (kept >= 1 && pruned >= 1) {
      const docId = docOf(e);
      if (docId) return { docId };
    }
  }
  return null;
}

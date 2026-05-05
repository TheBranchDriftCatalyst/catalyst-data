/**
 * Runtime-discovery helpers for State Inspector Playwright specs.
 *
 * Each helper takes a `Page`, scans the active corpus's events, and
 * returns either a typed match or `null` so callers can do
 * `test.skip(!result, "no shape match")`.
 *
 * **Fixture-mode is the only mode.** The `useFixtureCorpus(page, name)`
 * helper sets which corpus a page is reading; specs that don't call it
 * get the default ("happy-path"). The Node-side helpers here read
 * `events.ndjson` and `report.json` directly off disk — there is no
 * APIRequestContext / live-API fallback. The page-side `page.route`
 * interception (also in fixture-mode.ts) covers SPA fetches.
 *
 * Caching: events are cached per-Page in a module-scope WeakMap so 3
 * helper calls in one test make 1 disk read. Playwright tears down the
 * Page between tests so cross-test contamination is impossible. Use
 * `clearDiscoveryCache(page)` to force a re-read mid-test.
 *
 * Env overrides:
 *   PLAYWRIGHT_RUN_ID — pin a specific run id for debugging
 *   PLAYWRIGHT_MIN_RUN_AGE_MS — min age before a run is queryable (default 3min)
 */
import { type Page } from "@playwright/test";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { getCorpusForPage } from "./fixture-mode";

// ESM-safe `__dirname`: the project's tsconfig emits ES modules, so
// the CommonJS `__dirname` global isn't available at runtime.
const _DIR = dirname(fileURLToPath(import.meta.url));
const CORPORA_DIR = join(_DIR, "corpora");

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

/** Resolve the corpus root for a page. Specs that called
 *  `useFixtureCorpus(page, name)` get their pinned corpus; everything
 *  else defaults to "happy-path". */
function corpusRoot(page: Page): string {
  const name = getCorpusForPage(page) ?? "happy-path";
  const root = join(CORPORA_DIR, name);
  if (!existsSync(root)) {
    throw new Error(
      `corpus '${name}' missing: ${root}. ` +
        `Run \`python scripts/dev/seed_e2e_fixtures.py --corpus ${name}\`.`,
    );
  }
  return root;
}

/** Enumerate the active corpus's run ids by walking disk. Mirrors what
 *  `fixture-mode.ts`'s page.route handler exposes, but read directly so
 *  Node-side helpers don't have to round-trip through Chromium. */
function fixtureListRuns(page: Page): RunsListing {
  const root = corpusRoot(page);
  const runsSubdir = join(root, "runs");
  if (existsSync(runsSubdir) && statSync(runsSubdir).isDirectory()) {
    const runs = readdirSync(runsSubdir)
      .filter((d) => statSync(join(runsSubdir, d)).isDirectory())
      .sort()
      .reverse(); // newest-first
    return { runs, latest: runs[0] ?? null, live: null };
  }
  // Single-run corpus — use the same synthetic run id as fixture-mode.ts.
  const corpusName = getCorpusForPage(page) ?? "happy-path";
  const SINGLE_RUN = `2025-04-01-115500-fixture-${corpusName}`;
  return { runs: [SINGLE_RUN], latest: SINGLE_RUN, live: null };
}

function fixtureReadRunFile(
  page: Page,
  runId: string,
  filename: "report.json" | "events.ndjson",
): string | null {
  const root = corpusRoot(page);
  // single-run vs multi-run dispatch matches fixture-mode.ts/readRunFile
  const single = join(root, filename);
  if (existsSync(single) && !existsSync(join(root, "runs"))) {
    return readFileSync(single, "utf-8");
  }
  const multi = join(root, "runs", runId, filename);
  return existsSync(multi) ? readFileSync(multi, "utf-8") : null;
}

export async function listRuns(page: Page): Promise<RunsListing> {
  return fixtureListRuns(page);
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
    const text = fixtureReadRunFile(page, runId, "events.ndjson");
    if (text == null) return [] as BenchEvent[];
    const out: BenchEvent[] = [];
    for (const ln of text.split("\n")) {
      if (!ln) continue;
      try {
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
  const text = fixtureReadRunFile(page, runId, "report.json");
  if (text == null) return null;
  const body = JSON.parse(text) as ReportBody;
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

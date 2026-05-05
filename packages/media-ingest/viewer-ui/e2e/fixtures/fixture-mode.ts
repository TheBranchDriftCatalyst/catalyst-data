/**
 * Fixture-mode network interception for Playwright (CD-1qqy).
 *
 * When `PLAYWRIGHT_FIXTURE_MODE=1` is set, every spec gets a
 * `page.route('**\/viewer/api/bench/runs/**', ...)` handler installed
 * via the `useFixtureCorpus(page, name)` helper. The handler resolves
 * three URL shapes:
 *
 *   GET /viewer/api/bench/runs                       → runs index
 *   GET /viewer/api/bench/runs/<run_id>/report.json  → report.json
 *   GET /viewer/api/bench/runs/<run_id>/events       → ndjson body
 *
 * Single-corpus corpora (happy-path, diversity-composite, edge-cases)
 * pretend to be one run named `fixture-<corpus>-<YYYY-MM-DD-HHMMSS>`
 * (the timestamp makes the run id older than `MIN_RUN_AGE_MS` so
 * `resolveRunId` accepts it without override).
 *
 * Multi-run corpora (trend-window) expose every directory in `runs/`
 * verbatim — the manifest's `doc_id` is shared across them so the
 * sparkline's "this doc, history of runs" pivot resolves correctly.
 *
 * Live mode (env unset): this file's helpers are no-ops, so behavior
 * is unchanged — Playwright fetches go through to the dev server.
 */
import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Page, Route } from "@playwright/test";

const FIXTURE_MODE = process.env.PLAYWRIGHT_FIXTURE_MODE === "1";

// Repo-root-relative path to the corpora dir. Resolved off this file's
// own location so it works regardless of cwd. Project compiles to
// ES modules so the CommonJS `__dirname` global isn't available.
const _DIR = dirname(fileURLToPath(import.meta.url));
const CORPORA_DIR = join(_DIR, "corpora");

export type CorpusName =
  | "happy-path"
  | "diversity-composite"
  | "edge-cases"
  | "trend-window";

export interface CorpusInfo {
  /** Run-ids the corpus exposes (oldest first — resolveRunId scans newest-first). */
  runs: string[];
  /** Latest = newest run id (mirrors live API shape). */
  latest: string;
  /** No "live" run in fixture mode — return null to keep helpers happy. */
  live: null;
  /** Disk path to the corpus root. */
  rootDir: string;
}

/** Whether fixture mode is active. Cheap helper for callers that want
 *  to branch (e.g. discovery helpers reading a corpus file directly). */
export function isFixtureMode(): boolean {
  return FIXTURE_MODE;
}

export function corpusDir(name: CorpusName): string {
  return join(CORPORA_DIR, name);
}

/** Single-corpus run id is fixture-<name>-<frozen ts>. The frozen ts is
 *  >>3 minutes in the past so `MIN_RUN_AGE_MS` doesn't filter it out. */
const SINGLE_RUN_TS = "2025-04-01-115500";
function singleRunId(name: CorpusName): string {
  return `${SINGLE_RUN_TS}-fixture-${name}`;
}

function loadCorpus(name: CorpusName): CorpusInfo {
  const rootDir = corpusDir(name);
  if (!existsSync(rootDir)) {
    throw new Error(
      `PLAYWRIGHT_FIXTURE_MODE=1 but corpus dir does not exist: ${rootDir}. ` +
        `Run \`python scripts/dev/seed_e2e_fixtures.py --corpus ${name}\` first.`,
    );
  }
  // Multi-run corpus (trend-window) → list runs/ subdirs.
  const runsSubdir = join(rootDir, "runs");
  if (existsSync(runsSubdir) && statSync(runsSubdir).isDirectory()) {
    const runs = readdirSync(runsSubdir)
      .filter((d) => statSync(join(runsSubdir, d)).isDirectory())
      .sort(); // ISO-ish ts → lexicographic = chronological
    if (runs.length === 0) {
      throw new Error(
        `Corpus ${name} has empty runs/ dir at ${runsSubdir}. ` +
          `Re-run the seeder.`,
      );
    }
    return {
      runs,
      latest: runs[runs.length - 1],
      live: null,
      rootDir,
    };
  }
  // Single-run corpus — needs report.json + events.ndjson at root.
  const report = join(rootDir, "report.json");
  const events = join(rootDir, "events.ndjson");
  if (!existsSync(report) || !existsSync(events)) {
    throw new Error(
      `Corpus ${name} at ${rootDir} is missing report.json and/or ` +
        `events.ndjson — only manifest stub present? Run the seeder.`,
    );
  }
  const runId = singleRunId(name);
  return { runs: [runId], latest: runId, live: null, rootDir };
}

function readRunFile(
  info: CorpusInfo,
  runId: string,
  filename: "report.json" | "events.ndjson",
): string | null {
  // Single-run corpus: files at corpus root.
  if (info.runs.length === 1 && info.runs[0] === runId) {
    const single = join(info.rootDir, filename);
    return existsSync(single) ? readFileSync(single, "utf-8") : null;
  }
  // Multi-run corpus: files under runs/<runId>/.
  const multi = join(info.rootDir, "runs", runId, filename);
  return existsSync(multi) ? readFileSync(multi, "utf-8") : null;
}

/**
 * Install a route handler that intercepts `/viewer/api/bench/runs*`
 * calls and serves bytes from the named corpus. No-op when
 * `PLAYWRIGHT_FIXTURE_MODE` is not set — live API behavior preserved.
 *
 * Idempotent — calling twice on the same page replaces the handler
 * (Playwright internally tracks one route registration per glob).
 */
export async function useFixtureCorpus(
  page: Page,
  name: CorpusName,
): Promise<CorpusInfo | null> {
  if (!FIXTURE_MODE) return null;
  const info = loadCorpus(name);

  await page.route("**/viewer/api/bench/runs**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    // GET /viewer/api/bench/runs (no run id) → runs index.
    if (/\/viewer\/api\/bench\/runs\/?$/.test(path)) {
      const body = JSON.stringify({
        // Newest-first — `resolveRunId` walks `runs[]` in order.
        runs: [...info.runs].reverse(),
        latest: info.latest,
        live: info.live,
      });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body,
      });
      return;
    }

    // GET /viewer/api/bench/runs/<run>/report.json
    const reportMatch = path.match(/\/viewer\/api\/bench\/runs\/([^/]+)\/report\.json$/);
    if (reportMatch) {
      const runId = decodeURIComponent(reportMatch[1]);
      const body = readRunFile(info, runId, "report.json");
      if (body == null) {
        await route.fulfill({ status: 404, body: "report.json not in corpus" });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body });
      return;
    }

    // GET /viewer/api/bench/runs/<run>/events*  (events with optional ?limit=)
    const eventsMatch = path.match(/\/viewer\/api\/bench\/runs\/([^/]+)\/events/);
    if (eventsMatch) {
      const runId = decodeURIComponent(eventsMatch[1]);
      const body = readRunFile(info, runId, "events.ndjson");
      if (body == null) {
        await route.fulfill({ status: 404, body: "events.ndjson not in corpus" });
        return;
      }
      await route.fulfill({
        status: 200,
        // Match the live API content-type so safeNdjsonFromResponse's
        // SPA-fallback HTML guard never trips.
        contentType: "application/x-ndjson",
        body,
      });
      return;
    }

    // Anything else under /bench/runs* — let it fall through to the live
    // API. Safer than a blanket 404; the SPA may legitimately probe
    // endpoints we haven't mocked yet (e.g. /summary).
    await route.continue();
  });

  return info;
}

/**
 * Fixture-mode network interception for Playwright (CD-1qqy).
 *
 * Fixture mode is the only mode. Specs call `useFixtureCorpus(page, name)`
 * to install a `page.route('**\/viewer/api/bench/**', ...)` handler that
 * serves the named corpus's bytes off disk. Handlers resolve four URL
 * shapes:
 *
 *   GET /viewer/api/bench/runs                       → runs index
 *   GET /viewer/api/bench/runs/<run_id>/report.json  → report.json
 *   GET /viewer/api/bench/runs/<run_id>/events       → ndjson body
 *   GET /viewer/api/bench/ground-truth/<name>.json   → corpus GT (if present)
 *
 * Single-corpus corpora (happy-path, diversity-composite, edge-cases)
 * pretend to be one run named `<YYYY-MM-DD-HHMMSS>-fixture-<corpus>`
 * (the timestamp makes the run id older than `MIN_RUN_AGE_MS` so
 * `resolveRunId` accepts it without override).
 *
 * Multi-run corpora (trend-window) expose every directory in `runs/`
 * verbatim — the manifest's `doc_id` is shared across them so the
 * sparkline's "this doc, history of runs" pivot resolves correctly.
 *
 * Per-page corpus tracking: `useFixtureCorpus` records the corpus name
 * in a WeakMap so Node-side helpers in `inspector-discovery.ts` can
 * read the same corpus the route handler is serving. Specs that don't
 * call `useFixtureCorpus` get the default corpus ("happy-path").
 */
import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Page, Route } from "@playwright/test";

// Repo-root-relative path to the corpora dir. Resolved off this file's
// own location so it works regardless of cwd. Project compiles to
// ES modules so the CommonJS `__dirname` global isn't available.
const _DIR = dirname(fileURLToPath(import.meta.url));
const CORPORA_DIR = join(_DIR, "corpora");

// Per-page corpus selection. inspector-discovery's Node-side helpers
// look this up to know which corpus dir to read.
const corpusByPage = new WeakMap<Page, CorpusName>();

export function getCorpusForPage(page: Page): CorpusName | undefined {
  return corpusByPage.get(page);
}

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
      `Corpus dir does not exist: ${rootDir}. ` +
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

/** Read the corpus's ground-truth.json. The GT is corpus-level (not
 *  run-level) because the same active GT is consulted across every run
 *  in a corpus. Returns ``null`` when the corpus has no GT file —
 *  caller should serve 404 so ``useActiveGroundTruth`` resolves to []. */
function readGroundTruth(info: CorpusInfo): string | null {
  const gt = join(info.rootDir, "ground-truth.json");
  return existsSync(gt) ? readFileSync(gt, "utf-8") : null;
}

/**
 * Install route handlers that intercept `/viewer/api/bench/runs*` and
 * `/viewer/api/bench/ground-truth/*` calls and serve bytes from the
 * named corpus. Also pins the page→corpus mapping so Node-side
 * helpers (inspector-discovery) read the same corpus.
 *
 * Idempotent — calling twice on the same page replaces both the
 * handler registration and the corpus pin.
 */
export async function useFixtureCorpus(
  page: Page,
  name: CorpusName,
): Promise<CorpusInfo> {
  const info = loadCorpus(name);
  corpusByPage.set(page, name);

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

  // Separate handler for the GT endpoint — it lives outside /bench/runs,
  // and useActiveGroundTruth fetches it on every State Inspector mount.
  // Without this, the SPA falls through to the live API and Gap #1's GT
  // chips never render against the synthetic corpus (the dev GT doesn't
  // know about ``happy-path-doc-001``).
  await page.route("**/viewer/api/bench/ground-truth/**", async (route: Route) => {
    const body = readGroundTruth(info);
    if (body == null) {
      await route.fulfill({ status: 404, body: "ground-truth.json not in corpus" });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body });
  });

  return info;
}

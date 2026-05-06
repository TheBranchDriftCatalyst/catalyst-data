/**
 * E2E test data layer for Playwright. The viewer-ui dev server is the
 * only thing tests need running — every `/viewer/api/**` endpoint the
 * SPA hits is intercepted here and answered from disk corpora and
 * synthetic stubs. There is no "live mode" / "fixture mode" toggle —
 * this is just how e2e runs.
 *
 * Specs select a corpus via `useCorpus(page, name)` (auto-installed to
 * "happy-path" for every page by `coverage.ts`). The chosen corpus
 * drives the State Inspector endpoints. Non-bench endpoints (s3 explorer,
 * docs/text, runner, prompts) are answered from synthetic stubs that
 * don't depend on which corpus is active — they only need to be
 * structurally valid for the SPA to render and for assertions to land.
 *
 * Endpoint coverage:
 *
 *   /viewer/api/bench/runs                      → corpus runs index
 *   /viewer/api/bench/runs/<id>/report.json     → corpus report
 *   /viewer/api/bench/runs/<id>/events          → corpus events ndjson
 *   /viewer/api/bench/runs/<id>/responses/<c>   → synthetic SPO response stub
 *   /viewer/api/bench/ground-truth              → list of GT names
 *   /viewer/api/bench/ground-truth/<name>.json  → corpus GT file
 *   /viewer/api/bench/prompts/<hash>            → synthetic prompt stub
 *   /viewer/api/bench/runner/configs            → empty list
 *   /viewer/api/bench/runner/runs               → empty list
 *   /viewer/api/bench/runner/run                → 200 OK stub
 *   /viewer/api/docs/<docId>/text               → synthetic doc text from corpus chunks
 *   /viewer/api/domains                         → media | congress | open-leaks
 *   /viewer/api/congress/documents              → empty list
 *   /viewer/api/s3/list                         → synthetic medallion tree
 *   /viewer/api/s3/index                        → flat key index
 *   /viewer/api/s3/search                       → fuzzy match against synthetic tree
 *   /viewer/api/s3/read                         → file content stub
 *   /viewer/api/s3/raw                          → raw bytes stub
 *   /viewer/api/s3/stats                        → bucket stats
 *   /viewer/api/s3/folder_stats                 → per-folder stats
 *
 * Anything else under /viewer/api/** returns a loud 501 with a hint —
 * we'd rather fail fast and add a handler than silently fall through.
 *
 * Per-page corpus tracking lives in a WeakMap so the Node-side helpers
 * in `inspector-discovery.ts` can read the same corpus the route
 * handlers are serving.
 */
import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { Page, Route } from "@playwright/test";

const _DIR = dirname(fileURLToPath(import.meta.url));
const CORPORA_DIR = join(_DIR, "corpora");

export type CorpusName =
  | "happy-path"
  | "diversity-composite"
  | "edge-cases"
  | "trend-window";

export interface CorpusInfo {
  runs: string[];
  latest: string;
  live: null;
  rootDir: string;
}

const corpusByPage = new WeakMap<Page, CorpusName>();
export function getCorpusForPage(page: Page): CorpusName | undefined {
  return corpusByPage.get(page);
}

export function corpusDir(name: CorpusName): string {
  return join(CORPORA_DIR, name);
}

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
  const runsSubdir = join(rootDir, "runs");
  if (existsSync(runsSubdir) && statSync(runsSubdir).isDirectory()) {
    const runs = readdirSync(runsSubdir)
      .filter((d) => statSync(join(runsSubdir, d)).isDirectory())
      .sort();
    if (runs.length === 0) {
      throw new Error(`Corpus ${name} has empty runs/ dir at ${runsSubdir}.`);
    }
    return { runs, latest: runs[runs.length - 1], live: null, rootDir };
  }
  const report = join(rootDir, "report.json");
  const events = join(rootDir, "events.ndjson");
  if (!existsSync(report) || !existsSync(events)) {
    throw new Error(
      `Corpus ${name} at ${rootDir} is missing report.json and/or events.ndjson.`,
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
  if (info.runs.length === 1 && info.runs[0] === runId) {
    const single = join(info.rootDir, filename);
    return existsSync(single) ? readFileSync(single, "utf-8") : null;
  }
  const multi = join(info.rootDir, "runs", runId, filename);
  return existsSync(multi) ? readFileSync(multi, "utf-8") : null;
}

function readGroundTruth(info: CorpusInfo): string | null {
  const gt = join(info.rootDir, "ground-truth.json");
  return existsSync(gt) ? readFileSync(gt, "utf-8") : null;
}

// ── Synthetic doc text from chunk_loaded events ───────────────────────

interface DocTextChunk {
  chunk_id: string;
  index: number;
  start: number;
  end: number;
  text_preview: string;
}

interface DocTextPayload {
  doc_id: string;
  text: string;
  char_count: number;
  source: string;
  chunks: DocTextChunk[];
}

function chunkTextFromCorpus(info: CorpusInfo, docId: string): DocTextPayload {
  const runId = info.latest;
  const events = readRunFile(info, runId, "events.ndjson");
  if (!events) return { doc_id: docId, text: "", char_count: 0, source: "fixture", chunks: [] };
  const parts: string[] = [];
  const chunks: DocTextChunk[] = [];
  let cursor = 0;
  let chunkIndex = 0;
  for (const line of events.split("\n")) {
    if (!line) continue;
    try {
      const ev = JSON.parse(line) as {
        node_name?: string;
        doc_id?: string;
        chunk_id?: string;
        details?: { text?: string };
      };
      if (ev.node_name !== "chunk_loaded") continue;
      if (ev.doc_id !== docId) continue;
      const t = ev.details?.text;
      if (typeof t !== "string") continue;
      const start = cursor;
      const end = start + t.length;
      parts.push(t);
      chunks.push({
        chunk_id: ev.chunk_id ?? `${docId}:chunk-${chunkIndex}`,
        index: chunkIndex,
        start,
        end,
        text_preview: t.slice(0, 80),
      });
      // \n\n separator between chunks (matches join below)
      cursor = end + 2;
      chunkIndex += 1;
    } catch {
      /* skip malformed */
    }
  }
  const text = parts.join("\n\n");
  return { doc_id: docId, text, char_count: text.length, source: "fixture", chunks };
}

// ── Synthetic S3 tree ──────────────────────────────────────────────────
// Just enough hierarchy for s3-explorer specs to render header, pinned
// rail, navigate into prefixes, and assert sort + count badges.

interface S3Entry {
  key: string;
  size: number;
  modified: string; // ISO
}

const S3_TREE: S3Entry[] = [
  { key: "bronze/media/raw/audio_001.wav", size: 1_048_576, modified: "2025-04-01T10:00:00Z" },
  { key: "bronze/media/raw/audio_002.wav", size: 2_097_152, modified: "2025-04-01T10:05:00Z" },
  { key: "bronze/congress/raw/bill_h1234.xml", size: 32_768, modified: "2025-04-01T10:10:00Z" },
  { key: "silver/media/transcripts/audio_001.json", size: 8192, modified: "2025-04-01T11:00:00Z" },
  { key: "silver/media/chunks/audio_001/data.parquet", size: 16_384, modified: "2025-04-01T11:05:00Z" },
  { key: "silver/congress/chunks/bill_h1234/data.parquet", size: 4_096, modified: "2025-04-01T11:10:00Z" },
  { key: "gold/media_ingest/media/media_mentions/audio_001/data.parquet", size: 12_288, modified: "2025-04-01T12:00:00Z" },
  { key: "gold/media_ingest/media/media_assertions/audio_001/data.parquet", size: 4_096, modified: "2025-04-01T12:05:00Z" },
  { key: "bench/runs/2025-04-01-115500-fixture-happy-path/report.json", size: 1024, modified: "2025-04-01T11:55:00Z" },
  { key: "bench/runs/2025-04-01-115500-fixture-happy-path/events.ndjson", size: 8192, modified: "2025-04-01T11:55:00Z" },
];

function s3List(prefix: string): {
  prefix: string;
  folders: { name: string; key: string }[];
  files: { name: string; key: string; size: number; modified: string }[];
  truncated: boolean;
} {
  const norm = prefix === "" || prefix.endsWith("/") ? prefix : prefix + "/";
  const folders = new Map<string, string>();
  const files: { name: string; key: string; size: number; modified: string }[] = [];
  for (const e of S3_TREE) {
    if (!e.key.startsWith(norm)) continue;
    const rest = e.key.slice(norm.length);
    if (!rest) continue;
    const slash = rest.indexOf("/");
    if (slash >= 0) {
      const folderName = rest.slice(0, slash);
      const folderKey = norm + folderName + "/";
      folders.set(folderName, folderKey);
    } else {
      files.push({ name: rest, key: e.key, size: e.size, modified: e.modified });
    }
  }
  return {
    prefix: norm,
    folders: [...folders.entries()].map(([name, key]) => ({ name, key })),
    files,
    truncated: false,
  };
}

function s3Read(key: string): { content: string; mimeType: string; size: number } | null {
  const e = S3_TREE.find((x) => x.key === key);
  if (!e) return null;
  if (key.endsWith(".json")) {
    return {
      content: JSON.stringify({ stub: true, key, size: e.size }, null, 2),
      mimeType: "application/json",
      size: e.size,
    };
  }
  if (key.endsWith(".ndjson")) {
    return {
      content:
        JSON.stringify({ stub: true, line: 1 }) + "\n" + JSON.stringify({ stub: true, line: 2 }),
      mimeType: "application/x-ndjson",
      size: e.size,
    };
  }
  if (key.endsWith(".parquet") || key.endsWith(".wav") || key.endsWith(".xml")) {
    return { content: "", mimeType: "application/octet-stream", size: e.size };
  }
  return { content: `stub content for ${key}`, mimeType: "text/plain", size: e.size };
}

function fuzzyScore(query: string, hay: string): { score: number; indices: number[] } | null {
  if (!query) return { score: 0, indices: [] };
  const q = query.toLowerCase();
  const h = hay.toLowerCase();
  const indices: number[] = [];
  let i = 0;
  for (let j = 0; j < h.length && i < q.length; j++) {
    if (h[j] === q[i]) {
      indices.push(j);
      i++;
    }
  }
  if (i < q.length) return null;
  const score = indices.length >= 2 ? indices[indices.length - 1] - indices[0] : 0;
  return { score, indices };
}

// ── The route handler ──────────────────────────────────────────────────

export async function useCorpus(page: Page, name: CorpusName): Promise<CorpusInfo> {
  const info = loadCorpus(name);
  corpusByPage.set(page, name);

  await page.route("**/viewer/api/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();

    // ── /viewer/api/bench/runs index ───────────────────────────────
    if (/\/viewer\/api\/bench\/runs\/?$/.test(path)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          runs: [...info.runs].reverse(),
          latest: info.latest,
          live: info.live,
        }),
      });
    }

    {
      const m = path.match(/\/viewer\/api\/bench\/runs\/([^/]+)\/report\.json$/);
      if (m) {
        const runId = decodeURIComponent(m[1]);
        const body = readRunFile(info, runId, "report.json");
        if (body == null) return route.fulfill({ status: 404, body: "report missing" });
        return route.fulfill({ status: 200, contentType: "application/json", body });
      }
    }

    {
      const m = path.match(/\/viewer\/api\/bench\/runs\/([^/]+)\/events\b/);
      if (m) {
        const runId = decodeURIComponent(m[1]);
        const body = readRunFile(info, runId, "events.ndjson");
        if (body == null) return route.fulfill({ status: 404, body: "events missing" });
        return route.fulfill({
          status: 200,
          contentType: "application/x-ndjson",
          body,
        });
      }
    }

    {
      const m = path.match(/\/viewer\/api\/bench\/runs\/([^/]+)\/responses\/([^/]+)$/);
      if (m) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            chunk_id: decodeURIComponent(m[2]),
            response_text: "synthetic SPO response stub",
            tokens_in: 100,
            tokens_out: 50,
          }),
        });
      }
    }

    if (/\/viewer\/api\/bench\/ground-truth\/?$/.test(path)) {
      const hasGt = readGroundTruth(info) != null;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ground_truths: hasGt ? [{ name: "active", mention_count: 5, doc_count: 1 }] : [],
        }),
      });
    }

    if (/\/viewer\/api\/bench\/ground-truth\/[^/]+/.test(path)) {
      const body = readGroundTruth(info);
      if (body == null) return route.fulfill({ status: 404, body: "no GT" });
      return route.fulfill({ status: 200, contentType: "application/json", body });
    }

    if (path.match(/\/viewer\/api\/bench\/prompts\/[^/]+$/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          prompt_text: "synthetic SPO prompt stub — extract subject/predicate/object triples",
        }),
      });
    }

    if (path.match(/\/viewer\/api\/bench\/runner\/configs/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ configs: [] }),
      });
    }
    if (path.match(/\/viewer\/api\/bench\/runner\/runs/)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ runs: [] }),
      });
    }
    if (path.match(/\/viewer\/api\/bench\/runner\/run/) && method === "POST") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ run_id: info.latest, started: true }),
      });
    }

    {
      const m = path.match(/\/viewer\/api\/docs\/([^/]+)\/text$/);
      if (m) {
        const docId = decodeURIComponent(m[1]);
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(chunkTextFromCorpus(info, docId)),
        });
      }
    }

    if (/\/viewer\/api\/domains\/?$/.test(path)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          domains: [
            { name: "media", display_name: "Media", document_count: 1 },
            { name: "congress", display_name: "Congress", document_count: 0 },
            { name: "open-leaks", display_name: "Open Leaks", document_count: 0 },
          ],
        }),
      });
    }

    if (/\/viewer\/api\/congress\/documents/.test(path)) {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ documents: [], total: 0 }),
      });
    }

    if (/\/viewer\/api\/s3\/list\b/.test(path)) {
      const prefix = url.searchParams.get("prefix") ?? "";
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(s3List(prefix)),
      });
    }

    if (/\/viewer\/api\/s3\/index\b/.test(path)) {
      const prefix = url.searchParams.get("prefix") ?? "";
      const matched = S3_TREE.filter((e) => e.key.startsWith(prefix));
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          prefix,
          keys: matched.map((e) => ({ key: e.key, size: e.size, modified: e.modified })),
          truncated: false,
        }),
      });
    }

    if (/\/viewer\/api\/s3\/search\b/.test(path)) {
      const q = url.searchParams.get("q") ?? "";
      const prefix = url.searchParams.get("prefix") ?? "";
      const hits: { key: string; size: number; score: number; indices: number[] }[] = [];
      for (const e of S3_TREE) {
        if (!e.key.startsWith(prefix)) continue;
        const m = fuzzyScore(q, e.key);
        if (!m) continue;
        hits.push({ key: e.key, size: e.size, score: m.score, indices: m.indices });
      }
      hits.sort((a, b) => a.score - b.score);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ q, prefix, hits: hits.slice(0, 50) }),
      });
    }

    if (/\/viewer\/api\/s3\/read\b/.test(path)) {
      const key = url.searchParams.get("key") ?? "";
      const r = s3Read(key);
      if (!r) return route.fulfill({ status: 404, body: "key not in tree" });
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ key, ...r }),
      });
    }

    if (/\/viewer\/api\/s3\/raw\b/.test(path)) {
      const key = url.searchParams.get("key") ?? "";
      const r = s3Read(key);
      if (!r) return route.fulfill({ status: 404, body: "key not in tree" });
      return route.fulfill({
        status: 200,
        contentType: r.mimeType,
        body: r.content,
      });
    }

    if (/\/viewer\/api\/s3\/object\b/.test(path) && method === "DELETE") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }

    if (/\/viewer\/api\/s3\/prefix\b/.test(path) && method === "DELETE") {
      return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    }

    if (/\/viewer\/api\/s3\/stats\b/.test(path)) {
      const totalBytes = S3_TREE.reduce((s, e) => s + e.size, 0);
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          total_objects: S3_TREE.length,
          total_bytes: totalBytes,
          prefixes: ["bronze/", "silver/", "gold/", "platinum/", "bench/"],
        }),
      });
    }

    if (/\/viewer\/api\/s3\/folder_stats\b/.test(path)) {
      const prefix = url.searchParams.get("prefix") ?? "";
      const matched = S3_TREE.filter((e) => e.key.startsWith(prefix));
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          prefix,
          object_count: matched.length,
          total_bytes: matched.reduce((s, e) => s + e.size, 0),
        }),
      });
    }

    return route.fulfill({
      status: 501,
      contentType: "application/json",
      body: JSON.stringify({
        error: "e2e: unhandled API endpoint",
        path,
        method,
        hint: "Add a handler in e2e/fixtures/corpora.ts",
      }),
    });
  });

  return info;
}

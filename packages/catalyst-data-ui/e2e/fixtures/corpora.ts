/**
 * Per-spec corpus selection.
 *
 * The four corpora directories under `corpora/{happy-path,
 * diversity-composite, edge-cases, trend-window}/` are seeded into
 * moto's S3 bucket once by `playwright-global-setup.ts`. Specs that
 * need different fixture data either:
 *
 *   - hit a different run_id directly (the URL determines which
 *     corpus the SPA reads), or
 *   - call `useCorpus(page, name)` to track their corpus locally —
 *     the inspector-discovery helpers read that to find shape
 *     matches in the right ndjson on disk.
 *
 * No HTTP interception. No TS-side reimplementation of FastAPI
 * routes. The real backend is running; this module is just a
 * test-side bookmark of "which run does this page expect."
 */
import type { Page } from "@playwright/test";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const _DIR = dirname(fileURLToPath(import.meta.url));
const CORPORA_DIR = join(_DIR, "corpora");

export type CorpusName =
  | "happy-path"
  | "diversity-composite"
  | "edge-cases"
  | "trend-window";

const RUN_ID_PREFIX = "2025-04-01-115500-fixture-";

const corpusByPage = new WeakMap<Page, CorpusName>();

export function useCorpus(page: Page, name: CorpusName): void {
  corpusByPage.set(page, name);
}

export function getCorpusForPage(page: Page): CorpusName | undefined {
  return corpusByPage.get(page);
}

export function corpusDir(name: CorpusName): string {
  return join(CORPORA_DIR, name);
}

/** The S3 run_id the seeder used for a flat corpus. Multi-run corpora
 *  (trend-window) have their own timestamps; helpers fall back to
 *  list_runs for those. */
export function corpusRunId(name: CorpusName): string {
  return `${RUN_ID_PREFIX}${name}`;
}

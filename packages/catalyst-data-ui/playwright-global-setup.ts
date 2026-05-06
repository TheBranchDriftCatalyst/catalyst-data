/**
 * Playwright globalSetup (CD-1qqy).
 *
 * Fail-loud corpus presence check: fixture mode is the only mode, so
 * the corpora dir must be populated before any spec runs. If a corpus
 * is missing, abort with a message pointing at the seeder. Without
 * this, the first spec to need the corpus would error out one-test-at-
 * a-time with a less actionable failure.
 */
import { existsSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// ESM-safe `__dirname` — the project compiles tests as ES modules.
const _DIR = dirname(fileURLToPath(import.meta.url));
// Lives one level up from e2e/ now; corpora are still under e2e/fixtures/corpora.
const CORPORA_DIR = join(_DIR, "e2e", "fixtures", "corpora");

// Corpora the seeder produces. Keep in lockstep with
// `scripts/dev/seed_e2e_fixtures.py`. A subset of these may legitimately
// be stub-only at this point in CD-1qqy delivery; the presence of a
// `manifest.yaml` is the minimum bar so we don't false-positive on
// stubbed corpora.
const REQUIRED_CORPORA = [
  "happy-path",
  "trend-window",
  // diversity-composite + edge-cases land in CD-1qqy follow-up. Stubs
  // are written by the seeder; manifest-only is acceptable.
  "diversity-composite",
  "edge-cases",
] as const;

export default async function globalSetup(): Promise<void> {
  if (!existsSync(CORPORA_DIR)) {
    throw new Error(
      `Corpora dir is missing: ${CORPORA_DIR}. ` +
        `Run \`python scripts/dev/seed_e2e_fixtures.py\` first.`,
    );
  }
  const found = readdirSync(CORPORA_DIR).filter((d) =>
    statSync(join(CORPORA_DIR, d)).isDirectory(),
  );
  const missing = REQUIRED_CORPORA.filter((c) => !found.includes(c));
  if (missing.length > 0) {
    throw new Error(
      `Missing corpora: ${missing.join(", ")}. ` +
        `Found: ${found.join(", ")}. ` +
        `Run \`python scripts/dev/seed_e2e_fixtures.py\` to regenerate.`,
    );
  }
  // eslint-disable-next-line no-console
  console.log(`[e2e] corpora available: ${found.sort().join(", ")}`);
}

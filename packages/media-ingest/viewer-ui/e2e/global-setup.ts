/**
 * Playwright globalSetup (CD-1qqy).
 *
 * Two responsibilities, both gated by `PLAYWRIGHT_FIXTURE_MODE=1`:
 *
 *   1. **Fail-loud corpus presence check.** When fixture mode is set
 *      but the corpora dir is missing or empty, abort the run with a
 *      message pointing at the seeder. Without this, every spec would
 *      silently fall through to the live API (because `useFixtureCorpus`
 *      throws inside the route handler — too late, the test framework
 *      treats it as a per-test failure rather than a setup failure).
 *
 *   2. **Determinism reminder.** Print the seed and corpus inventory so
 *      CI logs make it obvious which fixture matrix was exercised.
 *
 * Live mode (env unset): this is a no-op.
 */
import { existsSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const FIXTURE_MODE = process.env.PLAYWRIGHT_FIXTURE_MODE === "1";

// ESM-safe `__dirname` — the project compiles tests as ES modules.
const _DIR = dirname(fileURLToPath(import.meta.url));
const CORPORA_DIR = join(_DIR, "fixtures", "corpora");

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
  if (!FIXTURE_MODE) {
    return;
  }
  if (!existsSync(CORPORA_DIR)) {
    throw new Error(
      `PLAYWRIGHT_FIXTURE_MODE=1 but corpora dir is missing: ${CORPORA_DIR}. ` +
        `Run \`python scripts/dev/seed_e2e_fixtures.py\` first.`,
    );
  }
  const found = readdirSync(CORPORA_DIR).filter((d) =>
    statSync(join(CORPORA_DIR, d)).isDirectory(),
  );
  const missing = REQUIRED_CORPORA.filter((c) => !found.includes(c));
  if (missing.length > 0) {
    throw new Error(
      `PLAYWRIGHT_FIXTURE_MODE=1 but missing corpora: ${missing.join(", ")}. ` +
        `Found: ${found.join(", ")}. ` +
        `Run \`python scripts/dev/seed_e2e_fixtures.py\` to regenerate.`,
    );
  }
  // eslint-disable-next-line no-console
  console.log(
    `[fixture-mode] PLAYWRIGHT_FIXTURE_MODE=1 — corpora available: ${found.sort().join(", ")}`,
  );
}

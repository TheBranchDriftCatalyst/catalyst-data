/**
 * Playwright globalSetup.
 *
 * 1. Verify the corpus directories exist on disk (fail loud if seed
 *    script hasn't been run).
 * 2. Seed moto-server's `dagster` bucket with each corpus's bench
 *    artefacts + a small synthetic medallion tree for s3-explorer
 *    specs. Runs after webServer brings moto up.
 *
 * No TS interception, no mock HTTP server. The real FastAPI talks to
 * a real boto3-compatible S3 (moto), which holds real bytes derived
 * from the corpora directories on disk. Specs hit the same code path
 * dev/prod hits.
 */
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const _DIR = dirname(fileURLToPath(import.meta.url));
const CORPORA_DIR = join(_DIR, "e2e", "fixtures", "corpora");
const SEED_SCRIPT = join(_DIR, "scripts", "seed_e2e_s3.py");

const REQUIRED_CORPORA = [
  "happy-path",
  "trend-window",
  "diversity-composite",
  "edge-cases",
] as const;

export default async function globalSetup(): Promise<void> {
  if (!existsSync(CORPORA_DIR)) {
    throw new Error(
      `Corpora dir is missing: ${CORPORA_DIR}. ` +
        `Run \`python scripts/dev/seed_e2e_fixtures.py\` from the repo root first.`,
    );
  }
  const found = readdirSync(CORPORA_DIR).filter((d) =>
    statSync(join(CORPORA_DIR, d)).isDirectory(),
  );
  const missing = REQUIRED_CORPORA.filter((c) => !found.includes(c));
  if (missing.length > 0) {
    throw new Error(
      `Missing corpora: ${missing.join(", ")}. Found: ${found.join(", ")}. ` +
        `Run \`python scripts/dev/seed_e2e_fixtures.py\` to regenerate.`,
    );
  }
  // eslint-disable-next-line no-console
  console.log(`[e2e] corpora available: ${found.sort().join(", ")}`);

  // Seed moto. The webServer block has already gated on the moto
  // healthcheck, so by the time we run, port 4566 is responsive.
  // `mise exec` matches the env the FastAPI uses so we hit the same
  // S3 endpoint with the same credentials.
  execFileSync("mise", ["exec", "--", "python", SEED_SCRIPT], {
    stdio: "inherit",
    cwd: _DIR,
  });
}

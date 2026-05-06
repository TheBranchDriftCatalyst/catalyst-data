/** Canonical test document — present in both the local seed and the
 *  talos00 deployment. Title matches across both so the smoke "Full Show"
 *  filter assertion works in either env. */
export const CANONICAL_DOC_ID = "4-9-26-full-show-video";
export const CANONICAL_DOC_TITLE = "4/9/26 Full Show Video";

/** Expected minimum counts. Tuned to the local-dev seed (5 docs); talos00
 *  has many more so this stays as a floor in both envs. Override via env
 *  if you need stricter numbers in CI:
 *    MIN_DOC_COUNT=15 npx playwright test e2e/smoke
 */
export const MIN_DOC_COUNT = parseInt(process.env.MIN_DOC_COUNT ?? "3", 10);
export const MIN_ENTITY_COUNT = parseInt(process.env.MIN_ENTITY_COUNT ?? "100", 10);
export const MIN_ASSERTION_COUNT = parseInt(process.env.MIN_ASSERTION_COUNT ?? "100", 10);
// 50 = ceil of what fits in the virtualized transcript viewport on a
// 1920×1080 page; raise via env if the test runs against a deployment
// with longer documents.
export const MIN_SEGMENT_COUNT = parseInt(process.env.MIN_SEGMENT_COUNT ?? "50", 10);

/** Timeouts */
export const DATA_LOAD_TIMEOUT = 15_000;

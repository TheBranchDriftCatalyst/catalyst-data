/**
 * ENV BLEED GUARD for the qa-verify-* / screenshot-state-inspector scripts.
 *
 * These ad-hoc verification scripts construct their own URLs (they don't
 * go through Playwright's baseURL plumbing) so they need their own guard
 * paralleling `e2e/fixtures/coverage.ts`. Behavior:
 *
 *   - If VIEWER_URL or PLAYWRIGHT_BASE_URL is set, use it (after assertion).
 *   - Otherwise default to http://localhost:5173 (Vite dev) — never a
 *     deployed talos host.
 *   - Refuse to proceed if the resolved URL's hostname is not loopback.
 *
 * Usage:
 *
 *   import { resolveLocalViewerURL, resolveLocalApiURL } from "./_local-only.mjs";
 *   const VIEWER = resolveLocalViewerURL();        // → http://localhost:5173 (default)
 *   const API    = resolveLocalApiURL();           // → http://localhost:8080 (default)
 *
 * Both helpers throw a fail-loud Error on any non-localhost override.
 *
 * NOTE: defaults are ``localhost`` (not ``127.0.0.1``). Vite's dev
 * server binds to ``localhost`` only and returns a blank page when hit
 * on the IPv4 literal — the Gap #4 verifier hit this and had to
 * manually override the URL. The env-guard's allowlist still accepts
 * 127.0.0.1 / ::1 as overrides when explicitly set.
 */

const LOCALHOST_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);

function assertLocalhost(url, varName) {
  let host;
  try {
    host = new URL(url).hostname;
  } catch {
    throw new Error(
      `[qa-verify env-guard] ${varName}=${url} is not a valid URL. ` +
        `Either unset ${varName} or set it to http://localhost:<port>.`,
    );
  }
  if (!LOCALHOST_HOSTS.has(host)) {
    throw new Error(
      `[qa-verify env-guard] refusing to run qa-verify against non-localhost ` +
        `host "${host}" (${varName}=${url}). These scripts only support the ` +
        `local dev stack (vite :5173 + FastAPI :8080). Unset ${varName} or ` +
        `point it at 127.0.0.1.`,
    );
  }
  return url;
}

/**
 * Returns the viewer (Vite SPA) base URL. Honors PLAYWRIGHT_BASE_URL or
 * VIEWER_URL env vars, asserting localhost. Defaults to http://localhost:5173.
 */
export function resolveLocalViewerURL() {
  const raw =
    process.env.PLAYWRIGHT_BASE_URL ??
    process.env.VIEWER_URL ??
    "http://localhost:5173";
  return assertLocalhost(raw, "VIEWER_URL/PLAYWRIGHT_BASE_URL");
}

/**
 * Returns the FastAPI backend URL. Honors VIEWER_API_URL env var,
 * asserting localhost. Defaults to http://localhost:8080.
 */
export function resolveLocalApiURL() {
  const raw = process.env.VIEWER_API_URL ?? "http://localhost:8080";
  return assertLocalhost(raw, "VIEWER_API_URL");
}

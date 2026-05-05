/**
 * Fail-loud fetch helpers for ad-hoc QA verifier scripts.
 *
 * Mirrors `e2e/fixtures/api-fetch.ts` for the script side. The same
 * SPA-fallback pathology exists here: when API/VIEWER point at a host
 * whose `/viewer/api/*` resolves to `index.html`, every fetched JSON is
 * HTML, JSON.parse silently fails, the script either crashes with a
 * useless SyntaxError or returns empty arrays and reports zero findings.
 * We'd rather throw with a useful message at the first hop.
 *
 * URL resolution (with localhost-only env-guard) lives in `_local-only.mjs`.
 * This file only adds the fetch wrappers.
 */

export {
  resolveLocalViewerURL,
  resolveLocalApiURL,
} from "./_local-only.mjs";

function describe(text) {
  const trimmed = text.trim();
  return trimmed.length > 80 ? `${trimmed.slice(0, 80)}…` : trimmed;
}

function looksLikeHtml(text) {
  const trimmed = text.trim();
  return (
    trimmed.startsWith("<!doctype") ||
    trimmed.startsWith("<!DOCTYPE") ||
    trimmed.startsWith("<html") ||
    trimmed.startsWith("<HTML")
  );
}

/**
 * GET + parse JSON, throwing a LOUD error on SPA-fallback HTML or non-2xx.
 * `url` is the full URL string (these scripts run outside any Playwright
 * APIRequestContext). Use this in place of `await fetch(url).then(r => r.json())`.
 */
export async function safeFetchJson(url, opts) {
  const r = await fetch(url, opts);
  const text = await r.text();
  if (!r.ok || looksLikeHtml(text)) {
    throw new Error(
      `viewer-api unreachable at ${url} — got ${r.status} ${
        r.headers.get("content-type") ?? "(no content-type)"
      }; first 80 chars: ${describe(text)}. ` +
        `Check that vite dev server (:5173) is up AND its /viewer/api/* ` +
        `proxy to :8080 is wired (vite.config.ts), or that viewer-api on :8080 ` +
        `is reachable directly.`,
    );
  }
  try {
    return JSON.parse(text);
  } catch (e) {
    throw new Error(
      `viewer-api at ${url} returned non-JSON (status=${r.status}, ` +
        `content-type=${r.headers.get("content-type") ?? "(none)"}); ` +
        `first 80 chars: ${describe(text)}; parse error: ${e.message}`,
    );
  }
}

/**
 * GET + return raw text body (for ndjson endpoints). Same loud
 * SPA-fallback guard; caller does its own line-by-line parsing.
 */
export async function safeFetchText(url, opts) {
  const r = await fetch(url, opts);
  const text = await r.text();
  if (!r.ok || looksLikeHtml(text)) {
    throw new Error(
      `viewer-api unreachable at ${url} — got ${r.status} ${
        r.headers.get("content-type") ?? "(no content-type)"
      }; first 80 chars: ${describe(text)}. ` +
        `Check that vite dev server (:5173) is up AND its /viewer/api/* ` +
        `proxy to :8080 is wired (vite.config.ts), or that viewer-api on :8080 ` +
        `is reachable directly.`,
    );
  }
  return text;
}

/**
 * Fail-loud JSON fetch helpers for e2e fixtures.
 *
 * Why: the viewer-ui dev server at :5173 proxies `/viewer/api/*` → :8080
 * (FastAPI). When the proxy is misconfigured, the dev server is down, or
 * VIEWER_URL points at a deployed host (e.g. media-explorer.talos00) that
 * SPA-falls-back any unknown route to `index.html`, our discovery fetches
 * receive `<!doctype html>...</html>` instead of JSON. JSON.parse then
 * either throws (caught + swallowed) or — worse — happens to succeed on a
 * partial parse, returning bogus data.
 *
 * Either way the symptom is the same: helpers return `null`, every
 * `test.skip(!result, ...)` fires, and a regression spec reports
 * `0 passed, 0 failed, N skipped` — completely invisible.
 *
 * These helpers detect the SPA-fallback HTML response and throw a LOUD
 * error so the test fails with a useful message instead of skipping
 * silently.
 */
import type { APIRequestContext, APIResponse, Page } from "@playwright/test";

function describe(text: string): string {
  const trimmed = text.trim();
  return trimmed.length > 80 ? `${trimmed.slice(0, 80)}…` : trimmed;
}

function looksLikeHtml(text: string): boolean {
  const trimmed = text.trim();
  return (
    trimmed.startsWith("<!doctype") ||
    trimmed.startsWith("<!DOCTYPE") ||
    trimmed.startsWith("<html") ||
    trimmed.startsWith("<HTML")
  );
}

/**
 * Read an APIResponse as JSON, but throw a LOUD error if the response
 * body is HTML (proves the dev server didn't proxy correctly) or the
 * status is non-2xx for unexpected reasons.
 *
 * Caller still owns "is empty an acceptable answer?" — this helper only
 * promises that whatever it returns IS valid JSON, never SPA HTML.
 */
export async function safeJsonFromResponse<T = unknown>(
  resp: APIResponse,
  url: string,
): Promise<T> {
  const text = await resp.text();
  if (!resp.ok() || looksLikeHtml(text)) {
    throw new Error(
      `viewer-api unreachable at ${url} — ` +
        `got ${resp.status()} ${resp.headers()["content-type"] ?? "(no content-type)"}; ` +
        `first 80 chars: ${describe(text)}. ` +
        `Check that vite dev server (:5173) is up AND its /viewer/api/* ` +
        `proxy to :8080 is wired (vite.config.ts).`,
    );
  }
  try {
    return JSON.parse(text) as T;
  } catch (e) {
    throw new Error(
      `viewer-api at ${url} returned non-JSON (status=${resp.status()}, ` +
        `content-type=${resp.headers()["content-type"] ?? "(none)"}); ` +
        `first 80 chars: ${describe(text)}; parse error: ${(e as Error).message}`,
    );
  }
}

/**
 * Read an APIResponse as ndjson (one JSON object per line). Same loud
 * SPA-fallback guard as `safeJsonFromResponse`. Returns the raw text so
 * callers can do their own per-line parsing — most discovery helpers
 * already filter malformed lines.
 */
export async function safeNdjsonFromResponse(
  resp: APIResponse,
  url: string,
): Promise<string> {
  const text = await resp.text();
  if (!resp.ok() || looksLikeHtml(text)) {
    throw new Error(
      `viewer-api unreachable at ${url} — ` +
        `got ${resp.status()} ${resp.headers()["content-type"] ?? "(no content-type)"}; ` +
        `first 80 chars: ${describe(text)}. ` +
        `Check that vite dev server (:5173) is up AND its /viewer/api/* ` +
        `proxy to :8080 is wired (vite.config.ts).`,
    );
  }
  return text;
}

/**
 * GET + JSON-parse with SPA-fallback guard, using a Playwright
 * APIRequestContext (Node-side fetch).
 */
export async function safeFetchJson<T = unknown>(
  ctx: APIRequestContext,
  path: string,
  opts?: { timeout?: number },
): Promise<T> {
  const resp = await ctx.get(path, opts);
  return safeJsonFromResponse<T>(resp, path);
}

/**
 * In-page (browser-side) variant — runs `fetch` inside `page.evaluate`
 * so the request goes through the same Vite proxy the SPA uses. Throws
 * the same loud error on SPA-fallback HTML responses.
 *
 * Note: callers that need a Node-side fetch (e.g. before `page.goto`
 * lands a real origin) should prefer `safeFetchJson` against an
 * `APIRequestContext` instead.
 */
export async function safeFetchJsonInPage<T = unknown>(
  page: Page,
  path: string,
): Promise<T> {
  const result = await page.evaluate(async (p: string) => {
    const r = await fetch(p);
    const text = await r.text();
    return {
      ok: r.ok,
      status: r.status,
      contentType: r.headers.get("content-type") ?? "",
      text,
    };
  }, path);
  if (!result.ok || looksLikeHtml(result.text)) {
    throw new Error(
      `viewer-api unreachable at ${path} (in-page) — ` +
        `got ${result.status} ${result.contentType}; ` +
        `first 80 chars: ${describe(result.text)}. ` +
        `Check that vite dev server (:5173) is up AND its /viewer/api/* ` +
        `proxy to :8080 is wired (vite.config.ts).`,
    );
  }
  try {
    return JSON.parse(result.text) as T;
  } catch (e) {
    throw new Error(
      `viewer-api at ${path} (in-page) returned non-JSON ` +
        `(status=${result.status}, content-type=${result.contentType}); ` +
        `first 80 chars: ${describe(result.text)}; ` +
        `parse error: ${(e as Error).message}`,
    );
  }
}

/**
 * In-page (browser-side) fetch for ndjson (one JSON per line).
 * Returns raw text with SPA-fallback guard. Callers handle per-line parsing.
 *
 * Note: callers that need a Node-side fetch should prefer `safeFetchJson`
 * + `safeNdjsonFromResponse` against an `APIRequestContext` instead.
 */
export async function safeFetchNdjsonInPage(
  page: Page,
  path: string,
): Promise<string> {
  const result = await page.evaluate(async (p: string) => {
    const r = await fetch(p);
    const text = await r.text();
    return {
      ok: r.ok,
      status: r.status,
      contentType: r.headers.get("content-type") ?? "",
      text,
    };
  }, path);
  if (!result.ok || looksLikeHtml(result.text)) {
    throw new Error(
      `viewer-api unreachable at ${path} (in-page) — ` +
        `got ${result.status} ${result.contentType}; ` +
        `first 80 chars: ${describe(result.text)}. ` +
        `Check that vite dev server (:5173) is up AND its /viewer/api/* ` +
        `proxy to :8080 is wired (vite.config.ts).`,
    );
  }
  return result.text;
}

/**
 * useRunIndex — react-query backed snapshot of the bench run index.
 *
 * Endpoint: ``GET /viewer/api/bench/runs``
 *
 * Differs from ``useRuns`` in two ways:
 *  - One-shot fetch, react-query handles dedup + 5-min staleTime; no
 *    polling tick. Sparkline rails on five panels can read this hook
 *    simultaneously and the network sees a single GET.
 *  - Returns the lex-sorted (newest-first) ``runs`` slice + the latest
 *    id, plus react-query's ``isLoading`` / ``error`` so callers can
 *    skip render cleanly.
 *
 * Used by ``useTrendData`` to resolve the "last N runs" window.
 */

import { useQuery } from "@tanstack/react-query";

interface RunsResponse {
  runs?: string[];
  latest?: string | null;
  live?: string | null;
}

export interface UseRunIndexResult {
  /** Newest-first, lex-sorted run ids. */
  runs: string[];
  latest: string | null;
  isLoading: boolean;
  error: Error | null;
}

export function useRunIndex(): UseRunIndexResult {
  const q = useQuery<RunsResponse>({
    queryKey: ["bench", "runs", "index"],
    queryFn: async () => {
      // Inline SPA-fallback guard (post-ENV-bleed convention parity with
      // e2e/fixtures/api-fetch.ts:safeFetchJson). When the Vite dev
      // proxy or backend is misconfigured, /viewer/api/* routes return
      // index.html instead of JSON; JSON.parse then either throws or —
      // worse — returns bogus data and silently breaks the sparkline.
      // Fail LOUD with the first 80 chars of the response body so the
      // operator sees the actual SPA HTML in the console.
      const res = await fetch("/viewer/api/bench/runs");
      const text = await res.text();
      const trimmed = text.trim();
      const looksLikeHtml =
        trimmed.startsWith("<!doctype") ||
        trimmed.startsWith("<!DOCTYPE") ||
        trimmed.startsWith("<html") ||
        trimmed.startsWith("<HTML");
      if (!res.ok || looksLikeHtml) {
        throw new Error(
          `/viewer/api/bench/runs returned non-JSON ` +
            `(status=${res.status}, content-type=${res.headers.get("content-type") ?? "(none)"}); ` +
            `first 80 chars: ${trimmed.length > 80 ? `${trimmed.slice(0, 80)}…` : trimmed}`,
        );
      }
      try {
        return JSON.parse(text) as RunsResponse;
      } catch (e) {
        throw new Error(
          `/viewer/api/bench/runs JSON parse failed (status=${res.status}); ` +
            `first 80 chars: ${trimmed.length > 80 ? `${trimmed.slice(0, 80)}…` : trimmed}; ` +
            `error: ${(e as Error).message}`,
        );
      }
    },
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  });
  // Backend already returns newest-first; defensive sort makes the
  // ordering invariant a property of this hook so callers can trust it.
  const runs = [...(q.data?.runs ?? [])].sort((a, b) => (a < b ? 1 : a > b ? -1 : 0));
  const latest = q.data?.latest ?? runs[0] ?? null;
  return {
    runs,
    latest,
    isLoading: q.isLoading,
    error: (q.error as Error | null) ?? null,
  };
}

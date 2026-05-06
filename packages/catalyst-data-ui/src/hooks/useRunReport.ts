/**
 * useRunReport — react-query backed fetch for the per-run benchmark report.
 *
 * Endpoint: ``GET /viewer/api/bench/runs/<run_id>/report.json``
 *
 * The report contains a single global ``models[]`` array; each entry has
 * ``scores`` with ``mention_strict_*`` / ``mention_relaxed_*`` fields. The
 * scoring is run-level (aggregated across all docs in the run), not per-doc:
 * compute_model_scores() in tests/benchmark_harness.py concatenates every
 * doc's mentions before scoring. So the F1 strip in the State Inspector
 * is a run-level number, not a doc-level number — surfaced here so the
 * UI doesn't have to know the indexing scheme.
 *
 * Companion ``useActiveGroundTruth`` fetches the GT mention list for the
 * "active" GT name — used by the consensus chip-marking pass. The two
 * queries are split because the report.json is a single fetch worth
 * caching aggressively, while the GT file is a separate (much larger)
 * S3 object that not every State Inspector view needs.
 */

import { useQuery } from "@tanstack/react-query";

// ── Narrow types for fields the UI consumes ─────────────────────────────────

/** Per-model scores subset — full schema lives in
 *  tests/shared/extraction_scoring.py::compute_model_scores. */
export interface RunModelScores {
  mention_strict_precision: number;
  mention_strict_recall: number;
  mention_strict_f1: number;
  mention_relaxed_precision: number;
  mention_relaxed_recall: number;
  mention_relaxed_f1: number;
  mention_type_accuracy: number;
}

export interface RunModelEntry {
  name: string;
  type: string;
  scores?: Partial<RunModelScores>;
  /** Free-form per-model stats bag emitted by the bench harness — fields
   *  vary by model type (encoders carry mention_count, SPO models carry
   *  assertion_count + chunk_count, persist carries duration_s). Read
   *  defensively at the call site (Gap #8 useTrendData uses unknown
   *  casts to extract specific fields). */
  stats?: Record<string, unknown>;
}

export interface RunReportGroundTruth {
  available: boolean;
  reference_model?: string;
  manually_reviewed?: boolean;
  mention_count?: number;
}

export interface RunReport {
  generated_at: string;
  model_count: number;
  model_names: string[];
  ground_truth?: RunReportGroundTruth;
  models: RunModelEntry[];
}

// ── GT mention shape (subset) ───────────────────────────────────────────────

/** Per-mention shape used by the chip-matching code. The bench route
 *  ``/viewer/api/bench/ground-truth/<name>.json`` returns chunks→mentions;
 *  we flatten and only keep the canonical-match fields. */
export interface GtMention {
  text: string;
  mention_type?: string;
  span_start: number | null;
  span_end: number | null;
  doc_id?: string;
  chunk_id?: string;
}

interface GtChunk {
  doc_id?: string;
  chunk_id?: string;
  legacy_chunk_id?: string;
  mentions?: Array<{
    text: string;
    mention_type?: string;
    type?: string;
    span_start?: number | null;
    span_end?: number | null;
  }>;
}

interface GtFile {
  chunks?: GtChunk[];
  total_mentions?: number;
}

// ── Hooks ────────────────────────────────────────────────────────────────────

/** Fetch the per-run report.json. Returns ``null`` data when ``runId`` is
 *  null (no run pinned + no latest yet). */
export function useRunReport(runId: string | null) {
  return useQuery<RunReport | null>({
    queryKey: ["bench", "report", runId],
    enabled: !!runId,
    queryFn: async () => {
      if (!runId) return null;
      const res = await fetch(`/viewer/api/bench/runs/${encodeURIComponent(runId)}/report.json`);
      if (!res.ok) {
        // 404s are common for in-flight runs — treat as "no report yet".
        if (res.status === 404) return null;
        throw new Error(`report.json failed: ${res.status}`);
      }
      return (await res.json()) as RunReport;
    },
    refetchOnWindowFocus: false,
    staleTime: 5 * 60_000,
  });
}

/** Fetch the "active" GT and flatten it to a list of mentions. Returns
 *  ``[]`` when the GT exists but is empty (i.e. the active GT has no
 *  mentions populated yet — common before the manual review pass).
 *
 *  Reads the active GT name from URL param `?gt=` or localStorage `viewer:activeGt`,
 *  falling back to the server's symbolic "active" if neither is set. */
export function useActiveGroundTruth() {
  // Get active GT name from URL, localStorage, or fall back to "active"
  const getActiveGtPath = () => {
    const params = new URLSearchParams(window.location.search);
    const urlGt = params.get("gt");
    if (urlGt) return urlGt;
    const storedGt = window.localStorage.getItem("viewer:activeGt");
    if (storedGt) return storedGt;
    return "active";
  };

  return useQuery<GtMention[]>({
    queryKey: ["bench", "ground-truth", getActiveGtPath()],
    queryFn: async () => {
      const gtPath = getActiveGtPath();
      const res = await fetch(`/viewer/api/bench/ground-truth/${encodeURIComponent(gtPath)}.json`);
      if (!res.ok) {
        if (res.status === 404) return [];
        throw new Error(`${gtPath}.json failed: ${res.status}`);
      }
      const body = (await res.json()) as GtFile;
      const out: GtMention[] = [];
      for (const ch of body.chunks ?? []) {
        const docId = ch.doc_id;
        const chunkId = ch.chunk_id ?? ch.legacy_chunk_id;
        for (const m of ch.mentions ?? []) {
          out.push({
            text: m.text,
            mention_type: m.mention_type ?? m.type,
            span_start: m.span_start ?? null,
            span_end: m.span_end ?? null,
            doc_id: docId,
            chunk_id: chunkId,
          });
        }
      }
      return out;
    },
    refetchOnWindowFocus: false,
    staleTime: 5 * 60_000,
  });
}

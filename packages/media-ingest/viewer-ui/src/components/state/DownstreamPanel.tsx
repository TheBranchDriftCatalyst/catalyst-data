/**
 * DownstreamPanel — bottom-pane addon for the `persist` graph node.
 *
 * Closes the State Inspector lineage loop: the `document` node hosts
 * `<UpstreamPanel>` (where the doc's chunks came from in Dagster);
 * the `persist` node hosts this component (where the consensus +
 * SPO outputs landed). Visually mirrors `<UpstreamPanel>`'s card
 * layout so the operator's eye treats the two as a matched pair —
 * but the components are deliberately independent so they can evolve
 * separately (different upstream/downstream metadata, different
 * deep-link semantics).
 *
 * Source data: a single `persist_artifacts` event per doc carrying
 * the canonical schema documented in
 * `libs/catalyst-exgraph/src/catalyst_exgraph/nodes/persist.py`. Every
 * field is optional from the panel's perspective — pre-schema events
 * that ship only `status: "completed"` fall through to the empty
 * state, identical to "no persist event observed".
 *
 * testids:
 *   - root:               `downstream-panel`
 *   - per-card:           `downstream-card-{asset_key}` with
 *                         `data-status="ok|error"`
 *   - per-card deep link: `downstream-dagster-link-{asset_key}`
 *   - empty state:        `downstream-empty`
 */

import { useMemo } from "react";

import type { RunEvent } from "@/types/benchmark";

interface Props {
  events: RunEvent[];
  docId: string;
}

interface DownstreamCard {
  assetKey: string;
  outputPath: string | null;
  rowCount: number | null;
  sizeBytes: number | null;
  status: "ok" | "error";
  reason: string | null;
}

interface DownstreamSummary {
  cards: DownstreamCard[];
  dagsterRunId: string | null;
  materializedAt: string | null;
  startedAt: string | null;
  completedAt: string | null;
  topLevelStatus: string | null;
}

const DAGSTER_BASE_URL = "http://localhost:3000";

function formatBytes(n: number | null): string {
  if (n == null || !isFinite(n)) return "—";
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatNumber(n: number | null): string {
  if (n == null || !isFinite(n)) return "—";
  return n.toLocaleString();
}

function formatTimestamp(ts: string | null): string | null {
  if (!ts) return null;
  // Accept ISO strings; fall back to raw if not parseable.
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toISOString().slice(0, 19).replace("T", " ");
}

/** Pull a string→number map out of a details bag, ignoring non-numeric
 *  values so a stray null on one asset doesn't kill the whole map. */
function asNumberMap(raw: unknown): Record<string, number> {
  if (!raw || typeof raw !== "object") return {};
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === "number" && Number.isFinite(v)) out[k] = v;
  }
  return out;
}

function asStringMap(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== "object") return {};
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === "string") out[k] = v;
  }
  return out;
}

/** Per-asset status payload. Tolerates both `{status, reason}` objects
 *  and bare string values ("ok"/"error"). */
function readPerAssetStatus(
  raw: unknown,
): Record<string, { status: "ok" | "error"; reason: string | null }> {
  if (!raw || typeof raw !== "object") return {};
  const out: Record<string, { status: "ok" | "error"; reason: string | null }> = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (typeof v === "string") {
      out[k] = { status: v === "error" ? "error" : "ok", reason: null };
    } else if (v && typeof v === "object") {
      const obj = v as Record<string, unknown>;
      const status = obj.status === "error" ? "error" : "ok";
      const reason = typeof obj.reason === "string" ? obj.reason : null;
      out[k] = { status, reason };
    }
  }
  return out;
}

function buildSummary(events: RunEvent[], docId: string): DownstreamSummary {
  // Find the latest persist_artifacts event for this doc — the harness +
  // future Dagster persist op both emit at most one per (doc, run), but
  // multiple cache replays in a single session may stack. Take the
  // newest by ts so the panel reflects the most recent state.
  const persistEvents = events
    .filter((e) => e.node_name === "persist_artifacts" && e.doc_id === docId)
    .sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0));

  const latest = persistEvents[0];
  if (!latest) {
    return {
      cards: [],
      dagsterRunId: null,
      materializedAt: null,
      startedAt: null,
      completedAt: null,
      topLevelStatus: null,
    };
  }
  const d = (latest.details ?? {}) as Record<string, unknown>;

  const outputPaths = asStringMap(d.output_paths);
  const rowCounts = asNumberMap(d.row_counts);
  const sizeBytes = asNumberMap(d.size_bytes);
  const perAssetStatus = readPerAssetStatus(d.per_asset_status);

  // Canonical iteration order: `details.asset_keys` if present, else
  // sorted union of any keys we know about. ``output_paths`` keys come
  // first (they're the actual emit), `per_asset_status` keys can extend
  // for assets that errored before the path was assigned.
  const explicitKeys = Array.isArray(d.asset_keys)
    ? (d.asset_keys as unknown[]).filter((k): k is string => typeof k === "string")
    : null;
  const keyUnion = new Set<string>(explicitKeys ?? []);
  for (const k of Object.keys(outputPaths)) keyUnion.add(k);
  for (const k of Object.keys(rowCounts)) keyUnion.add(k);
  for (const k of Object.keys(sizeBytes)) keyUnion.add(k);
  for (const k of Object.keys(perAssetStatus)) keyUnion.add(k);
  const orderedKeys =
    explicitKeys && explicitKeys.length > 0
      ? Array.from(keyUnion).sort((a, b) => {
          const ai = explicitKeys.indexOf(a);
          const bi = explicitKeys.indexOf(b);
          // Items not in the explicit list go to the end, sorted lex.
          if (ai === -1 && bi === -1) return a < b ? -1 : a > b ? 1 : 0;
          if (ai === -1) return 1;
          if (bi === -1) return -1;
          return ai - bi;
        })
      : Array.from(keyUnion).sort();

  // Convenience sentinels — emitted at the top level for the legacy
  // shape, but also bubble up into the per-asset card if we can match
  // the asset_key name.
  const mentionsWritten =
    typeof d.mentions_written === "number" ? (d.mentions_written as number) : null;
  const propsWritten =
    typeof d.propositions_written === "number" ? (d.propositions_written as number) : null;

  const cards: DownstreamCard[] = orderedKeys.map((assetKey) => {
    const lower = assetKey.toLowerCase();
    let rowCount: number | null = rowCounts[assetKey] ?? null;
    if (rowCount == null) {
      // Heuristic fallback: if the canonical fields are populated and
      // the asset name suggests mentions vs propositions, surface them.
      if (mentionsWritten != null && /mention/.test(lower)) rowCount = mentionsWritten;
      else if (propsWritten != null && /prop|assertion/.test(lower)) rowCount = propsWritten;
    }
    const status = perAssetStatus[assetKey] ?? { status: "ok" as const, reason: null };
    return {
      assetKey,
      outputPath: outputPaths[assetKey] ?? null,
      rowCount,
      sizeBytes: sizeBytes[assetKey] ?? null,
      status: status.status,
      reason: status.reason,
    };
  });

  const dagsterRunId = typeof d.dagster_run_id === "string" ? (d.dagster_run_id as string) : null;
  const startedAt = typeof d.started_at === "string" ? (d.started_at as string) : null;
  const completedAt = typeof d.completed_at === "string" ? (d.completed_at as string) : null;
  const materializedAt =
    typeof d.materialized_at === "string"
      ? (d.materialized_at as string)
      : (completedAt ?? latest.ts ?? null);

  return {
    cards,
    dagsterRunId,
    materializedAt,
    startedAt,
    completedAt,
    topLevelStatus: latest.status ?? null,
  };
}

export function DownstreamPanel({ events, docId }: Props) {
  const summary = useMemo(() => buildSummary(events, docId), [events, docId]);

  if (summary.cards.length === 0) {
    return (
      <div data-testid="downstream-panel" className="p-3 font-mono">
        <div className="text-zinc-500 text-[10px] uppercase tracking-wide mb-1">
          dagster lineage · downstream
        </div>
        <div data-testid="downstream-empty" className="text-zinc-600 text-[10px]">
          persist not yet observed for this doc
        </div>
      </div>
    );
  }

  const n = summary.cards.length;
  return (
    <div data-testid="downstream-panel" className="p-3 font-mono space-y-2">
      <div className="text-zinc-500 text-[10px] uppercase tracking-wide">
        dagster lineage · downstream · {n} asset{n === 1 ? "" : "s"} materialized
      </div>
      <div className="space-y-1.5">
        {summary.cards.map((card) => (
          <DownstreamCardView
            key={card.assetKey}
            card={card}
            dagsterRunId={summary.dagsterRunId}
            materializedAt={summary.materializedAt}
          />
        ))}
      </div>
    </div>
  );
}

function DownstreamCardView({
  card,
  dagsterRunId,
  materializedAt,
}: {
  card: DownstreamCard;
  dagsterRunId: string | null;
  materializedAt: string | null;
}) {
  const isError = card.status === "error";
  const wrapClass = isError
    ? "rounded border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 space-y-1"
    : "rounded border border-white/10 bg-surface-1 px-2 py-1.5 space-y-1";
  const cardTestId = `downstream-card-${card.assetKey}`;
  const linkTestId = `downstream-dagster-link-${card.assetKey}`;
  const formattedTs = formatTimestamp(materializedAt);

  return (
    <div data-testid={cardTestId} data-status={card.status} className={wrapClass}>
      {/* Row 1: asset_key (cyan) + dagster_run_id deep link */}
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11px]">
        <span className="text-cyan-300 truncate">{card.assetKey}</span>
        {dagsterRunId && (
          <a
            data-testid={linkTestId}
            href={`${DAGSTER_BASE_URL}/runs/${dagsterRunId}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-cyan-300 hover:text-cyan-200 hover:underline text-[10px]"
            title="Open Dagster run"
          >
            run: {dagsterRunId.slice(0, 8)}…
          </a>
        )}
      </div>

      {/* Row 2: row count + size + materialized_at */}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-zinc-500 text-[10px]">
        {card.rowCount != null && <span>{formatNumber(card.rowCount)} rows</span>}
        {card.sizeBytes != null && (
          <>
            <span className="text-zinc-700">·</span>
            <span>{formatBytes(card.sizeBytes)}</span>
          </>
        )}
        {formattedTs && (
          <>
            <span className="text-zinc-700">·</span>
            <span title={materializedAt ?? ""}>materialized {formattedTs}</span>
          </>
        )}
      </div>

      {/* Row 3: S3 path (zinc-mono) — empty falls through silently. */}
      {card.outputPath && (
        <div className="text-zinc-400 text-[10px] truncate" title={card.outputPath}>
          {card.outputPath}
        </div>
      )}

      {/* Failure footer — amber. ``data-status="error"`` on the card lets
       *  specs assert the partial-failure path without grepping textcontent. */}
      {isError && (
        <div className="text-amber-300 text-[10px]">failed — {card.reason ?? "unknown error"}</div>
      )}
    </div>
  );
}

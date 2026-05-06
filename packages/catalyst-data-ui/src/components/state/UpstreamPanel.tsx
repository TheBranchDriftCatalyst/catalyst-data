/**
 * UpstreamPanel — bottom-pane addon for the `document` graph node.
 *
 * Surfaces the Dagster lineage for the selected doc by joining two
 * upstream event streams that already flow through the bench audit log:
 *
 *   1. ``source=dagster, node_name=asset_materialized`` — emitted from
 *      ``dagster_io.run_status_sensor`` per Dagster materialization
 *      (CD-7pr0). ``details = {asset_key, partition_key, dagster_run_id,
 *      description, metadata, ts}``. Failed runs emit ``asset_missing``
 *      with ``details.reason``.
 *
 *   2. ``source=harness, node_name=asset_read`` — emitted from
 *      ``tests/shared/medallion.py:_emit_asset_read_events`` whenever
 *      ``load_chunks()`` reads a chunks asset (CD-d7tb).
 *      ``details = {asset_key, partition_key, dagster_run_id, layer,
 *      output_path, row_count, size_bytes, schema, upstream_assets,
 *      materialized_at}``.
 *
 * The join key is ``dagster_run_id`` — the harness's read of
 * ``media_chunks/demo-video`` lines up with the Dagster materialization
 * that produced the bytes it loaded. We render one card per
 * ``(asset_key, dagster_run_id)`` pair, latest-first, with the
 * materialization summary on top and any subsequent reads stacked under
 * it.
 */

import { useMemo } from "react";

import type { RunEvent } from "@/types/benchmark";

interface Props {
  events: RunEvent[];
  docId: string;
}

interface UpstreamCard {
  /** Stable join key — the (asset_key, dagster_run_id) pair. */
  key: string;
  assetKey: string;
  dagsterRunId: string | null;
  partitionKey: string | null;
  /** Latest of (materialized.ts, read.materialized_at, event.ts). */
  sortTs: string;
  /** Defined when a Dagster materialization landed for this pair. */
  materialized: {
    ts: string;
    description: string | null;
    metadata: Record<string, unknown>;
    rowCount: number | null;
    sizeBytes: number | null;
    layer: string | null;
    schema: SchemaField[] | null;
    upstreamAssets: string[];
  } | null;
  /** Defined when the harness reported the planned-but-missing case. */
  missing: {
    reason: string;
  } | null;
  /** Harness reads of this asset within the run. */
  reads: ReadRow[];
}

interface SchemaField {
  name: string;
  type: string;
}

interface ReadRow {
  ts: string;
  outputPath: string | null;
  rowCount: number | null;
  sizeBytes: number | null;
  layer: string | null;
  upstreamAssets: string[];
  materializedAt: string | null;
  schema: SchemaField[] | null;
}

const DAGSTER_BASE_URL = "http://localhost:3000";

function formatBytes(n: number | null): string {
  if (n == null || !isFinite(n)) return "—";
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KiB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)}MiB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)}GiB`;
}

function formatNumber(n: number | null): string {
  if (n == null || !isFinite(n)) return "—";
  return n.toLocaleString();
}

/** Coerce whatever shape the parquet round-trip handed us into a list of
 *  ``{name, type}`` rows. The harness sidecar writes a list-of-objects;
 *  Dagster materialization metadata sometimes has a TableSchema dict. */
function _normalizeSchema(raw: unknown): SchemaField[] | null {
  if (!raw) return null;
  if (Array.isArray(raw)) {
    const out: SchemaField[] = [];
    for (const f of raw) {
      if (f && typeof f === "object") {
        const obj = f as Record<string, unknown>;
        const name = obj.name ?? obj.column ?? obj.field;
        const type = obj.type ?? obj.dtype ?? obj.data_type ?? "?";
        if (typeof name === "string") {
          out.push({ name, type: String(type) });
        }
      } else if (typeof f === "string") {
        out.push({ name: f, type: "?" });
      }
    }
    return out.length > 0 ? out : null;
  }
  if (typeof raw === "object") {
    const obj = raw as Record<string, unknown>;
    // TableSchema-ish: { columns: [{name, type}, …] }
    if (Array.isArray(obj.columns)) {
      return _normalizeSchema(obj.columns);
    }
    // Plain map: { col: type, … }
    const out: SchemaField[] = [];
    for (const [name, type] of Object.entries(obj)) {
      out.push({ name, type: String(type) });
    }
    return out.length > 0 ? out : null;
  }
  return null;
}

function _stringList(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((x) => typeof x === "string") as string[];
}

export function UpstreamPanel({ events, docId }: Props) {
  const cards = useMemo<UpstreamCard[]>(() => {
    // Filter to dagster + harness lineage events for this doc. ``__run__``
    // events (run-level summaries) aren't doc-scoped so they never match.
    const upstream = events.filter(
      (e) =>
        e.doc_id === docId &&
        ((e.source === "dagster" &&
          (e.node_name === "asset_materialized" || e.node_name === "asset_missing")) ||
          (e.source === "harness" && e.node_name === "asset_read")),
    );

    // Group by (asset_key, dagster_run_id). The pair uniquely identifies a
    // single materialization output — re-runs of the same partition produce
    // distinct dagster_run_ids and therefore distinct cards.
    const byKey = new Map<string, UpstreamCard>();

    for (const e of upstream) {
      const d = (e.details ?? {}) as Record<string, unknown>;
      const assetKey = String(d.asset_key ?? "?");
      const dagsterRunId = (d.dagster_run_id as string | null | undefined) ?? null;
      const partitionKey = (d.partition_key as string | null | undefined) ?? null;
      const key = `${assetKey}|${dagsterRunId ?? "_"}`;
      let card = byKey.get(key);
      if (!card) {
        card = {
          key,
          assetKey,
          dagsterRunId,
          partitionKey,
          sortTs: e.ts,
          materialized: null,
          missing: null,
          reads: [],
        };
        byKey.set(key, card);
      } else {
        // Pick latest ts as the sort handle.
        if (e.ts > card.sortTs) card.sortTs = e.ts;
        if (partitionKey && !card.partitionKey) card.partitionKey = partitionKey;
      }

      if (e.source === "dagster" && e.node_name === "asset_materialized") {
        card.materialized = {
          ts: (d.ts as string | undefined) ?? e.ts,
          description: (d.description as string | null | undefined) ?? null,
          metadata: (d.metadata as Record<string, unknown> | undefined) ?? {},
          rowCount:
            typeof (d.metadata as Record<string, unknown> | undefined)?.row_count === "number"
              ? ((d.metadata as Record<string, unknown>).row_count as number)
              : null,
          sizeBytes:
            typeof (d.metadata as Record<string, unknown> | undefined)?.size_bytes === "number"
              ? ((d.metadata as Record<string, unknown>).size_bytes as number)
              : null,
          layer:
            typeof (d.metadata as Record<string, unknown> | undefined)?.layer === "string"
              ? ((d.metadata as Record<string, unknown>).layer as string)
              : null,
          schema: _normalizeSchema((d.metadata as Record<string, unknown> | undefined)?.schema),
          upstreamAssets: _stringList(
            (d.metadata as Record<string, unknown> | undefined)?.upstream_assets,
          ),
        };
      } else if (e.source === "dagster" && e.node_name === "asset_missing") {
        card.missing = {
          reason: String(d.reason ?? "planned but not materialized"),
        };
      } else if (e.source === "harness" && e.node_name === "asset_read") {
        card.reads.push({
          ts: e.ts,
          outputPath: (d.output_path as string | null | undefined) ?? null,
          rowCount: typeof d.row_count === "number" ? (d.row_count as number) : null,
          sizeBytes: typeof d.size_bytes === "number" ? (d.size_bytes as number) : null,
          layer: (d.layer as string | null | undefined) ?? null,
          upstreamAssets: _stringList(d.upstream_assets),
          materializedAt: (d.materialized_at as string | null | undefined) ?? null,
          schema: _normalizeSchema(d.schema),
        });
      }
    }

    // Hoist schema/row_count/size from the read row when the materialized
    // event didn't carry that metadata. The harness sidecar is the
    // canonical source for size_bytes; Dagster metadata is the canonical
    // source for the materialization timestamp.
    for (const card of byKey.values()) {
      const read = card.reads[0];
      if (card.materialized && read) {
        if (card.materialized.rowCount == null) card.materialized.rowCount = read.rowCount;
        if (card.materialized.sizeBytes == null) card.materialized.sizeBytes = read.sizeBytes;
        if (card.materialized.layer == null) card.materialized.layer = read.layer;
        if (!card.materialized.schema) card.materialized.schema = read.schema;
        if (card.materialized.upstreamAssets.length === 0)
          card.materialized.upstreamAssets = read.upstreamAssets;
      }
    }

    const out = [...byKey.values()];
    // Latest first by sortTs.
    out.sort((a, b) => (a.sortTs < b.sortTs ? 1 : a.sortTs > b.sortTs ? -1 : 0));
    return out;
  }, [events, docId]);

  if (cards.length === 0) {
    return (
      <div data-testid="upstream-panel" className="p-3 font-mono">
        <div className="text-zinc-500 text-[10px] uppercase tracking-wide mb-1">
          dagster lineage
        </div>
        <div className="text-zinc-600 text-[10px]">
          no Dagster materialization events for this doc yet — make sure run_status_sensor is
          registered for this code location.
        </div>
      </div>
    );
  }

  const materializationCount = cards.filter((c) => c.materialized).length;

  return (
    <div data-testid="upstream-panel" className="p-3 font-mono space-y-2">
      <div className="text-zinc-500 text-[10px] uppercase tracking-wide">
        dagster lineage · {materializationCount} materialization
        {materializationCount === 1 ? "" : "s"}
      </div>
      <div className="space-y-1.5">
        {cards.map((card) => (
          <UpstreamCardView key={card.key} card={card} />
        ))}
      </div>
    </div>
  );
}

function UpstreamCardView({ card }: { card: UpstreamCard }) {
  const isMissing = card.missing !== null && card.materialized === null;
  const wrapClass = isMissing
    ? "rounded border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 space-y-1"
    : "rounded border border-white/10 bg-surface-1 px-2 py-1.5 space-y-1";
  const cardTestId = `upstream-card-${card.assetKey}-${card.dagsterRunId ?? "_"}`;

  const m = card.materialized;
  const layer = m?.layer ?? card.reads[0]?.layer ?? null;
  const rowCount = m?.rowCount ?? card.reads[0]?.rowCount ?? null;
  const sizeBytes = m?.sizeBytes ?? card.reads[0]?.sizeBytes ?? null;
  const upstreamAssets = m?.upstreamAssets?.length
    ? m.upstreamAssets
    : (card.reads[0]?.upstreamAssets ?? []);
  const schema = m?.schema ?? card.reads[0]?.schema ?? null;
  const materializedAt = m?.ts ?? card.reads[0]?.materializedAt ?? null;

  return (
    <div data-testid={cardTestId} className={wrapClass}>
      {/* Row 1: asset_key (cyan) + partition + dagster_run_id deep link */}
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11px]">
        <span className="text-cyan-300 truncate">{card.assetKey}</span>
        {card.partitionKey && (
          <span className="text-zinc-400 text-[10px]">
            partition: <span className="text-zinc-300">{card.partitionKey}</span>
          </span>
        )}
        {card.dagsterRunId && (
          <a
            href={`${DAGSTER_BASE_URL}/runs/${card.dagsterRunId}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-cyan-300 hover:underline text-[10px]"
            title="Open Dagster run"
          >
            run: {card.dagsterRunId.slice(0, 8)}…
          </a>
        )}
      </div>

      {/* Row 2: metrics */}
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-zinc-500 text-[10px]">
        {rowCount != null && <span>{formatNumber(rowCount)} rows</span>}
        {sizeBytes != null && (
          <>
            <span className="text-zinc-700">·</span>
            <span>{formatBytes(sizeBytes)}</span>
          </>
        )}
        {layer && (
          <>
            <span className="text-zinc-700">·</span>
            <span>{layer}</span>
          </>
        )}
        {materializedAt && (
          <>
            <span className="text-zinc-700">·</span>
            <span title={materializedAt}>
              materialized {String(materializedAt).slice(0, 19).replace("T", " ")}
            </span>
          </>
        )}
        {card.reads.length > 0 && (
          <>
            <span className="text-zinc-700">·</span>
            <span>
              {card.reads.length} read{card.reads.length === 1 ? "" : "s"}
            </span>
          </>
        )}
      </div>

      {/* Missing reason */}
      {isMissing && card.missing && (
        <div className="text-amber-300 text-[10px]">missing — {card.missing.reason}</div>
      )}

      {/* Description */}
      {m?.description && (
        <div className="text-zinc-400 text-[10px] truncate" title={m.description}>
          {m.description}
        </div>
      )}

      {/* Upstream asset chain */}
      {upstreamAssets.length > 0 && (
        <div className="flex flex-wrap items-baseline gap-1 text-[10px]">
          <span className="text-zinc-600">upstream:</span>
          {upstreamAssets.map((a, i) => (
            <span key={`${a}:${i}`} className="text-zinc-400">
              {a}
              {i < upstreamAssets.length - 1 ? <span className="text-zinc-700"> → </span> : null}
            </span>
          ))}
        </div>
      )}

      {/* Schema (collapsible) */}
      {schema && schema.length > 0 && (
        <details className="text-[10px]">
          <summary className="cursor-pointer text-zinc-500 hover:text-zinc-300 select-none">
            schema ({schema.length} field{schema.length === 1 ? "" : "s"})
          </summary>
          <div className="mt-1 rounded border border-white/5 max-h-40 overflow-y-auto">
            <table className="w-full text-[10px]">
              <thead className="sticky top-0 bg-surface-1/80 backdrop-blur">
                <tr className="text-zinc-500 text-left">
                  <th className="px-2 py-0.5 font-normal">column</th>
                  <th className="px-2 py-0.5 font-normal">type</th>
                </tr>
              </thead>
              <tbody>
                {schema.map((f, i) => (
                  <tr key={`${f.name}:${i}`} className="border-t border-white/5">
                    <td className="px-2 py-0.5 text-zinc-300 truncate max-w-[180px]">{f.name}</td>
                    <td className="px-2 py-0.5 text-violet-300">{f.type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {/* Read output paths — surface so the operator can sanity check the
       *  exact S3 URI the harness pulled, useful when debugging "wrong
       *  partition loaded". */}
      {card.reads.length > 0 && (
        <details className="text-[10px]">
          <summary className="cursor-pointer text-zinc-500 hover:text-zinc-300 select-none">
            harness reads ({card.reads.length})
          </summary>
          <div className="mt-1 space-y-0.5">
            {card.reads.map((r, i) => (
              <div key={`${r.ts}:${i}`} className="flex gap-2 text-zinc-400">
                <span className="text-zinc-600 w-16 flex-shrink-0">{r.ts.slice(11, 19)}</span>
                <span className="truncate" title={r.outputPath ?? ""}>
                  {r.outputPath ?? "—"}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

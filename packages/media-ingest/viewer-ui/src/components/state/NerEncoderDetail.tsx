/**
 * NerEncoderDetail — right-pane panel for a single encoder node in the
 * StateInspector V2 graph. Shows that encoder's mentions for the
 * selected doc, plus duration / status from `ner_encoder_completed`.
 */

import { useMemo } from "react";
import type { RunEvent } from "@/types/benchmark";

interface Props {
  events: RunEvent[];
  docId: string;
  encoder: string;
}

interface MentionLite {
  text: string;
  mention_type?: string;
  type?: string;
  span_start?: number | null;
  span_end?: number | null;
  confidence?: number;
}

export function NerEncoderDetail({ events, docId, encoder }: Props) {
  const chunkId = `${docId}:_ner_${encoder}`;
  const summary = useMemo(() => {
    const ext = events.find((e) => e.node_name === "chunk_extracted" && e.chunk_id === chunkId);
    const completed = events.find(
      (e) => e.node_name === "ner_encoder_completed" && e.chunk_id === chunkId,
    );
    const mentions: MentionLite[] = (ext?.details?.mentions as MentionLite[]) ?? [];
    const d = (completed?.details ?? {}) as Record<string, unknown>;
    return {
      mentions,
      duration: typeof d.duration_s === "number" ? (d.duration_s as number) : null,
      status: completed?.status ?? "unknown",
      error:
        (d.error as { type?: string; message?: string } | undefined) ??
        (ext?.details?.error as { type?: string; message?: string } | undefined),
    };
  }, [events, chunkId]);

  const typeTally = useMemo(() => {
    const m = new Map<string, number>();
    for (const x of summary.mentions) {
      const t = x.mention_type ?? x.type ?? "—";
      m.set(t, (m.get(t) ?? 0) + 1);
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [summary.mentions]);

  return (
    <div className="p-3 font-mono text-[11px] space-y-3">
      <div>
        <div className="text-zinc-300 text-[12px]">{encoder}</div>
        <div className="text-zinc-500 text-[10px] mt-0.5">{docId}</div>
      </div>
      <div className="flex flex-wrap gap-3 text-[10px] text-zinc-400">
        <span>
          status:{" "}
          <span
            className={
              summary.status === "ok"
                ? "text-emerald-300"
                : summary.status === "error"
                  ? "text-red-300"
                  : "text-zinc-500"
            }
          >
            {summary.status}
          </span>
        </span>
        {summary.duration != null && (
          <span>
            duration: <span className="text-zinc-200">{summary.duration.toFixed(2)}s</span>
          </span>
        )}
        <span>
          mentions: <span className="text-zinc-200">{summary.mentions.length}</span>
        </span>
      </div>
      {summary.error?.message && (
        <div className="bg-red-500/10 border border-red-500/40 rounded px-2 py-1 text-red-300 text-[10px]">
          {summary.error.type ?? "error"}: {summary.error.message}
        </div>
      )}
      {typeTally.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {typeTally.map(([t, n]) => (
            <span
              key={t}
              className="px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-200 text-[9px]"
            >
              {t}×{n}
            </span>
          ))}
        </div>
      )}
      <div className="space-y-1">
        <div className="text-zinc-500 uppercase text-[9px] tracking-wide">mentions</div>
        {summary.mentions.length === 0 && (
          <div className="text-zinc-600 text-[10px]">no mentions extracted</div>
        )}
        {summary.mentions.map((m, i) => (
          <div
            key={i}
            className="px-2 py-1 rounded bg-white/[0.02] border border-white/5 text-[10px] flex items-center gap-2"
          >
            <span className="px-1 rounded bg-violet-500/20 text-violet-200 text-[9px]">
              {m.mention_type ?? m.type ?? "—"}
            </span>
            <span className="flex-1 truncate text-zinc-200">{m.text}</span>
            {m.confidence != null && (
              <span className="text-zinc-500 text-[9px]">{(m.confidence * 100).toFixed(0)}%</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

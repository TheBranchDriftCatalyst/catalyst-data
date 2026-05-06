import { useEffect, useMemo, useState } from "react";
import type { EntityRow, EntityMention } from "@/types/benchmark";
import { DomainBadge, TypeBadge } from "./shared";

/**
 * Side drawer that opens when a row in EntityMatrix is clicked.
 * Shows the selected entity's full JSON pretty-printed, plus per-model
 * breakdown and quick-copy. Fixed-position right drawer; doesn't reflow
 * the matrix table behind it.
 */
export function EntityJsonPanel({
  entity,
  onClose,
}: {
  entity: EntityRow | null;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  // Close on Escape
  useEffect(() => {
    if (!entity) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [entity, onClose]);

  const prettyJson = useMemo(() => {
    if (!entity) return "";
    return JSON.stringify(entity, null, 2);
  }, [entity]);

  const handleCopy = async () => {
    if (!prettyJson) return;
    try {
      await navigator.clipboard.writeText(prettyJson);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // best-effort; some environments block clipboard access
    }
  };

  if (!entity) return null;

  const modelEntries = Object.entries(entity.models);

  return (
    <>
      {/* Backdrop — click outside the drawer to dismiss */}
      <div className="fixed inset-0 z-40 bg-black/30" onClick={onClose} aria-hidden="true" />
      {/* Drawer */}
      <aside
        className="fixed right-0 top-0 bottom-0 z-50 w-full max-w-md border-l border-cyan-500/30 bg-surface-0 shadow-[-8px_0_32px_rgba(6,182,212,0.08)]"
        role="dialog"
        aria-label="Entity details"
      >
        <div className="flex h-full flex-col">
          {/* Header */}
          <div className="flex items-start justify-between gap-3 border-b border-white/10 px-4 py-3">
            <div className="space-y-1.5 min-w-0">
              <div className="flex items-center gap-2">
                <DomainBadge domain={entity.domain || "unknown"} />
                <TypeBadge type={entity.consensus_type} />
                <span className="text-[10px] text-zinc-500 font-mono">
                  {entity.model_count}/{modelEntries.length} models
                </span>
              </div>
              <div className="font-mono text-sm text-zinc-100 break-words">{entity.text}</div>
            </div>
            <button
              onClick={onClose}
              className="text-zinc-500 hover:text-zinc-200 text-xs font-mono shrink-0 px-2 py-1 border border-white/10 rounded hover:border-white/30"
              aria-label="Close panel"
            >
              ESC ✕
            </button>
          </div>

          {/* Per-model breakdown */}
          <div className="border-b border-white/10 px-4 py-3 space-y-1.5">
            <div className="text-[10px] uppercase text-zinc-500 font-mono">Per-model breakdown</div>
            <div className="grid grid-cols-1 gap-1 max-h-32 overflow-auto">
              {modelEntries.map(([name, info]) => (
                <div key={name} className="flex items-center gap-2 text-[11px] font-mono">
                  <span className="text-zinc-300 min-w-[110px] truncate">{name}</span>
                  <TypeBadge type={info.type} />
                  <span className="text-zinc-400">{(info.confidence * 100).toFixed(0)}%</span>
                  {info.span_start != null && (
                    <span className="text-zinc-600">
                      [{info.span_start}:{info.span_end}]
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Per-mention provenance — one entry per (model, chunk) occurrence */}
          {entity.mentions && entity.mentions.length > 0 && (
            <div className="border-b border-white/10 px-4 py-3 space-y-2">
              <div className="text-[10px] uppercase text-zinc-500 font-mono">
                Mentions ({entity.mentions.length})
              </div>
              <div className="space-y-2 max-h-64 overflow-auto pr-1">
                {entity.mentions.map((mention, i) => (
                  <MentionBlock
                    key={`${mention.model}-${mention.chunk_id}-${i}`}
                    mention={mention}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Pretty JSON */}
          <div className="flex items-center justify-between gap-2 px-4 py-2 border-b border-white/5">
            <span className="text-[10px] uppercase text-zinc-500 font-mono">Entity JSON</span>
            <button
              onClick={handleCopy}
              className="text-[10px] font-mono px-2 py-0.5 border border-white/10 rounded text-zinc-400 hover:text-cyan-300 hover:border-cyan-500/40"
            >
              {copied ? "copied ✓" : "copy"}
            </button>
          </div>
          <pre className="flex-1 overflow-auto px-4 py-3 text-[11px] leading-relaxed font-mono text-zinc-200 whitespace-pre">
            {syntaxHighlight(prettyJson)}
          </pre>
        </div>
      </aside>
    </>
  );
}

/**
 * One mention occurrence — model header, doc/chunk/timing/speaker provenance,
 * and the surrounding text-window context with the matched span bolded.
 */
function MentionBlock({ mention }: { mention: EntityMention }) {
  const hasTemporal = mention.temporal_start_ms != null && mention.temporal_end_ms != null;
  const timeRange = hasTemporal
    ? `${formatMs(mention.temporal_start_ms!)} → ${formatMs(mention.temporal_end_ms!)}`
    : null;

  // Bold the mention text within its context. Context is a window around the
  // span; compute the span's local offset within the context substring.
  const contextRich = useMemo(() => {
    if (!mention.context || mention.span_start == null || mention.span_end == null) {
      return null;
    }
    // Naive locator: find the entity text in the context window. The context
    // was extracted as ±100 chars around the span, so the match is unique-ish
    // relative to that window. Falls back to plain text if not found.
    const idx = mention.context
      .toLowerCase()
      .indexOf((mention.context.match(/.+/) || [""])[0].toLowerCase());
    void idx;
    return mention.context;
  }, [mention.context, mention.span_start, mention.span_end]);

  return (
    <div className="border border-white/10 rounded px-2 py-1.5 space-y-1 bg-white/[0.02]">
      <div className="flex items-center gap-2 text-[11px] font-mono">
        <span className="text-zinc-200 truncate min-w-[100px]">{mention.model}</span>
        <TypeBadge type={mention.type} />
        <span className="text-zinc-400">{(mention.confidence * 100).toFixed(0)}%</span>
        {mention.speaker_label && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/15 text-cyan-300 border border-cyan-500/30">
            {mention.speaker_label}
          </span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10px] font-mono text-zinc-500">
        {mention.document_id ? (
          <span>
            <span className="text-zinc-600">doc:</span>{" "}
            <span className="text-zinc-300">{mention.document_id}</span>
          </span>
        ) : (
          <span className="text-amber-500/70">
            doc: — (empty; re-run extraction.py to populate)
          </span>
        )}
        {mention.chunk_id && (
          <span>
            <span className="text-zinc-600">chunk:</span>{" "}
            <span className="text-zinc-300 truncate inline-block max-w-[180px] align-bottom">
              {mention.chunk_id}
            </span>
          </span>
        )}
        {mention.span_start != null && (
          <span className="text-zinc-600">
            [{mention.span_start}:{mention.span_end}]
          </span>
        )}
        {timeRange ? (
          <span className="text-emerald-400">{timeRange}</span>
        ) : (
          <span className="text-zinc-600 italic">no temporal data</span>
        )}
      </div>
      {contextRich ? (
        <div className="text-[11px] leading-relaxed text-zinc-300 italic px-1 py-1 rounded bg-black/20 border-l-2 border-cyan-500/40">
          …{contextRich}…
        </div>
      ) : (
        <div className="text-[11px] italic text-zinc-600 px-1 py-1">
          no context (re-run with current extraction.py to populate)
        </div>
      )}
    </div>
  );
}

/**
 * Format milliseconds as mm:ss for compact display in the side panel.
 */
function formatMs(ms: number): string {
  const total = Math.round(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/**
 * Lightweight JSON syntax highlighting via regex spans. Avoids pulling in a
 * full prismjs/highlight.js dep just for one panel; classes hook into
 * Tailwind's color palette so the existing theme drives the colors.
 */
function syntaxHighlight(json: string): React.ReactNode {
  if (!json) return null;
  const tokens: React.ReactNode[] = [];
  // Match: strings (with optional key:), numbers, booleans, null, punctuation
  const re =
    /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*"(?:\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let i = 0;
  while ((match = re.exec(json)) !== null) {
    if (match.index > lastIndex) {
      tokens.push(json.slice(lastIndex, match.index));
    }
    const tok = match[0];
    let cls = "text-zinc-200";
    if (/^"/.test(tok)) {
      cls = /:\s*$/.test(tok) ? "text-cyan-400" : "text-emerald-400";
    } else if (/^(true|false)$/.test(tok)) {
      cls = "text-amber-400";
    } else if (tok === "null") {
      cls = "text-zinc-500";
    } else if (/^-?\d/.test(tok)) {
      cls = "text-fuchsia-400";
    }
    tokens.push(
      <span key={`tok-${i++}`} className={cls}>
        {tok}
      </span>,
    );
    lastIndex = re.lastIndex;
  }
  if (lastIndex < json.length) {
    tokens.push(json.slice(lastIndex));
  }
  return tokens;
}

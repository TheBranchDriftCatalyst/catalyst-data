/**
 * Left rail of the StateInspector — collapsed to a three-level tree:
 *
 *   model
 *     └── domain   (media | congress_data | open_leaks | unknown)
 *           └── doc_id   (one entry per source document)
 *
 * Selecting a doc loads the ChunkTimeline (center pane). Per-doc rollups
 * (chunk count, mention total, error count) live here so an operator
 * can spot an outlier doc at a glance.
 *
 * Header controls:
 *   - text search (substring match on doc_id)
 *   - domain chip filter (multi-select; toggles which domain groups
 *     are included)
 *
 * Domain comes from chunk_loaded.details.domain (set by the source
 * asset). We fall back to a heuristic on the doc_id prefix so legacy
 * runs that didn't set domain still group reasonably.
 */

import { useMemo, useState } from "react";

import type { RunEvent } from "@/types/benchmark";

interface Props {
  events: RunEvent[];
  selectedModel: string | null;
  selectedDoc: string | null;
  onSelect: (model: string, docId: string) => void;
}

interface DocSummary {
  docId: string;
  domain: string;
  chunkIds: Set<string>;
  mentionTotal: number;
  propositionTotal: number;
  errorCount: number;
}

function _docIdFromChunkId(chunkId: string): string {
  const i = chunkId.lastIndexOf(":");
  return i >= 0 ? chunkId.slice(0, i) : chunkId;
}

const CHUNK_KIND_CLASSES = {
  CONSENSUS: "bg-cyan-500/20 text-cyan-200",
  NER: "bg-violet-500/20 text-violet-200",
  WIN: "bg-amber-500/20 text-amber-200",
} as const;

function chunkKindBadge(chunkId: string): { label: string; className: string } | null {
  if (chunkId.endsWith(":_consensus"))
    return { label: "CONSENSUS", className: CHUNK_KIND_CLASSES.CONSENSUS };
  if (chunkId.match(/:_ner_(.+)$/)) return { label: "NER", className: CHUNK_KIND_CLASSES.NER };
  if (chunkId.match(/:win-/)) return { label: "WIN", className: CHUNK_KIND_CLASSES.WIN };
  return null;
}

const NO_DOMAIN = "unknown";

function _inferDomain(docId: string): string {
  if (docId.startsWith("congress-bill-") || docId.startsWith("congress-")) return "congress";
  if (docId.startsWith("wikileaks-") || docId.startsWith("cable-")) return "leaks";
  if (
    docId.startsWith("joe-rogan") ||
    docId.startsWith("saagar-") ||
    docId.startsWith("inside-the-") ||
    docId.startsWith("4-9-26") ||
    docId.startsWith("sarah-")
  )
    return "media";
  return NO_DOMAIN;
}

export function ChunkRail({ events, selectedModel, selectedDoc, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [excludedDomains, setExcludedDomains] = useState<Set<string>>(new Set());
  const [collapsedDomains, setCollapsedDomains] = useState<Set<string>>(new Set());

  // model -> domain -> docId -> DocSummary
  const grouped = useMemo(() => {
    const out = new Map<string, Map<string, Map<string, DocSummary>>>();
    // First pass: pull domain off chunk_loaded events (model:null, applies
    // to every model that processes the chunk).
    const docDomain = new Map<string, string>();
    for (const e of events) {
      if (e.node_name !== "chunk_loaded" || !e.chunk_id) continue;
      const docId = e.doc_id ?? _docIdFromChunkId(e.chunk_id);
      const d = (e.details ?? {}) as Record<string, unknown>;
      const dom = (d.domain as string) ?? null;
      if (dom) docDomain.set(docId, dom);
    }

    for (const e of events) {
      if (!e.chunk_id) continue;
      const model = e.model ?? "—";
      const docId = e.doc_id ?? _docIdFromChunkId(e.chunk_id);
      const domain = docDomain.get(docId) ?? _inferDomain(docId);

      let modelBucket = out.get(model);
      if (!modelBucket) {
        modelBucket = new Map();
        out.set(model, modelBucket);
      }
      let domainBucket = modelBucket.get(domain);
      if (!domainBucket) {
        domainBucket = new Map();
        modelBucket.set(domain, domainBucket);
      }
      let summary = domainBucket.get(docId);
      if (!summary) {
        summary = {
          docId,
          domain,
          chunkIds: new Set(),
          mentionTotal: 0,
          propositionTotal: 0,
          errorCount: 0,
        };
        domainBucket.set(docId, summary);
      }
      summary.chunkIds.add(e.chunk_id);
      if (e.node_name === "chunk_extracted") {
        const d = e.details as Record<string, unknown>;
        summary.mentionTotal += (d.mention_count as number) ?? 0;
        summary.propositionTotal += (d.proposition_count as number) ?? 0;
      }
      if (e.status === "error" || e.status === "failed") summary.errorCount += 1;
    }
    return out;
  }, [events]);

  const models = useMemo(() => [...grouped.keys()].filter((m) => m !== "—").sort(), [grouped]);

  // Universe of domains across all models — for the chip filter.
  const allDomains = useMemo(() => {
    const set = new Set<string>();
    for (const modelBucket of grouped.values()) {
      for (const dom of modelBucket.keys()) set.add(dom);
    }
    return [...set].sort();
  }, [grouped]);

  const q = query.trim().toLowerCase();

  const toggleDomain = (d: string) => {
    setExcludedDomains((prev) => {
      const next = new Set(prev);
      if (next.has(d)) next.delete(d);
      else next.add(d);
      return next;
    });
  };
  const toggleDomainCollapse = (model: string, d: string) => {
    const key = `${model}::${d}`;
    setCollapsedDomains((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const isDomainCollapsed = (model: string, d: string) => collapsedDomains.has(`${model}::${d}`);

  if (models.length === 0) {
    return (
      <div className="p-4 font-mono text-xs text-zinc-600">
        No chunks observed yet. The harness emits a `chunk_loaded` event when each chunk first
        enters the graph.
      </div>
    );
  }

  return (
    <div>
      {/* Sticky header: search + domain chips */}
      <div className="sticky top-0 z-10 bg-surface-1/95 backdrop-blur border-b border-white/5 px-2 py-2 space-y-1.5">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="filter docs…"
          className="w-full px-2 py-1 font-mono text-[10px] rounded bg-black/30 border border-white/10 text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:border-cyan-500/50"
        />
        {allDomains.length > 1 && (
          <div className="flex flex-wrap gap-1">
            {allDomains.map((d) => {
              const active = !excludedDomains.has(d);
              return (
                <button
                  key={d}
                  type="button"
                  onClick={() => toggleDomain(d)}
                  className={`px-1.5 py-0.5 rounded font-mono text-[9.5px] border transition-colors ${
                    active
                      ? "bg-cyan-500/15 border-cyan-500/40 text-cyan-200"
                      : "bg-white/[0.02] border-white/10 text-zinc-600"
                  }`}
                  title={active ? `hide ${d}` : `show ${d}`}
                >
                  {d}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div className="py-2">
        {models.map((model) => {
          const domainMap = grouped.get(model);
          if (!domainMap) return null;

          // Filtered + sorted domain → doc structure for this model.
          const domains = [...domainMap.entries()]
            .filter(([d]) => !excludedDomains.has(d))
            .map(([dom, docMap]) => {
              const docs = [...docMap.values()]
                .filter((doc) => !q || doc.docId.toLowerCase().includes(q))
                .sort((a, b) => a.docId.localeCompare(b.docId));
              return { domain: dom, docs };
            })
            .filter((g) => g.docs.length > 0)
            .sort((a, b) => a.domain.localeCompare(b.domain));

          const totalChunks = domains.reduce(
            (s, g) => s + g.docs.reduce((ss, d) => ss + d.chunkIds.size, 0),
            0,
          );
          const totalDocs = domains.reduce((s, g) => s + g.docs.length, 0);
          const modelExpanded = model === selectedModel;

          if (totalDocs === 0 && q) {
            // Drop empty model entirely when search has no hits inside it.
            return null;
          }

          return (
            <div key={model} className="mb-1">
              <button
                type="button"
                onClick={() => {
                  // Click model header → select first doc so the timeline
                  // always has something to render.
                  const first = domains[0]?.docs[0];
                  if (first) onSelect(model, first.docId);
                }}
                className={`w-full text-left px-3 py-1.5 font-mono text-[11px] flex items-center gap-2 ${
                  modelExpanded
                    ? "bg-white/[0.04] text-zinc-200"
                    : "text-zinc-400 hover:bg-white/[0.02]"
                }`}
              >
                <span className="text-zinc-500">{modelExpanded ? "▾" : "▸"}</span>
                <span className="flex-1 truncate">{model}</span>
                <span className="text-zinc-600">
                  {totalDocs}d·{totalChunks}c
                </span>
              </button>
              {modelExpanded && (
                <div className="border-l border-white/5 ml-3">
                  {domains.map(({ domain, docs }) => {
                    const collapsed = isDomainCollapsed(model, domain);
                    const domTotalChunks = docs.reduce((s, d) => s + d.chunkIds.size, 0);
                    return (
                      <div key={domain}>
                        <button
                          type="button"
                          onClick={() => toggleDomainCollapse(model, domain)}
                          className="w-full text-left px-3 py-1 font-mono text-[10px] flex items-center gap-2 text-zinc-500 hover:bg-white/[0.02]"
                        >
                          <span className="text-zinc-700">{collapsed ? "▸" : "▾"}</span>
                          <span className="flex-1 truncate uppercase tracking-wide">{domain}</span>
                          <span className="text-zinc-600">
                            {docs.length}d·{domTotalChunks}c
                          </span>
                        </button>
                        {!collapsed && (
                          <div className="border-l border-white/5 ml-4">
                            {docs.map((doc) => {
                              const isSel = selectedDoc === doc.docId && selectedModel === model;
                              // Collect unique chunk kinds present for this doc.
                              const kindBadges: Array<{ label: string; className: string }> = [];
                              const seenLabels = new Set<string>();
                              for (const cid of doc.chunkIds) {
                                const badge = chunkKindBadge(cid);
                                if (badge && !seenLabels.has(badge.label)) {
                                  seenLabels.add(badge.label);
                                  kindBadges.push(badge);
                                }
                              }
                              return (
                                <button
                                  key={doc.docId}
                                  type="button"
                                  onClick={() => onSelect(model, doc.docId)}
                                  className={`w-full text-left px-3 py-1 font-mono text-[10px] flex items-center gap-2 ${
                                    isSel
                                      ? "bg-cyan-500/10 text-cyan-200"
                                      : "text-zinc-400 hover:bg-white/[0.02]"
                                  }`}
                                  title={doc.docId}
                                >
                                  <span className={isSel ? "text-cyan-400" : "text-zinc-600"}>
                                    {isSel ? "●" : "○"}
                                  </span>
                                  <span className="flex-1 truncate">{doc.docId}</span>
                                  {kindBadges.map((b) => (
                                    <span
                                      key={b.label}
                                      className={`${b.className} px-1 rounded text-[9px]`}
                                    >
                                      {b.label}
                                    </span>
                                  ))}
                                  <span className="text-zinc-600">
                                    {doc.chunkIds.size}c · {doc.mentionTotal}m·
                                    {doc.propositionTotal}p
                                  </span>
                                  {doc.errorCount > 0 && (
                                    <span
                                      className="text-red-400"
                                      title={`${doc.errorCount} error(s)`}
                                    >
                                      !{doc.errorCount}
                                    </span>
                                  )}
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

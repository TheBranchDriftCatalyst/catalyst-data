/**
 * Left rail of the StateInspector — doc-first tree:
 *
 *   domain (media | congress | leaks | unknown)
 *     └── doc_id   (one entry per source document)
 *           └── stage row   (NER · CONSENSUS · SPO)
 *
 * v4 made model-first grouping confusing because every doc is processed
 * by every encoder, then consensus, then SPO — fragmenting one doc's
 * trace across 7 sidebar entries. Doc-first puts the unit of analysis
 * (the document) at the top and exposes per-stage rows underneath.
 *
 * Each stage row is a clickable (model, doc) pair: clicking it selects
 * the corresponding chunk timeline in the right pane (handler unchanged).
 *
 * Header controls:
 *   - text search (substring on doc_id)
 *   - domain chips (multi-select)
 *   - model chips (multi-select; hide rows for excluded models)
 */

import { useEffect, useMemo, useState } from "react";

import type { RunEvent } from "@/types/benchmark";

interface Props {
  events: RunEvent[];
  selectedModel: string | null;
  selectedDoc: string | null;
  onSelect: (model: string, docId: string) => void;
}

type Stage = "NER" | "CONSENSUS" | "SPO";

const STAGE_CLASSES: Record<Stage, string> = {
  NER: "bg-violet-500/20 text-violet-200",
  CONSENSUS: "bg-cyan-500/20 text-cyan-200",
  SPO: "bg-amber-500/20 text-amber-200",
};

const STAGE_ORDER: readonly Stage[] = ["NER", "CONSENSUS", "SPO"] as const;

function classifyChunk(
  chunkId: string,
  eventModel: string | null,
): { stage: Stage; model: string } | null {
  if (chunkId.endsWith(":_consensus")) return { stage: "CONSENSUS", model: "ensemble" };
  const ner = chunkId.match(/:_ner_(.+)$/);
  if (ner && ner[1]) return { stage: "NER", model: ner[1] };
  if (chunkId.match(/:win-/)) return { stage: "SPO", model: eventModel ?? "—" };
  return null;
}

function _docIdFromChunkId(chunkId: string): string {
  const i = chunkId.lastIndexOf(":");
  return i >= 0 ? chunkId.slice(0, i) : chunkId;
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

interface StageEntry {
  stage: Stage;
  model: string;
  chunkIds: Set<string>;
  mentions: number;
  propositions: number;
}

interface DocEntry {
  docId: string;
  domain: string;
  chunkIds: Set<string>;
  totalMentions: number;
  totalPropositions: number;
  stages: Map<string, StageEntry>;
}

function stageKey(stage: Stage, model: string): string {
  return `${stage}::${model}`;
}

export function ChunkRail({ events, selectedModel, selectedDoc, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [excludedDomains, setExcludedDomains] = useState<Set<string>>(new Set());
  const [excludedModels, setExcludedModels] = useState<Set<string>>(new Set());
  const [collapsedDomains, setCollapsedDomains] = useState<Set<string>>(new Set());
  const [expandedDocs, setExpandedDocs] = useState<Set<string>>(new Set());

  // Build: domain -> doc -> stages
  const grouped = useMemo(() => {
    const docs = new Map<string, DocEntry>();

    // Pass 1: docId -> domain (from chunk_loaded.details.domain)
    const docDomain = new Map<string, string>();
    // Pass 1b: chunk_id -> model (from any event that has model set —
    // covers chunk_loaded for :win- chunks that come in with model=null
    // before the SPO node attributes the model).
    const chunkModel = new Map<string, string>();
    for (const e of events) {
      if (!e.chunk_id) continue;
      if (e.node_name === "chunk_loaded") {
        const docId = e.doc_id ?? _docIdFromChunkId(e.chunk_id);
        const d = (e.details ?? {}) as Record<string, unknown>;
        const dom = (d.domain as string) ?? null;
        if (dom) docDomain.set(docId, dom);
      }
      if (e.model && !chunkModel.has(e.chunk_id)) chunkModel.set(e.chunk_id, e.model);
    }

    for (const e of events) {
      if (!e.chunk_id) continue;
      const docId = e.doc_id ?? _docIdFromChunkId(e.chunk_id);
      const domain = docDomain.get(docId) ?? _inferDomain(docId);

      let doc = docs.get(docId);
      if (!doc) {
        doc = {
          docId,
          domain,
          chunkIds: new Set(),
          totalMentions: 0,
          totalPropositions: 0,
          stages: new Map(),
        };
        docs.set(docId, doc);
      }
      doc.chunkIds.add(e.chunk_id);

      const cls = classifyChunk(e.chunk_id, e.model ?? chunkModel.get(e.chunk_id) ?? null);
      if (!cls || cls.model === "—") continue;

      const key = stageKey(cls.stage, cls.model);
      let st = doc.stages.get(key);
      if (!st) {
        st = {
          stage: cls.stage,
          model: cls.model,
          chunkIds: new Set(),
          mentions: 0,
          propositions: 0,
        };
        doc.stages.set(key, st);
      }
      st.chunkIds.add(e.chunk_id);

      if (e.node_name === "chunk_extracted") {
        const d = (e.details ?? {}) as Record<string, unknown>;
        const m = (d.mention_count as number) ?? 0;
        const p = (d.proposition_count as number) ?? 0;
        st.mentions += m;
        st.propositions += p;
        doc.totalMentions += m;
        doc.totalPropositions += p;
      }
    }

    // domain -> [doc, doc, …]
    const byDomain = new Map<string, DocEntry[]>();
    for (const doc of docs.values()) {
      const arr = byDomain.get(doc.domain) ?? [];
      arr.push(doc);
      byDomain.set(doc.domain, arr);
    }
    for (const arr of byDomain.values()) arr.sort((a, b) => a.docId.localeCompare(b.docId));
    return byDomain;
  }, [events]);

  const allDomains = useMemo(
    () => [...grouped.keys()].sort((a, b) => a.localeCompare(b)),
    [grouped],
  );

  const allModels = useMemo(() => {
    const set = new Set<string>();
    for (const docs of grouped.values()) {
      for (const doc of docs) for (const st of doc.stages.values()) set.add(st.model);
    }
    return [...set].sort();
  }, [grouped]);

  // Auto-expand the doc containing the current selection so the user
  // can see which stage row is highlighted without clicking around.
  useEffect(() => {
    if (selectedDoc) {
      setExpandedDocs((prev) => {
        if (prev.has(selectedDoc)) return prev;
        const next = new Set(prev);
        next.add(selectedDoc);
        return next;
      });
    }
  }, [selectedDoc]);

  const q = query.trim().toLowerCase();

  const toggleDomainExclude = (d: string) => {
    setExcludedDomains((prev) => {
      const next = new Set(prev);
      if (next.has(d)) next.delete(d);
      else next.add(d);
      return next;
    });
  };
  const toggleModelExclude = (m: string) => {
    setExcludedModels((prev) => {
      const next = new Set(prev);
      if (next.has(m)) next.delete(m);
      else next.add(m);
      return next;
    });
  };
  const toggleDomainCollapse = (d: string) => {
    setCollapsedDomains((prev) => {
      const next = new Set(prev);
      if (next.has(d)) next.delete(d);
      else next.add(d);
      return next;
    });
  };
  const toggleDocExpand = (docId: string) => {
    setExpandedDocs((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      return next;
    });
  };

  if (allDomains.length === 0) {
    return (
      <div className="p-4 font-mono text-xs text-zinc-600">
        No chunks observed yet. The harness emits a `chunk_loaded` event when each chunk first
        enters the graph.
      </div>
    );
  }

  return (
    <div>
      {/* Sticky header: search + domain chips + model chips */}
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
                  onClick={() => toggleDomainExclude(d)}
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
        {allModels.length > 1 && (
          <div className="flex flex-wrap gap-1">
            {allModels.map((m) => {
              const active = !excludedModels.has(m);
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => toggleModelExclude(m)}
                  className={`px-1.5 py-0.5 rounded font-mono text-[9.5px] border transition-colors ${
                    active
                      ? "bg-zinc-500/15 border-zinc-500/40 text-zinc-200"
                      : "bg-white/[0.02] border-white/10 text-zinc-600"
                  }`}
                  title={active ? `hide ${m}` : `show ${m}`}
                >
                  {m}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div className="py-2">
        {allDomains.map((domain) => {
          if (excludedDomains.has(domain)) return null;
          const docs = (grouped.get(domain) ?? []).filter(
            (d) => !q || d.docId.toLowerCase().includes(q),
          );
          if (docs.length === 0) return null;
          const collapsed = collapsedDomains.has(domain);
          const totalChunks = docs.reduce((s, d) => s + d.chunkIds.size, 0);

          return (
            <div key={domain} className="mb-1">
              <button
                type="button"
                onClick={() => toggleDomainCollapse(domain)}
                className="w-full text-left px-3 py-1.5 font-mono text-[11px] flex items-center gap-2 text-zinc-400 hover:bg-white/[0.02]"
              >
                <span className="text-zinc-500">{collapsed ? "▸" : "▾"}</span>
                <span className="flex-1 truncate uppercase tracking-wide">{domain}</span>
                <span className="text-zinc-600">
                  {docs.length}d·{totalChunks}c
                </span>
              </button>
              {!collapsed && (
                <div className="border-l border-white/5 ml-3">
                  {docs.map((doc) => {
                    const isDocSelected = selectedDoc === doc.docId;
                    const isExpanded = expandedDocs.has(doc.docId);
                    const stages = [...doc.stages.values()]
                      .filter((s) => !excludedModels.has(s.model))
                      .sort((a, b) => {
                        const sa = STAGE_ORDER.indexOf(a.stage);
                        const sb = STAGE_ORDER.indexOf(b.stage);
                        if (sa !== sb) return sa - sb;
                        return a.model.localeCompare(b.model);
                      });
                    if (stages.length === 0) return null;
                    return (
                      <div key={doc.docId}>
                        <button
                          type="button"
                          onClick={() => {
                            toggleDocExpand(doc.docId);
                            // Also select the first stage so the right pane has something.
                            const first = stages[0];
                            if (first && !isDocSelected) onSelect(first.model, doc.docId);
                          }}
                          className={`w-full text-left px-3 py-1 font-mono text-[10px] flex items-center gap-2 ${
                            isDocSelected
                              ? "bg-cyan-500/10 text-cyan-200"
                              : "text-zinc-400 hover:bg-white/[0.02]"
                          }`}
                          title={doc.docId}
                        >
                          <span className={isDocSelected ? "text-cyan-400" : "text-zinc-600"}>
                            {isExpanded ? "▾" : "▸"}
                          </span>
                          <span className="flex-1 truncate">{doc.docId}</span>
                          <span className="text-zinc-600">
                            {stages.length}s · {doc.totalMentions}m·{doc.totalPropositions}p
                          </span>
                        </button>
                        {isExpanded && (
                          <div className="border-l border-white/5 ml-4">
                            {stages.map((st) => {
                              const isSel = selectedModel === st.model && selectedDoc === doc.docId;
                              return (
                                <button
                                  key={stageKey(st.stage, st.model)}
                                  type="button"
                                  onClick={() => onSelect(st.model, doc.docId)}
                                  className={`w-full text-left px-3 py-0.5 font-mono text-[10px] flex items-center gap-2 ${
                                    isSel
                                      ? "bg-cyan-500/10 text-cyan-200"
                                      : "text-zinc-500 hover:bg-white/[0.02]"
                                  }`}
                                  title={`${st.stage} · ${st.model}`}
                                >
                                  <span
                                    className={`${STAGE_CLASSES[st.stage]} px-1 rounded text-[9px]`}
                                  >
                                    {st.stage}
                                  </span>
                                  <span className="flex-1 truncate">{st.model}</span>
                                  <span className="text-zinc-600">
                                    {st.mentions}m
                                    {st.propositions > 0 ? `·${st.propositions}p` : ""}
                                  </span>
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

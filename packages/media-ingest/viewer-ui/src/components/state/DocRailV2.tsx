/**
 * DocRailV2 — minimal rail for StateInspector V2.
 *
 * In V2 the per-stage / per-model breakdown lives in the center-pane
 * graph, so the rail only needs to do one job: pick a document. Tree:
 *
 *   domain
 *     └── doc_id  (rollup: chunks · mentions · props)
 *
 * Domain comes from chunk_loaded.details.domain (set by the source
 * asset); falls back to a heuristic on the doc_id prefix for legacy
 * runs that didn't set it.
 */

import { useMemo, useState } from "react";

import type { RunEvent } from "@/types/benchmark";

interface Props {
  events: RunEvent[];
  selectedDoc: string | null;
  onSelect: (docId: string) => void;
}

interface DocRow {
  docId: string;
  domain: string;
  chunkCount: number;
  mentionTotal: number;
  propTotal: number;
}

const NO_DOMAIN = "unknown";

function _docIdFromChunkId(chunkId: string): string {
  const i = chunkId.lastIndexOf(":");
  return i >= 0 ? chunkId.slice(0, i) : chunkId;
}

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

export function DocRailV2({ events, selectedDoc, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [excludedDomains, setExcludedDomains] = useState<Set<string>>(new Set());

  const grouped = useMemo(() => {
    const docs = new Map<string, DocRow>();
    const chunkSeen = new Map<string, Set<string>>(); // docId -> chunk_ids

    for (const e of events) {
      if (!e.chunk_id) continue;
      const docId = e.doc_id ?? _docIdFromChunkId(e.chunk_id);
      let row = docs.get(docId);
      if (!row) {
        const d = (e.details ?? {}) as Record<string, unknown>;
        const dom = e.node_name === "chunk_loaded" ? ((d.domain as string) ?? null) : null;
        row = {
          docId,
          domain: dom ?? _inferDomain(docId),
          chunkCount: 0,
          mentionTotal: 0,
          propTotal: 0,
        };
        docs.set(docId, row);
        chunkSeen.set(docId, new Set());
      }
      // upgrade domain if a later chunk_loaded carries it
      if (row.domain === NO_DOMAIN && e.node_name === "chunk_loaded") {
        const d = (e.details ?? {}) as Record<string, unknown>;
        if (d.domain) row.domain = d.domain as string;
      }
      // Count *real* chunks only (pre-NER, not :_ner_, not :_consensus, not :win-)
      const cid = e.chunk_id;
      if (
        e.node_name === "chunk_loaded" &&
        !cid.includes(":_ner_") &&
        !cid.endsWith(":_consensus") &&
        !cid.includes(":win-") &&
        !cid.endsWith(":_doc_ensemble")
      ) {
        const s = chunkSeen.get(docId)!;
        if (!s.has(cid)) {
          s.add(cid);
          row.chunkCount += 1;
        }
      }
      if (e.node_name === "chunk_extracted") {
        const d = (e.details ?? {}) as Record<string, unknown>;
        // Only count consensus mentions + SPO props for the rail rollup, so the
        // number isn't inflated by 5× from per-encoder events.
        if (cid.endsWith(":_consensus")) {
          row.mentionTotal += (d.mention_count as number) ?? 0;
        }
        if (cid.includes(":win-")) {
          row.propTotal += (d.proposition_count as number) ?? 0;
        }
      }
    }

    const byDomain = new Map<string, DocRow[]>();
    for (const row of docs.values()) {
      const arr = byDomain.get(row.domain) ?? [];
      arr.push(row);
      byDomain.set(row.domain, arr);
    }
    for (const arr of byDomain.values()) arr.sort((a, b) => a.docId.localeCompare(b.docId));
    return byDomain;
  }, [events]);

  const allDomains = useMemo(
    () => [...grouped.keys()].sort((a, b) => a.localeCompare(b)),
    [grouped],
  );

  const q = query.trim().toLowerCase();

  const toggleDomain = (d: string) => {
    setExcludedDomains((prev) => {
      const next = new Set(prev);
      if (next.has(d)) next.delete(d);
      else next.add(d);
      return next;
    });
  };

  if (allDomains.length === 0) {
    return (
      <div className="p-4 font-mono text-xs text-zinc-600">
        No chunks observed yet. Start a benchmark run.
      </div>
    );
  }

  return (
    <div>
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
                >
                  {d}
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
          return (
            <div key={domain} className="mb-1">
              <div className="px-3 py-1 font-mono text-[10px] text-zinc-500 uppercase tracking-wide flex items-center gap-2">
                <span className="flex-1 truncate">{domain}</span>
                <span className="text-zinc-600">{docs.length}d</span>
              </div>
              <div className="border-l border-white/5 ml-3">
                {docs.map((doc) => {
                  const sel = selectedDoc === doc.docId;
                  return (
                    <button
                      key={doc.docId}
                      type="button"
                      onClick={() => onSelect(doc.docId)}
                      className={`w-full text-left px-3 py-1 font-mono text-[10px] flex items-center gap-2 ${
                        sel ? "bg-cyan-500/10 text-cyan-200" : "text-zinc-400 hover:bg-white/[0.02]"
                      }`}
                      title={doc.docId}
                    >
                      <span className={sel ? "text-cyan-400" : "text-zinc-600"}>
                        {sel ? "●" : "○"}
                      </span>
                      <span className="flex-1 truncate">{doc.docId}</span>
                      <span className="text-zinc-600">
                        {doc.chunkCount}c · {doc.mentionTotal}m·{doc.propTotal}p
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

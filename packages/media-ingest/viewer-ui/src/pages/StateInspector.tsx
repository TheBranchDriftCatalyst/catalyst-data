/**
 * StateInspector — chunker-debugging view for the bench's exgraph runs.
 *
 * Layout:
 *   ┌────────────┬────────────────────────────────┬───────────────┐
 *   │ ChunkRail  │ ChunkTimeline                  │ ChunkTextPanel│
 *   │ (model →   │  - doc header (strategy/size)  │  (provenance, │
 *   │  doc only) │  - horizontal strip of chunks  │   chunking,   │
 *   │            │  - selected chunk: input →     │   full text)  │
 *   │            │    stages → output flow        │               │
 *   └────────────┴────────────────────────────────┴───────────────┘
 *
 * Deep-link query: ?model=...&doc=...&chunk_id=...
 * (Backwards-compatible: old `chunk_id` links still work — the doc is
 * recovered from the chunk_id pattern `<doc>:chunk-<n>`.)
 */

import { useEffect, useMemo, useState } from "react";

import type { RunEvent } from "@/types/benchmark";
import { useRunStream } from "@/hooks/useRunStream";
import { ChunkRail } from "@/components/state/ChunkRail";
import { ChunkTimeline } from "@/components/state/ChunkTimeline";
import { ChunkTextPanel } from "@/components/state/ChunkTextPanel";
import { ConsensusDetail } from "@/components/state/ConsensusDetail";

function readQuery(): { model: string | null; docId: string | null; chunkId: string | null } {
  const p = new URLSearchParams(window.location.search);
  return {
    model: p.get("model"),
    docId: p.get("doc"),
    chunkId: p.get("chunk_id"),
  };
}

function writeQuery(model: string | null, docId: string | null, chunkId: string | null) {
  const p = new URLSearchParams();
  if (model) p.set("model", model);
  if (docId) p.set("doc", docId);
  if (chunkId) p.set("chunk_id", chunkId);
  const qs = p.toString();
  const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
  window.history.replaceState({}, "", url);
}

function docFromChunkId(chunkId: string | null): string | null {
  if (!chunkId) return null;
  const i = chunkId.lastIndexOf(":");
  return i >= 0 ? chunkId.slice(0, i) : chunkId;
}

export function StateInspector() {
  const { events, connected, error } = useRunStream();
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);
  const [selectedChunk, setSelectedChunk] = useState<string | null>(null);

  // Initial selection from URL on mount.
  useEffect(() => {
    const q = readQuery();
    if (q.model) setSelectedModel(q.model);
    if (q.docId) setSelectedDoc(q.docId);
    else if (q.chunkId) setSelectedDoc(docFromChunkId(q.chunkId));
    if (q.chunkId) setSelectedChunk(q.chunkId);
  }, []);

  // Persist selection to URL so the page is reload-safe + shareable.
  useEffect(() => {
    writeQuery(selectedModel, selectedDoc, selectedChunk);
  }, [selectedModel, selectedDoc, selectedChunk]);

  // Default selection once the stream has data.
  useEffect(() => {
    if (events.length === 0) return;
    if (!selectedModel) {
      const firstModel = events.find((e) => e.model)?.model;
      if (firstModel) setSelectedModel(firstModel);
    }
    if (!selectedDoc) {
      const firstDoc = events.find((e) => e.doc_id)?.doc_id;
      if (firstDoc) setSelectedDoc(firstDoc);
    }
  }, [events, selectedModel, selectedDoc]);

  // Per-doc filtered events for the timeline. Includes:
  //   - chunk_loaded (model:null, applies to every model that processes the doc)
  //   - exgraph stage events with model set
  //   - exgraph stage events with model:null (legacy / older runs that didn't
  //     thread bench_model through ExGraphState.model — without these the stage
  //     dots stay grey forever; the harness chunk_extracted event still
  //     correctly attributes mentions to a model)
  const docEvents = useMemo(() => {
    if (!selectedModel || !selectedDoc) return [] as RunEvent[];
    return events.filter((e) => {
      if (!e.chunk_id) return false;
      const eDoc = e.doc_id ?? docFromChunkId(e.chunk_id);
      if (eDoc !== selectedDoc) return false;
      if (e.node_name === "chunk_loaded") return true;
      if (e.model === selectedModel) return true;
      // Legacy exgraph events without a model tag — let them through so
      // the per-stage dots aren't all grey for runs predating the
      // CATALYST_BENCH_MODEL → ExGraphState.model wiring.
      if (e.source === "exgraph" && e.model == null) return true;
      return false;
    });
  }, [events, selectedModel, selectedDoc]);

  // Right-pane data — for the selected chunk only.
  const chunkText = useMemo(() => {
    if (!selectedChunk) return null;
    const loaded = events.find(
      (e) => e.node_name === "chunk_loaded" && e.chunk_id === selectedChunk,
    );
    return loaded?.details ?? null;
  }, [events, selectedChunk]);

  const extracted = useMemo(() => {
    if (!selectedChunk || !selectedModel) return null;
    const final = events.find(
      (e) =>
        e.node_name === "chunk_extracted" &&
        e.chunk_id === selectedChunk &&
        e.model === selectedModel,
    );
    return final?.details ?? null;
  }, [events, selectedChunk, selectedModel]);

  return (
    <div className="flex h-full">
      <div className="w-60 flex-shrink-0 border-r border-white/10 overflow-y-auto">
        <ChunkRail
          events={events}
          selectedModel={selectedModel}
          selectedDoc={selectedDoc}
          onSelect={(model, docId) => {
            setSelectedModel(model);
            setSelectedDoc(docId);
            // Reset chunk selection on doc switch — the new doc may not
            // contain the previously-selected chunk.
            setSelectedChunk(null);
          }}
        />
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        <div className="px-4 py-2 border-b border-white/10 flex items-center gap-2 font-mono text-[11px]">
          <span className="text-zinc-300">state inspector</span>
          <span
            className={`px-1.5 py-0.5 rounded text-[10px] ${
              connected
                ? "bg-emerald-500/20 text-emerald-300"
                : error === "offline (replay)"
                  ? "bg-zinc-500/20 text-zinc-400"
                  : "bg-amber-500/20 text-amber-300"
            }`}
          >
            {connected ? "live" : (error ?? "connecting…")}
          </span>
          <span className="text-zinc-500">{events.length} events</span>
        </div>
        <div className="flex-1 min-h-0">
          {selectedModel && selectedDoc ? (
            <ChunkTimeline
              events={docEvents}
              allEvents={events}
              model={selectedModel}
              docId={selectedDoc}
              selectedChunk={selectedChunk}
              onSelectChunk={(chunkId) => setSelectedChunk(chunkId)}
            />
          ) : (
            <div className="p-6 font-mono text-xs text-zinc-500">
              {events.length === 0
                ? "No events yet — start a benchmark run."
                : "Pick a model + doc on the left to inspect chunking."}
            </div>
          )}
        </div>
      </div>

      <div className="w-[420px] flex-shrink-0 border-l border-white/10 overflow-y-auto">
        {selectedChunk?.endsWith(":_consensus") ? (
          <ConsensusDetail chunkId={selectedChunk} events={events} />
        ) : (
          <ChunkTextPanel
            chunkText={chunkText}
            extracted={extracted}
            events={docEvents}
            hoveredErrorIndex={null}
          />
        )}
      </div>
    </div>
  );
}

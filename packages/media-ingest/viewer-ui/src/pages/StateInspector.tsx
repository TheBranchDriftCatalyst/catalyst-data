/**
 * StateInspector — per-chunk × per-model deep-dive into the langgraph
 * / exgraph state machine. Reads the same `events.jsonl` the LiveGantt
 * uses; deep-linked from LiveGantt with `?model=...&chunk_id=...`.
 *
 * Layout:
 *   ┌────────────┬───────────────────────────────┬────────────┐
 *   │ chunk rail │  vertical event timeline      │ chunk text │
 *   │ (per       │  (cards with state summaries) │ + spans    │
 *   │  model)    │                               │ + extracted│
 *   └────────────┴───────────────────────────────┴────────────┘
 */

import { useEffect, useMemo, useState } from "react";

import type { RunEvent } from "@/types/benchmark";
import { useRunStream } from "@/hooks/useRunStream";
import { ChunkRail } from "@/components/state/ChunkRail";
import { EventStream } from "@/components/state/EventStream";
import { ChunkTextPanel } from "@/components/state/ChunkTextPanel";

function readQuery(): { model: string | null; chunkId: string | null } {
  const p = new URLSearchParams(window.location.search);
  return { model: p.get("model"), chunkId: p.get("chunk_id") };
}

function writeQuery(model: string | null, chunkId: string | null) {
  const p = new URLSearchParams();
  if (model) p.set("model", model);
  if (chunkId) p.set("chunk_id", chunkId);
  const url = `${window.location.pathname}?${p.toString()}`;
  window.history.replaceState({}, "", url);
}

export function StateInspector() {
  const { events, connected, error } = useRunStream();
  const [selectedModel, setSelectedModel] = useState<string | null>(null);
  const [selectedChunk, setSelectedChunk] = useState<string | null>(null);
  const [hoveredErrorIndex, setHoveredErrorIndex] = useState<number | null>(null);

  // Initial selection from URL on mount.
  useEffect(() => {
    const q = readQuery();
    if (q.model) setSelectedModel(q.model);
    if (q.chunkId) setSelectedChunk(q.chunkId);
  }, []);

  // Persist selection to URL so the page is reload-safe + shareable.
  useEffect(() => {
    writeQuery(selectedModel, selectedChunk);
  }, [selectedModel, selectedChunk]);

  // Default selection once the stream has data.
  useEffect(() => {
    if (events.length === 0) return;
    if (!selectedModel) {
      const firstModel = events.find((e) => e.model)?.model;
      if (firstModel) setSelectedModel(firstModel);
    }
    if (!selectedChunk) {
      const firstChunk = events.find((e) => e.chunk_id)?.chunk_id;
      if (firstChunk) setSelectedChunk(firstChunk);
    }
  }, [events, selectedModel, selectedChunk]);

  const filtered = useMemo(() => {
    if (!selectedModel || !selectedChunk) return [] as RunEvent[];
    return events.filter(
      (e) =>
        e.chunk_id === selectedChunk &&
        // chunk_loaded events have model:null but apply to the chunk overall
        (e.node_name === "chunk_loaded" || e.model === selectedModel),
    );
  }, [events, selectedModel, selectedChunk]);

  const chunkText = useMemo(() => {
    const loaded = events.find(
      (e) => e.node_name === "chunk_loaded" && e.chunk_id === selectedChunk,
    );
    return loaded?.details ?? null;
  }, [events, selectedChunk]);

  const extracted = useMemo(() => {
    const final = filtered.find((e) => e.node_name === "chunk_extracted");
    return final?.details ?? null;
  }, [filtered]);

  return (
    <div className="flex h-full">
      <div className="w-64 flex-shrink-0 border-r border-white/10 overflow-y-auto">
        <ChunkRail
          events={events}
          selectedModel={selectedModel}
          selectedChunk={selectedChunk}
          onSelect={(model, chunkId) => {
            setSelectedModel(model);
            setSelectedChunk(chunkId);
          }}
        />
      </div>

      <div className="flex-1 overflow-y-auto p-4 min-w-0">
        <div className="mb-3 flex items-center gap-2 font-mono text-[11px]">
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
          <span className="text-zinc-500">{filtered.length} events</span>
          {selectedModel && (
            <>
              <span className="text-zinc-700">·</span>
              <span className="text-zinc-300">{selectedModel}</span>
            </>
          )}
          {selectedChunk && (
            <>
              <span className="text-zinc-700">·</span>
              <span className="text-zinc-500 truncate max-w-md">{selectedChunk}</span>
            </>
          )}
        </div>

        {filtered.length === 0 ? (
          <div className="rounded border border-white/10 bg-surface-1 p-6 font-mono text-xs text-zinc-500">
            {events.length === 0
              ? "No events yet — start a benchmark run."
              : "Pick a chunk on the left to inspect its state."}
          </div>
        ) : (
          <EventStream events={filtered} onHoverError={setHoveredErrorIndex} />
        )}
      </div>

      <div className="w-96 flex-shrink-0 border-l border-white/10 overflow-y-auto">
        <ChunkTextPanel
          chunkText={chunkText}
          extracted={extracted}
          events={filtered}
          hoveredErrorIndex={hoveredErrorIndex}
        />
      </div>
    </div>
  );
}

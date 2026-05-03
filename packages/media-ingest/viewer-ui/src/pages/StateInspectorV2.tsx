/**
 * StateInspectorV2 — graph-native view of the v4 LangGraph execution.
 *
 *   ┌────────────┬──────────────────────────────┬───────────────┐
 *   │ DocRailV2  │ PipelineGraph (reactflow LR) │ DetailPanel   │
 *   │ (domain →  │   chunks → 5 enc → consen →  │  (consensus / │
 *   │  doc only) │    pack → win-* → spo →      │   ner enc /   │
 *   │            │    persist)                  │   chunk text) │
 *   └────────────┴──────────────────────────────┴───────────────┘
 *
 * The graph IS the topology: each LangGraph node = one graph node, edges
 * follow the literal data flow. Live updates are driven by `useRunStream`
 * — when events arrive, node statuses (queued / running / ok / error) and
 * payload badges (mention count, duration, retries) recompute and the
 * graph re-renders in place without losing pan/zoom.
 *
 * Deep-link query: ?doc=<docId>&node=<role>:<ref>
 */

import { useEffect, useState } from "react";

import { useRunStream } from "@/hooks/useRunStream";
import { DocRailV2 } from "@/components/state/DocRailV2";
import {
  PipelineGraph,
  type SelectedGraphNode,
  type NodeRole,
} from "@/components/state/PipelineGraph";
import { ConsensusDetail } from "@/components/state/ConsensusDetail";
import { ChunkTextPanel } from "@/components/state/ChunkTextPanel";
import { NerEncoderDetail } from "@/components/state/NerEncoderDetail";

const VALID_ROLES: ReadonlySet<NodeRole> = new Set<NodeRole>([
  "chunks",
  "ner_encoder",
  "consensus",
  "pack",
  "spo_window",
  "spo_windows_collapsed",
  "spo_model",
  "persist",
]);

function readQuery(): { docId: string | null; node: SelectedGraphNode | null } {
  const p = new URLSearchParams(window.location.search);
  const docId = p.get("doc");
  const raw = p.get("node");
  let node: SelectedGraphNode | null = null;
  if (raw) {
    const [roleStr, ...rest] = raw.split(":");
    if (roleStr && VALID_ROLES.has(roleStr as NodeRole)) {
      node = {
        role: roleStr as NodeRole,
        ref: rest.length > 0 ? rest.join(":") : null,
      };
    }
  }
  return { docId, node };
}

function writeQuery(docId: string | null, node: SelectedGraphNode | null) {
  const p = new URLSearchParams();
  if (docId) p.set("doc", docId);
  if (node) p.set("node", node.ref ? `${node.role}:${node.ref}` : node.role);
  const qs = p.toString();
  const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
  window.history.replaceState({}, "", url);
}

export function StateInspectorV2() {
  const { events, connected, error } = useRunStream();
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<SelectedGraphNode | null>(null);

  // Restore selection on mount.
  useEffect(() => {
    const q = readQuery();
    if (q.docId) setSelectedDoc(q.docId);
    if (q.node) setSelectedNode(q.node);
  }, []);

  // Persist selection to URL.
  useEffect(() => {
    writeQuery(selectedDoc, selectedNode);
  }, [selectedDoc, selectedNode]);

  // Default to the first observed doc once events arrive.
  useEffect(() => {
    if (events.length === 0 || selectedDoc) return;
    const firstDoc = events.find((e) => e.doc_id)?.doc_id;
    if (firstDoc) setSelectedDoc(firstDoc);
  }, [events, selectedDoc]);

  return (
    <div className="flex h-full">
      <div className="w-60 flex-shrink-0 border-r border-white/10 overflow-y-auto">
        <DocRailV2
          events={events}
          selectedDoc={selectedDoc}
          onSelect={(docId) => {
            setSelectedDoc(docId);
            // Clear selected graph node — the new doc has its own graph.
            setSelectedNode(null);
          }}
        />
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        <div className="px-4 py-2 border-b border-white/10 flex items-center gap-2 font-mono text-[11px]">
          <span className="text-zinc-300">state inspector</span>
          <span className="px-1 py-0.5 rounded bg-violet-500/20 text-violet-200 text-[9px]">
            v2
          </span>
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
          {selectedDoc && <span className="text-zinc-400 truncate ml-2">{selectedDoc}</span>}
        </div>
        <div className="flex-1 min-h-0">
          {selectedDoc ? (
            <PipelineGraph
              events={events}
              docId={selectedDoc}
              selected={selectedNode}
              onSelectNode={setSelectedNode}
            />
          ) : (
            <div className="p-6 font-mono text-xs text-zinc-500">
              {events.length === 0
                ? "No events yet — start a benchmark run."
                : "Pick a doc on the left to see its LangGraph execution."}
            </div>
          )}
        </div>
      </div>

      <div className="w-[420px] flex-shrink-0 border-l border-white/10 overflow-y-auto">
        {selectedDoc && selectedNode ? (
          <DetailRouter events={events} docId={selectedDoc} node={selectedNode} />
        ) : (
          <div className="p-6 font-mono text-xs text-zinc-500">
            Click a node in the graph to see its I/O.
          </div>
        )}
      </div>
    </div>
  );
}

function DetailRouter({
  events,
  docId,
  node,
}: {
  events: import("@/types/benchmark").RunEvent[];
  docId: string;
  node: SelectedGraphNode;
}) {
  if (node.role === "consensus") {
    return <ConsensusDetail chunkId={`${docId}:_consensus`} events={events} />;
  }
  if (node.role === "ner_encoder" && node.ref) {
    return <NerEncoderDetail events={events} docId={docId} encoder={node.ref} />;
  }
  if (node.role === "spo_window" && node.ref) {
    const chunkId = node.ref;
    const loaded = events.find((e) => e.node_name === "chunk_loaded" && e.chunk_id === chunkId);
    const extracted = events.find(
      (e) => e.node_name === "chunk_extracted" && e.chunk_id === chunkId,
    );
    const docEvents = events.filter((e) => e.chunk_id === chunkId);
    return (
      <ChunkTextPanel
        chunkText={loaded?.details ?? null}
        extracted={extracted?.details ?? null}
        events={docEvents}
        hoveredErrorIndex={null}
      />
    );
  }
  // chunks / pack / persist / spo_model / collapsed: terse stats.
  return <NodeStats events={events} docId={docId} node={node} />;
}

function NodeStats({
  events,
  docId,
  node,
}: {
  events: import("@/types/benchmark").RunEvent[];
  docId: string;
  node: SelectedGraphNode;
}) {
  const docEvents = events.filter((e) => {
    if (!e.chunk_id) return false;
    const eDoc = e.doc_id ?? e.chunk_id.split(":")[0];
    return eDoc === docId;
  });
  let body: React.ReactNode = null;
  if (node.role === "chunks") {
    const chunks = new Set<string>();
    for (const e of docEvents) {
      const cid = e.chunk_id ?? "";
      if (
        e.node_name === "chunk_loaded" &&
        !cid.includes(":_ner_") &&
        !cid.endsWith(":_consensus") &&
        !cid.includes(":win-") &&
        !cid.endsWith(":_doc_ensemble")
      ) {
        chunks.add(cid);
      }
    }
    body = <div>{chunks.size} input chunks</div>;
  } else if (node.role === "pack") {
    const wins = new Set(
      docEvents.filter((e) => e.chunk_id?.includes(":win-")).map((e) => e.chunk_id!),
    );
    body = <div>{wins.size} packed windows</div>;
  } else if (node.role === "spo_model" && node.ref) {
    const m = node.ref;
    let mentions = 0;
    let props = 0;
    for (const e of docEvents) {
      if (e.node_name !== "chunk_extracted") continue;
      if (!e.chunk_id?.includes(":win-")) continue;
      if (e.model !== m) continue;
      const d = (e.details ?? {}) as Record<string, unknown>;
      mentions += (d.mention_count as number) ?? 0;
      props += (d.proposition_count as number) ?? 0;
    }
    body = (
      <div>
        {mentions} mentions · {props} propositions
      </div>
    );
  } else if (node.role === "spo_windows_collapsed") {
    body = <div className="text-zinc-500">click an individual window to inspect (TODO)</div>;
  } else if (node.role === "persist") {
    const persisted = docEvents.find((e) => e.node_name === "persist_artifacts");
    body = (
      <div>{persisted ? `persist_artifacts: ${persisted.status}` : "persist not yet observed"}</div>
    );
  }
  return (
    <div className="p-3 font-mono text-[11px] space-y-2">
      <div className="text-zinc-300 text-[12px]">
        {node.role}
        {node.ref ? ` · ${node.ref}` : ""}
      </div>
      <div className="text-zinc-500 text-[10px]">{docId}</div>
      <div className="text-zinc-200 text-[11px]">{body}</div>
    </div>
  );
}

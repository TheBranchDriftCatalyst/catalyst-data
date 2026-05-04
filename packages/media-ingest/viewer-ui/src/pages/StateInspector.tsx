/**
 * StateInspector — graph-native view of the v4 LangGraph execution.
 *
 * Layout (4-pane, T-shaped):
 *
 *   ┌──────┬──────────────────┬─────────────────────┐
 *   │ Doc  │ Pipeline Graph   │ Document Source     │
 *   │ Rail │  (reactflow TB)  │  (full text + chunk │
 *   │      │                  │   boundary overlay) │
 *   │      ├──────────────────┴─────────────────────┤
 *   │      │ Selected Node Detail                   │
 *   │      │  (consensus / ner_encoder / spo_window │
 *   │      │   / pack / spo_model / persist / …)    │
 *   └──────┴────────────────────────────────────────┘
 *
 * The graph IS the topology: each LangGraph node = one graph node, edges
 * follow the literal data flow. Live updates are driven by `useRunStream`
 * — when events arrive, node statuses (queued / running / ok / error) and
 * payload badges (mention count, duration, retries) recompute and the
 * graph re-renders in place without losing pan/zoom.
 *
 * The right pane shows the *whole* doc text with chunk-boundary overlays
 * so the chunking strategy is visible in context: pre-NER input chunks
 * are subtly bordered, SPO windows are amber, and whichever chunk maps to
 * the currently-selected graph node is highlighted cyan. When a NER
 * encoder is selected, its mention spans are inline-underlined too.
 *
 * The bottom pane is the per-node detail (consensus accept/reject table,
 * encoder mention list, window text + extraction output, etc.).
 *
 * Deep-link query: ?doc=<docId>&node=<role>:<ref>
 */

import { useEffect, useState } from "react";

import { useRunStream } from "@/hooks/useRunStream";
import { useRuns } from "@/hooks/useRuns";
import { DocRailV2 } from "@/components/state/DocRailV2";
import { RunPicker } from "@/components/state/RunPicker";
import ResizablePanel from "@/components/ResizablePanel";
import ResizableSidebar from "@/components/ResizableSidebar";
import {
  PipelineGraph,
  type SelectedGraphNode,
  type NodeRole,
} from "@/components/state/PipelineGraph";
import { ConsensusDetail } from "@/components/state/ConsensusDetail";
import { ChunkTextPanel } from "@/components/state/ChunkTextPanel";
import { NerEncoderDetail } from "@/components/state/NerEncoderDetail";
import { DocumentSourcePanel } from "@/components/state/DocumentSourcePanel";
import { ChunksDetail } from "@/components/state/ChunksDetail";
import { PackDetail } from "@/components/state/PackDetail";

const VALID_ROLES: ReadonlySet<NodeRole> = new Set<NodeRole>([
  "document",
  "ner_encoder",
  "consensus",
  "pack",
  "spo_window",
  "spo_windows_collapsed",
  "spo_model",
  "pruned_window",
  "persist",
]);

function readQuery(): {
  docId: string | null;
  node: SelectedGraphNode | null;
  runId: string | null;
} {
  const p = new URLSearchParams(window.location.search);
  const docId = p.get("doc");
  const runId = p.get("run");
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
  return { docId, node, runId };
}

function writeQuery(docId: string | null, node: SelectedGraphNode | null, runId: string | null) {
  const p = new URLSearchParams();
  if (runId) p.set("run", runId);
  if (docId) p.set("doc", docId);
  if (node) p.set("node", node.ref ? `${node.role}:${node.ref}` : node.role);
  const qs = p.toString();
  const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
  window.history.replaceState({}, "", url);
}

export function StateInspector() {
  const [pinnedRunId, setPinnedRunId] = useState<string | null>(null);
  const { events, connected, error } = useRunStream(pinnedRunId);
  const runs = useRuns();
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<SelectedGraphNode | null>(null);

  // Restore selection on mount.
  useEffect(() => {
    const q = readQuery();
    if (q.docId) setSelectedDoc(q.docId);
    if (q.node) setSelectedNode(q.node);
    if (q.runId) setPinnedRunId(q.runId);
  }, []);

  // Persist selection to URL.
  useEffect(() => {
    writeQuery(selectedDoc, selectedNode, pinnedRunId);
  }, [selectedDoc, selectedNode, pinnedRunId]);

  // Switching run wipes the per-run selections — a doc/node from run X
  // probably doesn't exist (or means something different) in run Y.
  const onRunSelect = (runId: string | null) => {
    setPinnedRunId(runId);
    setSelectedDoc(null);
    setSelectedNode(null);
  };

  // Default to the first observed doc once events arrive.
  useEffect(() => {
    if (events.length === 0 || selectedDoc) return;
    const firstDoc = events.find((e) => e.doc_id)?.doc_id;
    if (firstDoc) setSelectedDoc(firstDoc);
  }, [events, selectedDoc]);

  // Initial load: we haven't fetched anything yet AND we don't know the
  // run state. `connected` flips true on the first successful fetch
  // (even if events array is empty). Show the spinner only during that
  // "haven't talked to backend yet" window — once connected, switch to
  // the real empty-state copy ("No events yet — start a benchmark run").
  const isInitialLoad = !connected && error === null;

  return (
    <div className="flex h-full">
      <ResizableSidebar
        storageKey="state-inspector-rail"
        defaultWidth={240}
        minWidth={180}
        maxWidth={500}
        className="border-r border-white/10 overflow-y-auto"
      >
        <DocRailV2
          events={events}
          selectedDoc={selectedDoc}
          onSelect={(docId) => {
            setSelectedDoc(docId);
            // Clear selected graph node — the new doc has its own graph.
            setSelectedNode(null);
          }}
        />
      </ResizableSidebar>

      {/* Right of the rail: vertical split — top row is graph + doc-source
          side-by-side; bottom row is the selected-node detail. */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="px-4 py-2 border-b border-white/10 flex items-center gap-2 font-mono text-[11px]">
          <span className="text-zinc-300">state inspector</span>
          <RunPicker
            runs={runs.runs}
            liveRunId={runs.live}
            selectedRunId={pinnedRunId}
            onSelect={onRunSelect}
          />
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

        {/* L-shaped layout: doc source spans full height on the right,
         *  graph + detail stack vertically on the left. */}
        <div className="flex flex-1 min-h-0">
          <div className="flex-1 min-w-0 flex flex-col border-r border-white/10">
            <div className="flex-1 min-h-0">
              {selectedDoc ? (
                <PipelineGraph
                  events={events}
                  docId={selectedDoc}
                  selected={selectedNode}
                  onSelectNode={setSelectedNode}
                />
              ) : isInitialLoad ? (
                <LoadingSpinner label="Loading audit log…" />
              ) : (
                <div className="p-6 font-mono text-xs text-zinc-500">
                  {events.length === 0
                    ? "No events yet — start a benchmark run."
                    : "Pick a doc on the left to see its LangGraph execution."}
                </div>
              )}
            </div>
            <ResizablePanel
              storageKey="state-inspector-detail"
              defaultHeight={220}
              minHeight={120}
              maxHeight={600}
              className="bg-surface-1/50 overflow-y-auto border-t border-white/10"
            >
              {selectedDoc && selectedNode ? (
                <DetailRouter events={events} docId={selectedDoc} node={selectedNode} />
              ) : (
                <div className="p-4 font-mono text-[10px] text-zinc-600">
                  Click a node in the graph to see its I/O.
                </div>
              )}
            </ResizablePanel>
          </div>
          <div className="w-[480px] flex-shrink-0 overflow-hidden">
            {selectedDoc ? (
              <DocumentSourcePanel
                events={events}
                docId={selectedDoc}
                selectedNode={selectedNode}
              />
            ) : (
              <div className="p-4 font-mono text-[10px] text-zinc-600">
                Pick a doc to see its source text.
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function LoadingSpinner({ label }: { label: string }) {
  return (
    <div
      data-testid="state-inspector-loading"
      className="h-full flex flex-col items-center justify-center gap-3 font-mono text-xs text-zinc-500"
    >
      <div className="w-6 h-6 border-2 border-zinc-700 border-t-zinc-300 rounded-full animate-spin" />
      <span>{label}</span>
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
  if (node.role === "document") {
    return <ChunksDetail events={events} docId={docId} />;
  }
  if (node.role === "pack") {
    return <PackDetail events={events} docId={docId} />;
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
  if (node.role === "pruned_window" && node.ref) {
    const ev = events.find(
      (e) =>
        e.node_name === "evidence_window_pruned" &&
        ((e.details ?? {}) as { window_id?: string }).window_id === node.ref,
    );
    const d = (ev?.details ?? {}) as Record<string, unknown>;
    return (
      <div className="p-3 font-mono text-[11px] space-y-2">
        <div className="text-zinc-300">pruned window · {String(d.window_id ?? node.ref)}</div>
        <div className="text-amber-300 text-[10px]">{String(d.reason ?? "?")}</div>
        <div className="flex flex-wrap gap-3 text-zinc-500 text-[10px]">
          <span>cluster: {String(d.cluster_id ?? "—")}</span>
          <span>{String(d.mention_count ?? 0)}m</span>
          <span>{String(d.char_count ?? 0)}ch</span>
          {d.chars_per_mention != null && (
            <span>{Number(d.chars_per_mention).toFixed(0)} ch/mention</span>
          )}
        </div>
      </div>
    );
  }
  // persist / spo_model / collapsed: terse stats.
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
  if (node.role === "spo_model" && node.ref) {
    const m = node.ref;
    type Row = {
      chunkId: string;
      window: string;
      mentions: number;
      props: number;
      durationS: number | null;
      status: string;
    };
    const rows: Row[] = [];
    let mentions = 0;
    let props = 0;
    let durationTotal = 0;
    let durationCount = 0;
    for (const e of docEvents) {
      if (e.node_name !== "chunk_extracted") continue;
      if (!e.chunk_id?.includes(":win-")) continue;
      if (e.model !== m) continue;
      const d = (e.details ?? {}) as Record<string, unknown>;
      const ment = (d.mention_count as number) ?? 0;
      const pr = (d.proposition_count as number) ?? 0;
      const dur = typeof d.duration_s === "number" ? (d.duration_s as number) : null;
      mentions += ment;
      props += pr;
      if (dur != null) {
        durationTotal += dur;
        durationCount += 1;
      }
      const win = e.chunk_id.split(":win-")[1] ?? e.chunk_id;
      rows.push({
        chunkId: e.chunk_id,
        window: `win-${win.slice(0, 12)}`,
        mentions: ment,
        props: pr,
        durationS: dur,
        status: e.status ?? "?",
      });
    }
    rows.sort((a, b) => b.mentions + b.props - (a.mentions + a.props));
    body = (
      <div className="space-y-2">
        <div className="flex flex-wrap gap-3 text-zinc-400 text-[10px]">
          <span className="text-zinc-300">{rows.length} windows</span>
          <span className="text-zinc-600">·</span>
          <span>{mentions} mentions</span>
          <span>{props} propositions</span>
          {durationCount > 0 && (
            <>
              <span className="text-zinc-600">·</span>
              <span>{durationTotal.toFixed(1)}s total</span>
              <span>{(durationTotal / durationCount).toFixed(2)}s/win</span>
            </>
          )}
        </div>
        <div className="rounded border border-white/5 max-h-44 overflow-y-auto">
          <table className="w-full text-[10px]">
            <thead className="sticky top-0 bg-surface-1/80 backdrop-blur">
              <tr className="text-zinc-500 text-left">
                <th className="px-2 py-1 font-normal">window</th>
                <th className="px-2 py-1 font-normal text-right">mentions</th>
                <th className="px-2 py-1 font-normal text-right">props</th>
                <th className="px-2 py-1 font-normal text-right">dur</th>
                <th className="px-2 py-1 font-normal">status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.chunkId} className="border-t border-white/5">
                  <td className="px-2 py-0.5 text-zinc-400 truncate">{r.window}</td>
                  <td className="px-2 py-0.5 text-right text-cyan-300">{r.mentions}</td>
                  <td className="px-2 py-0.5 text-right text-violet-300">{r.props}</td>
                  <td className="px-2 py-0.5 text-right text-zinc-500">
                    {r.durationS != null ? `${r.durationS.toFixed(1)}s` : "—"}
                  </td>
                  <td className="px-2 py-0.5 text-zinc-500">{r.status}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-2 py-2 text-zinc-600">
                    no windows extracted yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
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

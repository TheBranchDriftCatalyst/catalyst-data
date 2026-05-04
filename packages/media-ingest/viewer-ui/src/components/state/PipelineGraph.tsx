/**
 * PipelineGraph — visual replica of the v4 LangGraph topology for the
 * selected document.
 *
 *   chunks ─┬─→ ner_gliner-medium    ─┐
 *           ├─→ ner_gliner-large     ─┤
 *           ├─→ ner_gliner-pii       ─┼─→ consensus ─→ pack ─┬─→ win-1 ─→ spo:llama3.1-8b ─→ persist
 *           ├─→ ner_nuextract-2.0-8b ─┤                       ├─→ win-2 ─→ ...
 *           └─→ ner_universalner-7b  ─┘                       └─→ ...
 *
 * Each node is rendered with status (queued / running / ok / error) and
 * a payload badge (mention count, retries, etc). Clicking a node calls
 * onSelectNode(role, model?) so the right pane can swap in the matching
 * detail panel. Layout via dagre LR; live updates via React state — when
 * `events` changes, nodes/edges recompute and re-render in place.
 *
 * Window collapse: when the SPO stage produces > MAX_WIN windows, they
 * collapse into a single "windows ×N" node to keep the topology readable
 * (a bench run can produce hundreds).
 */

import { useEffect, useMemo } from "react";
import dagre from "@dagrejs/dagre";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { RunEvent } from "@/types/benchmark";

const MAX_WINDOW_NODES = 6;

// Logical roles a graph node can play. Each role maps to a right-pane
// detail component; `selectedNode` carries this back up.
export type NodeRole =
  | "document"
  | "ner_encoder"
  | "consensus"
  | "pack"
  | "spo_window"
  | "spo_windows_collapsed"
  | "spo_model"
  | "pruned_window"
  | "persist";

export type NodeStatus = "queued" | "running" | "ok" | "error";

export interface SelectedGraphNode {
  role: NodeRole;
  /** For `ner_encoder` and `spo_model`, the model name. For `spo_window`, the chunk_id. */
  ref: string | null;
}

interface PipelineNodeData extends Record<string, unknown> {
  role: NodeRole;
  ref: string | null;
  title: string;
  badge: string;
  status: NodeStatus;
  selected: boolean;
}

interface Props {
  events: RunEvent[];
  docId: string;
  selected: SelectedGraphNode | null;
  onSelectNode: (sel: SelectedGraphNode) => void;
}

const STATUS_BG: Record<NodeStatus, string> = {
  queued: "bg-zinc-800/60 border-zinc-700",
  running: "bg-amber-500/10 border-amber-500/60 animate-pulse",
  ok: "bg-emerald-500/10 border-emerald-500/60",
  error: "bg-red-500/10 border-red-500/60",
};
const STATUS_DOT: Record<NodeStatus, string> = {
  queued: "bg-zinc-600",
  running: "bg-amber-400",
  ok: "bg-emerald-400",
  error: "bg-red-400",
};

function PipelineNode({ data }: NodeProps) {
  const d = data as PipelineNodeData;
  return (
    <div
      className={`px-2 py-1.5 rounded border font-mono text-[10px] min-w-[120px] ${STATUS_BG[d.status]} ${
        d.selected ? "ring-2 ring-cyan-400" : ""
      }`}
    >
      <Handle type="target" position={Position.Top} className="!bg-zinc-600 !w-1.5 !h-1.5" />
      <div className="flex items-center gap-1.5">
        <span className={`w-1.5 h-1.5 rounded-full ${STATUS_DOT[d.status]}`} />
        <span className="text-zinc-200 truncate flex-1">{d.title}</span>
      </div>
      {d.badge && <div className="text-zinc-400 text-[9px] mt-0.5 pl-3">{d.badge}</div>}
      <Handle type="source" position={Position.Bottom} className="!bg-zinc-600 !w-1.5 !h-1.5" />
    </div>
  );
}

const nodeTypes = { pipeline: PipelineNode };

interface BuiltGraph {
  nodes: Node<PipelineNodeData>[];
  edges: Edge[];
}

function buildGraph(
  events: RunEvent[],
  docId: string,
  selected: SelectedGraphNode | null,
): BuiltGraph {
  // Filter to events for this doc.
  const docEvents = events.filter((e) => {
    if (!e.chunk_id) return false;
    const eDoc = e.doc_id ?? e.chunk_id.split(":")[0];
    return eDoc === docId;
  });

  // Discover encoders + SPO models from chunk_id patterns and event.model.
  const encoders = new Set<string>();
  const spoModels = new Set<string>();
  const winChunkIds = new Set<string>();
  let chunkCount = 0;

  // For status accumulation:
  const encoderStats = new Map<
    string,
    { mentions: number; status: NodeStatus; duration?: number }
  >();
  const winStats = new Map<string, { mentions: number; status: NodeStatus }>();
  const spoStats = new Map<string, { mentions: number; props: number; status: NodeStatus }>();
  let consensusStats: { accepted: number; rejected: number; status: NodeStatus } | null = null;
  let persistStatus: NodeStatus = "queued";

  // Pruned evidence windows (CD-lxcf research follow-up — pack.py emits
  // evidence_window_pruned events for each window dropped by the density
  // heuristic). Render as faded amber nodes downstream of pack so the
  // operator can click to see the prune reason.
  const prunedWindows: Array<{ windowId: string; reason: string }> = [];

  for (const e of docEvents) {
    const cid = e.chunk_id ?? "";
    // chunk count from chunk_loaded events whose chunk_id is the doc-level
    // pre-NER chunks (not :_ner_, not :_consensus, not :win-).
    if (
      e.node_name === "chunk_loaded" &&
      !cid.includes(":_ner_") &&
      !cid.endsWith(":_consensus") &&
      !cid.includes(":win-") &&
      !cid.endsWith(":_doc_ensemble")
    ) {
      chunkCount += 1;
    }

    // Pruned evidence windows
    if (e.node_name === "evidence_window_pruned") {
      const d = (e.details ?? {}) as { window_id?: string; reason?: string };
      if (d.window_id) {
        prunedWindows.push({ windowId: d.window_id, reason: d.reason ?? "?" });
      }
    }

    // NER encoders
    const ner = cid.match(/:_ner_(.+)$/);
    if (ner && ner[1]) {
      const enc = ner[1];
      encoders.add(enc);
      const cur = encoderStats.get(enc) ?? { mentions: 0, status: "queued" as NodeStatus };
      if (e.node_name === "ner_encoder_started") cur.status = "running";
      if (e.node_name === "ner_encoder_completed") {
        cur.status = e.status === "error" || e.status === "failed" ? "error" : "ok";
        const d = (e.details ?? {}) as Record<string, unknown>;
        if (typeof d.duration_s === "number") cur.duration = d.duration_s;
      }
      if (e.node_name === "chunk_extracted") {
        const d = (e.details ?? {}) as Record<string, unknown>;
        cur.mentions = (d.mention_count as number) ?? cur.mentions;
        if (cur.status === "queued") cur.status = "ok";
      }
      encoderStats.set(enc, cur);
    }

    // Consensus
    if (cid.endsWith(":_consensus")) {
      const cur: { accepted: number; rejected: number; status: NodeStatus } = consensusStats ?? {
        accepted: 0,
        rejected: 0,
        status: "queued",
      };
      if (e.node_name === "consensus_started") cur.status = "running";
      if (e.node_name === "consensus_completed") {
        cur.status = "ok";
        const d = (e.details ?? {}) as Record<string, unknown>;
        if (typeof d.accepted_count === "number") cur.accepted = d.accepted_count;
        if (typeof d.rejected_count === "number") cur.rejected = d.rejected_count;
      }
      consensusStats = cur;
    }

    // SPO windows
    if (cid.includes(":win-")) {
      winChunkIds.add(cid);
      const cur = winStats.get(cid) ?? { mentions: 0, status: "queued" as NodeStatus };
      if (e.node_name === "extract_spo" && e.status === "running") cur.status = "running";
      if (e.node_name === "chunk_extracted") {
        cur.status = "ok";
        const d = (e.details ?? {}) as Record<string, unknown>;
        cur.mentions = (d.mention_count as number) ?? cur.mentions;
        if (e.model) {
          spoModels.add(e.model);
          const s = spoStats.get(e.model) ?? {
            mentions: 0,
            props: 0,
            status: "queued" as NodeStatus,
          };
          s.mentions += (d.mention_count as number) ?? 0;
          s.props += (d.proposition_count as number) ?? 0;
          s.status = "ok";
          spoStats.set(e.model, s);
        }
      }
      if (e.status === "error" || e.status === "failed") cur.status = "error";
      winStats.set(cid, cur);
    }

    if (e.node_name === "persist_artifacts" && e.status !== "skipped") {
      persistStatus = e.status === "error" ? "error" : "ok";
    }
  }

  // Build nodes
  const nodes: Node<PipelineNodeData>[] = [];
  const edges: Edge[] = [];
  const isSel = (role: NodeRole, ref: string | null) =>
    !!selected && selected.role === role && selected.ref === ref;

  // document — semantic root of the pipeline. Chunks are the chunker's
  // output and travel as metadata through this node, not their own stage.
  nodes.push({
    id: "document",
    type: "pipeline",
    position: { x: 0, y: 0 },
    data: {
      role: "document",
      ref: null,
      title: "document",
      badge: chunkCount > 0 ? `${chunkCount} chunks` : "—",
      status: chunkCount > 0 ? "ok" : "queued",
      selected: isSel("document", null),
    },
  });

  // encoders (sorted for deterministic order)
  const encoderList = [...encoders].sort();
  for (const enc of encoderList) {
    const s = encoderStats.get(enc) ?? { mentions: 0, status: "queued" as NodeStatus };
    nodes.push({
      id: `ner:${enc}`,
      type: "pipeline",
      position: { x: 0, y: 0 },
      data: {
        role: "ner_encoder",
        ref: enc,
        title: enc,
        badge:
          s.status === "ok"
            ? `${s.mentions}m${s.duration ? ` · ${s.duration.toFixed(1)}s` : ""}`
            : s.status === "error"
              ? "error"
              : s.status === "running"
                ? "…"
                : "—",
        status: s.status,
        selected: isSel("ner_encoder", enc),
      },
    });
    edges.push({
      id: `e:document:${enc}`,
      source: "document",
      target: `ner:${enc}`,
      type: "smoothstep",
      style: { stroke: "rgb(139 92 246 / 0.6)" },
      markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(139 92 246)" },
    });
  }

  // consensus
  if (consensusStats || encoderList.length > 0) {
    const cs = consensusStats ?? { accepted: 0, rejected: 0, status: "queued" as NodeStatus };
    nodes.push({
      id: "consensus",
      type: "pipeline",
      position: { x: 0, y: 0 },
      data: {
        role: "consensus",
        ref: null,
        title: "consensus",
        badge:
          cs.status === "ok"
            ? `${cs.accepted}✓ ${cs.rejected}✗`
            : cs.status === "running"
              ? "…"
              : "—",
        status: cs.status,
        selected: isSel("consensus", null),
      },
    });
    for (const enc of encoderList) {
      edges.push({
        id: `e:${enc}:consensus`,
        source: `ner:${enc}`,
        target: "consensus",
        type: "smoothstep",
        style: { stroke: "rgb(34 211 238 / 0.6)" },
        markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(34 211 238)" },
      });
    }
  }

  // pack + windows (only if SPO ran)
  const winList = [...winChunkIds].sort();
  if (winList.length > 0) {
    nodes.push({
      id: "pack",
      type: "pipeline",
      position: { x: 0, y: 0 },
      data: {
        role: "pack",
        ref: null,
        title: "pack",
        badge: `${winList.length} windows`,
        status: "ok",
        selected: isSel("pack", null),
      },
    });
    edges.push({
      id: "e:consensus:pack",
      source: "consensus",
      target: "pack",
      type: "smoothstep",
      style: { stroke: "rgb(245 158 11 / 0.6)" },
      markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(245 158 11)" },
    });

    if (winList.length > MAX_WINDOW_NODES) {
      // Collapse all windows into one badge.
      nodes.push({
        id: "windows_collapsed",
        type: "pipeline",
        position: { x: 0, y: 0 },
        data: {
          role: "spo_windows_collapsed",
          ref: null,
          title: `windows ×${winList.length}`,
          badge: "click to expand",
          status: "ok",
          selected: isSel("spo_windows_collapsed", null),
        },
      });
      edges.push({
        id: "e:pack:windows",
        source: "pack",
        target: "windows_collapsed",
        type: "smoothstep",
        style: { stroke: "rgb(245 158 11 / 0.6)" },
        markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(245 158 11)" },
      });
      // Fan to spo
      for (const m of spoModels) {
        edges.push({
          id: `e:windows:${m}`,
          source: "windows_collapsed",
          target: `spo:${m}`,
          type: "smoothstep",
          style: { stroke: "rgb(245 158 11 / 0.6)" },
          markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(245 158 11)" },
        });
      }
    } else {
      for (const cid of winList) {
        const s = winStats.get(cid) ?? { mentions: 0, status: "queued" as NodeStatus };
        const winId = cid.split(":win-")[1] ?? cid;
        nodes.push({
          id: cid,
          type: "pipeline",
          position: { x: 0, y: 0 },
          data: {
            role: "spo_window",
            ref: cid,
            title: `win-${winId.slice(0, 6)}`,
            badge: s.status === "ok" ? `${s.mentions}m` : s.status === "running" ? "…" : "—",
            status: s.status,
            selected: isSel("spo_window", cid),
          },
        });
        edges.push({
          id: `e:pack:${cid}`,
          source: "pack",
          target: cid,
          type: "smoothstep",
          style: { stroke: "rgb(245 158 11 / 0.6)" },
          markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(245 158 11)" },
        });
        for (const m of spoModels) {
          edges.push({
            id: `e:${cid}:${m}`,
            source: cid,
            target: `spo:${m}`,
            type: "smoothstep",
            style: { stroke: "rgb(245 158 11 / 0.6)" },
            markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(245 158 11)" },
          });
        }
      }
    }

    // Pruned windows — sibling of kept windows under pack, but they
    // don't fan out to spo_models since the SPO call was skipped.
    // Collapse to one "pruned ×N" node when count > 4 to keep the graph
    // legible (full-corpus runs can produce dozens).
    if (prunedWindows.length > 0) {
      if (prunedWindows.length > 4) {
        nodes.push({
          id: "pruned_collapsed",
          type: "pipeline",
          position: { x: 0, y: 0 },
          data: {
            role: "pruned_window",
            ref: prunedWindows[0]?.windowId ?? null,
            title: `pruned ×${prunedWindows.length}`,
            badge: "click pack to see",
            status: "queued",
            selected: false,
          },
        });
        edges.push({
          id: "e:pack:pruned_collapsed",
          source: "pack",
          target: "pruned_collapsed",
          type: "smoothstep",
          style: { stroke: "rgb(245 158 11 / 0.3)", strokeDasharray: "4 4" },
          markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(245 158 11 / 0.5)" },
        });
      } else {
        for (const p of prunedWindows) {
          const id = `pruned:${p.windowId}`;
          nodes.push({
            id,
            type: "pipeline",
            position: { x: 0, y: 0 },
            data: {
              role: "pruned_window",
              ref: p.windowId,
              title: `pruned · ${p.windowId.slice(0, 8)}`,
              badge: p.reason.split("(")[0]!.trim(),
              status: "queued",
              selected: isSel("pruned_window", p.windowId),
            },
          });
          edges.push({
            id: `e:pack:${id}`,
            source: "pack",
            target: id,
            type: "smoothstep",
            style: { stroke: "rgb(245 158 11 / 0.3)", strokeDasharray: "4 4" },
            markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(245 158 11 / 0.5)" },
          });
        }
      }
    }
  }

  // SPO models
  for (const m of [...spoModels].sort()) {
    const s = spoStats.get(m) ?? { mentions: 0, props: 0, status: "queued" as NodeStatus };
    nodes.push({
      id: `spo:${m}`,
      type: "pipeline",
      position: { x: 0, y: 0 },
      data: {
        role: "spo_model",
        ref: m,
        title: m,
        badge:
          s.status === "ok" ? `${s.mentions}m · ${s.props}p` : s.status === "running" ? "…" : "—",
        status: s.status,
        selected: isSel("spo_model", m),
      },
    });
    edges.push({
      id: `e:${m}:persist`,
      source: `spo:${m}`,
      target: "persist",
      type: "smoothstep",
      style: { stroke: "rgb(34 211 238 / 0.4)" },
      markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(34 211 238 / 0.6)" },
    });
  }

  // persist
  if (spoModels.size > 0 || consensusStats?.status === "ok") {
    nodes.push({
      id: "persist",
      type: "pipeline",
      position: { x: 0, y: 0 },
      data: {
        role: "persist",
        ref: null,
        title: "persist",
        badge: persistStatus === "ok" ? "saved" : "—",
        status: persistStatus,
        selected: isSel("persist", null),
      },
    });
  }

  // Dagre layout (LR)
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 16, ranksep: 50 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) g.setNode(n.id, { width: 150, height: 40 });
  for (const e of edges) g.setEdge(e.source, e.target);
  dagre.layout(g);
  for (const n of nodes) {
    const pos = g.node(n.id);
    if (pos) {
      n.position = { x: pos.x - 75, y: pos.y - 20 };
    }
  }

  return { nodes, edges };
}

function PipelineGraphInner({ events, docId, selected, onSelectNode }: Props) {
  const built = useMemo(() => buildGraph(events, docId, selected), [events, docId, selected]);

  const [nodes, setNodes, onNodesChange] = useNodesState(built.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(built.edges);
  const { fitView } = useReactFlow();

  // Sync built graph → reactflow state on every recompute, then re-fit so
  // late-arriving SPO/persist nodes don't fall off the viewport.
  useEffect(() => {
    setNodes(built.nodes);
    setEdges(built.edges);
    // Defer fitView one tick so reactflow has the new positions before
    // computing the bounding box.
    const t = setTimeout(() => {
      fitView({ padding: 0.1, duration: 200 });
    }, 0);
    return () => clearTimeout(t);
  }, [built, setNodes, setEdges, fitView]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={(_, node) => {
        const d = node.data as PipelineNodeData;
        onSelectNode({ role: d.role, ref: d.ref });
      }}
      fitView
      fitViewOptions={{ padding: 0.1, minZoom: 0.2, maxZoom: 1.5 }}
      proOptions={{ hideAttribution: true }}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      defaultEdgeOptions={{ animated: false }}
    >
      <Background color="#27272a" gap={16} />
      {/* Dark-theme override for reactflow's default light-on-light Controls.
          Arbitrary-variant Tailwind selectors target the internal button
          spans + their fill-via-currentColor SVG icons. */}
      <Controls
        showInteractive={false}
        className={[
          "[&]:bg-zinc-900/90 [&]:border [&]:border-zinc-700 [&]:rounded [&]:overflow-hidden",
          "[&_.react-flow__controls-button]:bg-zinc-900/90",
          "[&_.react-flow__controls-button]:border-zinc-700",
          "[&_.react-flow__controls-button]:text-zinc-300",
          "[&_.react-flow__controls-button:hover]:bg-zinc-800",
          "[&_.react-flow__controls-button>svg]:fill-zinc-300",
        ].join(" ")}
      />
    </ReactFlow>
  );
}

export function PipelineGraph(props: Props) {
  return (
    <ReactFlowProvider>
      <PipelineGraphInner {...props} />
    </ReactFlowProvider>
  );
}

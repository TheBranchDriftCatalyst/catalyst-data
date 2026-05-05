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
import { UpstreamPanel } from "@/components/state/UpstreamPanel";
import { DownstreamPanel } from "@/components/state/DownstreamPanel";
import { MentionTable, type Mention } from "@/components/state/MentionTable";
import { TrendSparkline } from "@/components/TrendSparkline";
import { useTrendData } from "@/hooks/useTrendData";

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
  // Preserve unknown query params (e.g. Gap #7's transient packPreviewMin /
  // packPreviewMaxCpm seeds) — the persist-selection effect would otherwise
  // clobber any param a sibling panel had just stashed in the URL.
  const existing = new URLSearchParams(window.location.search);
  const p = new URLSearchParams();
  for (const [k, v] of existing) {
    if (k === "run" || k === "doc" || k === "node") continue;
    p.set(k, v);
  }
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

  // Gap #8 — TrendSparkline click-to-jump. Click on an older run's dot
  // in a panel header should swap the run while KEEPING doc + node
  // selection. The semantics are deliberately the inverse of
  // ``onRunSelect``: the operator wants to compare "this same panel
  // state across runs", so wiping doc + node would force them to
  // re-pick on every hop. Pass this down to the detail components.
  const setPinnedRunIdPreservingSelection = (runId: string) => {
    setPinnedRunId(runId);
  };

  // Default to the first observed doc once events arrive. Skip ``__run__``
  // (the synthetic harness-level partition key from event_store —
  // ``run_start`` / ``run_end`` events live there and have no chunks asset
  // backing them, so picking it auto-404s the doc-source panel).
  useEffect(() => {
    if (events.length === 0 || selectedDoc) return;
    const firstDoc = events.find((e) => e.doc_id && e.doc_id !== "__run__")?.doc_id;
    if (firstDoc) setSelectedDoc(firstDoc);
  }, [events, selectedDoc]);

  // Initial load: we haven't fetched anything yet AND we don't know the
  // run state. `connected` flips true on the first successful fetch
  // (even if events array is empty). Show the spinner only during that
  // "haven't talked to backend yet" window — once connected, switch to
  // the real empty-state copy ("No events yet — start a benchmark run").
  const isInitialLoad = !connected && error === null;

  return (
    <div data-testid="state-inspector" className="flex h-full">
      <ResizableSidebar
        storageKey="state-inspector-rail"
        defaultWidth={240}
        minWidth={180}
        maxWidth={500}
        className="border-r border-white/10 overflow-y-auto"
        testId="state-inspector-rail"
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
            data-testid="inspector-conn-badge"
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
          <span data-testid="inspector-event-count" className="text-zinc-500">
            {events.length} events
          </span>
          {selectedDoc && (
            <span
              data-testid="inspector-selected-doc-label"
              className="text-zinc-400 truncate ml-2"
            >
              {selectedDoc}
            </span>
          )}
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
              testId="state-inspector-detail-panel"
            >
              {selectedDoc && selectedNode ? (
                <DetailRouter
                  events={events}
                  docId={selectedDoc}
                  node={selectedNode}
                  onSelectNode={setSelectedNode}
                  runId={pinnedRunId ?? runs.latest}
                  onJumpRun={setPinnedRunIdPreservingSelection}
                />
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
  onSelectNode,
  runId,
  onJumpRun,
}: {
  events: import("@/types/benchmark").RunEvent[];
  docId: string;
  node: SelectedGraphNode;
  onSelectNode: (n: SelectedGraphNode | null) => void;
  runId: string | null;
  /** Gap #8 — click-to-jump from a sparkline dot. Selection-preserving
   *  variant of onRunSelect: only updates the run id. */
  onJumpRun: (runId: string) => void;
}) {
  if (node.role === "consensus") {
    return (
      <div data-testid="inspector-detail-consensus" className="h-full min-h-0">
        <ConsensusDetail
          chunkId={`${docId}:_consensus`}
          events={events}
          runId={runId}
          onJumpRun={onJumpRun}
        />
      </div>
    );
  }
  if (node.role === "ner_encoder" && node.ref) {
    return (
      <div data-testid="inspector-detail-ner_encoder" className="h-full min-h-0">
        <NerEncoderDetail
          events={events}
          docId={docId}
          encoder={node.ref}
          runId={runId}
          onJumpRun={onJumpRun}
        />
      </div>
    );
  }
  if (node.role === "document") {
    // Stack the Dagster lineage above the per-doc chunks list so the
    // operator sees both at the document scope. Each section gets its
    // own scroll container so a long chunks list doesn't push the
    // upstream panel offscreen.
    return (
      <div data-testid="inspector-detail-document" className="flex flex-col h-full min-h-0">
        <div className="flex-shrink-0 max-h-[40%] overflow-y-auto">
          <UpstreamPanel events={events} docId={docId} />
        </div>
        <div className="border-t border-white/5 flex-1 min-h-0 overflow-y-auto">
          <ChunksDetail events={events} docId={docId} />
        </div>
      </div>
    );
  }
  if (node.role === "persist") {
    // Gap #10 — persist gets its own block (mirror of the document
    // node hosting UpstreamPanel). Lifted out of NodeStats so the
    // downstream lineage cards aren't squeezed into a one-line status
    // strip. Symmetric naming with the document branch above. The
    // Gap #8 trend sparkline is preserved by mounting it next to the
    // panel header — keeping the existing
    // ``inspector-detail-persist > trend-sparkline`` selector chain
    // working for the cross-run regression tests.
    return (
      <div data-testid="inspector-detail-persist" className="h-full min-h-0 flex flex-col">
        <div className="flex justify-end px-3 pt-2">
          <NodeStatsTrend
            metric="persist_wall_clock_seconds"
            docId={docId}
            runId={runId}
            onJumpRun={onJumpRun}
            trend="down-good"
          />
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto">
          <DownstreamPanel events={events} docId={docId} />
        </div>
      </div>
    );
  }
  if (node.role === "pack") {
    return (
      <div data-testid="inspector-detail-pack" className="h-full min-h-0">
        <PackDetail events={events} docId={docId} runId={runId} onJumpRun={onJumpRun} />
      </div>
    );
  }
  if (node.role === "spo_window" && node.ref) {
    const chunkId = node.ref;
    const loaded = events.find((e) => e.node_name === "chunk_loaded" && e.chunk_id === chunkId);
    const extracted = events.find(
      (e) => e.node_name === "chunk_extracted" && e.chunk_id === chunkId,
    );
    const docEvents = events.filter((e) => e.chunk_id === chunkId);
    return (
      <div data-testid="inspector-detail-spo_window" className="h-full min-h-0">
        <ChunkTextPanel
          chunkText={loaded?.details ?? null}
          extracted={extracted?.details ?? null}
          events={docEvents}
          hoveredErrorIndex={null}
          runId={runId}
          chunkId={chunkId}
        />
      </div>
    );
  }
  if (node.role === "pruned_window" && node.ref) {
    const ev = events.find(
      (e) =>
        e.node_name === "evidence_window_pruned" &&
        ((e.details ?? {}) as { window_id?: string }).window_id === node.ref,
    );
    const d = (ev?.details ?? {}) as Record<string, unknown>;
    // Find the parent pack_evidence event to surface the threshold values
    // the operator can tune (PACK_MIN_MENTIONS / PACK_MAX_CHARS_PER_MENTION).
    const packEvent = events.find(
      (e) =>
        e.node_name === "pack_evidence" &&
        e.status === "completed" &&
        (e.doc_id === docId || e.chunk_id?.startsWith(`${docId}:`)),
    );
    const packDetails = (packEvent?.details ?? {}) as Record<string, unknown>;
    const minMentions = packDetails.prune_min_mentions as number | undefined;
    const maxCharsPerMention = packDetails.prune_max_chars_per_mention as number | undefined;
    // Find any consensus mentions whose cluster_id matches; the cluster
    // contributes its raw_mentions list which is useful context for "why
    // is this window so sparse".
    const clusterId = String(d.cluster_id ?? "");
    const consensusEv = events.find(
      (e) => e.node_name === "chunk_extracted" && e.chunk_id === `${docId}:_consensus`,
    );
    const consensusDetails = (consensusEv?.details ?? {}) as {
      mentions?: Array<Record<string, unknown>>;
    };
    const clusterMentions = (consensusDetails.mentions ?? []).filter(
      (m) => String(m.cluster_id ?? "") === clusterId,
    );

    const reason = String(d.reason ?? "?");
    const reasonHint = reason.startsWith("too_few_mentions")
      ? `→ raise --pack-min-mentions or accept this window manually`
      : reason.startsWith("sparse_density")
        ? `→ raise --pack-max-chars-per-mention or tighten the window context`
        : "";

    // ── Gap #7 — counterfactual delta + cross-panel handoff ────────────
    // Pruning rules in pack.py:
    //   too_few_mentions: mention_count < prune_min_mentions
    //   sparse_density:   chars_per_mention > prune_max_chars_per_mention
    // So the deterministic counterfactuals are:
    //   would-be-kept iff min_mentions ≤ mention_count       (suggest = mention_count)
    //   would-be-kept iff max_chars_per_mention ≥ chars_per_mention
    //                                                        (suggest = ceil(cpm))
    // Both conditions can fire on the same window if the emit logic ever
    // grows OR-of-reasons; today the elif means at most one is set, but we
    // still defend against composite reason strings like "too_few_mentions+sparse_density".
    const mentionCount = typeof d.mention_count === "number" ? (d.mention_count as number) : 0;
    const charCount = typeof d.char_count === "number" ? (d.char_count as number) : 0;
    // cpm may be null for legacy events — derive from char_count / mention_count.
    let cpm: number | null;
    if (typeof d.chars_per_mention === "number") {
      cpm = d.chars_per_mention as number;
    } else if (mentionCount > 0) {
      cpm = charCount / mentionCount;
    } else {
      cpm = null;
    }

    const reasonHasTooFew = reason.startsWith("too_few_mentions");
    const reasonHasSparse = reason.startsWith("sparse_density");
    // Defensive: composite "too_few_mentions+sparse_density" or callers
    // that ever change the reason format — match anywhere.
    const showTooFewRow = reasonHasTooFew || /too_few_mentions/.test(reason);
    const showSparseRow = reasonHasSparse || /sparse_density/.test(reason);

    // Suggested thresholds:
    //   min_mentions: integer = mention_count (pruning was at min, would
    //                 keep at min ≤ mention_count, so suggest = mention_count)
    //   max_chars_per_mention: ceil(cpm) so the threshold actually
    //                 clears the offending window (cpm > max would prune;
    //                 we need max ≥ cpm — ceil makes that hold for
    //                 fractional cpm without rounding into rejection).
    const suggestedMinMentions = mentionCount;
    const suggestedMaxCpm = cpm != null ? Math.ceil(cpm) : null;

    const handleTuneInPack = () => {
      const params = new URLSearchParams(window.location.search);
      // Drop any prior preview params, then attach the relevant ones.
      params.delete("packPreviewMin");
      params.delete("packPreviewMaxCpm");
      if (showTooFewRow) {
        params.set("packPreviewMin", String(suggestedMinMentions));
      }
      if (showSparseRow && suggestedMaxCpm != null) {
        params.set("packPreviewMaxCpm", String(suggestedMaxCpm));
      }
      // Persist the run/doc state, swap node → pack. We rewrite the
      // search string ourselves (rather than relying on writeQuery) so
      // the preview params survive past the next selection effect's
      // writeQuery call — that helper rebuilds the URL from selection
      // state alone. We let the Pack histogram strip them on mount.
      const qs = params.toString();
      const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
      window.history.replaceState({}, "", url);
      onSelectNode({ role: "pack", ref: null });
    };

    return (
      <div data-testid="inspector-detail-pruned_window" className="h-full min-h-0">
        <div data-testid="pruned-window-detail" className="p-3 font-mono text-[11px] space-y-3">
          <div className="flex items-baseline gap-3">
            <span className="text-zinc-300 text-[12px]">
              pruned window · {String(d.window_id ?? node.ref)}
            </span>
            <span className="text-zinc-500 text-[10px]">cluster: {clusterId || "—"}</span>
          </div>

          {/* Pruning reason + suggested action */}
          <div
            data-testid="pruned-thresholds"
            className="rounded border border-amber-500/20 bg-amber-500/5 px-2 py-1.5 space-y-1"
          >
            <div data-testid="pruned-reason" className="text-amber-300">
              {reason}
            </div>
            {reasonHint && <div className="text-amber-200 text-[10px]">{reasonHint}</div>}
            <div className="flex flex-wrap gap-3 text-zinc-500 text-[10px]">
              <span>
                {String(d.mention_count ?? 0)} mentions
                {minMentions != null ? (
                  <span className="text-zinc-600"> (min={minMentions})</span>
                ) : null}
              </span>
              <span>{String(d.char_count ?? 0)} chars</span>
              {d.chars_per_mention != null ? (
                <span>
                  {Number(d.chars_per_mention).toFixed(0)} ch/mention
                  {maxCharsPerMention != null ? (
                    <span className="text-zinc-600"> (max={maxCharsPerMention})</span>
                  ) : null}
                </span>
              ) : mentionCount === 0 ? (
                <span className="text-zinc-600">(no chars/mention — empty window)</span>
              ) : null}
            </div>

            {/* Gap #7 — Counterfactual delta block + cross-panel handoff */}
            {(showTooFewRow || showSparseRow) && (
              <div
                data-testid="pruned-counterfactual-block"
                className="mt-1.5 pt-1.5 border-t border-amber-500/15 space-y-1"
              >
                {showTooFewRow && (
                  <div
                    data-testid="pruned-counterfactual-row-too-few-mentions"
                    className="text-emerald-300 text-[10px] leading-tight"
                  >
                    {mentionCount === 0 ? (
                      <>
                        <div>
                          ✓ would be kept if min_mentions ={" "}
                          <span data-testid="pruned-counterfactual-suggested-min-mentions">0</span>
                          <span className="text-zinc-500">
                            {"  "}(degenerate — 0-mention windows produce no SPO output anyway)
                          </span>
                        </div>
                      </>
                    ) : (
                      <>
                        <div>
                          ✓ would be kept if min_mentions ≤{" "}
                          <span data-testid="pruned-counterfactual-suggested-min-mentions">
                            {suggestedMinMentions}
                          </span>
                        </div>
                        <div className="text-zinc-500 text-[10px] pl-3">
                          was pruned at min={minMentions ?? "?"}; this window had {mentionCount}{" "}
                          mention{mentionCount === 1 ? "" : "s"}
                        </div>
                      </>
                    )}
                  </div>
                )}
                {showSparseRow && cpm != null && suggestedMaxCpm != null && (
                  <div
                    data-testid="pruned-counterfactual-row-sparse-density"
                    className="text-emerald-300 text-[10px] leading-tight"
                  >
                    <div>
                      ✓ would be kept if max_chars_per_mention ≥{" "}
                      <span data-testid="pruned-counterfactual-suggested-max-chars-per-mention">
                        {suggestedMaxCpm}
                      </span>
                    </div>
                    <div className="text-zinc-500 text-[10px] pl-3">
                      was pruned at max={maxCharsPerMention ?? "?"}; this window had{" "}
                      {cpm.toFixed(0)} ch/mention
                    </div>
                  </div>
                )}
                <button
                  type="button"
                  data-testid="pruned-tune-in-pack"
                  onClick={handleTuneInPack}
                  className="mt-0.5 text-cyan-300 hover:text-cyan-200 text-[10px] underline-offset-2 hover:underline"
                >
                  → tune in pack threshold histograms
                </button>
              </div>
            )}
          </div>

          {/* Cluster mentions that landed in this window */}
          {clusterMentions.length > 0 && (
            <div data-testid="pruned-cluster-mentions" className="space-y-1">
              <div className="text-zinc-500 text-[9px] uppercase tracking-wide">
                cluster mentions ({clusterMentions.length})
              </div>
              <MentionTable
                rows={clusterMentions.map<Mention>((m) => {
                  const typeStr = (m.canonical_type ?? m.mention_type) as string | undefined;
                  const voteCount = m.vote_count as number | undefined;
                  const nEnc = m.n_encoders as number | undefined;
                  const conf = m.mean_confidence as number | undefined;
                  return {
                    text: String(m.text ?? ""),
                    type: typeStr ?? undefined,
                    vote:
                      voteCount != null && nEnc != null
                        ? { count: voteCount, total: nEnc }
                        : undefined,
                    confidence: typeof conf === "number" ? conf : undefined,
                    variant: "accepted",
                  };
                })}
                columns={["text", "type", "vote", "conf"]}
                className="space-y-1 max-h-32 overflow-y-auto pr-0.5"
                rowTestId="pruned-cluster-row"
              />
            </div>
          )}
        </div>
      </div>
    );
  }
  // spo_model / spo_windows_collapsed: terse stats. (persist was lifted
  // out into its own DownstreamPanel block above as part of Gap #10.)
  return (
    <NodeStats
      events={events}
      docId={docId}
      node={node}
      onSelectNode={onSelectNode}
      runId={runId}
      onJumpRun={onJumpRun}
    />
  );
}

function NodeStats({
  events,
  docId,
  node,
  onSelectNode,
  runId,
  onJumpRun,
}: {
  events: import("@/types/benchmark").RunEvent[];
  docId: string;
  node: SelectedGraphNode;
  onSelectNode: (n: SelectedGraphNode | null) => void;
  runId: string | null;
  onJumpRun: (runId: string) => void;
}) {
  // Gap #8 wiring lands here when NodeStats grows a sparkline header;
  // until then suppress unused-var lints rather than dropping the props.
  void runId;
  void onJumpRun;
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
    let tokensIn = 0;
    let tokensOut = 0;
    let costTotal = 0;
    let anyCostNull = false;
    let anyTokens = false;
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
      // Sum tokens + cost across SPO calls for this model on this doc.
      // If any window's cost_usd is null (model not in cost table) we
      // suppress the dollar rendering entirely — a half-known sum would
      // mislead the operator.
      const usage = (d.usage as Record<string, unknown> | undefined) ?? {};
      const tIn = typeof usage.tokens_in === "number" ? (usage.tokens_in as number) : null;
      const tOut = typeof usage.tokens_out === "number" ? (usage.tokens_out as number) : null;
      if (tIn != null) {
        tokensIn += tIn;
        anyTokens = true;
      }
      if (tOut != null) {
        tokensOut += tOut;
        anyTokens = true;
      }
      const cost = d.cost_usd;
      if (cost === null) {
        anyCostNull = true;
      } else if (typeof cost === "number") {
        costTotal += cost;
      } else {
        // Field absent — no signal either way; treat as null to be safe.
        anyCostNull = true;
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
    const formatTokenCount = (n: number) =>
      n >= 1000 ? `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k` : String(n);
    const formatTotalCost = (v: number) => (v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(3)}`);
    body = (
      <div className="space-y-2">
        <div className="flex flex-wrap gap-3 text-zinc-400 text-[10px]">
          <span className="text-zinc-300">{rows.length} windows</span>
          <span className="text-zinc-600">·</span>
          <span data-testid="spo-model-total-mentions">{mentions} mentions</span>
          <span data-testid="spo-model-total-props">{props} propositions</span>
          {durationCount > 0 && (
            <>
              <span className="text-zinc-600">·</span>
              <span data-testid="spo-model-total-duration">{durationTotal.toFixed(1)}s total</span>
              <span>{(durationTotal / durationCount).toFixed(2)}s/win</span>
            </>
          )}
          {anyTokens && (
            <>
              <span className="text-zinc-600">·</span>
              <span data-testid="spo-model-tokens-cost" className="text-zinc-300">
                {formatTokenCount(tokensIn)} tok in
                <span className="text-zinc-600"> · </span>
                {formatTokenCount(tokensOut)} out
                {!anyCostNull && costTotal > 0 && (
                  <>
                    <span className="text-zinc-600"> · </span>
                    <span className="text-emerald-300">{formatTotalCost(costTotal)}</span>
                  </>
                )}
              </span>
            </>
          )}
        </div>
        <div
          data-testid="spo-model-table"
          className="rounded border border-white/5 max-h-44 overflow-y-auto"
        >
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
                <tr
                  key={r.chunkId}
                  data-testid={`spo-model-row-${r.chunkId}`}
                  className="border-t border-white/5"
                >
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
    // Aggregate every chunk_extracted event whose chunk_id includes ":win-"
    // into a (window, model) matrix. Lets the operator see at a glance
    // which model produced what for each evidence window, and click any
    // window cell to drill into the spo_window detail.
    type Cell = {
      mentions: number;
      props: number;
      durationS: number | null;
      status: string;
    };
    const windowOrder: string[] = [];
    const winSeen = new Set<string>();
    const models = new Set<string>();
    const cells = new Map<string, Cell>(); // key: `${cid}|${model}`
    let totalMentions = 0;
    let totalProps = 0;
    let totalDuration = 0;
    let totalDurationCount = 0;
    for (const e of docEvents) {
      if (e.node_name !== "chunk_extracted") continue;
      if (!e.chunk_id?.includes(":win-")) continue;
      const cid = e.chunk_id;
      const model = e.model ?? "?";
      models.add(model);
      if (!winSeen.has(cid)) {
        winSeen.add(cid);
        windowOrder.push(cid);
      }
      const d = (e.details ?? {}) as Record<string, unknown>;
      const ment = (d.mention_count as number) ?? 0;
      const pr = (d.proposition_count as number) ?? 0;
      const dur = typeof d.duration_s === "number" ? (d.duration_s as number) : null;
      cells.set(`${cid}|${model}`, {
        mentions: ment,
        props: pr,
        durationS: dur,
        status: e.status ?? "?",
      });
      totalMentions += ment;
      totalProps += pr;
      if (dur != null) {
        totalDuration += dur;
        totalDurationCount += 1;
      }
    }
    const modelList = [...models].sort();
    const zeroPropWindows = windowOrder.filter((cid) =>
      modelList.every((m) => (cells.get(`${cid}|${m}`)?.props ?? 0) === 0),
    );

    body = (
      <div className="space-y-2">
        <div className="flex flex-wrap gap-3 text-zinc-400 text-[10px]">
          <span className="text-zinc-300">{windowOrder.length} windows</span>
          <span className="text-zinc-600">·</span>
          <span>{modelList.length} models</span>
          <span className="text-zinc-600">·</span>
          <span>{totalMentions} mentions</span>
          <span>{totalProps} props</span>
          {totalDurationCount > 0 && (
            <>
              <span className="text-zinc-600">·</span>
              <span>{totalDuration.toFixed(1)}s total</span>
            </>
          )}
        </div>
        {/* Pathology callout — gemma3-12b on this run returned 0 props for
         *  every window. Surface it so the operator notices the prompt /
         *  model is failing rather than each window being legitimately empty. */}
        {totalProps === 0 && windowOrder.length > 0 && (
          <div
            data-testid="spo-collapsed-pathology"
            className="rounded border border-amber-500/30 bg-amber-500/5 px-2 py-1.5 text-amber-300 text-[10px]"
          >
            ⚠ every window returned 0 propositions — likely a SPO prompt or model-output parsing
            failure (mentions ≠ propositions). Click a window to inspect the raw extractor output.
          </div>
        )}
        {totalProps > 0 && zeroPropWindows.length > 0 && (
          <div className="text-amber-300/80 text-[10px]">
            {zeroPropWindows.length}/{windowOrder.length} windows produced 0 props across all
            models.
          </div>
        )}
        <div
          data-testid="spo-collapsed-matrix"
          className="rounded border border-white/5 max-h-44 overflow-y-auto"
        >
          <table className="w-full text-[10px]">
            <thead className="sticky top-0 bg-surface-1/80 backdrop-blur">
              <tr className="text-zinc-500 text-left">
                <th className="px-2 py-1 font-normal">window</th>
                {modelList.map((m) => (
                  <th key={m} className="px-2 py-1 font-normal text-right">
                    {m}
                  </th>
                ))}
                <th className="px-2 py-1 font-normal text-right">total</th>
              </tr>
            </thead>
            <tbody>
              {windowOrder.map((cid) => {
                const winId = cid.split(":win-")[1] ?? cid;
                let rowMent = 0;
                let rowProps = 0;
                return (
                  <tr
                    key={cid}
                    data-testid={`spo-collapsed-row-${cid}`}
                    onClick={() => onSelectNode({ role: "spo_window", ref: cid })}
                    className="border-t border-white/5 hover:bg-cyan-500/5 cursor-pointer"
                  >
                    <td className="px-2 py-0.5 text-cyan-300/90 truncate max-w-[140px]">
                      win-{winId.slice(0, 8)}
                    </td>
                    {modelList.map((m) => {
                      const c = cells.get(`${cid}|${m}`);
                      const ment = c?.mentions ?? 0;
                      const pr = c?.props ?? 0;
                      rowMent += ment;
                      rowProps += pr;
                      return (
                        <td key={m} className="px-2 py-0.5 text-right">
                          <span className="text-cyan-300">{ment}m</span>
                          <span className="text-zinc-600 mx-0.5">·</span>
                          <span className={pr === 0 ? "text-amber-300/70" : "text-violet-300"}>
                            {pr}p
                          </span>
                        </td>
                      );
                    })}
                    <td className="px-2 py-0.5 text-right text-zinc-400">
                      {rowMent}m / {rowProps}p
                    </td>
                  </tr>
                );
              })}
              {windowOrder.length === 0 && (
                <tr>
                  <td colSpan={modelList.length + 2} className="px-2 py-2 text-zinc-600">
                    no SPO windows extracted yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="text-zinc-600 text-[10px]">
          click any row to inspect that window's mentions + raw SPO output.
        </div>
      </div>
    );
  }
  // Gap #10 — the `persist` branch was lifted out of NodeStats into a
  // dedicated DetailRouter block hosting <DownstreamPanel>. NodeStats
  // is now `spo_model | spo_windows_collapsed` only.
  // Gap #8 — Cross-run trend sparkline. Renders for spo_model (mean
  // props/win). Falls back gracefully: <TrendSparkline /> renders a stub
  // when no runs are loaded yet.
  let sparkline: React.ReactNode = null;
  if (node.role === "spo_model" && node.ref) {
    sparkline = (
      <NodeStatsTrend
        metric="spo_mean_props_per_window"
        docId={docId}
        model={node.ref}
        runId={runId}
        onJumpRun={onJumpRun}
        trend="up-good"
      />
    );
  }
  return (
    <div
      data-testid={`inspector-detail-${node.role}`}
      className="p-3 font-mono text-[11px] space-y-2"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-zinc-300 text-[12px]">
            {node.role}
            {node.ref ? ` · ${node.ref}` : ""}
          </div>
          <div className="text-zinc-500 text-[10px]">{docId}</div>
        </div>
        {sparkline}
      </div>
      <div className="text-zinc-200 text-[11px]">{body}</div>
    </div>
  );
}

/** Tiny adapter that wires ``useTrendData`` to ``<TrendSparkline>`` for
 *  the NodeStats panels (spo_model) plus the persist branch in
 *  DetailRouter. Pulled out so the data hook only fires when the panel
 *  actually renders, not on every detail-router branch. */
function NodeStatsTrend({
  metric,
  docId,
  model,
  runId,
  onJumpRun,
  trend,
}: {
  metric: import("@/hooks/useTrendData").TrendMetric;
  docId: string;
  model?: string;
  runId: string | null;
  onJumpRun: (runId: string) => void;
  trend: "up-good" | "down-good";
}) {
  const { points } = useTrendData({ axis: "doc", metric, docId, model });
  return (
    <TrendSparkline
      points={points}
      metric={metric}
      currentRunId={runId}
      onSelectRun={onJumpRun}
      trend={trend}
    />
  );
}

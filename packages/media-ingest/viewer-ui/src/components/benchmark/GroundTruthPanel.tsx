import { useState, useEffect, useMemo } from "react";
import type { GroundTruthFile, GroundTruthChunk } from "@/types/benchmark";
import { TypeBadge, MetricLabel } from "./shared";

// ── Data fetching ────────────────────────────────────────────────────

interface GTListEntry {
  name: string;
  label: string;
}

async function fetchGTList(): Promise<GTListEntry[]> {
  // Try to fetch a manifest, fall back to known filenames
  const known = [
    "active",
    "ensemble-4model",
    "ensemble-5model",
    "gpt-4o-single",
    "manually-reviewed",
  ];
  const results: GTListEntry[] = [];

  for (const name of known) {
    try {
      const res = await fetch(`/viewer/ground-truth/${name}.json`, { method: "HEAD" });
      if (res.ok) {
        results.push({
          name,
          label: name.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
        });
      }
    } catch {
      // skip
    }
  }

  return results;
}

async function fetchGT(name: string): Promise<GroundTruthFile | null> {
  try {
    const res = await fetch(`/viewer/ground-truth/${name}.json`);
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

// ── Chunk Detail ─────────────────────────────────────────────────────

function ChunkDetail({ chunk }: { chunk: GroundTruthChunk }) {
  const [showMentions, setShowMentions] = useState(true);
  const [showProps, setShowProps] = useState(true);

  // Highlight mentions in text
  const highlightedText = useMemo(() => {
    if (!chunk.mentions.length) return <span className="text-zinc-300">{chunk.text}</span>;

    // Sort mentions by span_start (handle nulls)
    const sorted = [...chunk.mentions]
      .filter((m) => m.span_start != null && m.span_end != null)
      .sort((a, b) => a.span_start! - b.span_start!);

    if (sorted.length === 0) return <span className="text-zinc-300">{chunk.text}</span>;

    const parts: React.ReactNode[] = [];
    let lastEnd = 0;

    for (let i = 0; i < sorted.length; i++) {
      const m = sorted[i]!;
      const start = m.span_start!;
      const end = m.span_end!;

      // Text before this mention
      if (start > lastEnd) {
        parts.push(
          <span key={`pre-${i}`} className="text-zinc-300">
            {chunk.text.slice(lastEnd, start)}
          </span>,
        );
      }

      // The mention itself
      parts.push(
        <mark
          key={`m-${i}`}
          className="bg-cyan-500/20 text-cyan-200 rounded px-0.5 border-b border-cyan-500/40"
          title={`${m.mention_type} (${(m.confidence * 100).toFixed(0)}%)`}
        >
          {chunk.text.slice(start, end)}
        </mark>,
      );

      lastEnd = end;
    }

    // Remaining text
    if (lastEnd < chunk.text.length) {
      parts.push(
        <span key="tail" className="text-zinc-300">
          {chunk.text.slice(lastEnd)}
        </span>,
      );
    }

    return <>{parts}</>;
  }, [chunk]);

  return (
    <div className="space-y-3">
      {/* Source text with highlights */}
      <div className="bg-surface-0 rounded p-3 text-xs font-mono leading-relaxed max-h-[200px] overflow-y-auto border border-white/5">
        {highlightedText}
      </div>

      {/* Mentions */}
      <div>
        <button
          onClick={() => setShowMentions(!showMentions)}
          className="text-xs font-mono text-zinc-400 hover:text-zinc-200 focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400 rounded px-1"
          aria-expanded={showMentions}
        >
          {showMentions ? "▼" : "▶"} Mentions ({chunk.mentions.length})
        </button>
        {showMentions && (
          <table className="w-full text-xs font-mono mt-2">
            <thead>
              <tr className="text-zinc-400 border-b border-white/5">
                <th className="text-left py-1 px-2">Text</th>
                <th className="text-left py-1 px-2">Type</th>
                <th className="text-center py-1 px-2">Span</th>
                <th className="text-center py-1 px-2">Conf</th>
              </tr>
            </thead>
            <tbody>
              {chunk.mentions.map((m, i) => (
                <tr key={i} className="border-b border-white/5 hover:bg-white/[0.02]">
                  <td className="py-1.5 px-2 text-zinc-200">{m.text}</td>
                  <td className="py-1.5 px-2">
                    <TypeBadge type={m.mention_type} />
                  </td>
                  <td className="py-1.5 px-2 text-center text-zinc-500">
                    {m.span_start != null ? `${m.span_start}:${m.span_end}` : "—"}
                  </td>
                  <td className="py-1.5 px-2 text-center text-zinc-400">
                    {(m.confidence * 100).toFixed(0)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Propositions */}
      <div>
        <button
          onClick={() => setShowProps(!showProps)}
          className="text-xs font-mono text-zinc-400 hover:text-zinc-200 focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400 rounded px-1"
          aria-expanded={showProps}
        >
          {showProps ? "▼" : "▶"} Propositions ({chunk.propositions.length})
        </button>
        {showProps && (
          <table className="w-full text-xs font-mono mt-2">
            <thead>
              <tr className="text-zinc-400 border-b border-white/5">
                <th className="text-left py-1 px-2">Subject</th>
                <th className="text-left py-1 px-2">Predicate</th>
                <th className="text-left py-1 px-2">Object</th>
                <th className="text-center py-1 px-2">Conf</th>
              </tr>
            </thead>
            <tbody>
              {chunk.propositions.map((p, i) => (
                <tr key={i} className="border-b border-white/5 hover:bg-white/[0.02]">
                  <td className="py-1.5 px-2 text-zinc-200">{p.subject}</td>
                  <td className="py-1.5 px-2 text-cyan-400">{p.predicate}</td>
                  <td className="py-1.5 px-2 text-zinc-200">{p.object}</td>
                  <td className="py-1.5 px-2 text-center text-zinc-400">
                    {(p.confidence * 100).toFixed(0)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── Main Component ───────────────────────────────────────────────────

export function GroundTruthPanel() {
  const [gtList, setGtList] = useState<GTListEntry[]>([]);
  const [selectedGT, setSelectedGT] = useState("active");
  const [gt, setGt] = useState<GroundTruthFile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedChunk, setSelectedChunk] = useState<number>(0);

  // Discover available ground truths
  useEffect(() => {
    fetchGTList().then((list) => {
      setGtList(list);
      if (list.length > 0 && !list.find((l) => l.name === selectedGT)) {
        setSelectedGT(list[0]!.name);
      }
    });
  }, []);

  // Load selected ground truth
  useEffect(() => {
    if (!selectedGT) return;
    setLoading(true);
    setError(null);
    fetchGT(selectedGT).then((data) => {
      if (data) {
        setGt(data);
        setSelectedChunk(0);
      } else {
        setError(`Could not load ground truth: ${selectedGT}`);
      }
      setLoading(false);
    });
  }, [selectedGT]);

  if (loading) {
    return <div className="text-zinc-500 text-xs font-mono py-4">Loading ground truth...</div>;
  }

  if (error) {
    return <div className="text-amber-400 text-xs font-mono py-4">{error}</div>;
  }

  if (!gt) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <div className="text-zinc-400 text-sm mb-2">No ground truth available.</div>
        <div className="text-zinc-500 text-xs mb-4">
          Generate ground truth to see and edit annotations.
        </div>
        <pre className="text-xs text-zinc-400 bg-surface-1 border border-white/5 rounded p-3">
          python tests/benchmark_harness.py --ensemble-gt
        </pre>
      </div>
    );
  }

  const currentChunk = gt.chunks[selectedChunk];

  return (
    <div className="space-y-4">
      {/* Header: selector + metadata */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <label htmlFor="gt-selector" className="text-xs text-zinc-400 font-mono">
            <MetricLabel
              label="Ground Truth"
              tooltip="Select which ground truth file to view/edit. 'active' is used for scoring."
            />
          </label>
          <select
            id="gt-selector"
            value={selectedGT}
            onChange={(e) => setSelectedGT(e.target.value)}
            aria-label="Select ground truth file"
            className="bg-surface-1 border border-white/10 rounded px-2 py-1 text-xs font-mono text-zinc-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
          >
            {gtList.map((entry) => (
              <option key={entry.name} value={entry.name}>
                {entry.label}
              </option>
            ))}
          </select>
        </div>

        {/* Metadata badges */}
        <div className="flex items-center gap-3 text-xs font-mono">
          <span className={gt.manually_reviewed ? "text-emerald-400" : "text-amber-400"}>
            {gt.manually_reviewed ? "Reviewed" : "Unreviewed"}
          </span>
          <span className="text-zinc-500">{gt.reference_model}</span>
          <span className="text-zinc-500">{gt.chunk_count} chunks</span>
          <span className="text-zinc-500">{gt.total_mentions} mentions</span>
          <span className="text-zinc-500">{gt.total_propositions} propositions</span>
        </div>
      </div>

      {/* Ensemble config if present */}
      {gt.ensemble_config && (
        <div className="bg-surface-1 border border-white/5 rounded p-3 text-xs font-mono text-zinc-500">
          <span className="text-zinc-400">Ensemble:</span> NER models:{" "}
          {gt.ensemble_config.ner_models.join(", ")} | SPO models:{" "}
          {gt.ensemble_config.spo_models.join(", ")} | Threshold: {gt.ensemble_config.threshold}
        </div>
      )}

      {/* Two-panel layout: chunk list + detail */}
      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">
        {/* Left: Chunk list */}
        <div className="bg-surface-1 border border-white/5 rounded-lg overflow-y-auto max-h-[500px]">
          <div className="p-2 text-xs font-mono text-zinc-400 border-b border-white/5 sticky top-0 bg-surface-1">
            Chunks ({gt.chunks.length})
          </div>
          <div className="space-y-0.5 p-1">
            {gt.chunks.map((chunk, i) => (
              <button
                key={chunk.chunk_id}
                onClick={() => setSelectedChunk(i)}
                className={`w-full text-left px-3 py-2 rounded text-xs font-mono transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400 ${
                  i === selectedChunk
                    ? "bg-cyan-500/10 text-zinc-100 border border-cyan-500/20"
                    : "text-zinc-400 hover:bg-white/[0.03] hover:text-zinc-200"
                }`}
                aria-selected={i === selectedChunk}
              >
                <div className="flex justify-between items-center">
                  <span className="truncate max-w-[180px]">{chunk.chunk_id.slice(0, 20)}</span>
                  <span className="text-zinc-600 flex-shrink-0 ml-2">
                    {chunk.mentions.length}m {chunk.propositions.length}p
                  </span>
                </div>
                <div className="text-zinc-600 truncate mt-0.5 text-[11px]">
                  {chunk.text.slice(0, 60)}...
                </div>
              </button>
            ))}
          </div>
        </div>

        {/* Right: Chunk detail */}
        <div className="bg-surface-1 border border-white/5 rounded-lg p-4 overflow-y-auto max-h-[500px]">
          {currentChunk ? (
            <div>
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-xs font-mono text-zinc-300">
                  Chunk {selectedChunk + 1}/{gt.chunks.length}
                </h4>
                <span className="text-xs font-mono text-zinc-600">{currentChunk.chunk_id}</span>
              </div>
              <ChunkDetail chunk={currentChunk} />
            </div>
          ) : (
            <div className="text-zinc-500 text-xs">No chunk selected</div>
          )}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={() => {
            if (!gt) return;
            const blob = new Blob([JSON.stringify(gt, null, 2)], { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `${selectedGT}.json`;
            a.click();
            URL.revokeObjectURL(url);
          }}
          className="px-3 py-1.5 text-xs font-mono bg-surface-1 border border-white/10 rounded text-zinc-300 hover:text-zinc-100 hover:border-white/20 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 transition-colors"
        >
          Download JSON
        </button>
        <span className="text-xs font-mono text-zinc-600">
          Re-score:{" "}
          <code className="text-zinc-400">
            python tests/benchmark_harness.py --score --use-gt {selectedGT}
          </code>
        </span>
      </div>
    </div>
  );
}

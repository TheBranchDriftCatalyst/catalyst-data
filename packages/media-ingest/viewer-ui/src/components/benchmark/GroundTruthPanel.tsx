import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import type {
  GroundTruthFile,
  GroundTruthChunk,
  GroundTruthMention,
  GroundTruthProposition,
} from "@/types/benchmark";
import type { AutosaveStatus, VisibleChunkEntry } from "@/types/gt-editor";
import { TypeBadge, MetricLabel, TYPE_COLORS } from "./shared";
import { GTSelector } from "./GTSelector";

/** Debounce window for autosave writes (ms). */
const AUTOSAVE_DEBOUNCE_MS = 500;

/** Format an epoch ms timestamp as HH:MM:SS for the saved-at indicator. */
function formatSavedAt(at: number): string {
  const d = new Date(at);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

/**
 * Returns true when the keyboard event originated inside an editable element
 * (form fields, contenteditable). We swallow keyboard nav in that case so the
 * user can type 'j' or 'k' into a mention/text input without jumping chunks.
 */
function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  return false;
}

// ── Data fetching ────────────────────────────────────────────────────

const ENTITY_TYPES = Object.keys(TYPE_COLORS);

async function fetchGT(name: string): Promise<GroundTruthFile | null> {
  try {
    const res = await fetch(`/viewer/ground-truth/${name}.json`);
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

function deepClone<T>(obj: T): T {
  return JSON.parse(JSON.stringify(obj));
}

// ── Editable Mention Row ─────────────────────────────────────────────

function MentionRow({
  mention,
  index,
  onUpdate,
  onRemove,
  editing,
  onStartEdit,
  onStopEdit,
}: {
  mention: GroundTruthMention;
  index: number;
  onUpdate: (idx: number, m: GroundTruthMention) => void;
  onRemove: (idx: number) => void;
  editing: boolean;
  onStartEdit: () => void;
  onStopEdit: () => void;
}) {
  if (!editing) {
    return (
      <tr
        className="border-b border-white/5 hover:bg-white/[0.02] cursor-pointer"
        onClick={onStartEdit}
      >
        <td className="py-1.5 px-2 text-zinc-200">{mention.text}</td>
        <td className="py-1.5 px-2">
          <TypeBadge type={mention.mention_type} />
        </td>
        <td className="py-1.5 px-2 text-center text-zinc-500">
          {mention.span_start != null ? `${mention.span_start}:${mention.span_end}` : "—"}
        </td>
        <td className="py-1.5 px-2 text-center text-zinc-400">
          {(mention.confidence * 100).toFixed(0)}%
        </td>
        <td className="py-1 px-1 w-8" />
      </tr>
    );
  }

  return (
    <tr className="border-b border-white/5 bg-cyan-500/5">
      <td className="py-1 px-1">
        <input
          type="text"
          value={mention.text}
          onChange={(e) => onUpdate(index, { ...mention, text: e.target.value })}
          className="w-full bg-surface-0 border border-white/10 rounded px-2 py-1 text-xs font-mono text-zinc-200 focus:outline-none focus:ring-1 focus:ring-cyan-400"
        />
      </td>
      <td className="py-1 px-1">
        <select
          value={mention.mention_type}
          onChange={(e) => onUpdate(index, { ...mention, mention_type: e.target.value })}
          className="bg-surface-0 border border-white/10 rounded px-1 py-1 text-xs font-mono text-zinc-200 focus:outline-none focus:ring-1 focus:ring-cyan-400"
        >
          {ENTITY_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </td>
      <td className="py-1 px-1 text-center text-zinc-500 text-xs">
        {mention.span_start != null ? `${mention.span_start}:${mention.span_end}` : "auto"}
      </td>
      <td className="py-1 px-1">
        <input
          type="number"
          min={0}
          max={100}
          value={Math.round(mention.confidence * 100)}
          onChange={(e) =>
            onUpdate(index, { ...mention, confidence: parseInt(e.target.value || "100") / 100 })
          }
          className="w-14 bg-surface-0 border border-white/10 rounded px-1 py-1 text-xs font-mono text-zinc-200 text-center focus:outline-none focus:ring-1 focus:ring-cyan-400"
        />
      </td>
      <td className="py-1 px-1 w-16">
        <div className="flex gap-1">
          <button
            onClick={onStopEdit}
            className="text-emerald-400 hover:text-emerald-300 text-xs px-1"
            title="Done"
          >
            ✓
          </button>
          <button
            onClick={() => onRemove(index)}
            className="text-red-400 hover:text-red-300 text-xs px-1"
            title="Remove"
          >
            ✕
          </button>
        </div>
      </td>
    </tr>
  );
}

// ── Editable Proposition Row ─────────────────────────────────────────

function PropositionRow({
  prop,
  index,
  onUpdate,
  onRemove,
  editing,
  onStartEdit,
  onStopEdit,
}: {
  prop: GroundTruthProposition;
  index: number;
  onUpdate: (idx: number, p: GroundTruthProposition) => void;
  onRemove: (idx: number) => void;
  editing: boolean;
  onStartEdit: () => void;
  onStopEdit: () => void;
}) {
  if (!editing) {
    return (
      <tr
        className="border-b border-white/5 hover:bg-white/[0.02] cursor-pointer"
        onClick={onStartEdit}
      >
        <td className="py-1.5 px-2 text-zinc-200">{prop.subject}</td>
        <td className="py-1.5 px-2 text-cyan-400">{prop.predicate}</td>
        <td className="py-1.5 px-2 text-zinc-200">{prop.object}</td>
        <td className="py-1.5 px-2 text-center text-zinc-400">
          {(prop.confidence * 100).toFixed(0)}%
        </td>
        <td className="py-1 px-1 w-8" />
      </tr>
    );
  }

  const inputClass =
    "w-full bg-surface-0 border border-white/10 rounded px-2 py-1 text-xs font-mono text-zinc-200 focus:outline-none focus:ring-1 focus:ring-cyan-400";

  return (
    <tr className="border-b border-white/5 bg-cyan-500/5">
      <td className="py-1 px-1">
        <input
          type="text"
          value={prop.subject}
          onChange={(e) => onUpdate(index, { ...prop, subject: e.target.value })}
          className={inputClass}
        />
      </td>
      <td className="py-1 px-1">
        <input
          type="text"
          value={prop.predicate}
          onChange={(e) => onUpdate(index, { ...prop, predicate: e.target.value })}
          className={inputClass}
        />
      </td>
      <td className="py-1 px-1">
        <input
          type="text"
          value={prop.object}
          onChange={(e) => onUpdate(index, { ...prop, object: e.target.value })}
          className={inputClass}
        />
      </td>
      <td className="py-1 px-1">
        <input
          type="number"
          min={0}
          max={100}
          value={Math.round(prop.confidence * 100)}
          onChange={(e) =>
            onUpdate(index, { ...prop, confidence: parseInt(e.target.value || "100") / 100 })
          }
          className="w-14 bg-surface-0 border border-white/10 rounded px-1 py-1 text-xs font-mono text-zinc-200 text-center focus:outline-none focus:ring-1 focus:ring-cyan-400"
        />
      </td>
      <td className="py-1 px-1 w-16">
        <div className="flex gap-1">
          <button
            onClick={onStopEdit}
            className="text-emerald-400 hover:text-emerald-300 text-xs px-1"
            title="Done"
          >
            ✓
          </button>
          <button
            onClick={() => onRemove(index)}
            className="text-red-400 hover:text-red-300 text-xs px-1"
            title="Remove"
          >
            ✕
          </button>
        </div>
      </td>
    </tr>
  );
}

// ── Chunk Editor ─────────────────────────────────────────────────────

function ChunkEditor({
  chunk,
  onChunkChange,
}: {
  chunk: GroundTruthChunk;
  onChunkChange: (updated: GroundTruthChunk) => void;
}) {
  const [showMentions, setShowMentions] = useState(true);
  const [showProps, setShowProps] = useState(true);
  const [editingMention, setEditingMention] = useState<number | null>(null);
  const [editingProp, setEditingProp] = useState<number | null>(null);

  const updateMention = (idx: number, m: GroundTruthMention) => {
    const mentions = [...chunk.mentions];
    mentions[idx] = m;
    onChunkChange({ ...chunk, mentions });
  };

  const removeMention = (idx: number) => {
    const mentions = chunk.mentions.filter((_, i) => i !== idx);
    onChunkChange({ ...chunk, mentions });
    setEditingMention(null);
  };

  const addMention = () => {
    const m: GroundTruthMention = {
      text: "",
      mention_type: "PERSON",
      span_start: null,
      span_end: null,
      confidence: 1.0,
    };
    onChunkChange({ ...chunk, mentions: [...chunk.mentions, m] });
    setEditingMention(chunk.mentions.length);
  };

  const updateProp = (idx: number, p: GroundTruthProposition) => {
    const propositions = [...chunk.propositions];
    propositions[idx] = p;
    onChunkChange({ ...chunk, propositions });
  };

  const removeProp = (idx: number) => {
    const propositions = chunk.propositions.filter((_, i) => i !== idx);
    onChunkChange({ ...chunk, propositions });
    setEditingProp(null);
  };

  const addProp = () => {
    const p: GroundTruthProposition = {
      subject: "",
      predicate: "",
      object: "",
      confidence: 1.0,
    };
    onChunkChange({ ...chunk, propositions: [...chunk.propositions, p] });
    setEditingProp(chunk.propositions.length);
  };

  // Highlight mentions in text
  const highlightedText = useMemo(() => {
    if (!chunk.mentions.length) return <span className="text-zinc-300">{chunk.text}</span>;

    const sorted = [...chunk.mentions]
      .map((m, i) => ({ ...m, origIdx: i }))
      .filter((m) => m.span_start != null && m.span_end != null)
      .sort((a, b) => a.span_start! - b.span_start!);

    if (sorted.length === 0) return <span className="text-zinc-300">{chunk.text}</span>;

    const parts: React.ReactNode[] = [];
    let lastEnd = 0;

    for (let i = 0; i < sorted.length; i++) {
      const m = sorted[i]!;
      const start = m.span_start!;
      const end = m.span_end!;

      if (start > lastEnd) {
        parts.push(
          <span key={`pre-${i}`} className="text-zinc-300">
            {chunk.text.slice(lastEnd, start)}
          </span>,
        );
      }

      const isEditing = editingMention === m.origIdx;
      parts.push(
        <mark
          key={`m-${i}`}
          className={`rounded px-0.5 border-b cursor-pointer ${
            isEditing
              ? "bg-cyan-500/40 text-cyan-100 border-cyan-400"
              : "bg-cyan-500/20 text-cyan-200 border-cyan-500/40 hover:bg-cyan-500/30"
          }`}
          title={`${m.mention_type} — click to edit`}
          onClick={() => setEditingMention(isEditing ? null : m.origIdx)}
        >
          {chunk.text.slice(start, end)}
        </mark>,
      );

      lastEnd = end;
    }

    if (lastEnd < chunk.text.length) {
      parts.push(
        <span key="tail" className="text-zinc-300">
          {chunk.text.slice(lastEnd)}
        </span>,
      );
    }

    return <>{parts}</>;
  }, [chunk, editingMention]);

  return (
    <div className="space-y-3">
      {/* Source text with clickable highlights */}
      <div className="bg-surface-0 rounded p-3 text-xs font-mono leading-relaxed max-h-[200px] overflow-y-auto border border-white/5">
        {highlightedText}
      </div>

      {/* Mentions */}
      <div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowMentions(!showMentions)}
            className="text-xs font-mono text-zinc-400 hover:text-zinc-200 focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400 rounded px-1"
            aria-expanded={showMentions}
          >
            {showMentions ? "▼" : "▶"} Mentions ({chunk.mentions.length})
          </button>
          {showMentions && (
            <button
              onClick={addMention}
              className="text-xs font-mono text-cyan-400 hover:text-cyan-300 px-1.5 py-0.5 border border-cyan-500/30 rounded hover:border-cyan-400/50"
            >
              + Add
            </button>
          )}
        </div>
        {showMentions && (
          <table className="w-full text-xs font-mono mt-2">
            <thead>
              <tr className="text-zinc-400 border-b border-white/5">
                <th className="text-left py-1 px-2">Text</th>
                <th className="text-left py-1 px-2">Type</th>
                <th className="text-center py-1 px-2">Span</th>
                <th className="text-center py-1 px-2">Conf</th>
                <th className="w-16" />
              </tr>
            </thead>
            <tbody>
              {chunk.mentions.map((m, i) => (
                <MentionRow
                  key={i}
                  mention={m}
                  index={i}
                  onUpdate={updateMention}
                  onRemove={removeMention}
                  editing={editingMention === i}
                  onStartEdit={() => setEditingMention(i)}
                  onStopEdit={() => setEditingMention(null)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Propositions */}
      <div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowProps(!showProps)}
            className="text-xs font-mono text-zinc-400 hover:text-zinc-200 focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400 rounded px-1"
            aria-expanded={showProps}
          >
            {showProps ? "▼" : "▶"} Propositions ({chunk.propositions.length})
          </button>
          {showProps && (
            <button
              onClick={addProp}
              className="text-xs font-mono text-cyan-400 hover:text-cyan-300 px-1.5 py-0.5 border border-cyan-500/30 rounded hover:border-cyan-400/50"
            >
              + Add
            </button>
          )}
        </div>
        {showProps && (
          <table className="w-full text-xs font-mono mt-2">
            <thead>
              <tr className="text-zinc-400 border-b border-white/5">
                <th className="text-left py-1 px-2">Subject</th>
                <th className="text-left py-1 px-2">Predicate</th>
                <th className="text-left py-1 px-2">Object</th>
                <th className="text-center py-1 px-2">Conf</th>
                <th className="w-16" />
              </tr>
            </thead>
            <tbody>
              {chunk.propositions.map((p, i) => (
                <PropositionRow
                  key={i}
                  prop={p}
                  index={i}
                  onUpdate={updateProp}
                  onRemove={removeProp}
                  editing={editingProp === i}
                  onStartEdit={() => setEditingProp(i)}
                  onStopEdit={() => setEditingProp(null)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ── Main Component ───────────────────────────────────────────────────

export function GroundTruthPanel({
  selectedGT: externalSelectedGT,
  onSelectGT: externalOnSelectGT,
}: {
  selectedGT?: string;
  onSelectGT?: (name: string) => void;
} = {}) {
  const [internalSelectedGT, setInternalSelectedGT] = useState("active");
  const selectedGT = externalSelectedGT ?? internalSelectedGT;
  const setSelectedGT = externalOnSelectGT ?? setInternalSelectedGT;
  const [gt, setGt] = useState<GroundTruthFile | null>(null);
  const [originalGt, setOriginalGt] = useState<GroundTruthFile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedChunk, setSelectedChunk] = useState<number>(0);
  const [chunkFilter, setChunkFilter] = useState<string>("");

  const isDirty = useMemo(() => {
    if (!gt || !originalGt) return false;
    return JSON.stringify(gt) !== JSON.stringify(originalGt);
  }, [gt, originalGt]);

  // Filtered chunk list: substring match on chunk_id (case-insensitive). The
  // filter is informational only — it does not mutate the GT file or shift
  // indices; we keep the original index alongside each visible entry so
  // selection + keyboard nav still address the underlying array.
  const visibleChunks = useMemo<VisibleChunkEntry[]>(() => {
    if (!gt) return [];
    const all: VisibleChunkEntry[] = gt.chunks.map((chunk, origIndex) => ({ chunk, origIndex }));
    const q = chunkFilter.trim().toLowerCase();
    if (!q) return all;
    return all.filter(({ chunk }) => chunk.chunk_id.toLowerCase().includes(q));
  }, [gt, chunkFilter]);

  // GT list discovery is handled by GTSelector component

  // Load selected ground truth.
  //
  // We null out gt/originalGt synchronously *before* kicking off the fetch so
  // that any autosave timer the previous file scheduled (still-dirty edits at
  // switch time) is cancelled by the autosave effect's next run: with gt===null
  // the effect's `if (!isDirty || !gt) return` early-exits, and its cleanup
  // clears the pending timer. Without this, a 500ms-debounced PUT against the
  // *new* selectedGT path can fire with the *old* file's edits and corrupt
  // the freshly-loaded file on disk.
  useEffect(() => {
    if (!selectedGT) return;
    setGt(null);
    setOriginalGt(null);
    setLoading(true);
    setError(null);
    fetchGT(selectedGT).then((data) => {
      if (data) {
        setGt(deepClone(data));
        setOriginalGt(deepClone(data));
        setSelectedChunk(0);
        setChunkFilter("");
      } else {
        setError(`Could not load ground truth: ${selectedGT}`);
      }
      setLoading(false);
    });
  }, [selectedGT]);

  // Warn before leaving with unsaved changes
  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  // Per-chunk reviewed counter. Drives the header indicator + Next-unreviewed
  // shortcut. Computed from the live gt state so it updates as the reviewer
  // ticks chunks off; round-trips to disk via the autosave effect.
  const reviewedCount = useMemo(() => {
    if (!gt) return 0;
    return gt.chunks.reduce((n, c) => n + (c.reviewed ? 1 : 0), 0);
  }, [gt]);

  const handleToggleReviewed = useCallback((origIndex: number, value: boolean) => {
    setGt((prev) => {
      if (!prev) return prev;
      const chunks = [...prev.chunks];
      const target = chunks[origIndex];
      if (!target) return prev;
      chunks[origIndex] = { ...target, reviewed: value };
      return { ...prev, chunks };
    });
  }, []);

  const handleJumpToNextUnreviewed = useCallback(() => {
    if (!gt || gt.chunks.length === 0) return;
    // Search forward from selectedChunk+1, wrap to 0 on miss. Bails out when
    // every chunk is already reviewed (UI handles the disabled state).
    const total = gt.chunks.length;
    for (let step = 1; step <= total; step++) {
      const idx = (selectedChunk + step) % total;
      const candidate = gt.chunks[idx];
      if (candidate && !candidate.reviewed) {
        setSelectedChunk(idx);
        return;
      }
    }
  }, [gt, selectedChunk]);

  const handleChunkChange = useCallback(
    (updated: GroundTruthChunk) => {
      if (!gt) return;
      const chunks = [...gt.chunks];
      chunks[selectedChunk] = updated;
      setGt({
        ...gt,
        chunks,
        total_mentions: chunks.reduce((s, c) => s + c.mentions.length, 0),
        total_propositions: chunks.reduce((s, c) => s + c.propositions.length, 0),
      });
    },
    [gt, selectedChunk],
  );

  const [saveStatus, setSaveStatus] = useState<AutosaveStatus>({ kind: "idle" });

  // Latest GT snapshot mirrored into a ref so the debounce timer reads the
  // most recent edits at the moment it fires (avoids stale-closure writes).
  const gtRef = useRef<GroundTruthFile | null>(null);
  useEffect(() => {
    gtRef.current = gt;
  }, [gt]);

  /**
   * Persist the GT file. `markReviewed` flips `manually_reviewed: true` —
   * autosave passes false (review status is intent, not a side effect of
   * editing); the explicit Save button passes true.
   *
   * Returns the resulting AutosaveStatus so callers can react to it.
   */
  const persistGT = useCallback(
    async (snapshot: GroundTruthFile, markReviewed: boolean): Promise<AutosaveStatus> => {
      const exported: GroundTruthFile = markReviewed
        ? { ...snapshot, manually_reviewed: true }
        : snapshot;
      const json = JSON.stringify(exported, null, 2);

      // Try PUT to Vite dev server (writes to disk)
      try {
        const res = await fetch(`/viewer/ground-truth/${selectedGT}.json`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: json,
        });
        if (res.ok) {
          setOriginalGt(deepClone(exported));
          return { kind: "saved", at: Date.now() };
        }
      } catch {
        // Dev server not available — fall back to download
      }

      // Fallback: download as file (only meaningful for explicit save)
      if (markReviewed) {
        const blob = new Blob([json], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${selectedGT}.json`;
        a.click();
        URL.revokeObjectURL(url);
        setOriginalGt(deepClone(exported));
        return { kind: "fallback", at: Date.now() };
      }

      // Autosave failed silently — surface as error so the user knows their
      // edits aren't on disk. We keep originalGt unchanged so isDirty stays
      // true and a retry happens on the next edit.
      return { kind: "error", message: "Autosave failed (dev server unreachable)" };
    },
    [selectedGT],
  );

  const handleSave = useCallback(async () => {
    if (!gt) return;
    setSaveStatus({ kind: "saving" });
    const next = await persistGT(gt, true);
    setSaveStatus(next);
  }, [gt, persistGT]);

  // ── Debounced autosave ───────────────────────────────────────────────
  // Schedule a write AUTOSAVE_DEBOUNCE_MS after the most recent edit. The
  // timer is cleared on every change so rapid edits coalesce into one PUT.
  // We also clear the timer on unmount and when switching GT files so a
  // pending write can't clobber a freshly-loaded file.
  const autosaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (autosaveTimer.current) {
      clearTimeout(autosaveTimer.current);
      autosaveTimer.current = null;
    }
    if (!isDirty || !gt) return;

    autosaveTimer.current = setTimeout(() => {
      const snapshot = gtRef.current;
      if (!snapshot) return;
      setSaveStatus({ kind: "saving" });
      void persistGT(snapshot, false).then(setSaveStatus);
    }, AUTOSAVE_DEBOUNCE_MS);

    return () => {
      if (autosaveTimer.current) {
        clearTimeout(autosaveTimer.current);
        autosaveTimer.current = null;
      }
    };
  }, [gt, isDirty, persistGT]);

  // ── Keyboard nav (j/k + arrow up/down) ───────────────────────────────
  // Navigates within the currently-visible (filtered) chunk list. Skipped
  // entirely when focus is in a form field so editing values isn't hijacked.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isEditableTarget(e.target)) return;
      let delta = 0;
      if (e.key === "j" || e.key === "ArrowDown") delta = 1;
      else if (e.key === "k" || e.key === "ArrowUp") delta = -1;
      else return;
      if (visibleChunks.length === 0) return;
      e.preventDefault();
      // Find current selection within the visible window; if it's filtered
      // out, fall back to the start (delta=1) or end (delta=-1) of the list.
      const visIdx = visibleChunks.findIndex((v) => v.origIndex === selectedChunk);
      let nextVis: number;
      if (visIdx === -1) {
        nextVis = delta > 0 ? 0 : visibleChunks.length - 1;
      } else {
        nextVis = Math.max(0, Math.min(visibleChunks.length - 1, visIdx + delta));
      }
      const target = visibleChunks[nextVis];
      if (target) setSelectedChunk(target.origIndex);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [visibleChunks, selectedChunk]);

  const handleReset = useCallback(() => {
    if (!originalGt) return;
    setGt(deepClone(originalGt));
  }, [originalGt]);

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
      {/* Header: selector + metadata + save controls */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <span className="text-xs text-zinc-400 font-mono">
            <MetricLabel
              label="Ground Truth"
              tooltip="Select which ground truth file to view/edit. 'active' is used for scoring."
            />
          </span>
          <GTSelector
            selected={selectedGT}
            onChange={(name) => {
              if (isDirty && !confirm("You have unsaved changes. Switch anyway?")) return;
              setSelectedGT(name);
            }}
          />
        </div>

        {/* Metadata */}
        <div className="flex items-center gap-3 text-xs font-mono">
          <span className={gt.manually_reviewed ? "text-emerald-400" : "text-amber-400"}>
            {gt.manually_reviewed ? "Reviewed" : "Unreviewed"}
          </span>
          <span className="text-zinc-500">{gt.reference_model}</span>
          <span className="text-zinc-500">{gt.total_mentions} mentions</span>
          <span className="text-zinc-500">{gt.total_propositions} propositions</span>
          <span
            className="text-cyan-400/80"
            title="Per-chunk reviewed count — distinct from the file-level manually_reviewed flag"
          >
            {reviewedCount}/{gt.chunks.length} chunks reviewed
          </span>
          <button
            type="button"
            onClick={handleJumpToNextUnreviewed}
            disabled={reviewedCount >= gt.chunks.length}
            className="px-2 py-0.5 border border-white/10 rounded text-zinc-300 hover:bg-white/[0.05] hover:text-zinc-100 disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400"
            title="Jump to the next chunk that hasn't been marked reviewed (wraps from the bottom)"
            aria-label="Jump to next unreviewed chunk"
          >
            Next unreviewed →
          </button>
        </div>

        {/* Save controls */}
        <div className="flex items-center gap-2 ml-auto">
          {isDirty && saveStatus.kind !== "saving" && (
            <>
              <span className="text-amber-400 text-xs font-mono">Unsaved</span>
              <button
                onClick={handleReset}
                className="px-2 py-1 text-xs font-mono bg-surface-1 border border-white/10 rounded text-zinc-400 hover:text-zinc-200 transition-colors"
              >
                Reset
              </button>
            </>
          )}
          {saveStatus.kind === "saving" && (
            <span className="text-cyan-400 text-xs font-mono">Saving…</span>
          )}
          {saveStatus.kind === "saved" && (
            <span className="text-emerald-400 text-xs font-mono">
              Saved at {formatSavedAt(saveStatus.at)}
            </span>
          )}
          {saveStatus.kind === "fallback" && (
            <span className="text-amber-400 text-xs font-mono">
              Downloaded at {formatSavedAt(saveStatus.at)}
            </span>
          )}
          {saveStatus.kind === "error" && (
            <span className="text-red-400 text-xs font-mono" title={saveStatus.message}>
              Autosave failed
            </span>
          )}
          <button
            onClick={handleSave}
            disabled={saveStatus.kind === "saving"}
            className={`px-3 py-1.5 text-xs font-mono rounded border transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 ${
              isDirty
                ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/30"
                : "bg-surface-1 border-white/10 text-zinc-400 hover:text-zinc-200"
            }`}
          >
            {isDirty ? "Save & mark reviewed" : "Mark reviewed"}
          </button>
        </div>
      </div>

      {/* Ensemble config */}
      {gt.ensemble_config && (
        <div className="bg-surface-1 border border-white/5 rounded p-3 text-xs font-mono text-zinc-500">
          <span className="text-zinc-400">Ensemble:</span> NER:{" "}
          {gt.ensemble_config.ner_models.join(", ")} | SPO:{" "}
          {gt.ensemble_config.spo_models.join(", ")} | Threshold: {gt.ensemble_config.threshold}
        </div>
      )}

      {/* Two-panel layout */}
      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">
        {/* Left: Chunk list */}
        <div className="bg-surface-1 border border-white/5 rounded-lg overflow-y-auto max-h-[500px]">
          <div className="sticky top-0 bg-surface-1 border-b border-white/5 z-10">
            <div className="p-2 text-xs font-mono text-zinc-400 flex items-center justify-between">
              <span>
                Chunks ({visibleChunks.length}
                {visibleChunks.length !== gt.chunks.length && `/${gt.chunks.length}`})
              </span>
              <span className="text-[10px] text-zinc-600" title="j/k or arrow keys navigate">
                j/k
              </span>
            </div>
            <div className="px-2 pb-2">
              <input
                type="text"
                value={chunkFilter}
                onChange={(e) => setChunkFilter(e.target.value)}
                placeholder="Filter by chunk_id…"
                aria-label="Filter chunks by chunk_id substring"
                className="w-full bg-surface-0 border border-white/10 rounded px-2 py-1 text-xs font-mono text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-cyan-400"
              />
            </div>
          </div>
          <div className="space-y-0.5 p-1">
            {visibleChunks.length === 0 && (
              <div className="px-3 py-4 text-xs font-mono text-zinc-500 text-center">
                No chunks match "{chunkFilter}"
              </div>
            )}
            {visibleChunks.map(({ chunk, origIndex }) => {
              const isSelected = origIndex === selectedChunk;
              const isReviewed = !!chunk.reviewed;
              // De-emphasize reviewed-but-not-selected rows; selection always
              // dominates so the reviewer can still find their place.
              const baseClass = isSelected
                ? "bg-cyan-500/10 text-zinc-100 border border-cyan-500/20"
                : isReviewed
                  ? "text-zinc-500 hover:bg-white/[0.02] hover:text-zinc-300 opacity-60"
                  : "text-zinc-400 hover:bg-white/[0.03] hover:text-zinc-200";
              return (
                <button
                  key={chunk.chunk_id}
                  onClick={() => setSelectedChunk(origIndex)}
                  className={`w-full text-left px-3 py-2 rounded text-xs font-mono transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-cyan-400 ${baseClass}`}
                  aria-selected={isSelected}
                >
                  <div className="flex justify-between items-center">
                    <span className="truncate max-w-[160px] flex items-center gap-1">
                      {isReviewed && (
                        <span className="text-emerald-400" aria-label="reviewed">
                          ✓
                        </span>
                      )}
                      {chunk.chunk_id.slice(0, 20)}
                    </span>
                    <span className="text-zinc-600 flex-shrink-0 ml-2">
                      {chunk.mentions.length}m {chunk.propositions.length}p
                    </span>
                  </div>
                  <div className="text-zinc-600 truncate mt-0.5 text-[11px]">
                    {chunk.text.slice(0, 60)}...
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Right: Chunk editor */}
        <div className="bg-surface-1 border border-white/5 rounded-lg p-4 overflow-y-auto max-h-[500px]">
          {currentChunk ? (
            <div>
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-xs font-mono text-zinc-300">
                  Chunk {selectedChunk + 1}/{gt.chunks.length}
                </h4>
                <div className="flex items-center gap-3">
                  <label
                    className="flex items-center gap-1.5 text-xs font-mono text-zinc-400 cursor-pointer select-none hover:text-zinc-200"
                    title="Mark this chunk as reviewed. Distinct from the file-level manually_reviewed flag — tracks per-chunk progress through the GT pass."
                  >
                    <input
                      type="checkbox"
                      checked={!!currentChunk.reviewed}
                      onChange={(e) => handleToggleReviewed(selectedChunk, e.target.checked)}
                      className="accent-emerald-500"
                      aria-label="Mark chunk as reviewed"
                    />
                    Reviewed
                  </label>
                  <span className="text-xs font-mono text-zinc-600">{currentChunk.chunk_id}</span>
                </div>
              </div>
              <ChunkEditor chunk={currentChunk} onChunkChange={handleChunkChange} />
            </div>
          ) : (
            <div className="text-zinc-500 text-xs">No chunk selected</div>
          )}
        </div>
      </div>

      {/* Actions footer */}
      <div className="flex items-center gap-3 text-xs font-mono text-zinc-600 flex-wrap">
        Click any row to edit. Edits autosave to disk every {AUTOSAVE_DEBOUNCE_MS}ms (dev server);
        explicit Save also flips <code className="text-zinc-400">manually_reviewed: true</code>. Use{" "}
        <kbd className="px-1 border border-white/10 rounded text-zinc-400">j</kbd>/
        <kbd className="px-1 border border-white/10 rounded text-zinc-400">k</kbd> or arrow keys to
        navigate chunks. Place the file at{" "}
        <code className="text-zinc-400">.test-output/media-ingest/ground-truth/active.json</code> to
        use for scoring.
      </div>
    </div>
  );
}

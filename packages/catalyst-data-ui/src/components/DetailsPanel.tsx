/**
 * Right-rail details drawer for the currently-selected row.
 *
 * Reads from SelectionContext. Renders a kind-specific drilldown
 * for assertion / mention / chunk rows. Always at the same place in
 * the layout (rendered by AppShell), so every page can push selections
 * into it without owning panel state.
 *
 * Width is fixed at 380px for now — enough for the AMR breakdown
 * table without crowding the main view. Future: make it resizable.
 */

import {
  X,
  ArrowRight,
  Network,
  Calendar,
  Sparkles,
  Users as UsersIcon,
  Tag,
  User as UserIcon,
  Clock,
  CalendarRange,
  Hash,
  FileText,
  ChevronRight,
} from "lucide-react";
import { Badge, Button, ScrollArea } from "@thebranchdriftcatalyst/catalyst-ui";
import { useSelection } from "@/contexts/SelectionContext";
import type { Assertion, Mention } from "@/types/contracts";
import type { BillChunk } from "@/types/bills";
import { lookupFrame } from "@/data/amrFrames";
import { cn } from "@/lib/utils";

/** Extraction-method → display config. Kept aligned with AssertionCard
 *  so the same glyph and tone appear in both card row and detail panel. */
interface MethodDisplay {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  tone: string;
}
const METHOD_DISPLAY: Record<string, MethodDisplay> = {
  amr_projection: { icon: Network, label: "AMR projection", tone: "text-violet-300" },
  structured: { icon: Calendar, label: "Structured field", tone: "text-emerald-300" },
  llm: { icon: Sparkles, label: "LLM extraction", tone: "text-sky-300" },
  ner_ensemble: { icon: UsersIcon, label: "NER ensemble", tone: "text-amber-300" },
  spacy: { icon: Tag, label: "spaCy", tone: "text-zinc-300" },
  regex: { icon: Tag, label: "Regex match", tone: "text-zinc-400" },
  manual: { icon: UserIcon, label: "Manual annotation", tone: "text-pink-300" },
};
const FALLBACK_METHOD: MethodDisplay = {
  icon: Sparkles,
  label: "Unknown extraction",
  tone: "text-zinc-400",
};

export default function DetailsPanel() {
  const { selection, clear, isOpen } = useSelection();

  if (!isOpen) return null;

  return (
    <aside
      data-testid="details-panel"
      className="w-[380px] flex-shrink-0 border-l border-white/5 bg-surface-1 flex flex-col"
    >
      <div className="flex items-center justify-between px-3 py-2 border-b border-white/5">
        <div className="text-[10px] uppercase tracking-wider text-zinc-500 font-mono">
          {selection.kind === "assertion" && "Assertion"}
          {selection.kind === "mention" && "Mention"}
          {selection.kind === "chunk" && "Chunk"}
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={clear}
          className="h-6 w-6 p-0"
          data-testid="details-panel-close"
          title="Close panel (esc)"
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-3">
          {selection.kind === "assertion" && (
            <AssertionDetails assertion={selection.assertion} chunks={selection.context?.chunks} />
          )}
          {selection.kind === "mention" && <MentionDetails mention={selection.mention} />}
          {selection.kind === "chunk" && <ChunkDetails chunk={selection.chunk} />}
        </div>
      </ScrollArea>
    </aside>
  );
}

// ── Assertion drilldown ─────────────────────────────────────────────────────

function AssertionDetails({ assertion, chunks }: { assertion: Assertion; chunks?: BillChunk[] }) {
  // Frame lookup — prefer the explicit AMR frame, fall back to the
  // predicate so structured assertions (co_sponsors, member_of) still
  // get a gloss from our domain dictionary.
  const frame = lookupFrame(assertion.amr_frame) ?? lookupFrame(assertion.predicate);
  const method: MethodDisplay =
    METHOD_DISPLAY[assertion.provenance?.extraction_method ?? ""] ?? FALLBACK_METHOD;
  const MethodIcon = method.icon;

  // Source chunk + highlighted sentence (only when both the chunks
  // list AND the assertion's provenance pointer are present).
  const sourceChunk =
    chunks && assertion.provenance?.chunk_id
      ? chunks.find((c) => c.chunk_id === assertion.provenance.chunk_id)
      : null;

  return (
    <div className="space-y-4 text-xs">
      {/* SPO line — same shape as the card row but bigger + wrapping */}
      <div className="space-y-1">
        <div className="text-[9px] uppercase tracking-wider text-zinc-500 font-mono">
          Subject → Predicate → Object
        </div>
        <div className="flex items-baseline gap-1.5 flex-wrap">
          <span className="text-zinc-100 font-medium">
            {assertion.subject_text || <em className="text-zinc-600">(empty)</em>}
          </span>
          <ArrowRight className="h-3 w-3 text-zinc-600 self-center" />
          <span className="text-zinc-300">{assertion.predicate}</span>
          <ArrowRight className="h-3 w-3 text-zinc-600 self-center" />
          <span className="text-zinc-300">
            {assertion.object_text || <em className="text-zinc-600">(intransitive)</em>}
          </span>
        </div>
        {frame && (
          <p className="text-[11px] text-zinc-400 italic leading-relaxed pt-1">{frame.gloss}</p>
        )}
      </div>

      {/* AMR frame + variable (only when AMR-projected — structured
          assertions skip this section since their predicate gloss
          already landed under the SPO line above). */}
      {assertion.amr_frame && (
        <Section title="AMR frame">
          <div className="flex items-center gap-2">
            <Badge
              variant="outline"
              className={cn(
                "text-[10px] font-mono",
                assertion.is_novel_predicate
                  ? "text-orange-300 border-orange-800/60"
                  : "text-violet-300 border-violet-800/60",
              )}
            >
              {assertion.is_novel_predicate && "NOVEL "}
              {assertion.amr_frame}
            </Badge>
            {assertion.amr_variable && (
              <span className="text-[10px] font-mono text-zinc-600">
                var: {assertion.amr_variable}
              </span>
            )}
          </div>
          {assertion.is_novel_predicate && (
            <p className="text-[10px] text-orange-300/80 mt-1.5 leading-relaxed">
              Frame not in the active label pack — likely needs a frame entry under
              <span className="font-mono"> amr_frames.frames</span> for this domain.
            </p>
          )}
        </Section>
      )}

      {/* Role mapping table */}
      {assertion.amr_role_mapping && Object.keys(assertion.amr_role_mapping).length > 0 && (
        <Section title="Role mapping">
          <table className="w-full text-[10px] font-mono">
            <tbody>
              {Object.entries(assertion.amr_role_mapping).map(([arg, slot]) => (
                <tr key={arg} className="border-t border-white/[0.03] first:border-t-0">
                  <td className="py-0.5 pr-2 text-violet-300 align-top w-12">{arg}</td>
                  <td className="py-0.5 pr-2 text-zinc-500 align-top w-14">
                    {frame?.roles?.[arg] ?? ""}
                  </td>
                  <td className="py-0.5 text-zinc-300">→ {slot}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      {/* Polarity / modality / hedged flags */}
      {(assertion.modality ||
        assertion.negated ||
        assertion.hedged ||
        assertion.polarity === false) && (
        <Section title="Modality & polarity">
          <div className="flex flex-wrap gap-1.5">
            {(assertion.negated || assertion.polarity === false) && (
              <Badge variant="destructive" className="text-[10px]">
                negated
              </Badge>
            )}
            {assertion.hedged && (
              <Badge variant="outline" className="text-[10px] text-yellow-300 border-yellow-800/50">
                hedged
              </Badge>
            )}
            {assertion.modality && (
              <Badge variant="outline" className="text-[10px] text-cyan-300 border-cyan-800/50">
                {assertion.modality}
              </Badge>
            )}
          </div>
        </Section>
      )}

      {/* Qualifiers */}
      {assertion.qualifiers && Object.keys(assertion.qualifiers).length > 0 && (
        <Section title="Qualifiers">
          <dl className="space-y-1 text-[10px] font-mono">
            {Object.entries(assertion.qualifiers).map(([k, v]) => (
              <div key={k} className="flex gap-2">
                <dt className="text-zinc-500 w-20 flex-shrink-0">{k}</dt>
                <dd className="text-zinc-300">{String(v)}</dd>
              </div>
            ))}
          </dl>
        </Section>
      )}

      {/* Temporal validity */}
      {(assertion.t_valid_from || assertion.t_valid_until || assertion.is_atemporal) && (
        <Section title="Temporal validity">
          <div className="space-y-1 text-[10px] font-mono">
            {assertion.is_atemporal && (
              <div className="text-emerald-300">atemporal — no validity window</div>
            )}
            {assertion.t_valid_from && (
              <Row icon={CalendarRange} label="from" value={assertion.t_valid_from} />
            )}
            {assertion.t_valid_until && (
              <Row icon={CalendarRange} label="until" value={assertion.t_valid_until} />
            )}
            {!assertion.t_valid_until && assertion.t_valid_from && (
              <div className="text-zinc-500 italic">open-ended</div>
            )}
          </div>
        </Section>
      )}

      {/* Provenance */}
      <Section title="Provenance">
        <div className="space-y-1.5 text-[10px] font-mono">
          <div className="flex items-center gap-1.5">
            <MethodIcon className={cn("h-3 w-3", method.tone)} />
            <span className="text-zinc-300">{method.label}</span>
            {assertion.provenance?.extraction_model && (
              <span className="text-zinc-600">· {assertion.provenance.extraction_model}</span>
            )}
          </div>
          {assertion.provenance?.source_document_id && (
            <Row icon={FileText} label="document" value={assertion.provenance.source_document_id} />
          )}
          {assertion.provenance?.chunk_id && (
            <Row icon={Hash} label="chunk" value={assertion.provenance.chunk_id} />
          )}
          {typeof assertion.sentence_index === "number" && (
            <Row icon={Hash} label="sentence" value={String(assertion.sentence_index)} />
          )}
          {assertion.provenance?.timestamp && (
            <Row icon={Clock} label="extracted" value={assertion.provenance.timestamp} />
          )}
          {assertion.provenance?.code_location && (
            <Row icon={Tag} label="code-location" value={assertion.provenance.code_location} />
          )}
        </div>
      </Section>

      {/* Source chunk — inline preview with the source sentence
          highlighted (when char offsets are present). */}
      {sourceChunk && (
        <Section title={`Source chunk · ${sourceChunk.index + 1} / ${sourceChunk.total_chunks}`}>
          <HighlightedChunkText
            text={sourceChunk.text}
            start={assertion.sentence_char_start}
            end={assertion.sentence_char_end}
          />
        </Section>
      )}

      {/* Confidence + IDs */}
      <Section title="IDs & confidence">
        <div className="space-y-1 text-[10px] font-mono">
          <Row icon={Hash} label="assertion_id" value={assertion.assertion_id} />
          <Row
            icon={Hash}
            label="confidence"
            value={(assertion.confidence * 100).toFixed(0) + "%"}
          />
          {assertion.subject_entity_id && (
            <Row icon={ChevronRight} label="subj entity" value={assertion.subject_entity_id} />
          )}
          {assertion.object_entity_id && (
            <Row icon={ChevronRight} label="obj entity" value={assertion.object_entity_id} />
          )}
        </div>
      </Section>
    </div>
  );
}

// ── Mention drilldown ───────────────────────────────────────────────────────

function MentionDetails({ mention }: { mention: Mention }) {
  return (
    <div className="space-y-4 text-xs">
      <Section title="Surface">
        <div className="text-zinc-100 font-medium">{mention.text}</div>
        <Badge variant="outline" className="text-[10px] mt-2">
          {mention.canonical_type}
        </Badge>
      </Section>

      {(typeof mention.vote_count === "number" || typeof mention.n_encoders === "number") && (
        <Section title="Consensus">
          <div className="space-y-1 text-[10px] font-mono">
            {typeof mention.vote_count === "number" && (
              <Row icon={Hash} label="votes" value={String(mention.vote_count)} />
            )}
            {typeof mention.n_encoders === "number" && (
              <Row icon={Hash} label="encoders" value={String(mention.n_encoders)} />
            )}
            {typeof mention.mean_confidence === "number" && (
              <Row
                icon={Hash}
                label="mean conf"
                value={(mention.mean_confidence * 100).toFixed(0) + "%"}
              />
            )}
            {mention.source_models && mention.source_models.length > 0 && (
              <div className="flex gap-2">
                <span className="text-zinc-500 w-20 flex-shrink-0">voters</span>
                <span className="text-zinc-300">{mention.source_models.join(", ")}</span>
              </div>
            )}
          </div>
        </Section>
      )}

      {mention.canonical_entity_id && (
        <Section title="Canonical entity">
          <Row icon={Hash} label="entity_id" value={mention.canonical_entity_id} />
        </Section>
      )}

      <Section title="IDs">
        <div className="space-y-1 text-[10px] font-mono">
          <Row icon={Hash} label="mention_id" value={mention.mention_id} />
          {mention.provenance?.source_document_id && (
            <Row icon={FileText} label="document" value={mention.provenance.source_document_id} />
          )}
          {mention.provenance?.chunk_id && (
            <Row icon={Hash} label="chunk" value={mention.provenance.chunk_id} />
          )}
        </div>
      </Section>
    </div>
  );
}

// ── Chunk drilldown ─────────────────────────────────────────────────────────

function ChunkDetails({ chunk }: { chunk: BillChunk }) {
  return (
    <div className="space-y-4 text-xs">
      <Section title="Chunk">
        <div className="text-[10px] font-mono text-zinc-500 mb-1">
          {chunk.index + 1} / {chunk.total_chunks}
        </div>
        <pre className="text-[11px] text-zinc-300 whitespace-pre-wrap leading-relaxed font-mono bg-black/30 rounded p-2 max-h-[400px] overflow-auto">
          {chunk.text}
        </pre>
      </Section>
      <Section title="IDs">
        <div className="space-y-1 text-[10px] font-mono">
          <Row icon={Hash} label="chunk_id" value={chunk.chunk_id} />
          <Row icon={FileText} label="document" value={chunk.document_id} />
        </div>
      </Section>
    </div>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1.5">
      <div className="text-[9px] uppercase tracking-wider text-zinc-500 font-mono">{title}</div>
      <div>{children}</div>
    </section>
  );
}

function Row({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-1.5 min-w-0">
      <Icon className="h-3 w-3 text-zinc-600 flex-shrink-0" />
      <span className="text-zinc-500 w-20 flex-shrink-0">{label}</span>
      <span className="text-zinc-300 truncate">{value}</span>
    </div>
  );
}

/** Render chunk text with the assertion's source sentence wrapped in a
 *  highlight span. Falls back to plain text when the offsets are
 *  missing or out of bounds — never silently breaks. */
function HighlightedChunkText({
  text,
  start,
  end,
}: {
  text: string;
  start: number | null;
  end: number | null;
}) {
  const inBounds =
    typeof start === "number" &&
    typeof end === "number" &&
    start >= 0 &&
    end > start &&
    end <= text.length;
  if (!inBounds) {
    return (
      <pre className="text-[11px] text-zinc-300 whitespace-pre-wrap leading-relaxed font-mono bg-black/30 rounded p-2 max-h-[300px] overflow-auto">
        {text}
      </pre>
    );
  }
  return (
    <pre className="text-[11px] text-zinc-300 whitespace-pre-wrap leading-relaxed font-mono bg-black/30 rounded p-2 max-h-[300px] overflow-auto">
      {text.slice(0, start as number)}
      <mark className="bg-cyan-500/20 text-cyan-200 rounded px-0.5">
        {text.slice(start as number, end as number)}
      </mark>
      {text.slice(end as number)}
    </pre>
  );
}

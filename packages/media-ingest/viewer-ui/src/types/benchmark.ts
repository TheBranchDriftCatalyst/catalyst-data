/** Types for the extraction benchmark report */

export interface GroundTruthMeta {
  available: boolean;
  reference_model: string;
  manually_reviewed: boolean;
  mention_count: number;
  proposition_count: number;
}

export interface ModelScores {
  mention_strict_precision: number;
  mention_strict_recall: number;
  mention_strict_f1: number;
  mention_relaxed_precision: number;
  mention_relaxed_recall: number;
  mention_relaxed_f1: number;
  mention_type_accuracy: number;
  mention_span_accuracy: number;
  proposition_strict_precision: number;
  proposition_strict_recall: number;
  proposition_strict_f1: number;
  proposition_relaxed_precision: number;
  proposition_relaxed_recall: number;
  proposition_relaxed_f1: number;
  hallucination_rate: number;
  quality_speed_ratio: number;
  per_chunk_latency: number;
}

export interface BenchmarkReport {
  generated_at: string;
  model_count: number;
  entity_count: number;
  proposition_count: number;
  model_names: string[];
  domains: Record<string, number>;
  ground_truth?: GroundTruthMeta;
  models: ModelResult[];
  entities: EntityRow[];
  propositions: PropositionRow[];
}

export interface ProvenanceScores {
  overall: number;
  mention_count: number;
  assertion_count: number;
  mention_has_provenance: number;
  has_document_id: number;
  has_chunk_id: number;
  has_span: number;
  has_extraction_model: number;
  has_code_location: number;
  assertion_has_provenance: number;
  assertion_linked_subject: number;
  assertion_linked_object: number;
}

export interface ModelResult {
  name: string;
  type: "encoder" | "specialist" | "llm";
  tags: string[];
  stats: ModelStats;
  scores?: ModelScores;
  pipeline: Record<string, PipelineStage>;
  provenance?: ProvenanceScores;
}

export interface ModelStats {
  mention_count: number;
  assertion_count: number;
  duration_s: number;
  tokens_per_sec: number;
  mention_retries: number;
  proposition_retries: number;
  errors: number;
  chunk_count: number;
}

export interface PipelineStage {
  calls: number;
  completed: number;
  error: number;
  failed: number;
  ambiguous?: number;
  total_candidates?: number;
  error_codes?: Record<string, number>;
}

/**
 * One extraction occurrence — preserved per (model, chunk) so the side-panel
 * detail view can show full provenance (where in the corpus, what was said,
 * who was speaking, when in the timeline). Aggregated EntityRow.models still
 * exists for the matrix consensus view.
 */
export interface EntityMention {
  model: string;
  type: string;
  chunk_id: string;
  document_id: string;
  span_start: number | null;
  span_end: number | null;
  confidence: number;
  context: string;
  temporal_start_ms: number | null;
  temporal_end_ms: number | null;
  speaker_label: string | null;
  source_media_uri: string | null;
}

export interface EntityRow {
  text: string;
  consensus_type: string;
  domain: string;
  model_count: number;
  models: Record<
    string,
    {
      type: string;
      confidence: number;
      span_start: number | null;
      span_end: number | null;
    }
  >;
  mentions?: EntityMention[];
}

/**
 * A single row from the bench audit log — the unified event stream
 * emitted by harness, exgraph, langgraph, and dagster. Persisted as
 * Parquet under ``events.parquet`` (CD-jzkg) and exposed via the
 * ``GET /viewer/api/bench/runs/<id>/events`` endpoint as NDJSON. Every
 * consumer (LiveGantt, AuditViewer, StateInspector) reads this shape.
 */
export interface RunEvent {
  ts: string;
  run_id: string | null;
  source: "harness" | "exgraph" | "langgraph" | "dagster";
  node_name: string;
  status: string;
  model: string | null;
  doc_id: string | null;
  chunk_idx: number | null;
  /** Stable chunk identifier — lets the StateInspector join intermediate
   *  events to the one-shot `chunk_loaded` text and the terminal
   *  `chunk_extracted` NER/SPO output. */
  chunk_id: string | null;
  retry_count: number | null;
  code_location: string | null;
  /** Per-node state summary (verdict, candidate_sample, errors,
   *  provenance counts). Always present, may be empty. */
  state: Record<string, unknown>;
  details: Record<string, unknown>;
}

/** A compact mention summary as it appears in `state.candidate_sample`
 *  and the terminal `chunk_extracted.details.mentions` list. */
export interface MentionLite {
  text: string;
  type?: string;
  mention_type?: string;
  span_start?: number | null;
  span_end?: number | null;
  span?: [number | null, number | null];
  conf?: number;
  confidence?: number;
}

export interface PropositionLite {
  subject: string;
  predicate: string;
  object: string;
  conf?: number;
  confidence?: number;
}

/** Details payload of a `chunk_loaded` event — what the chunker put on
 *  disk + what the audio chunker promotes (speaker, time range). The
 *  chunk_metadata bag is whatever the ChunkingResource attached
 *  (strategy, chunk_size, chunk_overlap, chunk_char_offset, …). */
export interface ChunkLoadedDetails {
  text: string;
  char_count: number;
  truncated: boolean;
  domain: string | null;
  speaker_label: string | null;
  temporal_start_ms: number | null;
  temporal_end_ms: number | null;
  chunk_index: number | null;
  total_chunks: number | null;
  chunk_metadata: Record<string, unknown>;
}

/** AuditEvent: derived per-model view of RunEvent for the timeline. */
export interface AuditEvent {
  timestamp: string;
  node_name: string;
  status: string;
  duration_s: number;
  details: {
    candidate_count?: number;
    verdict?: string;
    errors?: Array<{ code: string; message: string; path?: string }>;
    [k: string]: unknown;
  };
}

export interface AuditLog {
  model: string;
  name: string;
  tags: string[];
  stats: ModelStats & { pipeline: Record<string, PipelineStage> };
  audit_events: AuditEvent[];
  event_count: number;
}

export interface PropositionRow {
  subject: string;
  predicate: string;
  object: string;
  domain: string;
  model_count: number;
  models: string[];
}

// ── Ground Truth (full data, not just the summary meta) ──────────────

/**
 * Phase 0 (CD-9wno): mentions carry absolute doc-frame spans so GT survives
 * any future chunker change.  `span_start`/`span_end` are chunk-relative and
 * used only transiently in the editor UI; they are NOT stored in S3.
 */
export interface GroundTruthMention {
  text: string;
  mention_type: string;
  /** Absolute doc-frame start (new format). */
  doc_char_start?: number | null;
  /** Absolute doc-frame end (new format). */
  doc_char_end?: number | null;
  /**
   * Chunk-relative spans — present only in the editor UI after a
   * translate-on-read pass from the bench API.  NOT persisted in S3.
   */
  span_start?: number | null;
  span_end?: number | null;
  confidence: number;
}

export interface GroundTruthProposition {
  subject: string;
  predicate: string;
  object: string;
  confidence: number;
  evidence?: string;
}

/**
 * Doc-anchored GT entry (new format, Phase 0 CD-9wno).
 *
 * The join key is `(doc_id, [doc_char_start, doc_char_end))` via IntervalTree.
 * `legacy_chunk_id` is retained for diagnostics only — not a join key.
 *
 * The SPA editor works chunk-by-chunk; the bench API translates doc-frame
 * spans → chunk-frame on read and chunk-frame → doc-frame on save, so the
 * editing experience is unchanged from the operator's perspective.
 */
export interface GroundTruthChunk {
  /** New: parent document id.  Replaces `chunk_id` as the join key. */
  doc_id: string;
  /** New: absolute start in parent document (char offset, inclusive). */
  doc_char_start: number | null;
  /** New: absolute end in parent document (char offset, exclusive). */
  doc_char_end: number | null;
  /** Human-readable excerpt — not the join key. */
  text_excerpt: string;
  /**
   * Diagnostic only — the old chunk_id from which this entry was derived.
   * Not used for scoring or GT join operations.
   */
  legacy_chunk_id?: string;
  /**
   * Back-compat alias for `legacy_chunk_id`.  Some older UI paths read
   * `chunk.chunk_id`; the API sets both so existing components don't break
   * until the editor is updated to use `doc_id`.
   */
  chunk_id?: string;
  /** Back-compat alias for `text_excerpt` (used by ChunkEditor). */
  text?: string;
  mentions: GroundTruthMention[];
  propositions: GroundTruthProposition[];
  /**
   * Per-chunk human-review flag. Distinct from the file-level
   * `manually_reviewed` (which marks the entire GT file as human-edited).
   * Used by the GT editor to track which chunks the reviewer has worked
   * through during a 200-chunk pass — drives the "Next unreviewed"
   * shortcut and visual de-emphasis of completed rows. Optional for
   * back-compat with files written before this field existed; absent or
   * false both mean "not yet reviewed."
   */
  reviewed?: boolean | null;
}

// ── Consensus events (Phase B, CD-94ow) ──────────────────────────────────────

/**
 * Details for `consensus_started` events (chunk_id ends with `:_consensus`).
 */
export interface ConsensusStartedDetails {
  n_encoders: number;
  total_input_mentions: number;
}

/**
 * Details for `mention_decision` events — accepted consensus mentions.
 * Field names mirror consensus.py's event_tail.append call exactly.
 */
export interface MentionDecisionDetails {
  text: string;
  canonical_type: string;
  vote_count: number;
  n_encoders: number;
  source_models: string[];
  mean_confidence: number;
  /** Per-type vote counts, e.g. { "PERSON": 4, "GPE": 1 } */
  type_votes: Record<string, number>;
  /** Encoder name that contributed the highest-confidence span. */
  span_provenance: string;
  /** Max char-offset difference between chosen span and any other cluster span. */
  span_disagreement_chars: number;
}

/**
 * Details for `mention_rejected` events — mentions that didn't reach quorum.
 * Note: consensus.py does NOT emit source_models or mean_confidence for
 * rejected mentions — only the 5 fields below.
 */
export interface MentionRejectedDetails {
  text: string;
  vote_count: number;
  n_encoders: number;
  quorum: number;
  reason: string; // always "below_quorum" in current impl
}

/**
 * Details for `consensus_completed` — one per doc, summary across all clusters.
 */
export interface ConsensusCompletedDetails {
  accepted_count: number;
  rejected_count: number;
  mean_vote_count: number;
  /** Per-type accepted mention counts, e.g. { "PERSON": 12, "ORG": 5 } */
  type_distribution: Record<string, number>;
  /** Fraction of accepted mentions where any encoder disagreed on span boundaries. */
  span_disagreement_rate: number;
}

export interface GroundTruthFile {
  domain: string;
  reference_model: string;
  manually_reviewed: boolean;
  chunk_count: number;
  total_mentions: number;
  total_propositions: number;
  ensemble_config?: {
    ner_models: string[];
    spo_models: string[];
    threshold: number;
    ner_threshold?: number;
    spo_threshold?: number;
  };
  chunks: GroundTruthChunk[];
}

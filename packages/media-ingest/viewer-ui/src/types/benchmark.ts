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

export interface AuditEvent {
  timestamp: string;
  node_name: string;
  status: string;
  duration_s: number;
  details: {
    candidate_count?: number;
    verdict?: string;
    errors?: Array<{ code: string; message: string; path?: string }>;
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

export interface GroundTruthMention {
  text: string;
  mention_type: string;
  span_start: number | null;
  span_end: number | null;
  confidence: number;
}

export interface GroundTruthProposition {
  subject: string;
  predicate: string;
  object: string;
  confidence: number;
  evidence?: string;
}

export interface GroundTruthChunk {
  chunk_id: string;
  text: string;
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
  reviewed?: boolean;
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

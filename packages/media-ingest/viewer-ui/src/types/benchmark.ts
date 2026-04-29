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

export interface ModelResult {
  name: string;
  type: "encoder" | "specialist" | "llm";
  tags: string[];
  stats: ModelStats;
  scores?: ModelScores;
  pipeline: Record<string, PipelineStage>;
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

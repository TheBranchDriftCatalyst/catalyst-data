/** Types for the extraction benchmark report */

export interface BenchmarkReport {
  generated_at: string;
  model_count: number;
  entity_count: number;
  proposition_count: number;
  model_names: string[];
  models: ModelResult[];
  entities: EntityRow[];
  propositions: PropositionRow[];
}

export interface ModelResult {
  name: string;
  type: "encoder" | "specialist" | "llm";
  tags: string[];
  stats: ModelStats;
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

export interface PropositionRow {
  subject: string;
  predicate: string;
  object: string;
  model_count: number;
  models: string[];
}

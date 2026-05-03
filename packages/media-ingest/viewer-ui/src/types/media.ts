export interface MediaDocument {
  id: string;
  title: string;
  source: string;
  source_path: string;
  document_type: string;
  domain: string;
  /** Backend-resolved streaming URL (preferred over client-side construction). */
  media_url?: string;
  thumbnail_url?: string;
  metadata: {
    extension: string;
    size_bytes: number;
    has_audio: boolean;
    has_video: boolean;
    duration_seconds?: number;
    video_codec?: string;
    audio_codec?: string;
    [k: string]: unknown;
  };
}

export interface Word {
  word: string;
  start: number;
  end: number;
  probability: number;
  speaker?: string;
}

export interface Segment {
  start: number;
  end: number;
  text: string;
  speaker?: string;
  words?: Word[];
}

export interface Transcription {
  document_id: string;
  title: string;
  text: string;
  language: string;
  language_probability: number;
  duration_s: number;
  segments: Segment[];
  segment_count: number;
  source_path: string;
  error?: string;
}

export interface Diarization extends Transcription {
  speaker_text: string | null;
  speaker_count: number;
  speakers: string[];
  diarization_time_s: number;
  diarization_error?: string;
}

export interface ChunkInfo {
  chunk_id: string;
  document_id: string;
  text: string;
  index: number;
  total_chunks: number;
  metadata: {
    speaker?: string;
    start_s?: number;
    end_s?: number;
    strategy?: string;
    source?: string;
    language?: string;
    speaker_count?: number;
    [k: string]: unknown;
  };
}

export interface Mention {
  text: string;
  mention_type: string;
  context: string;
  span_start: number;
  span_end: number;
  document_id: string;
  chunk_id: string;
  provenance?: Provenance | null;
}

export interface Provenance {
  source_document_id: string;
  chunk_id: string;
  span_start?: number | null;
  span_end?: number | null;
  temporal_start_ms?: number | null;
  temporal_end_ms?: number | null;
  speaker_label?: string | null;
  source_media_uri?: string | null;
  extraction_method?: string;
  extraction_model?: string;
  confidence?: number;
}

export interface Assertion {
  assertion_id?: string;
  subject_text: string;
  predicate: string;
  predicate_canonical: string;
  object_text: string;
  confidence: number;
  negated: boolean;
  hedged: boolean;
  qualifiers: Record<string, string>;
  provenance?: Provenance | null;
}

/** Marker on the video timeline representing an entity mention or assertion. */
export interface TimelineMarker {
  id: string;
  timestamp: number;
  endTimestamp?: number;
  label: string;
  color: string;
  type: "entity" | "assertion";
  category?: string;
}

/** Human annotation (approve/reject/edit/flag) on a pipeline output. */
export interface Annotation {
  annotation_id: string;
  document_id: string;
  target_type: "mention" | "assertion" | "segment" | "speaker";
  target_id: string;
  action: "approve" | "reject" | "edit" | "flag";
  edits: Record<string, unknown>;
  reviewer: string;
  notes: string;
  created_at: string | null;
}

export type AnnotationStatus = "pending" | "approved" | "rejected";

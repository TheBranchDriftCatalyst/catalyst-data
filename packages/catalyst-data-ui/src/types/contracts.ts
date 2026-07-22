/**
 * Wire-shape mirror of `catalyst_contracts_core.types`.
 *
 * These types are the canonical gold-layer Pydantic models — Mention,
 * Assertion, Provenance — serialized to JSONL in S3 and consumed by the
 * SPA via the FastAPI viewer routes.
 *
 * Hand-maintained (no codegen yet) — when the Python contract changes,
 * update this file in lock-step. The Python source of truth lives at:
 *
 *   catalyst-llm/packages/catalyst-contracts-core/src/catalyst_contracts_core/
 *
 * Keep this file domain-agnostic. Media-specific types (Segment, Word,
 * Transcription, MediaDocument, TimelineMarker, Annotation) belong in
 * `./media.ts`. Anything that crosses domains (congress / leaks / media)
 * lives here so a CongressBillDetail panel can reuse the same
 * AssertionPanel that the media Player uses.
 */

// ── Enums (mirror catalyst_contracts_core.enums) ────────────────────────

/** Source of an extraction record — distinguishes AMR projection from LLM
 *  output from structured-field projection. The UI branches on this to
 *  pick provenance glyphs (frame icon for AMR, calendar icon for
 *  structured-from-API-date, brain icon for LLM, etc.). */
export type ExtractionMethod =
  | "llm"
  | "spacy"
  | "regex"
  | "manual"
  | "structured"
  | "amr_projection"
  | "ner_ensemble";

/** Legacy closed-vocab mention types kept as a hint for the GT editor.
 *  The wire format now uses free-form `canonical_type: string` so label
 *  packs can extend the universe — never narrow Mention.canonical_type
 *  to this union. */
export type MentionTypeHint =
  | "PERSON"
  | "ORG"
  | "LOC"
  | "GPE"
  | "DATE"
  | "TIME"
  | "MONEY"
  | "PERCENT"
  | "EVENT"
  | "PRODUCT"
  | "WORK_OF_ART"
  | "LAW"
  | "LANGUAGE"
  | "NORP"
  | "FAC"
  | "OTHER";

// ── Provenance ──────────────────────────────────────────────────────────

/** Where an extraction came from. Every Mention and Assertion carries one
 *  — `provenance` is required on the new contract (was optional in the
 *  pre-AMR media-only legacy shape). */
export interface Provenance {
  source_document_id: string;
  chunk_id: string;
  span_start: number | null;
  span_end: number | null;
  /** Audio/video offset in ms — null for non-media domains. */
  temporal_start_ms: number | null;
  temporal_end_ms: number | null;
  /** Diarization speaker label (e.g. "SPEAKER_00") — media-only. */
  speaker_label: string | null;
  /** S3 URI of the original audio/video — media-only. */
  source_media_uri: string | null;
  extraction_method: ExtractionMethod;
  extraction_model: string;
  confidence: number;
  /** ISO 8601 timestamp stamped at extraction. */
  timestamp: string;
  /** Dagster code location that produced this extraction. */
  code_location: string;
}

// ── Mention ─────────────────────────────────────────────────────────────

/** A named entity mention persisted to the gold layer.
 *
 *  Replaces the legacy media-only Mention shape. Adds consensus
 *  provenance (which NER voters agreed on it) + canonical entity linking
 *  (post-cross-source-alignment).
 *
 *  Wire-shape contract on the Python side: `frozen=True`, `extra="forbid"`. */
export interface Mention {
  /** Stable hash of (canonical_text, canonical_type, span_start). */
  mention_id: string;

  // Surface + classification
  text: string;
  /** Free-form canonical type from the active label pack — e.g. PERSON,
   *  BILL, PUBLIC_LAW. Replaces the legacy closed-vocab `mention_type`. */
  canonical_type: string;
  span_start: number;
  span_end: number;

  // Consensus provenance (from the NER ensemble + ConsensusNode)
  vote_count: number;
  n_encoders: number;
  source_models: string[];
  mean_confidence: number;
  /** Which encoder's span won the consensus tie-break. */
  span_provenance: string | null;

  // Entity linking (populated post-concordance; null until then)
  canonical_entity_id: string | null;

  // Audit
  context: string;
  content_hash: string;
  provenance: Provenance;
}

// ── Assertion ───────────────────────────────────────────────────────────

/** A proposition (SPO + AMR provenance) persisted to the gold layer.
 *
 *  Flat SPO view (`subject_text`/`predicate`/`object_text`) for fast
 *  queries. AMR-rich fields (`amr_frame`, `polarity`, `modality`,
 *  `qualifiers`) for graph-native semantics. Temporal validity windows
 *  for point-in-time queries.
 *
 *  Wire-shape contract on the Python side: `frozen=True`, `extra="forbid"`. */
export interface Assertion {
  /** Stable hash of (subject, predicate, object, source_chunk_id). */
  assertion_id: string;

  // ── Flat SPO view ─────────────────────────────────────────────────
  subject_text: string;
  /** Canonical predicate from the active label pack's vocab.
   *  Already canonical — there is no separate `predicate_canonical`. */
  predicate: string;
  /** null for intransitive predicates (e.g. pass-03 with only ARG1). */
  object_text: string | null;

  // ── Entity links (optional — populated post-concordance) ──────────
  subject_entity_id: string | null;
  object_entity_id: string | null;
  subject_mention_id: string | null;
  object_mention_id: string | null;

  // ── AMR provenance (where the predicate came from) ────────────────
  /** Raw PropBank frame from the AMR :instance edge, e.g. 'introduce-01'.
   *  null for non-AMR-projected assertions (structured / LLM paths). */
  amr_frame: string | null;
  amr_variable: string | null;
  /** AMR ARG → semantic role mapping actually applied,
   *  e.g. { ARG0: 'subject', ARG1: 'object' }. */
  amr_role_mapping: Record<string, string>;
  /** True when the AMR frame was not in the active pack's frame table. */
  is_novel_predicate: boolean;

  // ── Modality + polarity (AMR graph attributes) ────────────────────
  /** False when the AMR graph carried `:polarity -`. */
  polarity: boolean;
  /** AMR `:mode` attribute (e.g. 'possible', 'obligation'). */
  modality: string | null;
  /** Legacy mirror of !polarity. Always synced post-construction. */
  negated: boolean;
  /** "may" / "could" / "reportedly" markers. */
  hedged: boolean;

  // ── Qualifiers (n-ary edge metadata) ──────────────────────────────
  /** Adjunct edges projected as qualifiers: :time, :location, :condition,
   *  :manner, source_attribution. */
  qualifiers: Record<string, string>;

  // ── Temporal validity ─────────────────────────────────────────────
  /** ISO date when the fact starts holding. null until stamping runs. */
  t_valid_from: string | null;
  /** ISO date when the fact stops holding. null = open-ended. */
  t_valid_until: string | null;
  /** True for facts with no temporal validity (cites/amends/repeals/codifies).
   *  Mutually exclusive with t_valid_from/until in spirit (the constructor
   *  may still set both; downstream queries should treat this as "skip the
   *  time-window filter"). */
  is_atemporal: boolean;

  // ── Geospatial grounding (placeholders — future H3/GeoSPARQL bead) ─
  h3_cells: string[];
  geometry_geojson: Record<string, unknown> | null;

  // ── Source pointers (provenance into the source chunk) ────────────
  sentence_index: number | null;
  sentence_char_start: number | null;
  sentence_char_end: number | null;

  // ── Confidence + provenance ───────────────────────────────────────
  confidence: number;
  content_hash: string;
  provenance: Provenance;
}

// ── Helpers (UI-facing predicates for the type discrimination above) ──

/** True for assertions projected from a PropBank AMR frame. UI uses this
 *  to render the AMR-frame badge on AssertionCard. */
export function isAmrProjected(assertion: Assertion): boolean {
  return assertion.provenance.extraction_method === "amr_projection";
}

/** True for assertions deterministically projected from a structured
 *  entity field (e.g. Cosponsor.sponsorship_date → co_sponsors
 *  t_valid_from). UI uses this to render the calendar/structured glyph. */
export function isStructured(assertion: Assertion): boolean {
  return assertion.provenance.extraction_method === "structured";
}

/** True when the assertion has an explicit temporal validity window.
 *  Atemporal assertions (cites/amends/repeals) skip the time slider. */
export function hasTemporalWindow(assertion: Assertion): boolean {
  if (assertion.is_atemporal) return false;
  return assertion.t_valid_from !== null || assertion.t_valid_until !== null;
}

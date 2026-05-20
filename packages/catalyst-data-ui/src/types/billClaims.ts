/**
 * Wire-shape mirror of `congress_data.claim_models.BillClaim`.
 *
 * The Claims tab on the Bills detail page reads
 * `/viewer/api/congress/bills/{partition}/claims` which returns
 * `BillRowsResponse<BillClaim>` (from `./bills.ts`).
 *
 * Deliberately separate from `Assertion` (the flat SPO + AMR-aware
 * shape in `./contracts.ts`) — legal claims need typed Conditions
 * and Exceptions which wouldn't fit in `Assertion.qualifiers: Record<string, string>`.
 *
 * Hand-maintained — when the Python `BillClaim` Pydantic model
 * changes, update this file in lock-step. Source of truth:
 * `catalyst-data/packages/congress-data/src/congress_data/claim_models.py`.
 */

import type { Provenance } from "./contracts";

/** Closed deontic + structural operator vocabulary. */
export type ClaimOperator =
  // Deontic
  | "requires"
  | "prohibits"
  | "permits"
  // Structural / declarative
  | "defines"
  | "establishes"
  | "applies_to"
  | "amends"
  | "repeals"
  | "authorizes"
  | "appropriates"
  | "designates"
  | "exempts";

/** Whether the operator carries deontic force (obligation / prohibition /
 *  permission) or is structural / declarative. Drives chip colour in
 *  the UI. */
export type ClaimOperatorClass = "deontic" | "structural";

const DEONTIC_OPS: ReadonlySet<ClaimOperator> = new Set<ClaimOperator>([
  "requires",
  "prohibits",
  "permits",
]);

export function operatorClass(op: ClaimOperator): ClaimOperatorClass {
  return DEONTIC_OPS.has(op) ? "deontic" : "structural";
}

/** Closed condition-type vocabulary. */
export type ClaimConditionType =
  | "deadline"
  | "scope"
  | "trigger"
  | "jurisdiction"
  | "frequency"
  | "form";

export interface ClaimCondition {
  type: ClaimConditionType;
  text: string;
}

export interface ClaimTemporalWindow {
  valid_from: string | null;
  valid_until: string | null;
  is_atemporal: boolean;
}

export interface BillClaim {
  claim_id: string;

  // Normative core
  actor: string;
  operator: ClaimOperator;
  action: string;
  object: string | null;

  // Structured constraints
  conditions: ClaimCondition[];
  exceptions: string[];
  penalty: string | null;
  temporal_window: ClaimTemporalWindow | null;

  // Source grounding
  sentence_text: string;
  source_chunk_id: string | null;

  // Quality / review
  confidence: number;
  review_needed: boolean;
  review_reason: string | null;

  // Provenance (LLM + bill_claims_v1)
  provenance: Provenance | null;
}

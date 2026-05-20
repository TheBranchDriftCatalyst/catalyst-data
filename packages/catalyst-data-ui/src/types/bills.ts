/**
 * Wire-shape mirror of the `/viewer/api/congress/bills*` endpoints.
 *
 * Each bill in the corpus is one Dagster partition keyed
 * `{congress}-{bill_type}-{number}` (e.g. `119-hres-1`). The viewer
 * factory in `documents_factory.py` exposes:
 *
 *   GET /viewer/api/congress/bills                    -> BillListItem[]
 *   GET /viewer/api/congress/bills/{partition}        -> BillDetail
 *   GET /viewer/api/congress/bills/{partition}/{name} -> BillAssetResponse
 *
 * Keep the field set in sync with what `bill_detail` writes to
 * `silver/congress_data/bill/bill_document/{partition}/data.json` —
 * the Python `Document` model + the bill-specific `metadata` dict.
 */

import type { Assertion } from "./contracts";

/** Bill metadata mirror of `bill_detail.metadata`. All fields optional —
 *  partial bills (e.g. fixtures or in-flight materialisations) may
 *  ship only a subset. */
export interface BillMetadata {
  congress?: number;
  bill_type?: string; // "hr", "hres", "hjres", "s", "sjres", ...
  origin_chamber?: string; // "House" | "Senate"
  policy_area?: string;
  introduced_date?: string; // ISO date
  sponsor_bioguide?: string;
  cosponsor_count?: number;
  text_version_count?: number;
  text_version_used?: string;
  became_law?: boolean;
  // Forward-compat: anything else the asset stamps later.
  [key: string]: unknown;
}

/** One row in the bill list — the list endpoint folds in the silver
 *  bill_document metadata so the SPA can render cards without an N+1
 *  per-partition fetch. */
export interface BillListItem {
  partition: string;
  title?: string;
  domain?: string;
  document_type?: string;
  source?: string;
  metadata?: BillMetadata;
}

/** Full bill detail — the silver bill_document JSON, with the
 *  partition key folded on the top level by the API. */
export interface BillDetail extends BillListItem {
  id?: string;
  content?: string;
  source_url?: string | null;
  entity_type?: string;
  sections?: Record<string, string | string[]>;
  content_hash?: string;
}

/** Generic envelope for `/viewer/api/<domain>/bills/<partition>/<name>`
 *  responses. The shape is uniform across asset specs whose format is
 *  `jsonl` or `events` (a row-stream). The `json`-format primary spec
 *  is served through the detail endpoint instead. */
export interface BillRowsResponse<TRow = unknown> {
  partition: string;
  asset: string;
  count: number;
  rows: TRow[];
}

/** AssertionPanel is shape-compatible with both gold `bill_assertions`
 *  (AMR-projected) and gold `congress_structured_assertions` (cosponsor
 *  + public-law dated). The wire type is `Assertion` either way; the
 *  source-of-truth difference is in `provenance.extraction_method`. */
export type BillAssertionsResponse = BillRowsResponse<Assertion>;

/** TextChunk envelope shape — mirrors `dagster_io.chunking.TextChunk`. */
export interface BillChunk {
  chunk_id: string;
  document_id: string;
  text: string;
  index: number;
  total_chunks: number;
  metadata: Record<string, unknown>;
  content_hash: string;
}

export type BillChunksResponse = BillRowsResponse<BillChunk>;

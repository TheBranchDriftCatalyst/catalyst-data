/** Generic document shape — superset across every domain (media-ingest,
 *  congress-wtf, open-leaks). The backend's silver row schema is the source
 *  of truth: each silver/<code_location>/<group>/<asset>/data.jsonl emits
 *  these fields. Domain-specific extras (media's media_url + thumbnail_url,
 *  congress bill metadata, leak source URLs, …) live in `metadata` or are
 *  added by domain-specific extensions of this type (see `MediaDocument`).
 */
export interface Document {
  id: string;
  title: string;
  source: string;
  source_path: string;
  document_type?: string;
  domain: string;
  ingested_at?: string;
  metadata: Record<string, unknown>;
}

/** One row of the `/viewer/api/domains` registry — used by the Documents
 *  shell to render sub-tabs and the generic detail page. */
export interface Domain {
  slug: string;
  label: string;
  code_location: string;
  group: string;
  asset: string;
}

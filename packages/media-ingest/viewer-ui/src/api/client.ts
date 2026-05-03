import type {
  MediaDocument,
  Transcription,
  Diarization,
  ChunkInfo,
  Mention,
  Assertion,
  Annotation,
} from "@/types/media";

const API_BASE = "/viewer/api";

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export function fetchDocuments(): Promise<MediaDocument[]> {
  return apiFetch<MediaDocument[]>("/documents");
}

export function fetchDocument(id: string): Promise<MediaDocument> {
  return apiFetch<MediaDocument>(`/documents/${encodeURIComponent(id)}`);
}

export function fetchTranscription(id: string): Promise<Transcription> {
  return apiFetch<Transcription>(`/documents/${encodeURIComponent(id)}/transcription`);
}

export function fetchDiarization(id: string): Promise<Diarization> {
  return apiFetch<Diarization>(`/documents/${encodeURIComponent(id)}/diarization`);
}

export function fetchChunks(id: string): Promise<ChunkInfo[]> {
  return apiFetch<ChunkInfo[]>(`/documents/${encodeURIComponent(id)}/chunks`);
}

export function fetchMentions(id: string): Promise<Mention[]> {
  return apiFetch<Mention[]>(`/documents/${encodeURIComponent(id)}/mentions`);
}

export function fetchAssertions(id: string): Promise<Assertion[]> {
  return apiFetch<Assertion[]>(`/documents/${encodeURIComponent(id)}/assertions`);
}

/** Speaker name mappings: { "SPEAKER_00": { display_name: "...", color_index: ... }, ... } */
export type SpeakerMappings = Record<string, { display_name: string; color_index: number | null }>;

export function fetchSpeakerNames(id: string): Promise<SpeakerMappings> {
  return apiFetch<SpeakerMappings>(`/documents/${encodeURIComponent(id)}/speakers`);
}

export async function updateSpeakerName(
  documentId: string,
  label: string,
  displayName: string,
): Promise<{ label: string; display_name: string }> {
  const res = await fetch(
    `${API_BASE}/documents/${encodeURIComponent(documentId)}/speakers/${encodeURIComponent(label)}/name`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: displayName }),
    },
  );
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

/**
 * Returns the media streaming URL for a document.
 *
 * Prefers the backend-resolved `media_url` field (already correct for both
 * prod NFS and dev fixture overrides). Falls back to client-side construction
 * from source_path for documents that pre-date the field.
 */
export function getMediaUrl(doc: MediaDocument): string {
  if (doc.media_url) return doc.media_url;

  // Legacy fallback: source_path is the full NFS path /data/metube/Some Video.mp4
  const sourcePath = doc.source_path;
  const prefix = `/data/${doc.source}/`;
  const relativePath = sourcePath.startsWith(prefix)
    ? sourcePath.slice(prefix.length)
    : sourcePath.replace(/^\/data\/[^/]+\//, "");

  return `/viewer/media/${encodeURIComponent(doc.source)}/${relativePath
    .split("/")
    .map(encodeURIComponent)
    .join("/")}`;
}

// ── Annotation endpoints ──────────────────────────────────────────────────

export function fetchAnnotations(documentId: string): Promise<Annotation[]> {
  return apiFetch<Annotation[]>(`/documents/${encodeURIComponent(documentId)}/annotations`);
}

export interface AnnotationCreatePayload {
  target_type: "mention" | "assertion" | "segment" | "speaker";
  target_id: string;
  action: "approve" | "reject" | "edit" | "flag";
  edits?: Record<string, unknown>;
  reviewer?: string;
  notes?: string;
}

export async function createAnnotation(
  documentId: string,
  payload: AnnotationCreatePayload,
): Promise<Annotation> {
  const res = await fetch(`${API_BASE}/documents/${encodeURIComponent(documentId)}/annotations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

export async function updateAnnotation(
  annotationId: string,
  payload: Partial<Pick<AnnotationCreatePayload, "action" | "edits" | "reviewer" | "notes">>,
): Promise<Annotation> {
  const res = await fetch(`${API_BASE}/annotations/${encodeURIComponent(annotationId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

export async function bulkCreateAnnotations(
  documentId: string,
  annotations: AnnotationCreatePayload[],
): Promise<{ created: number }> {
  const res = await fetch(
    `${API_BASE}/documents/${encodeURIComponent(documentId)}/annotations/bulk`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ annotations }),
    },
  );
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

// ── S3 Explorer endpoints ─────────────────────────────────────────────────

export interface S3Folder {
  prefix: string;
  name: string;
  /** Aggregated size across all descendant objects (only when fetched with `with_stats=true`). */
  total_size?: number;
  /** Number of descendant files (only when fetched with `with_stats=true`). */
  file_count?: number;
  /** Most-recent `last_modified` of any descendant (only when fetched with `with_stats=true`). */
  last_modified?: string;
}

export interface S3PrefixStats {
  total_size: number;
  file_count: number;
  folder_count: number;
  immediate_files: number;
  last_modified: string;
}

export interface S3File {
  key: string;
  name: string;
  size: number;
  last_modified: string;
}

export interface S3ListResult {
  prefix: string;
  folders: S3Folder[];
  files: S3File[];
  truncated: boolean;
}

export interface S3FolderStats {
  total_size: number;
  file_count: number;
  last_modified: string;
}

export type S3FolderStatsResponse =
  | {
      prefix: string;
      status: "ready";
      age_seconds: number;
      folder_stats: Record<string, S3FolderStats>;
      prefix_stats: S3PrefixStats;
    }
  | { prefix: string; status: "computing" };

export interface S3ReadResult {
  key: string;
  size: number;
  content_type: string;
  format: string;
  data: unknown;
  total_lines?: number;
  truncated?: boolean;
  error?: string;
  preview?: string;
}

export interface S3SearchHit {
  key: string;
  name: string;
  size: number;
  last_modified: string;
  score: number;
  /** Indices into `key` of matched query characters (for highlighting). */
  match_indices: number[];
}

export interface S3SearchResult {
  q: string;
  prefix: string;
  total: number;
  hits: S3SearchHit[];
}

export function fetchS3List(prefix = "", delimiter = "/"): Promise<S3ListResult> {
  return apiFetch<S3ListResult>(
    `/s3/list?prefix=${encodeURIComponent(prefix)}&delimiter=${encodeURIComponent(delimiter)}`,
  );
}

export function fetchS3FolderStats(prefix = ""): Promise<S3FolderStatsResponse> {
  return apiFetch<S3FolderStatsResponse>(`/s3/folder_stats?prefix=${encodeURIComponent(prefix)}`);
}

export function fetchS3Read(key: string, maxLines = 500): Promise<S3ReadResult> {
  return apiFetch<S3ReadResult>(`/s3/read?key=${encodeURIComponent(key)}&max_lines=${maxLines}`);
}

export function fetchS3Search(q: string, prefix = "", limit = 200): Promise<S3SearchResult> {
  return apiFetch<S3SearchResult>(
    `/s3/search?q=${encodeURIComponent(q)}&prefix=${encodeURIComponent(prefix)}&limit=${limit}`,
  );
}

/** URL for streaming raw object bytes (for `<img>`, `<audio>`, `<video>`, downloads). */
export function s3RawUrl(key: string, download = false): string {
  const base = `/viewer/api/s3/raw?key=${encodeURIComponent(key)}`;
  return download ? `${base}&download=true` : base;
}

/** File extensions that should render as video */
const VIDEO_EXTENSIONS = new Set([".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".flv", ".wmv"]);

/** Returns true if the document contains video content */
export function isVideoFile(doc: MediaDocument): boolean {
  if (doc.metadata.has_video) return true;
  const ext = (doc.metadata.extension ?? "").toLowerCase();
  return VIDEO_EXTENSIONS.has(ext.startsWith(".") ? ext : `.${ext}`);
}

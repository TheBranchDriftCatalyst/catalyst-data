import type { MediaDocument, Transcription, Diarization, Mention, Assertion } from "@/types/media";

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

export function fetchMentions(id: string): Promise<Mention[]> {
  return apiFetch<Mention[]>(`/documents/${encodeURIComponent(id)}/mentions`);
}

export function fetchAssertions(id: string): Promise<Assertion[]> {
  return apiFetch<Assertion[]>(`/documents/${encodeURIComponent(id)}/assertions`);
}

/**
 * Constructs the media streaming URL for a document.
 * The backend serves files at /viewer/media/{source}/{relative_path}
 */
export function getMediaUrl(doc: MediaDocument): string {
  // source_path is the full NFS path like /data/metube/Some Video.mp4
  // We need to extract the relative path after /data/{source}/
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

/** File extensions that should render as video */
const VIDEO_EXTENSIONS = new Set([".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".flv", ".wmv"]);

/** Returns true if the document contains video content */
export function isVideoFile(doc: MediaDocument): boolean {
  if (doc.metadata.has_video) return true;
  const ext = (doc.metadata.extension ?? "").toLowerCase();
  return VIDEO_EXTENSIONS.has(ext.startsWith(".") ? ext : `.${ext}`);
}

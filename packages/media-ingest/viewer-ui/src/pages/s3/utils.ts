import {
  File as FileIcon,
  FileAudio,
  FileCode,
  FileImage,
  FileJson,
  FileText,
  FileVideo,
  type LucideIcon,
} from "lucide-react";

export function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
  return `${(bytes / Math.pow(k, i)).toFixed(i > 1 ? 1 : 0)} ${sizes[i]}`;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Tailwind text color for a medallion-layer prefix. */
export function layerColor(prefix: string): string | undefined {
  const p = prefix.toLowerCase();
  if (p.startsWith("bronze")) return "text-amber-500";
  if (p.startsWith("silver")) return "text-zinc-400";
  if (p.startsWith("gold")) return "text-yellow-400";
  if (p.startsWith("platinum")) return "text-cyan-400";
  if (p.startsWith("bench")) return "text-fuchsia-400";
  return undefined;
}

const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico", "avif"]);
const AUDIO_EXT = new Set(["mp3", "wav", "ogg", "flac", "m4a", "aac", "opus"]);
const VIDEO_EXT = new Set(["mp4", "mkv", "webm", "mov", "avi", "m4v", "flv", "wmv"]);
const TEXT_EXT = new Set([
  "txt",
  "md",
  "yaml",
  "yml",
  "toml",
  "cfg",
  "ini",
  "prompt",
  "log",
  "csv",
  "tsv",
]);
const CODE_EXT = new Set(["py", "ts", "tsx", "js", "jsx", "go", "rs", "sh", "html", "css"]);

export type FileKind = "image" | "audio" | "video" | "json" | "jsonl" | "text" | "code" | "binary";

export function fileKind(name: string): FileKind {
  const ext = extOf(name);
  if (ext === "json") return "json";
  if (ext === "jsonl") return "jsonl";
  if (IMAGE_EXT.has(ext)) return "image";
  if (AUDIO_EXT.has(ext)) return "audio";
  if (VIDEO_EXT.has(ext)) return "video";
  if (TEXT_EXT.has(ext)) return "text";
  if (CODE_EXT.has(ext)) return "code";
  return "binary";
}

export function extOf(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

export function fileIcon(name: string): LucideIcon {
  switch (fileKind(name)) {
    case "image":
      return FileImage;
    case "audio":
      return FileAudio;
    case "video":
      return FileVideo;
    case "json":
    case "jsonl":
      return FileJson;
    case "text":
      return FileText;
    case "code":
      return FileCode;
    default:
      return FileIcon;
  }
}

/** Try to extract `(family, document_id)` from a gold/media partition key.
 *
 * Matches `gold/<code_location>/media/<family>/<document_id>/...`. Returns
 * `null` if the key doesn't follow the partition convention so the caller
 * can hide the "open in player" affordance.
 */
export function parseMediaDeepLink(key: string): { family: string; documentId: string } | null {
  const parts = key.split("/");
  if (parts.length < 5) return null;
  if (parts[0] !== "gold") return null;
  if (parts[2] !== "media") return null;
  const family = parts[3];
  const documentId = parts[4];
  if (!family || !documentId) return null;
  if (!family.startsWith("media_")) return null;
  return { family, documentId };
}

import type { S3File, S3Folder } from "@/api/client";
import type { SortKey } from "./hooks/useExplorerState";

/** Sort folders + files by the chosen criterion.
 *
 *  When the listing was fetched with stats (`with_stats=true`), folders also
 *  sort by their aggregated `total_size` and `last_modified`. If stats are
 *  missing the folder array falls back to alphabetical so the UI never
 *  silently looks unsorted.
 */
export function sortListing(
  folders: S3Folder[],
  files: S3File[],
  sort: SortKey,
  desc: boolean,
): { folders: S3Folder[]; files: S3File[] } {
  const sign = desc ? -1 : 1;
  const sortedFolders = [...folders].sort((a, b) => {
    let cmp = 0;
    if (sort === "size") cmp = (a.total_size ?? -1) - (b.total_size ?? -1);
    else if (sort === "modified")
      cmp = (a.last_modified ?? "").localeCompare(b.last_modified ?? "");
    else cmp = a.name.localeCompare(b.name);
    return cmp === 0 ? a.name.localeCompare(b.name) : sign * cmp;
  });
  const sortedFiles = [...files].sort((a, b) => {
    let cmp = 0;
    if (sort === "size") cmp = a.size - b.size;
    else if (sort === "modified") cmp = a.last_modified.localeCompare(b.last_modified);
    else cmp = a.name.localeCompare(b.name);
    return cmp === 0 ? a.name.localeCompare(b.name) : sign * cmp;
  });
  return { folders: sortedFolders, files: sortedFiles };
}

/** Split a string at `indices` into matched/unmatched runs for highlighting. */
export function highlightRuns(
  haystack: string,
  indices: number[],
): { text: string; matched: boolean }[] {
  if (indices.length === 0) return [{ text: haystack, matched: false }];
  const out: { text: string; matched: boolean }[] = [];
  const idxSet = new Set(indices);
  let i = 0;
  while (i < haystack.length) {
    const matched = idxSet.has(i);
    let j = i + 1;
    while (j < haystack.length && idxSet.has(j) === matched) j++;
    out.push({ text: haystack.slice(i, j), matched });
    i = j;
  }
  return out;
}

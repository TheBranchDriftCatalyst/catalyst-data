import { s3RawUrl } from "@/api/client";
import type { FileKind } from "../utils";

interface MediaPreviewProps {
  kind: Extract<FileKind, "image" | "audio" | "video">;
  s3Key: string;
}

/** Inline preview for image/audio/video objects via the streaming /raw endpoint. */
export function MediaPreview({ kind, s3Key }: MediaPreviewProps) {
  const url = s3RawUrl(s3Key);
  if (kind === "image") {
    return (
      <div className="flex items-center justify-center p-4 h-full">
        <img
          src={url}
          alt={s3Key}
          className="max-w-full max-h-full object-contain rounded border border-white/5"
        />
      </div>
    );
  }
  if (kind === "audio") {
    return (
      <div className="p-4">
        <audio controls src={url} className="w-full" preload="metadata" />
      </div>
    );
  }
  return (
    <div className="p-4">
      <video
        controls
        src={url}
        className="w-full max-h-[70vh] rounded border border-white/5 bg-black"
        preload="metadata"
      />
    </div>
  );
}

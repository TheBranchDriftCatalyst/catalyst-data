import { ScrollArea } from "@thebranchdriftcatalyst/catalyst-ui";
import type { S3ReadResult } from "@/api/client";
import { TruncationBanner } from "./TruncationBanner";

interface RawViewProps {
  content: S3ReadResult;
}

/** Universal "raw bytes" fallback. Renders text-as-`<pre>`, JSON-as-pretty-
 *  printed-string, or the backend-supplied `preview` placeholder for binary. */
export function RawView({ content }: RawViewProps) {
  return (
    <ScrollArea className="h-full">
      <div className="p-4">
        {content.truncated && <TruncationBanner content={content} />}
        {content.preview && !content.data && (
          <div className="text-sm text-zinc-500 font-mono">{content.preview}</div>
        )}
        {content.data != null && (
          <pre className="text-xs text-zinc-300 font-mono whitespace-pre-wrap break-all leading-relaxed">
            {typeof content.data === "string"
              ? content.data
              : JSON.stringify(content.data, null, 2)}
          </pre>
        )}
      </div>
    </ScrollArea>
  );
}

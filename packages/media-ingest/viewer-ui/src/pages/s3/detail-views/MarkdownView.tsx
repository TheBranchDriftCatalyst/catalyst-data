import { ScrollArea } from "@thebranchdriftcatalyst/catalyst-ui";
import { MarkdownRenderer } from "@thebranchdriftcatalyst/catalyst-ui/components/MarkdownRenderer";

interface MarkdownViewProps {
  content: string;
}

/** Rendered markdown view (catalyst-ui MarkdownRenderer). */
export function MarkdownView({ content }: MarkdownViewProps) {
  return (
    <ScrollArea className="h-full">
      <div className="p-4 prose prose-invert prose-sm max-w-none">
        <MarkdownRenderer content={content} />
      </div>
    </ScrollArea>
  );
}

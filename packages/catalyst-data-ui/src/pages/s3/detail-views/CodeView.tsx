import { ScrollArea } from "@thebranchdriftcatalyst/catalyst-ui";
import { CodeBlock } from "@thebranchdriftcatalyst/catalyst-ui/components/CodeBlock";

interface CodeViewProps {
  code: string;
  language: string;
}

/** Syntax-highlighted code view via catalyst-ui's CodeBlock. */
export function CodeView({ code, language }: CodeViewProps) {
  return (
    <ScrollArea className="h-full">
      <div className="p-2">
        <CodeBlock
          code={code}
          language={language}
          showLineNumbers
          showCopyButton
          useCardContext={false}
        />
      </div>
    </ScrollArea>
  );
}

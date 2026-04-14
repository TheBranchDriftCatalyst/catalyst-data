import type { InlineAnnotation } from "@/hooks/useInlineAnnotations";

interface AnnotatedTextProps {
  text: string;
  annotations: InlineAnnotation[];
  onEntityClick?: (text: string) => void;
}

/**
 * Renders text with inline entity highlights.
 *
 * Splits the input text into plain and annotated fragments using the
 * annotation start/end positions as split points. Annotated fragments
 * are rendered as colored `<span>` elements with type-specific CSS classes.
 */
export default function AnnotatedText({ text, annotations, onEntityClick }: AnnotatedTextProps) {
  if (annotations.length === 0) return <>{text}</>;

  // Build cut points from annotations
  const cuts = new Set<number>();
  cuts.add(0);
  cuts.add(text.length);
  for (const ann of annotations) {
    cuts.add(ann.start);
    cuts.add(ann.end);
  }
  const sortedCuts = Array.from(cuts).sort((a, b) => a - b);

  // Build an index for quick annotation lookup
  const annotationAt = (pos: number): InlineAnnotation | undefined =>
    annotations.find((a) => pos >= a.start && pos < a.end);

  const parts: React.ReactNode[] = [];
  for (let i = 0; i < sortedCuts.length - 1; i++) {
    const from = sortedCuts[i]!;
    const to = sortedCuts[i + 1]!;
    if (from === to) continue;

    const fragment = text.slice(from, to);
    const ann = annotationAt(from);

    if (ann) {
      parts.push(
        <span
          key={i}
          className={`entity-highlight entity-highlight-${ann.entityType}`}
          onClick={(e) => {
            e.stopPropagation();
            onEntityClick?.(ann.text);
          }}
          title={`${ann.entityType} (${Math.round(ann.confidence * 100)}%)`}
        >
          {fragment}
        </span>,
      );
    } else {
      parts.push(<span key={i}>{fragment}</span>);
    }
  }

  return <>{parts}</>;
}

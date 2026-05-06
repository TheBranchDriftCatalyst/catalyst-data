import React from "react";
import { cn } from "@/lib/utils";

interface SkeletonProps extends React.HTMLAttributes<HTMLDivElement> {
  className?: string;
}

export function Skeleton({ className, ...props }: SkeletonProps) {
  return <div className={cn("skeleton rounded-md", className)} aria-hidden="true" {...props} />;
}

/**
 * Loading skeleton for the document list cards.
 */
export function DocumentCardSkeleton() {
  return (
    <div className="rounded-lg border border-white/5 overflow-hidden bg-surface-1">
      <Skeleton className="h-32 rounded-none" />
      <div className="p-3 space-y-2">
        <Skeleton className="h-4 w-3/4" />
        <div className="flex gap-2">
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-4 w-12" />
        </div>
      </div>
    </div>
  );
}

/**
 * Loading skeleton for the document list row view.
 */
export function DocumentRowSkeleton() {
  return (
    <div className="flex items-center gap-4 px-4 py-2.5">
      <Skeleton className="h-5 w-5 rounded" />
      <Skeleton className="h-4 flex-1 max-w-xs" />
      <Skeleton className="h-4 w-16" />
      <Skeleton className="h-4 w-12" />
      <Skeleton className="h-4 w-14" />
    </div>
  );
}

/**
 * Loading skeleton for the player page.
 */
export function PlayerSkeleton() {
  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden animate-pulse">
      {/* Header skeleton */}
      <div className="flex items-center gap-4 px-4 py-2.5 bg-surface-1 border-b border-white/5">
        <Skeleton className="h-4 w-16" />
        <Skeleton className="h-4 flex-1 max-w-sm" />
        <Skeleton className="h-4 w-20" />
      </div>

      <div className="flex-1 flex min-h-0">
        {/* Left column */}
        <div className="flex-1 flex flex-col min-h-0 p-4 gap-3">
          <Skeleton className="h-[40vh] rounded-lg" />
          <Skeleton className="h-10 rounded-md" />
          <div className="flex-1 flex gap-4">
            <Skeleton className="flex-1 rounded-md" />
            <Skeleton className="flex-1 rounded-md" />
          </div>
        </div>

        {/* Right column - transcript */}
        <div className="w-[400px] border-l border-white/5 p-4 space-y-2">
          <Skeleton className="h-4 w-24 mb-4" />
          {Array.from({ length: 12 }).map((_, i) => (
            <Skeleton
              key={i}
              className="h-6"
              style={{ width: `${60 + Math.random() * 40}%` } as React.CSSProperties}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

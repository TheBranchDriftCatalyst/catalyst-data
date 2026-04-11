import { useMemo, useCallback } from "react";
import {
  Badge,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@thebranchdriftcatalyst/catalyst-ui";
import type { TimelineMarker } from "@/types/media";
import { cn } from "@/lib/utils";

interface ScrubberMarkersProps {
  markers: TimelineMarker[];
  duration: number;
  onMarkerClick: (marker: TimelineMarker) => void;
  className?: string;
}

/** Threshold (in %) below which markers are clustered together. */
const CLUSTER_THRESHOLD = 1;

interface MarkerCluster {
  /** All markers in this cluster. */
  markers: TimelineMarker[];
  /** Average position as a percentage of the track width. */
  position: number;
}

/**
 * Group markers that are within CLUSTER_THRESHOLD % of each other.
 */
function clusterMarkers(markers: TimelineMarker[], duration: number): MarkerCluster[] {
  if (duration <= 0 || markers.length === 0) return [];

  // Sort by timestamp
  const sorted = [...markers].sort((a, b) => a.timestamp - b.timestamp);
  const clusters: MarkerCluster[] = [];
  let current: TimelineMarker[] = [sorted[0]!];
  let currentPct = (sorted[0]!.timestamp / duration) * 100;

  for (let i = 1; i < sorted.length; i++) {
    const m = sorted[i]!;
    const pct = (m.timestamp / duration) * 100;

    if (pct - currentPct <= CLUSTER_THRESHOLD) {
      current.push(m);
    } else {
      // Finalize previous cluster
      const avgPct =
        current.reduce((sum, c) => sum + (c.timestamp / duration) * 100, 0) / current.length;
      clusters.push({ markers: current, position: avgPct });

      current = [m];
      currentPct = pct;
    }
  }

  // Finalize last cluster
  if (current.length > 0) {
    const avgPct =
      current.reduce((sum, c) => sum + (c.timestamp / duration) * 100, 0) / current.length;
    clusters.push({ markers: current, position: avgPct });
  }

  return clusters;
}

/**
 * Renders colored marker ticks on the video timeline / scrubber area.
 *
 * - Single markers: thin vertical tick at the correct position.
 * - Clustered markers: tick with a count badge.
 * - Range markers (with endTimestamp): a colored band instead of a tick.
 *
 * Positioned with pointer-events so the underlying scrubber still works for dragging.
 */
export default function ScrubberMarkers({
  markers,
  duration,
  onMarkerClick,
  className,
}: ScrubberMarkersProps) {
  const clusters = useMemo(() => clusterMarkers(markers, duration), [markers, duration]);

  const handleClusterClick = useCallback(
    (cluster: MarkerCluster) => {
      // Click the first marker in the cluster to seek there
      if (cluster.markers.length > 0) {
        onMarkerClick(cluster.markers[0]!);
      }
    },
    [onMarkerClick],
  );

  if (duration <= 0 || markers.length === 0) return null;

  // Separate range markers (with endTimestamp) from point markers
  const rangeMarkers = markers.filter(
    (m) => m.endTimestamp != null && m.endTimestamp !== m.timestamp,
  );

  return (
    <div className={cn("absolute inset-0 pointer-events-none", className)}>
      {/* Range bands */}
      {rangeMarkers.map((m) => {
        const leftPct = (m.timestamp / duration) * 100;
        const widthPct = ((m.endTimestamp! - m.timestamp) / duration) * 100;
        return (
          <Tooltip key={`range-${m.id}`}>
            <TooltipTrigger asChild>
              <button
                className="absolute top-0 h-full opacity-20 hover:opacity-40 transition-opacity pointer-events-auto cursor-pointer animate-in fade-in duration-300"
                style={{
                  left: `${leftPct}%`,
                  width: `${Math.max(widthPct, 0.3)}%`,
                  backgroundColor: m.color,
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  onMarkerClick(m);
                }}
              />
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs text-xs">
              {m.label}
            </TooltipContent>
          </Tooltip>
        );
      })}

      {/* Point markers / clusters */}
      {clusters.map((cluster, ci) => {
        const isSingle = cluster.markers.length === 1;
        const primary = cluster.markers[0]!;

        return (
          <Tooltip key={`cluster-${ci}`}>
            <TooltipTrigger asChild>
              <button
                className="absolute top-0 flex flex-col items-center pointer-events-auto cursor-pointer group animate-in fade-in duration-300"
                style={{
                  left: `${cluster.position}%`,
                  transform: "translateX(-50%)",
                  height: "100%",
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  handleClusterClick(cluster);
                }}
              >
                {/* Tick */}
                <div
                  className="w-[2px] h-3.5 rounded-full opacity-80 group-hover:opacity-100 group-hover:h-4 transition-all"
                  style={{ backgroundColor: primary.color }}
                />

                {/* Cluster count badge */}
                {!isSingle && (
                  <Badge
                    variant="secondary"
                    className="absolute -top-3.5 text-[8px] px-1 py-0 h-3.5 min-w-[16px] tabular-nums leading-none"
                  >
                    {cluster.markers.length}
                  </Badge>
                )}
              </button>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs">
              {isSingle ? (
                <p className="text-xs">{primary.label}</p>
              ) : (
                <div className="space-y-0.5">
                  <p className="text-xs font-medium">{cluster.markers.length} markers</p>
                  {cluster.markers.slice(0, 5).map((m) => (
                    <p key={m.id} className="text-[10px] text-zinc-400 truncate max-w-[200px]">
                      <span
                        className="inline-block w-1.5 h-1.5 rounded-full mr-1"
                        style={{ backgroundColor: m.color }}
                      />
                      {m.label}
                    </p>
                  ))}
                  {cluster.markers.length > 5 && (
                    <p className="text-[10px] text-zinc-500">+{cluster.markers.length - 5} more</p>
                  )}
                </div>
              )}
            </TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}

import { useRef, useState, useEffect, useCallback, forwardRef, useImperativeHandle } from "react";
import { Card, CardContent } from "@thebranchdriftcatalyst/catalyst-ui";
import { AudioLines } from "lucide-react";
import type { MediaDocument, TimelineMarker } from "@/types/media";
import { getMediaUrl, isVideoFile } from "@/api/client";
import ScrubberMarkers from "@/components/ScrubberMarkers";
import { cn } from "@/lib/utils";

export interface MediaPlayerHandle {
  seek: (time: number) => void;
  play: () => void;
  pause: () => void;
  get currentTime(): number;
  get duration(): number;
  get paused(): boolean;
}

interface MediaPlayerProps {
  document: MediaDocument;
  markers?: TimelineMarker[];
  onMarkerClick?: (marker: TimelineMarker) => void;
  onTimeUpdate?: (currentTime: number) => void;
  onDurationChange?: (duration: number) => void;
  onPlay?: () => void;
  onPause?: () => void;
  className?: string;
}

const MediaPlayer = forwardRef<MediaPlayerHandle, MediaPlayerProps>(function MediaPlayer(
  {
    document: doc,
    markers = [],
    onMarkerClick,
    onTimeUpdate,
    onDurationChange,
    onPlay,
    onPause,
    className = "",
  },
  ref,
) {
  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement>(null);
  const [mediaDuration, setMediaDuration] = useState(0);

  useImperativeHandle(
    ref,
    () => ({
      seek(time: number) {
        if (mediaRef.current) {
          mediaRef.current.currentTime = time;
        }
      },
      play() {
        mediaRef.current?.play();
      },
      pause() {
        mediaRef.current?.pause();
      },
      get currentTime() {
        return mediaRef.current?.currentTime ?? 0;
      },
      get duration() {
        return mediaRef.current?.duration ?? 0;
      },
      get paused() {
        return mediaRef.current?.paused ?? true;
      },
    }),
    [],
  );

  const handleTimeUpdate = useCallback(() => {
    if (mediaRef.current && onTimeUpdate) {
      onTimeUpdate(mediaRef.current.currentTime);
    }
  }, [onTimeUpdate]);

  const handleDurationChange = useCallback(() => {
    if (mediaRef.current) {
      setMediaDuration(mediaRef.current.duration);
      onDurationChange?.(mediaRef.current.duration);
    }
  }, [onDurationChange]);

  // Attach high-frequency time update via requestAnimationFrame for smoother sync
  useEffect(() => {
    const el = mediaRef.current;
    if (!el || !onTimeUpdate) return;

    let raf: number;
    let running = true;

    const tick = () => {
      if (!running) return;
      if (!el.paused) {
        onTimeUpdate(el.currentTime);
      }
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => {
      running = false;
      cancelAnimationFrame(raf);
    };
  }, [onTimeUpdate]);

  const mediaUrl = getMediaUrl(doc);
  const isVideo = isVideoFile(doc);

  const commonProps = {
    ref: mediaRef as React.RefObject<HTMLVideoElement>,
    src: mediaUrl,
    controls: true,
    preload: "metadata" as const,
    onTimeUpdate: handleTimeUpdate,
    onDurationChange: handleDurationChange,
    onPlay: onPlay,
    onPause: onPause,
  };

  const markerOverlay =
    markers.length > 0 && mediaDuration > 0 && onMarkerClick ? (
      <div className="relative w-full h-4 bg-surface-2/50 flex-shrink-0">
        <ScrubberMarkers markers={markers} duration={mediaDuration} onMarkerClick={onMarkerClick} />
      </div>
    ) : null;

  if (isVideo) {
    return (
      <div className={cn("relative bg-black rounded-lg overflow-hidden", className)}>
        <video {...commonProps} className="w-full h-full object-contain" playsInline />
        {markerOverlay}
      </div>
    );
  }

  // Audio-only: show a compact player with card styling
  return (
    <Card interactive={false} className={cn("overflow-hidden", className)}>
      <CardContent className="flex items-center gap-4 p-4">
        <div className="w-14 h-14 rounded-lg bg-surface-2 flex items-center justify-center flex-shrink-0">
          <AudioLines className="h-7 w-7 text-zinc-400" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-medium text-zinc-200 truncate">{doc.title}</h3>
          <p className="text-xs text-zinc-500">{doc.metadata.extension?.toUpperCase()} Audio</p>
          <audio
            {...commonProps}
            ref={mediaRef as React.RefObject<HTMLAudioElement>}
            className="w-full h-10 mt-2 [&::-webkit-media-controls-panel]:bg-surface-2"
          />
          {markerOverlay}
        </div>
      </CardContent>
    </Card>
  );
});

export default MediaPlayer;

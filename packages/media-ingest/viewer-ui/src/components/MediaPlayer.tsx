import { useRef, useEffect, useCallback, forwardRef, useImperativeHandle } from "react";
import type { MediaDocument } from "@/types/media";
import { getMediaUrl, isVideoFile } from "@/api/client";

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
  onTimeUpdate?: (currentTime: number) => void;
  onDurationChange?: (duration: number) => void;
  onPlay?: () => void;
  onPause?: () => void;
  className?: string;
}

const MediaPlayer = forwardRef<MediaPlayerHandle, MediaPlayerProps>(
  function MediaPlayer(
    { document: doc, onTimeUpdate, onDurationChange, onPlay, onPause, className = "" },
    ref
  ) {
    const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement>(null);

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
      []
    );

    const handleTimeUpdate = useCallback(() => {
      if (mediaRef.current && onTimeUpdate) {
        onTimeUpdate(mediaRef.current.currentTime);
      }
    }, [onTimeUpdate]);

    const handleDurationChange = useCallback(() => {
      if (mediaRef.current && onDurationChange) {
        onDurationChange(mediaRef.current.duration);
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

    if (isVideo) {
      return (
        <div className={`relative bg-black rounded-lg overflow-hidden ${className}`}>
          <video
            {...commonProps}
            className="w-full h-full object-contain"
            playsInline
          />
        </div>
      );
    }

    // Audio-only: show a compact player with waveform-style background
    return (
      <div
        className={`relative bg-surface-1 rounded-lg overflow-hidden flex items-center justify-center p-8 ${className}`}
      >
        <div className="w-full max-w-2xl">
          <div className="flex items-center gap-4 mb-4">
            <div className="w-16 h-16 rounded-lg bg-surface-2 flex items-center justify-center">
              <svg
                className="w-8 h-8 text-zinc-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1.5}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M19.114 5.636a9 9 0 010 12.728M16.463 8.288a5.25 5.25 0 010 7.424M6.75 8.25l4.72-4.72a.75.75 0 011.28.53v15.88a.75.75 0 01-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.01 9.01 0 012.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75z"
                />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-medium text-zinc-200 truncate">
                {doc.title}
              </h3>
              <p className="text-xs text-zinc-500">{doc.metadata.extension?.toUpperCase()} Audio</p>
            </div>
          </div>
          <audio
            {...commonProps}
            ref={mediaRef as React.RefObject<HTMLAudioElement>}
            className="w-full h-12 [&::-webkit-media-controls-panel]:bg-surface-2"
          />
        </div>
      </div>
    );
  }
);

export default MediaPlayer;

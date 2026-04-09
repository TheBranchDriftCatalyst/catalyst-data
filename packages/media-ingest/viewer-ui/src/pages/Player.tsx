import { useState, useRef, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import { useDocumentData } from "@/hooks/useDocumentData";
import { useMediaSync } from "@/hooks/useMediaSync";
import MediaPlayer, { type MediaPlayerHandle } from "@/components/MediaPlayer";
import SpeakerTimeline from "@/components/SpeakerTimeline";
import Transcript from "@/components/Transcript";
import SpeakerBreakdown from "@/components/SpeakerBreakdown";
import EntityPanel from "@/components/EntityPanel";
import AssertionPanel from "@/components/AssertionPanel";
import { formatTime } from "@/lib/speakers";

type BottomTab = "entities" | "assertions";

export default function PlayerPage() {
  const { documentId } = useParams<{ documentId: string }>();
  const {
    document: doc,
    transcription,
    diarization,
    mentions,
    assertions,
    isLoading,
    isError,
    errors,
  } = useDocumentData(documentId);

  const playerRef = useRef<MediaPlayerHandle>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [bottomTab, setBottomTab] = useState<BottomTab>("entities");
  const [highlightText, setHighlightText] = useState<string | undefined>();

  // Prefer diarization segments over plain transcription
  const segments = diarization?.segments ?? transcription?.segments ?? [];
  const speakers = diarization?.speakers ?? [];
  const effectiveDuration =
    duration || diarization?.duration_s || transcription?.duration_s || 0;

  const { activeSegmentIndex, activeWordIndex } = useMediaSync(
    segments,
    currentTime
  );

  const handleSeek = useCallback((time: number) => {
    playerRef.current?.seek(time);
    setCurrentTime(time);
  }, []);

  const handleTimeUpdate = useCallback((time: number) => {
    setCurrentTime(time);
  }, []);

  const handleDurationChange = useCallback((d: number) => {
    setDuration(d);
  }, []);

  const handleEntityClick = useCallback((text: string) => {
    setHighlightText((prev) => (prev === text ? undefined : text));
  }, []);

  // Loading state
  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-zinc-600 border-t-zinc-300 rounded-full animate-spin" />
          <span className="text-sm text-zinc-400">Loading media...</span>
        </div>
      </div>
    );
  }

  // Error state
  if (isError || !doc) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="max-w-md text-center">
          <div className="text-red-400 mb-3">
            <svg className="w-12 h-12 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z" />
            </svg>
          </div>
          <h2 className="text-lg font-medium text-zinc-200 mb-2">Failed to load document</h2>
          <p className="text-sm text-zinc-500 mb-4">
            {errors.map((e) => (e as Error).message).join("; ") || "Document not found"}
          </p>
          <Link
            to="/"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-md bg-surface-2 text-sm text-zinc-300 hover:bg-surface-3 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
            </svg>
            Back to library
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Top header bar */}
      <header className="flex items-center gap-4 px-4 py-2.5 bg-surface-1 border-b border-white/5 flex-shrink-0">
        <Link
          to="/"
          className="flex items-center gap-1.5 text-zinc-400 hover:text-zinc-200 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
          </svg>
          <span className="text-xs">Back</span>
        </Link>

        <div className="flex-1 min-w-0">
          <h1 className="text-sm font-medium text-zinc-200 truncate">{doc.title}</h1>
        </div>

        <div className="flex items-center gap-3 flex-shrink-0">
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-2 text-zinc-500 uppercase font-medium">
            {doc.source}
          </span>
          {doc.metadata.extension && (
            <span className="text-[10px] text-zinc-600 uppercase">
              {doc.metadata.extension}
            </span>
          )}
          {effectiveDuration > 0 && (
            <span className="text-xs text-zinc-400 tabular-nums">
              {formatTime(effectiveDuration)}
            </span>
          )}
          {speakers.length > 0 && (
            <span className="text-[10px] text-zinc-500">
              {speakers.length} speaker{speakers.length !== 1 ? "s" : ""}
            </span>
          )}
        </div>
      </header>

      {/* Main content area — two columns */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Left column: media + timeline + breakdown */}
        <div className="flex-1 flex flex-col min-h-0 min-w-0">
          {/* Media player */}
          <div className="flex-shrink-0 p-4 pb-2">
            <MediaPlayer
              ref={playerRef}
              document={doc}
              onTimeUpdate={handleTimeUpdate}
              onDurationChange={handleDurationChange}
              className="max-h-[50vh]"
            />
          </div>

          {/* Speaker timeline */}
          <div className="flex-shrink-0 px-4 pb-2">
            <SpeakerTimeline
              segments={segments}
              duration={effectiveDuration}
              currentTime={currentTime}
              speakers={speakers}
              onSeek={handleSeek}
            />
          </div>

          {/* Bottom section: breakdown + entities/assertions tabs */}
          <div className="flex-1 flex min-h-0 overflow-hidden">
            {/* Speaker breakdown */}
            <div className="w-1/2 border-r border-white/5 p-4 overflow-y-auto">
              <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-3">
                Speaker Breakdown
              </h3>
              <SpeakerBreakdown
                segments={segments}
                speakers={speakers}
                duration={effectiveDuration}
              />
            </div>

            {/* Entities / Assertions tabs */}
            <div className="w-1/2 flex flex-col min-h-0">
              {/* Tab bar */}
              <div className="flex border-b border-white/5 flex-shrink-0">
                <button
                  onClick={() => setBottomTab("entities")}
                  className={`flex-1 px-4 py-2 text-xs font-medium transition-colors ${
                    bottomTab === "entities"
                      ? "text-zinc-200 border-b-2 border-blue-500"
                      : "text-zinc-500 hover:text-zinc-300"
                  }`}
                >
                  Entities
                  {mentions.length > 0 && (
                    <span className="ml-1.5 px-1.5 py-0.5 rounded-full bg-surface-2 text-[10px] tabular-nums">
                      {mentions.length}
                    </span>
                  )}
                </button>
                <button
                  onClick={() => setBottomTab("assertions")}
                  className={`flex-1 px-4 py-2 text-xs font-medium transition-colors ${
                    bottomTab === "assertions"
                      ? "text-zinc-200 border-b-2 border-blue-500"
                      : "text-zinc-500 hover:text-zinc-300"
                  }`}
                >
                  Assertions
                  {assertions.length > 0 && (
                    <span className="ml-1.5 px-1.5 py-0.5 rounded-full bg-surface-2 text-[10px] tabular-nums">
                      {assertions.length}
                    </span>
                  )}
                </button>
              </div>

              {/* Tab content */}
              <div className="flex-1 min-h-0 overflow-hidden">
                {bottomTab === "entities" && (
                  <EntityPanel
                    mentions={mentions}
                    onEntityClick={handleEntityClick}
                    className="h-full"
                  />
                )}
                {bottomTab === "assertions" && (
                  <AssertionPanel assertions={assertions} className="h-full" />
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Right column: transcript */}
        <div className="w-[400px] xl:w-[480px] flex flex-col min-h-0 border-l border-white/5 flex-shrink-0">
          <div className="px-4 py-2.5 border-b border-white/5 flex items-center justify-between flex-shrink-0">
            <h2 className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">
              Transcript
            </h2>
            {transcription?.language && (
              <span className="text-[10px] text-zinc-600 uppercase">
                {transcription.language}
                {transcription.language_probability > 0 && (
                  <span className="ml-1 opacity-50">
                    {(transcription.language_probability * 100).toFixed(0)}%
                  </span>
                )}
              </span>
            )}
            {highlightText && (
              <button
                onClick={() => setHighlightText(undefined)}
                className="text-[10px] text-amber-500 hover:text-amber-400 transition-colors flex items-center gap-1"
              >
                <span>Highlighting: {highlightText}</span>
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
          <Transcript
            segments={segments}
            activeSegmentIndex={activeSegmentIndex}
            activeWordIndex={activeWordIndex}
            onSeek={handleSeek}
            highlightText={highlightText}
            className="flex-1 min-h-0"
          />
        </div>
      </div>
    </div>
  );
}

import { useState, useRef, useCallback, useMemo } from "react";
import { useParams, Link } from "react-router-dom";
import {
  Button,
  Badge,
  Separator,
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@thebranchdriftcatalyst/catalyst-ui";
import { ArrowLeft, Clock, Users, Globe, X, Highlighter, Play } from "lucide-react";
import { useDocumentData } from "@/hooks/useDocumentData";
import { useMediaSync } from "@/hooks/useMediaSync";
import { useMarkerData } from "@/hooks/useMarkerData";
import { useFilteredPlayback } from "@/hooks/useFilteredPlayback";
import { useTranscriptScroll } from "@/hooks/useTranscriptScroll";
import { useSpeakerNames } from "@/hooks/useSpeakerNames";
import { useAnnotations } from "@/hooks/useAnnotations";
import MediaPlayer, { type MediaPlayerHandle } from "@/components/MediaPlayer";
import SpeakerTimeline from "@/components/SpeakerTimeline";
import Transcript from "@/components/Transcript";
import SpeakerBreakdown from "@/components/SpeakerBreakdown";
import EntityPanel from "@/components/EntityPanel";
import AssertionPanel from "@/components/AssertionPanel";
import ResizablePanel from "@/components/ResizablePanel";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { PlayerSkeleton } from "@/components/Skeleton";
import { formatTime } from "@/lib/speakers";
import type { TimelineMarker } from "@/types/media";

export default function PlayerPage() {
  const { documentId } = useParams<{ documentId: string }>();
  const {
    document: doc,
    transcription,
    diarization,
    chunks,
    mentions,
    assertions,
    isLoading,
    isError,
    errors,
  } = useDocumentData(documentId);

  const {
    nameMap: speakerNames,
    setName: setSpeakerName,
    resolve: resolveSpeaker,
  } = useSpeakerNames(documentId);

  const { getStatus, approve, reject, edit, bulkApprove, bulkReject } = useAnnotations(documentId);

  const playerRef = useRef<MediaPlayerHandle>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [highlightText, setHighlightText] = useState<string | undefined>();
  const [selectedEntityText, setSelectedEntityText] = useState<string | null>(null);
  const [selectedAssertionId, setSelectedAssertionId] = useState<string | null>(null);

  // Prefer diarization segments over plain transcription
  const segments = diarization?.segments ?? transcription?.segments ?? [];
  const speakers = diarization?.speakers ?? [];
  const effectiveDuration = duration || diarization?.duration_s || transcription?.duration_s || 0;

  const { activeSegmentIndex, activeWordIndex } = useMediaSync(segments, currentTime);

  const { scrollToTimestamp, transcriptRef, scrollHighlightIndex } = useTranscriptScroll({
    segments,
  });

  // Compute timeline markers from mentions/assertions + selection state
  const markers = useMarkerData({
    mentions,
    assertions,
    transcription: diarization ?? transcription ?? null,
    selectedEntityText,
    selectedAssertionId,
  });

  // Build time ranges for filtered playback from the current markers
  const filteredRanges = useMemo(() => {
    if (!selectedEntityText) return [];
    return markers
      .filter((m) => m.endTimestamp != null && m.endTimestamp !== m.timestamp)
      .map((m) => ({ start: m.timestamp, end: m.endTimestamp! }))
      .concat(
        markers
          .filter((m) => m.endTimestamp == null || m.endTimestamp === m.timestamp)
          .map((m) => ({ start: m.timestamp, end: m.timestamp + 1 })),
      )
      .sort((a, b) => a.start - b.start);
  }, [markers, selectedEntityText]);

  const handleSeek = useCallback((time: number) => {
    playerRef.current?.seek(time);
    setCurrentTime(time);
  }, []);

  const handlePause = useCallback(() => {
    playerRef.current?.pause();
  }, []);

  const { isFilteredPlayback, onTimeUpdate: filteredPlaybackTimeUpdate } = useFilteredPlayback({
    ranges: filteredRanges,
    seek: handleSeek,
    pause: handlePause,
    enabled: selectedEntityText != null,
  });

  const handleTimeUpdate = useCallback(
    (time: number) => {
      setCurrentTime(time);
      filteredPlaybackTimeUpdate(time);
    },
    [filteredPlaybackTimeUpdate],
  );

  const handleDurationChange = useCallback((d: number) => {
    setDuration(d);
  }, []);

  const handleEntityClick = useCallback((text: string) => {
    setHighlightText((prev) => (prev === text ? undefined : text));
  }, []);

  const handleEntitySelect = useCallback((entityText: string | null) => {
    setSelectedEntityText(entityText);
    // Clear assertion selection when selecting an entity
    if (entityText) setSelectedAssertionId(null);
  }, []);

  const handleMentionSeek = useCallback(
    (timeInSeconds: number) => {
      playerRef.current?.seek(timeInSeconds);
      setCurrentTime(timeInSeconds);
      scrollToTimestamp(timeInSeconds);
    },
    [scrollToTimestamp],
  );

  const handleAssertionSelect = useCallback(
    (assertionId: string | null) => {
      setSelectedAssertionId(assertionId);
      // Clear entity selection when selecting an assertion
      if (assertionId) {
        setSelectedEntityText(null);
        // Seek + scroll to the assertion's temporal position via markers
        // The markers array already has the correct timestamp (computed by useMarkerData)
        const marker = markers.find((m) => m.id === `assertion-${assertionId}`);
        if (marker) {
          playerRef.current?.seek(marker.timestamp);
          setCurrentTime(marker.timestamp);
          scrollToTimestamp(marker.timestamp);
        }
      }
    },
    [markers, scrollToTimestamp],
  );

  const handleMentionApprove = useCallback(
    (targetId: string) => approve("mention", targetId),
    [approve],
  );

  const handleMentionReject = useCallback(
    (targetId: string) => reject("mention", targetId),
    [reject],
  );

  const handleMentionEdit = useCallback(
    (targetId: string, edits: Record<string, unknown>) => edit("mention", targetId, edits),
    [edit],
  );

  const handleAssertionApprove = useCallback(
    (targetId: string) => approve("assertion", targetId),
    [approve],
  );

  const handleAssertionReject = useCallback(
    (targetId: string) => reject("assertion", targetId),
    [reject],
  );

  const handleMarkerClick = useCallback(
    (marker: TimelineMarker) => {
      handleSeek(marker.timestamp);
      scrollToTimestamp(marker.timestamp);
    },
    [handleSeek, scrollToTimestamp],
  );

  // Loading state
  if (isLoading) {
    return <PlayerSkeleton />;
  }

  // Error state
  if (isError || !doc) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="max-w-md text-center">
          <div className="rounded-full bg-red-950/50 p-4 mb-4 inline-flex">
            <X className="h-8 w-8 text-red-400" />
          </div>
          <h2 className="text-lg font-medium text-zinc-200 mb-2">Failed to load document</h2>
          <p className="text-sm text-zinc-500 mb-4">
            {errors.map((e) => (e as Error).message).join("; ") || "Document not found"}
          </p>
          <Button variant="outline" asChild>
            <Link to="/documents/media" className="gap-2">
              <ArrowLeft className="h-4 w-4" />
              Back to library
            </Link>
          </Button>
        </div>
      </div>
    );
  }

  return (
    <ErrorBoundary>
      <div data-testid="player-page" className="flex-1 flex flex-col min-h-0 overflow-hidden">
        {/* Top header bar */}
        <header className="flex items-center gap-3 px-4 py-2 bg-surface-1 border-b border-white/5 flex-shrink-0">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-sm" asChild>
                <Link to="/documents/media" data-testid="back-button">
                  <ArrowLeft className="h-4 w-4" />
                </Link>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Back to library</TooltipContent>
          </Tooltip>

          <Separator orientation="vertical" className="h-5" />

          <div className="flex-1 min-w-0">
            <h1
              data-testid="document-title"
              className="text-sm font-semibold text-zinc-100 truncate uppercase tracking-wider"
              style={{ fontFamily: "var(--font-display)", fontSize: "0.75rem" }}
            >
              {doc.title}
            </h1>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <Badge variant="secondary" className="text-[10px] uppercase">
              {doc.source}
            </Badge>
            {doc.metadata.extension && (
              <Badge variant="outline" className="text-[10px] uppercase">
                {doc.metadata.extension}
              </Badge>
            )}
            {effectiveDuration > 0 && (
              <span className="flex items-center gap-1 text-xs text-zinc-400 tabular-nums font-mono">
                <Clock className="h-3 w-3" />
                {formatTime(effectiveDuration)}
              </span>
            )}
            {speakers.length > 0 && (
              <span className="flex items-center gap-1 text-xs text-zinc-500">
                <Users className="h-3 w-3" />
                {speakers.length}
              </span>
            )}
            {transcription?.language && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="flex items-center gap-1 text-xs text-zinc-600">
                    <Globe className="h-3 w-3" />
                    {transcription.language.toUpperCase()}
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  Language: {transcription.language} (
                  {(transcription.language_probability * 100).toFixed(0)}% confidence)
                </TooltipContent>
              </Tooltip>
            )}
          </div>
        </header>

        {/* Highlight bar */}
        {highlightText && (
          <div
            data-testid="highlight-bar"
            className="flex items-center gap-2 px-4 py-1.5 bg-amber-950/30 border-b border-amber-900/30 flex-shrink-0"
          >
            <Highlighter className="h-3 w-3 text-amber-500" />
            <span className="text-xs text-amber-300">
              Highlighting: <strong>{highlightText}</strong>
            </span>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => setHighlightText(undefined)}
              className="h-5 w-5 ml-auto"
            >
              <X className="h-3 w-3 text-amber-500" />
            </Button>
          </div>
        )}

        {/* Highlight reel bar */}
        {isFilteredPlayback && selectedEntityText && (
          <div
            data-testid="highlight-reel-bar"
            className="flex items-center gap-2 px-4 py-1.5 bg-indigo-950/30 border-b border-indigo-900/30 flex-shrink-0"
          >
            <Play className="h-3 w-3 text-indigo-400 fill-indigo-400" />
            <span className="text-xs text-indigo-300">
              Highlight Reel: <strong>{selectedEntityText}</strong>
            </span>
            <Badge variant="secondary" className="text-[9px] px-1.5 py-0 h-4 tabular-nums">
              {filteredRanges.length} segment{filteredRanges.length !== 1 ? "s" : ""}
            </Badge>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => handleEntitySelect(null)}
              className="h-5 w-5 ml-auto"
            >
              <X className="h-3 w-3 text-indigo-400" />
            </Button>
          </div>
        )}

        {/* Main content area — 2-column top + bottom dock */}
        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
          {/* Top section: 2-column layout */}
          <div className="flex-1 flex min-h-0 overflow-hidden">
            {/* Left column: media player + timeline + speaker breakdown */}
            <div className="w-[45%] min-w-[400px] flex flex-col min-h-0 border-r border-white/5">
              {/* Media player */}
              <div className="flex-shrink-0 p-4 pb-2">
                <MediaPlayer
                  ref={playerRef}
                  document={doc}
                  markers={markers}
                  onMarkerClick={handleMarkerClick}
                  onTimeUpdate={handleTimeUpdate}
                  onDurationChange={handleDurationChange}
                  className="max-h-[50vh]"
                />
              </div>

              {/* Speaker timeline */}
              <div className="flex-shrink-0 px-4 pb-3">
                <SpeakerTimeline
                  segments={segments}
                  duration={effectiveDuration}
                  currentTime={currentTime}
                  speakers={speakers}
                  onSeek={handleSeek}
                  resolveSpeaker={resolveSpeaker}
                />
              </div>

              <Separator />

              {/* Speaker breakdown */}
              <div className="flex-1 min-h-0 overflow-y-auto p-4">
                <h3 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-3">
                  Speaker Breakdown
                </h3>
                <SpeakerBreakdown
                  segments={segments}
                  speakers={speakers}
                  duration={effectiveDuration}
                  speakerNames={speakerNames}
                />
              </div>
            </div>

            {/* Right column: transcript (full remaining width) */}
            <div className="flex-1 min-w-[300px] flex flex-col min-h-0">
              <div className="px-4 py-2.5 border-b border-white/5 flex items-center justify-between flex-shrink-0">
                <h2 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                  Transcript
                </h2>
                {segments.length > 0 && (
                  <span className="text-[10px] text-zinc-600">{segments.length} segments</span>
                )}
              </div>
              <Transcript
                segments={segments}
                activeSegmentIndex={activeSegmentIndex}
                activeWordIndex={activeWordIndex}
                onSeek={handleSeek}
                highlightText={highlightText}
                resolveSpeaker={resolveSpeaker}
                transcriptContainerRef={transcriptRef}
                scrollHighlightIndex={scrollHighlightIndex}
                className="flex-1 min-h-0"
                mentions={mentions}
                chunks={chunks}
                onEntityClick={handleEntityClick}
              />
            </div>
          </div>

          {/* Bottom dock: Entities / Assertions / Speakers tabbed panel */}
          <ResizablePanel defaultHeight={280} minHeight={120} maxHeight={600}>
            <Tabs defaultValue="entities" className="flex flex-col h-full">
              <TabsList className="flex-shrink-0 w-full rounded-none border-b border-white/5 bg-surface-1 h-9 px-1">
                <TabsTrigger
                  value="entities"
                  className="flex-1 text-xs data-[state=active]:bg-surface-2"
                >
                  Entities
                  {mentions.length > 0 && (
                    <Badge
                      variant="secondary"
                      className="ml-1.5 text-[9px] px-1 py-0 h-4 tabular-nums"
                    >
                      {mentions.length}
                    </Badge>
                  )}
                </TabsTrigger>
                <TabsTrigger
                  value="assertions"
                  className="flex-1 text-xs data-[state=active]:bg-surface-2"
                >
                  Assertions
                  {assertions.length > 0 && (
                    <Badge
                      variant="secondary"
                      className="ml-1.5 text-[9px] px-1 py-0 h-4 tabular-nums"
                    >
                      {assertions.length}
                    </Badge>
                  )}
                </TabsTrigger>
                <TabsTrigger
                  value="speakers"
                  className="flex-1 text-xs data-[state=active]:bg-surface-2"
                >
                  Speakers
                  {speakers.length > 0 && (
                    <Badge
                      variant="secondary"
                      className="ml-1.5 text-[9px] px-1 py-0 h-4 tabular-nums"
                    >
                      {speakers.length}
                    </Badge>
                  )}
                </TabsTrigger>
              </TabsList>

              <TabsContent value="entities" className="flex-1 min-h-0 overflow-hidden mt-0">
                <EntityPanel
                  mentions={mentions}
                  onEntityClick={handleEntityClick}
                  onEntitySelect={handleEntitySelect}
                  onMentionSeek={handleMentionSeek}
                  selectedEntityText={selectedEntityText}
                  getStatus={getStatus}
                  onApprove={handleMentionApprove}
                  onReject={handleMentionReject}
                  onEdit={handleMentionEdit}
                  onBulkApprove={bulkApprove}
                  onBulkReject={bulkReject}
                  className="h-full"
                />
              </TabsContent>

              <TabsContent value="assertions" className="flex-1 min-h-0 overflow-hidden mt-0">
                <AssertionPanel
                  assertions={assertions}
                  onAssertionSelect={handleAssertionSelect}
                  selectedAssertionId={selectedAssertionId}
                  onSeek={handleMentionSeek}
                  getStatus={getStatus}
                  onApprove={handleAssertionApprove}
                  onReject={handleAssertionReject}
                  onBulkApprove={bulkApprove}
                  onBulkReject={bulkReject}
                  className="h-full"
                />
              </TabsContent>

              <TabsContent value="speakers" className="flex-1 min-h-0 overflow-hidden mt-0 p-4">
                <SpeakerBreakdown
                  segments={segments}
                  speakers={speakers}
                  duration={effectiveDuration}
                  speakerNames={speakerNames}
                  onSpeakerNameChange={setSpeakerName}
                />
              </TabsContent>
            </Tabs>
          </ResizablePanel>
        </div>
      </div>
    </ErrorBoundary>
  );
}

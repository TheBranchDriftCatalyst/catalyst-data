import { useQuery, useQueries } from "@tanstack/react-query";
import {
  fetchDocument,
  fetchTranscription,
  fetchDiarization,
  fetchChunks,
  fetchMentions,
  fetchAssertions,
} from "@/api/client";

/**
 * Fetches all data for a single document in parallel:
 * document metadata, transcription, diarization, mentions, and assertions.
 */
export function useDocumentData(documentId: string | undefined) {
  const enabled = !!documentId;
  const id = documentId ?? "";

  const documentQuery = useQuery({
    queryKey: ["document", id],
    queryFn: () => fetchDocument(id),
    enabled,
    staleTime: 60_000,
  });

  const [transcriptionQuery, diarizationQuery, chunksQuery, mentionsQuery, assertionsQuery] =
    useQueries({
      queries: [
        {
          queryKey: ["transcription", id],
          queryFn: () => fetchTranscription(id),
          enabled,
          staleTime: 60_000,
        },
        {
          queryKey: ["diarization", id],
          queryFn: () => fetchDiarization(id),
          enabled,
          staleTime: 60_000,
        },
        {
          queryKey: ["chunks", id],
          queryFn: () => fetchChunks(id),
          enabled,
          staleTime: 60_000,
        },
        {
          queryKey: ["mentions", id],
          queryFn: () => fetchMentions(id),
          enabled,
          staleTime: 60_000,
        },
        {
          queryKey: ["assertions", id],
          queryFn: () => fetchAssertions(id),
          enabled,
          staleTime: 60_000,
        },
      ],
    });

  const isLoading =
    documentQuery.isLoading || transcriptionQuery.isLoading || diarizationQuery.isLoading;

  const isError = documentQuery.isError || transcriptionQuery.isError || diarizationQuery.isError;

  return {
    document: documentQuery.data,
    transcription: transcriptionQuery.data,
    diarization: diarizationQuery.data,
    chunks: chunksQuery.data ?? [],
    mentions: mentionsQuery.data ?? [],
    assertions: assertionsQuery.data ?? [],
    isLoading,
    isError,
    errors: [
      documentQuery.error,
      transcriptionQuery.error,
      diarizationQuery.error,
      chunksQuery.error,
      mentionsQuery.error,
      assertionsQuery.error,
    ].filter(Boolean),
  };
}

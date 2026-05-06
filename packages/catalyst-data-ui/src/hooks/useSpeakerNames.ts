import { useCallback, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchSpeakerNames, updateSpeakerName, type SpeakerMappings } from "@/api/client";

/**
 * Hook for reading and writing speaker display names.
 *
 * Returns a `nameMap` (speaker_label -> display_name) and a `setName` function
 * that persists the change to the backend and optimistically updates the cache.
 */
export function useSpeakerNames(documentId: string | undefined) {
  const enabled = !!documentId;
  const id = documentId ?? "";
  const queryClient = useQueryClient();
  const queryKey = ["speakerNames", id];

  const { data: mappings = {} } = useQuery<SpeakerMappings>({
    queryKey,
    queryFn: () => fetchSpeakerNames(id),
    enabled,
    staleTime: 60_000,
  });

  const mutation = useMutation({
    mutationFn: ({ label, displayName }: { label: string; displayName: string }) =>
      updateSpeakerName(id, label, displayName),
    onMutate: async ({ label, displayName }) => {
      // Optimistic update
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<SpeakerMappings>(queryKey);
      queryClient.setQueryData<SpeakerMappings>(queryKey, (old) => ({
        ...old,
        [label]: { display_name: displayName, color_index: old?.[label]?.color_index ?? null },
      }));
      return { previous };
    },
    onError: (_err, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(queryKey, context.previous);
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey });
    },
  });

  // Build a simple label -> display_name map for consumers
  const nameMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const [label, info] of Object.entries(mappings)) {
      if (info.display_name) {
        map[label] = info.display_name;
      }
    }
    return map;
  }, [mappings]);

  const setName = useCallback(
    (label: string, displayName: string) => {
      if (!documentId) return;
      mutation.mutate({ label, displayName });
    },
    [documentId, mutation],
  );

  /** Resolve a speaker label to its display name (or the label itself). */
  const resolve = useCallback(
    (label: string | undefined): string => {
      if (!label) return "Unknown";
      return nameMap[label] ?? label;
    },
    [nameMap],
  );

  return { nameMap, setName, resolve, isSaving: mutation.isPending };
}

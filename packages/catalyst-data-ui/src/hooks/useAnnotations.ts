import { useCallback, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchAnnotations,
  createAnnotation,
  updateAnnotation,
  bulkCreateAnnotations,
  type AnnotationCreatePayload,
} from "@/api/client";
import type { Annotation, AnnotationStatus } from "@/types/media";

/**
 * CRUD operations for human annotations on mentions and assertions.
 *
 * Mirrors the pattern in useSpeakerNames: optimistic updates via TanStack Query,
 * with rollback on error.
 */
export function useAnnotations(documentId: string | undefined) {
  const enabled = !!documentId;
  const id = documentId ?? "";
  const queryClient = useQueryClient();
  const queryKey = ["annotations", id];

  const { data: annotations = [] } = useQuery<Annotation[]>({
    queryKey,
    queryFn: () => fetchAnnotations(id),
    enabled,
    staleTime: 60_000,
  });

  // Build lookup maps: target_id -> latest annotation
  const annotationMap = useMemo(() => {
    const map = new Map<string, Annotation>();
    // annotations are ordered newest-first from API; keep the latest per target
    for (const a of annotations) {
      if (!map.has(a.target_id)) {
        map.set(a.target_id, a);
      }
    }
    return map;
  }, [annotations]);

  /** Get the review status for a target (mention or assertion). */
  const getStatus = useCallback(
    (targetId: string): AnnotationStatus => {
      const a = annotationMap.get(targetId);
      if (!a) return "pending";
      if (a.action === "approve") return "approved";
      if (a.action === "reject") return "rejected";
      return "pending";
    },
    [annotationMap],
  );

  /** Get the full annotation for a target, if it exists. */
  const getAnnotation = useCallback(
    (targetId: string): Annotation | undefined => annotationMap.get(targetId),
    [annotationMap],
  );

  // ── Mutations ─────────────────────────────────────────────────────────

  const createMutation = useMutation({
    mutationFn: (payload: AnnotationCreatePayload) => createAnnotation(id, payload),
    onMutate: async (payload) => {
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<Annotation[]>(queryKey);
      const optimistic: Annotation = {
        annotation_id: `temp-${Date.now()}`,
        document_id: id,
        target_type: payload.target_type,
        target_id: payload.target_id,
        action: payload.action,
        edits: payload.edits ?? {},
        reviewer: payload.reviewer ?? "",
        notes: payload.notes ?? "",
        created_at: new Date().toISOString(),
      };
      queryClient.setQueryData<Annotation[]>(queryKey, (old) => [optimistic, ...(old ?? [])]);
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

  const updateMutation = useMutation({
    mutationFn: ({
      annotationId,
      ...payload
    }: { annotationId: string } & Partial<
      Pick<AnnotationCreatePayload, "action" | "edits" | "reviewer" | "notes">
    >) => updateAnnotation(annotationId, payload),
    onMutate: async ({ annotationId, ...payload }) => {
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<Annotation[]>(queryKey);
      queryClient.setQueryData<Annotation[]>(queryKey, (old) =>
        (old ?? []).map((a) => (a.annotation_id === annotationId ? { ...a, ...payload } : a)),
      );
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

  const bulkMutation = useMutation({
    mutationFn: (payloads: AnnotationCreatePayload[]) => bulkCreateAnnotations(id, payloads),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey });
    },
  });

  // ── Convenience wrappers ──────────────────────────────────────────────

  const approve = useCallback(
    (targetType: "mention" | "assertion", targetId: string) => {
      if (!documentId) return;
      const existing = annotationMap.get(targetId);
      if (existing) {
        updateMutation.mutate({ annotationId: existing.annotation_id, action: "approve" });
      } else {
        createMutation.mutate({ target_type: targetType, target_id: targetId, action: "approve" });
      }
    },
    [documentId, annotationMap, createMutation, updateMutation],
  );

  const reject = useCallback(
    (targetType: "mention" | "assertion", targetId: string) => {
      if (!documentId) return;
      const existing = annotationMap.get(targetId);
      if (existing) {
        updateMutation.mutate({ annotationId: existing.annotation_id, action: "reject" });
      } else {
        createMutation.mutate({ target_type: targetType, target_id: targetId, action: "reject" });
      }
    },
    [documentId, annotationMap, createMutation, updateMutation],
  );

  const edit = useCallback(
    (targetType: "mention" | "assertion", targetId: string, edits: Record<string, unknown>) => {
      if (!documentId) return;
      const existing = annotationMap.get(targetId);
      if (existing) {
        updateMutation.mutate({ annotationId: existing.annotation_id, action: "edit", edits });
      } else {
        createMutation.mutate({
          target_type: targetType,
          target_id: targetId,
          action: "edit",
          edits,
        });
      }
    },
    [documentId, annotationMap, createMutation, updateMutation],
  );

  const bulkApprove = useCallback(
    (items: { targetType: "mention" | "assertion"; targetId: string }[]) => {
      if (!documentId) return;
      bulkMutation.mutate(
        items.map((i) => ({
          target_type: i.targetType,
          target_id: i.targetId,
          action: "approve" as const,
        })),
      );
    },
    [documentId, bulkMutation],
  );

  const bulkReject = useCallback(
    (items: { targetType: "mention" | "assertion"; targetId: string }[]) => {
      if (!documentId) return;
      bulkMutation.mutate(
        items.map((i) => ({
          target_type: i.targetType,
          target_id: i.targetId,
          action: "reject" as const,
        })),
      );
    },
    [documentId, bulkMutation],
  );

  // ── Count helpers ─────────────────────────────────────────────────────

  const counts = useCallback(
    (targetIds: string[]) => {
      let approved = 0;
      let rejected = 0;
      let pending = 0;
      for (const tid of targetIds) {
        const status = getStatus(tid);
        if (status === "approved") approved++;
        else if (status === "rejected") rejected++;
        else pending++;
      }
      return { approved, rejected, pending, total: targetIds.length };
    },
    [getStatus],
  );

  return {
    annotations,
    annotationMap,
    getStatus,
    getAnnotation,
    approve,
    reject,
    edit,
    bulkApprove,
    bulkReject,
    counts,
    isSaving: createMutation.isPending || updateMutation.isPending || bulkMutation.isPending,
  };
}

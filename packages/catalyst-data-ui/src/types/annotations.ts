export interface Annotation {
  annotation_id: string;
  document_id: string;
  target_type: "mention" | "assertion" | "segment" | "speaker";
  target_id: string;
  action: "approve" | "reject" | "edit" | "flag";
  edits?: Record<string, unknown>;
  reviewer?: string;
  notes?: string;
  created_at: string;
}

export interface SpeakerMapping {
  document_id: string;
  speaker_label: string;
  display_name: string;
  color_index?: number;
}

export type AnnotationAction = Annotation["action"];
export type AnnotationTargetType = Annotation["target_type"];

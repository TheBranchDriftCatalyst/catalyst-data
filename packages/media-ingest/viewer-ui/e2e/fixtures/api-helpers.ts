import type { APIRequestContext } from "@playwright/test";

/**
 * Direct REST helper for test setup/teardown.
 * Talks to the viewer API without going through the UI.
 */
export class APIHelper {
  constructor(
    private request: APIRequestContext,
    private baseURL: string = "http://media-explorer.talos00",
  ) {}

  private url(path: string): string {
    return `${this.baseURL}${path}`;
  }

  async getDocuments() {
    const resp = await this.request.get(this.url("/viewer/api/documents"));
    return resp.json();
  }

  async getDocument(docId: string) {
    const resp = await this.request.get(this.url(`/viewer/api/documents/${docId}`));
    return resp.json();
  }

  async getAnnotations(docId: string) {
    const resp = await this.request.get(this.url(`/viewer/api/documents/${docId}/annotations`));
    if (!resp.ok()) return [];
    return resp.json();
  }

  async deleteAnnotation(annotationId: string) {
    await this.request.delete(this.url(`/viewer/api/annotations/${annotationId}`));
  }

  async getSpeakerMappings(docId: string) {
    const resp = await this.request.get(this.url(`/viewer/api/documents/${docId}/speakers`));
    if (!resp.ok()) return {};
    return resp.json();
  }

  async updateSpeakerName(docId: string, label: string, displayName: string) {
    await this.request.put(this.url(`/viewer/api/documents/${docId}/speakers/${label}/name`), {
      data: { display_name: displayName },
    });
  }

  /**
   * Delete all annotations created during a test.
   * Pass the set of annotation IDs that existed before the test started.
   */
  async cleanupTestAnnotations(docId: string, preExistingIds: Set<string>) {
    const current = await this.getAnnotations(docId);
    for (const a of current) {
      if (!preExistingIds.has(a.annotation_id)) {
        await this.deleteAnnotation(a.annotation_id);
      }
    }
  }
}

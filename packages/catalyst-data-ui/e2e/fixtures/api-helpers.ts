import type { APIRequestContext } from "@playwright/test";

/**
 * Direct REST helper for test setup/teardown.
 * Talks to the viewer API without going through the UI.
 */
export class APIHelper {
  constructor(
    private request: APIRequestContext,
    // Default to the LOCAL viewer-ui dev server (vite, :5173). It proxies
    // `/viewer/api/*` → :8080 (FastAPI). Never fall back to a deployed
    // talos host: the SPA fallback returns HTML for unknown routes which
    // makes downstream JSON parsing silently fail.
    // ``localhost`` (not ``127.0.0.1``) because Vite's dev server binds
    // to ``localhost`` only by default — the IPv4 literal returns blank
    // pages. Env override accepts either via the env-guard allowlist.
    private baseURL: string = process.env.PLAYWRIGHT_BASE_URL ??
      process.env.VIEWER_URL ??
      "http://localhost:5173",
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

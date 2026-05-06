/**
 * Export events ndjson — fetch the full event stream for offline analysis.
 */

export async function downloadEventsNdjson(runId: string, docId: string): Promise<void> {
  if (!runId || !docId) {
    throw new Error("runId and docId are required");
  }

  // Fetch with max limit (50000) — the API enforces this ceiling
  const url = `/viewer/api/bench/runs/${encodeURIComponent(runId)}/events?doc_id=${encodeURIComponent(docId)}&limit=50000&format=jsonl`;

  try {
    const response = await fetch(url);

    if (!response.ok) {
      if (response.status === 404) {
        throw new Error("No events found for this run/doc combination");
      }
      throw new Error(`Failed to fetch events: ${response.status} ${response.statusText}`);
    }

    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json") && !contentType.includes("text/plain")) {
      throw new Error("Unexpected response content type");
    }

    const blob = await response.blob();

    if (blob.size === 0) {
      throw new Error("No events available for export");
    }

    // Create blob URL and trigger download
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = `${runId}__${docId}__events.ndjson`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    // Revoke after a short delay to allow browser to initiate download
    setTimeout(() => URL.revokeObjectURL(objectUrl), 500);
  } catch (err) {
    throw new Error(err instanceof Error ? err.message : "Failed to export events");
  }
}

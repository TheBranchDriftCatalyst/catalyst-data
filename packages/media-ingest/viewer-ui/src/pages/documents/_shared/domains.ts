/** Mapping from the user-friendly URL slug used in the React route
 *  (e.g. `/documents/congress-wtf`) to the backend's API slug used by the
 *  domain registry (e.g. `/viewer/api/congress/documents`).
 *
 *  These differ on purpose: route slugs are recognizable product names, API
 *  slugs match the code-location identifier. Keep this mapping tiny — when
 *  we eventually move each domain into its own package, this lookup goes
 *  with it. */
export const ROUTE_TO_API_SLUG: Record<string, string> = {
  "media-ingest": "media",
  "congress-wtf": "congress",
  "open-leaks": "leaks",
};

export function apiSlugForRoute(routeSlug: string): string | null {
  return ROUTE_TO_API_SLUG[routeSlug] ?? null;
}

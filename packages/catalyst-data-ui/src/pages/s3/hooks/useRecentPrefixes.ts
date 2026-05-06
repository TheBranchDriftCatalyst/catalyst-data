import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "s3-explorer:recent-prefixes";
const MAX_RECENT = 10;

function read(): string[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((p): p is string => typeof p === "string") : [];
  } catch {
    return [];
  }
}

/** Tracks the last `MAX_RECENT` distinct prefixes the user has visited.
 *
 *  Persists to localStorage. The empty prefix (bucket root) is excluded
 *  since "go to root" already has a dedicated affordance.
 */
export function useRecentPrefixes(currentPrefix: string) {
  const [recent, setRecent] = useState<string[]>(() => read());

  useEffect(() => {
    if (!currentPrefix) return;
    setRecent((prev) => {
      const next = [currentPrefix, ...prev.filter((p) => p !== currentPrefix)].slice(0, MAX_RECENT);
      try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        // ignore quota / private-mode failures
      }
      return next;
    });
  }, [currentPrefix]);

  const clear = useCallback(() => {
    setRecent([]);
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
  }, []);

  return { recent, clear };
}

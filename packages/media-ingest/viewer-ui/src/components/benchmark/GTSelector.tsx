import { useState, useEffect } from "react";

interface GTListEntry {
  name: string;
  label: string;
}

const KNOWN_GT_NAMES = [
  "active",
  "ensemble-4model",
  "ensemble-5model",
  "gpt-4o-single",
  "manually-reviewed",
];

export function GTSelector({
  selected,
  onChange,
}: {
  selected: string;
  onChange: (name: string) => void;
}) {
  const [entries, setEntries] = useState<GTListEntry[]>([]);

  useEffect(() => {
    Promise.all(
      KNOWN_GT_NAMES.map(async (name) => {
        try {
          const res = await fetch(`/viewer/ground-truth/${name}.json`, { method: "HEAD" });
          if (res.ok) {
            return {
              name,
              label: name.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
            };
          }
        } catch {
          /* skip */
        }
        return null;
      }),
    ).then((results) => {
      const available = results.filter((r): r is GTListEntry => r !== null);
      setEntries(available);
      if (available.length > 0 && !available.find((e) => e.name === selected)) {
        onChange(available[0]!.name);
      }
    });
  }, []);

  if (entries.length === 0) return null;

  return (
    <select
      value={selected}
      onChange={(e) => onChange(e.target.value)}
      aria-label="Select ground truth file"
      className="bg-surface-0 border border-white/10 rounded px-2 py-0.5 text-xs font-mono text-zinc-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
    >
      {entries.map((e) => (
        <option key={e.name} value={e.name}>
          {e.label}
        </option>
      ))}
    </select>
  );
}

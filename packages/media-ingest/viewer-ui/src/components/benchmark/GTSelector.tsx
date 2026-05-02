import { useState, useEffect } from "react";

interface GTListEntry {
  name: string;
  label: string;
}

export function GTSelector({
  selected,
  onChange,
}: {
  selected: string;
  onChange: (name: string) => void;
}) {
  const [entries, setEntries] = useState<GTListEntry[]>([]);

  useEffect(() => {
    // Hydrate from the bench API instead of probing a hardcoded list. The
    // server returns whatever ground-truth files actually exist in S3.
    (async () => {
      try {
        const res = await fetch("/viewer/api/bench/ground-truth");
        if (!res.ok) return;
        const body = (await res.json()) as { names: string[] };
        const available = body.names.map((name) => ({
          name,
          label: name.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
        }));
        setEntries(available);
        if (available.length > 0 && !available.find((e) => e.name === selected)) {
          onChange(available[0]!.name);
        }
      } catch {
        // bench API down — leave entries empty; the panel handles that.
      }
    })();
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

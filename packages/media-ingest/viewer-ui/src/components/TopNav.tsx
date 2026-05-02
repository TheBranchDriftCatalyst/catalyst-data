/**
 * Global top navigation bar.
 *
 * Lives at the page root above both the sidebar and the main content,
 * so cross-page navigation (Media, Entity Overrides, S3, Benchmarks,
 * State Inspector) is always reachable and isn't visually nested
 * inside the Media Explorer sidebar — which previously made it look
 * like those pages were sub-views of the media list.
 */

import { NavLink } from "react-router-dom";
import { Activity, ArrowRightLeft, BarChart3, Database, Tv2 } from "lucide-react";

interface NavItem {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Treat the link as active for any path that starts with `to`. */
  prefix?: boolean;
}

const ITEMS: NavItem[] = [
  { to: "/", label: "Media", icon: Tv2 },
  { to: "/overrides", label: "Entity Overrides", icon: ArrowRightLeft },
  { to: "/s3", label: "S3 Explorer", icon: Database },
  { to: "/benchmarks", label: "Benchmarks", icon: BarChart3, prefix: true },
  { to: "/benchmarks/state", label: "State Inspector", icon: Activity },
];

export function TopNav() {
  return (
    <header className="flex items-center gap-2 px-4 h-11 bg-surface-1 border-b border-white/5 flex-shrink-0">
      <div
        className="text-sm font-semibold text-cyan-400 flex items-center gap-2 mr-4"
        style={{ fontFamily: "var(--font-display)" }}
      >
        <Tv2 className="h-4 w-4" />
        catalyst-data
      </div>
      <nav className="flex items-center gap-0.5">
        {ITEMS.map(({ to, label, icon: Icon, prefix }) => (
          <NavLink
            key={to}
            to={to}
            end={!prefix}
            className={({ isActive }) =>
              `flex items-center gap-1.5 px-3 h-8 rounded text-xs font-mono transition-colors ${
                isActive
                  ? "bg-white/[0.06] text-cyan-300"
                  : "text-zinc-400 hover:text-zinc-200 hover:bg-white/[0.03]"
              }`
            }
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </NavLink>
        ))}
      </nav>
    </header>
  );
}

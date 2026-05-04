/**
 * Global top navigation bar.
 *
 * Lives at the page root above both the sidebar and the main content,
 * so cross-page navigation (Media, S3, Benchmarks, State Inspector)
 * is always reachable and isn't visually nested
 * inside the Media Explorer sidebar — which previously made it look
 * like those pages were sub-views of the media list.
 *
 * The right-side Services dropdown surfaces every other UI in the
 * dev-mode stack (Dagster, MinIO console, FastAPI docs, Tilt). Same
 * URLs work in prod-ops mode because Tiltfile.prod port-forwards
 * everything to localhost.
 */

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import {
  BarChart3,
  Boxes,
  ChevronDown,
  Cloud,
  Code2,
  Database,
  ExternalLink,
  FileText,
  HardDrive,
  Tv2,
  Workflow,
} from "lucide-react";
import { NavLink } from "react-router-dom";

interface NavItem {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /** Treat the link as active for any path that starts with `to`. */
  prefix?: boolean;
}

const ITEMS: NavItem[] = [
  { to: "/documents", label: "Documents", icon: FileText, prefix: true },
  { to: "/s3", label: "S3 Explorer", icon: Database },
  { to: "/benchmarks", label: "Benchmarks", icon: BarChart3, prefix: true },
  { to: "/benchmarks/state", label: "State Inspector", icon: Workflow },
];

interface ServiceLink {
  url: string;
  label: string;
  description: string;
  icon: React.ComponentType<{ className?: string }>;
}

// Service URLs assume Tilt-managed local ports (dev mode) or
// Tiltfile.prod's port-forwards (ops mode) — both resolve to localhost.
const SERVICES: ServiceLink[] = [
  {
    url: "http://localhost:3000",
    label: "Dagster",
    description: "Pipeline UI · code locations, runs, sensors",
    icon: Workflow,
  },
  {
    url: "http://localhost:9001",
    label: "MinIO Console",
    description: "Object storage UI · login: minio / minio123",
    icon: HardDrive,
  },
  {
    url: "http://localhost:8080/viewer/docs",
    label: "Viewer API Docs",
    description: "FastAPI Swagger · /viewer/api/* schema",
    icon: Code2,
  },
  {
    url: "http://localhost:10350",
    label: "Tilt",
    description: "Local resource dashboard · build + log streams",
    icon: Boxes,
  },
  {
    url: "http://localhost:9000",
    label: "MinIO S3 API",
    description: "Raw S3 endpoint (boto3, mc, aws cli)",
    icon: Cloud,
  },
];

function ServicesMenu() {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          className="flex items-center gap-1.5 px-3 h-8 rounded text-xs font-mono text-zinc-400 hover:text-zinc-200 hover:bg-white/[0.03] data-[state=open]:bg-white/[0.06] data-[state=open]:text-cyan-300 transition-colors"
        >
          <Boxes className="h-3.5 w-3.5" />
          Services
          <ChevronDown className="h-3 w-3 opacity-60" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          className="z-50 min-w-[280px] rounded-md border border-white/10 bg-surface-1 shadow-xl py-1 outline-none"
        >
          <div className="px-3 py-1.5 text-[10px] uppercase tracking-wider text-zinc-500 font-mono">
            Local services
          </div>
          {SERVICES.map(({ url, label, description, icon: Icon }) => (
            <DropdownMenu.Item key={url} asChild>
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-start gap-3 px-3 py-2 text-xs font-mono text-zinc-300 hover:bg-white/[0.04] hover:text-cyan-300 cursor-pointer outline-none data-[highlighted]:bg-white/[0.04] data-[highlighted]:text-cyan-300"
              >
                <Icon className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span>{label}</span>
                    <ExternalLink className="h-3 w-3 opacity-50" />
                  </div>
                  <div className="text-[10px] text-zinc-500 mt-0.5">{description}</div>
                </div>
              </a>
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

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
      <div className="flex-1" />
      <ServicesMenu />
    </header>
  );
}

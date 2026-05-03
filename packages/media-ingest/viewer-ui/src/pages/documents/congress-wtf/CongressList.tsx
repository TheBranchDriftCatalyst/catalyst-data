import { FileText, Construction } from "lucide-react";
import { Badge } from "@thebranchdriftcatalyst/catalyst-ui";

/** Placeholder list for the congress-wtf domain. Real list will land once
 *  the backend exposes `/viewer/api/congress/documents` (or once we
 *  parameterize `S3DataService` on code_location/group/asset). Until then
 *  the user sees an honest "not wired up" panel rather than a broken page. */
export default function CongressList() {
  return (
    <DomainPlaceholder
      domain="congress-wtf"
      icon={FileText}
      silverPath="silver/congress_data/congress/congress_documents/data.jsonl"
    />
  );
}

function DomainPlaceholder({
  domain,
  icon: Icon,
  silverPath,
}: {
  domain: string;
  icon: typeof Construction;
  silverPath: string;
}) {
  return (
    <div
      data-testid={`documents-${domain}-placeholder`}
      className="h-full flex items-center justify-center p-8"
    >
      <div className="max-w-md space-y-4 text-center">
        <div className="inline-flex p-3 rounded-full bg-amber-500/10 text-amber-400">
          <Icon className="h-8 w-8" />
        </div>
        <h2 className="text-lg font-semibold text-zinc-200">{domain} backend not wired up yet</h2>
        <p className="text-sm text-zinc-500 leading-relaxed">
          The data exists in S3 but no viewer API endpoint has been added. Browse it directly via
          the S3 Explorer in the meantime.
        </p>
        <div className="flex flex-col gap-2 items-center text-xs">
          <Badge variant="secondary" className="font-mono text-[10px]">
            {silverPath}
          </Badge>
          <a
            href={`/viewer/s3?p=${encodeURIComponent(silverPath.split("/").slice(0, -1).join("/") + "/")}`}
            className="text-cyan-400 hover:text-cyan-300 underline"
          >
            Open in S3 Explorer →
          </a>
        </div>
      </div>
    </div>
  );
}

/**
 * DeepLinkButton — small icon button that copies the current full-state URL to clipboard.
 *
 * Mounts in panel headers to enable easy sharing of specific State Inspector views.
 * Shows a transient checkmark (✓) after copy for ~1.2s before reverting to the link icon.
 */

import { useState, useEffect } from "react";
import { Link2, Check } from "lucide-react";

interface Props {
  /** Optional CSS class for styling. */
  className?: string;
  /** Optional custom function to get the URL. Defaults to window.location.href */
  getUrl?: () => string;
  /** Optional data-testid prefix for the button. testId format: `{testidPrefix}-deep-link-copy` */
  testidPrefix?: string;
  /** Optional data-panel attribute for uniqueness. */
  panelName?: string;
}

export function DeepLinkButton({
  className = "",
  getUrl = () => window.location.href,
  testidPrefix,
  panelName,
}: Props) {
  const [copied, setCopied] = useState(false);

  // Auto-reset the checkmark after 1.2s
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1200);
    return () => clearTimeout(timer);
  }, [copied]);

  const handleCopy = async () => {
    try {
      const url = getUrl();
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      // Clipboard API not available (e.g., not secure context).
      // Fallback to deprecated execCommand.
      try {
        const textarea = document.createElement("textarea");
        textarea.value = getUrl();
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
        setCopied(true);
      } catch {
        // Silent fail — don't break the UI if copy fails.
        console.warn("Failed to copy URL to clipboard");
      }
    }
  };

  const testId = testidPrefix ? `${testidPrefix}-deep-link-copy` : "deep-link-copy";

  return (
    <button
      type="button"
      onClick={handleCopy}
      data-testid={testId}
      data-panel={panelName}
      className={`flex-shrink-0 p-1 rounded transition-colors ${
        copied
          ? "text-emerald-400 bg-emerald-500/10"
          : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-900/30"
      } ${className}`}
      title={copied ? "Copied!" : "Copy deep link to clipboard"}
      aria-label="Copy deep link"
    >
      {copied ? (
        <Check className="w-4 h-4" strokeWidth={2.5} />
      ) : (
        <Link2 className="w-4 h-4" strokeWidth={1.5} />
      )}
    </button>
  );
}

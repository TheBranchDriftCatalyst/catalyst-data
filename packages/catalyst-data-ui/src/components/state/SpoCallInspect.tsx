/**
 * SpoCallInspect — collapsible prompt + raw-response inspection for a
 * single SPO LangGraph window invocation, plus a usage/cost summary
 * strip and a parse-error callout.
 *
 * Everything renders from the inline previews on
 * `chunk_extracted.details` (`prompt_preview`, `response_preview`,
 * `usage`, `cost_usd`, `parse_errors`). The full prompt + response
 * texts live in S3 and are only fetched on demand when the operator
 * clicks "expand →" — a 2KB head/tail preview is enough to debug
 * 90% of "did the model see what we think it saw" questions, and
 * lazy-loading keeps the inspector snappy across many windows.
 *
 * Local component state caches the fetched full text so re-collapsing
 * + re-expanding the same pane in the same selection doesn't re-hit
 * the API. Switching to a different chunk_id remounts the component
 * via React's key-on-prop natural reset, so the cache is per-window.
 */
import { useState } from "react";

interface ParseError {
  stage: string;
  message: string;
}

interface Usage {
  tokens_in?: number;
  tokens_out?: number;
  tokens_total?: number;
}

interface Props {
  details: {
    prompt_hash?: string;
    prompt_preview?: string;
    response_preview?: string;
    usage?: Usage;
    cost_usd?: number | null;
    parse_errors?: ParseError[];
  };
  runId: string;
  chunkId: string;
}

type FetchState =
  | { kind: "preview" }
  | { kind: "loading" }
  | { kind: "loaded"; text: string }
  | { kind: "missing" }
  | { kind: "error"; message: string };

function formatCost(cost: number | null | undefined): string {
  if (cost == null) return "—";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(3)}`;
}

function formatTokens(n: number | undefined): string {
  if (n == null) return "?";
  return String(n);
}

export function SpoCallInspect({ details, runId, chunkId }: Props) {
  const promptHash = details.prompt_hash;
  const [promptState, setPromptState] = useState<FetchState>({ kind: "preview" });
  const [responseState, setResponseState] = useState<FetchState>({ kind: "preview" });
  // Empty / missing-state guard: the event predates the Gap #5 backend
  // (or wasn't produced by the SPO call path at all) — render nothing.
  if (!promptHash) return null;

  const usage = details.usage ?? {};
  const tokensIn = usage.tokens_in;
  const tokensOut = usage.tokens_out;
  const tokensTotal = usage.tokens_total;
  const cost = details.cost_usd ?? null;

  const promptPreview = details.prompt_preview ?? "";
  const responsePreview = details.response_preview ?? "";
  const parseErrors = details.parse_errors ?? [];

  async function expandPrompt() {
    if (promptState.kind === "loaded" || promptState.kind === "loading") return;
    setPromptState({ kind: "loading" });
    try {
      const res = await fetch(`/viewer/api/bench/prompts/${encodeURIComponent(promptHash!)}`);
      if (res.status === 404) {
        setPromptState({ kind: "missing" });
        return;
      }
      if (!res.ok) {
        setPromptState({ kind: "error", message: `HTTP ${res.status}` });
        return;
      }
      const text = await res.text();
      setPromptState({ kind: "loaded", text });
    } catch (e) {
      setPromptState({
        kind: "error",
        message: e instanceof Error ? e.message : String(e),
      });
    }
  }

  async function expandResponse() {
    if (responseState.kind === "loaded" || responseState.kind === "loading") return;
    setResponseState({ kind: "loading" });
    try {
      const res = await fetch(
        `/viewer/api/bench/runs/${encodeURIComponent(runId)}/responses/${encodeURIComponent(chunkId)}`,
      );
      if (res.status === 404) {
        setResponseState({ kind: "missing" });
        return;
      }
      if (!res.ok) {
        setResponseState({ kind: "error", message: `HTTP ${res.status}` });
        return;
      }
      const text = await res.text();
      setResponseState({ kind: "loaded", text });
    } catch (e) {
      setResponseState({
        kind: "error",
        message: e instanceof Error ? e.message : String(e),
      });
    }
  }

  const hashChip = promptHash.slice(0, 8);

  // Body resolution priority: full-loaded text > preview. Missing/error
  // don't replace the preview — they append a small note below.
  const promptBody = promptState.kind === "loaded" ? promptState.text : promptPreview;
  const responseBody = responseState.kind === "loaded" ? responseState.text : responsePreview;

  return (
    <div className="space-y-2">
      {parseErrors.length > 0 && (
        <div
          data-testid="spo-parse-errors"
          className="rounded border border-red-500/40 bg-red-500/10 px-2 py-1.5 space-y-1 text-[10px] font-mono"
        >
          <div className="text-red-300 uppercase tracking-wide">
            parse errors ({parseErrors.length})
          </div>
          {parseErrors.map((e, i) => (
            <div key={i} data-testid="spo-parse-error-row" className="text-red-200">
              <span className="text-red-400">{e.stage}</span>
              <span className="text-zinc-500">: </span>
              <span>{e.message}</span>
            </div>
          ))}
        </div>
      )}

      <details data-testid="spo-prompt-pane">
        <summary className="text-[10px] uppercase text-zinc-600 tracking-wide cursor-pointer flex items-center gap-2">
          <span>prompt · {formatTokens(tokensIn)} tokens</span>
          <span
            data-testid="spo-prompt-hash"
            className="px-1 py-0.5 rounded bg-zinc-700/40 text-zinc-400 font-mono normal-case"
            title={promptHash}
          >
            {hashChip}
          </span>
        </summary>
        <div data-testid="spo-prompt-body" className="mt-1 space-y-1">
          <pre className="text-[10px] font-mono text-zinc-300 whitespace-pre-wrap break-words bg-surface-0 border border-white/5 rounded p-2 max-h-72 overflow-y-auto">
            {promptBody}
          </pre>
          {promptState.kind === "missing" && (
            <div className="text-[10px] text-amber-400">(full prompt not archived in S3)</div>
          )}
          {promptState.kind === "error" && (
            <div className="text-[10px] text-red-400">fetch error: {promptState.message}</div>
          )}
          {promptState.kind !== "loaded" && (
            <button
              data-testid="spo-prompt-expand"
              onClick={expandPrompt}
              disabled={promptState.kind === "loading"}
              className="text-[10px] text-cyan-300 hover:text-cyan-200 disabled:text-zinc-600"
            >
              {promptState.kind === "loading" ? "loading…" : "expand →"}
            </button>
          )}
          {promptState.kind === "loaded" && (
            <div className="text-[10px] text-zinc-500">
              full prompt loaded ({promptState.text.length.toLocaleString()} chars)
            </div>
          )}
        </div>
      </details>

      <details data-testid="spo-response-pane">
        <summary className="text-[10px] uppercase text-zinc-600 tracking-wide cursor-pointer">
          raw response · {formatTokens(tokensOut)} tokens
        </summary>
        <div data-testid="spo-response-body" className="mt-1 space-y-1">
          <pre className="text-[10px] font-mono text-zinc-300 whitespace-pre-wrap break-words bg-surface-0 border border-white/5 rounded p-2 max-h-72 overflow-y-auto">
            {responseBody}
          </pre>
          {responseState.kind === "missing" && (
            <div className="text-[10px] text-amber-400">(full response not archived in S3)</div>
          )}
          {responseState.kind === "error" && (
            <div className="text-[10px] text-red-400">fetch error: {responseState.message}</div>
          )}
          {responseState.kind !== "loaded" && (
            <button
              data-testid="spo-response-expand"
              onClick={expandResponse}
              disabled={responseState.kind === "loading"}
              className="text-[10px] text-cyan-300 hover:text-cyan-200 disabled:text-zinc-600"
            >
              {responseState.kind === "loading" ? "loading…" : "expand →"}
            </button>
          )}
          {responseState.kind === "loaded" && (
            <div className="text-[10px] text-zinc-500">
              full response loaded ({responseState.text.length.toLocaleString()} chars)
            </div>
          )}
        </div>
      </details>

      <div
        data-testid="spo-usage-strip"
        className="text-[10px] font-mono text-zinc-400 flex flex-wrap gap-2"
      >
        <span>usage:</span>
        <span data-testid="spo-usage-in" className="text-cyan-300">
          {formatTokens(tokensIn)} in
        </span>
        <span className="text-zinc-600">·</span>
        <span data-testid="spo-usage-out" className="text-violet-300">
          {formatTokens(tokensOut)} out
        </span>
        <span className="text-zinc-600">·</span>
        <span data-testid="spo-usage-total" className="text-zinc-300">
          {formatTokens(tokensTotal)} total
        </span>
        <span className="text-zinc-600">·</span>
        <span
          data-testid="spo-usage-cost"
          className={cost == null ? "text-zinc-600" : "text-emerald-300"}
        >
          {formatCost(cost)}
        </span>
      </div>
    </div>
  );
}

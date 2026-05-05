/**
 * BenchmarkRunner — interactive bench-launcher tab.
 *
 * Layout (3 panes):
 *
 *   ┌─────────────┬───────────────────────────┬──────────────────┐
 *   │ Saved       │ Config form               │ Active runs      │
 *   │ configs     │  (ensemble · spo_models · │  (running + last │
 *   │ list        │   ner-quorum · phase     │   N completed)   │
 *   │             │   flags · run button)    │  + log tail      │
 *   └─────────────┴───────────────────────────┴──────────────────┘
 *
 * Backend: ``/viewer/api/bench/runner/*`` (see routes/bench_runner.py).
 *  - GET /models — populate dropdowns from tests/benchmark_config.py
 *  - GET/POST/DELETE /configs — saved configs CRUD
 *  - POST /run, GET /runs[/:id], /runs/:id/log, POST /runs/:id/stop
 *
 * MVP scope: single-config run, no queueing (capped 1 active run by the
 * backend), polled status (no SSE). Tail log refreshes every 2 s while
 * the run is alive.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Loader2, Play, Save, Trash2, Square, FileText, Plus } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@thebranchdriftcatalyst/catalyst-ui";

// Shared className for every TooltipContent on this page — the upstream
// catalyst-ui TooltipContent ships without a solid fill, so without this
// the tooltip text floats over the page (see-through / unreadable).
// Solid surface-1 fill + border + shadow + bumped z-index so tooltips
// always sit on top of inputs and the runs panel.
const TOOLTIP_CLS =
  "z-50 max-w-sm rounded-md border border-white/10 bg-surface-1 text-zinc-100 px-3 py-2 shadow-xl text-[11px] leading-relaxed font-mono whitespace-pre-line";

// ─────────────────────────────────────────────────────────────────────────────
// localStorage-backed state — DRY wrapper so every input/toggle on this page
// persists across reloads. Lives at the top of the file because both the
// page component and a few helper components reach for it.
// ─────────────────────────────────────────────────────────────────────────────

function useLocalStorageState<T>(key: string, initial: T): [T, (v: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") return initial;
    try {
      const raw = window.localStorage.getItem(key);
      if (raw == null) return initial;
      return JSON.parse(raw) as T;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* quota exceeded / private mode — silently no-op */
    }
  }, [key, value]);
  return [value, setValue];
}

// ─────────────────────────────────────────────────────────────────────────────
// Quorum presets — click to populate the ner_quorum input. Order roughly
// from "most-conservative" → "most-permissive" so operators can scan the
// strip top-to-bottom for the level of consensus they want.
// ─────────────────────────────────────────────────────────────────────────────

interface Preset {
  label: string;
  expr: string;
  hint: string;
  needsN?: number; // expression assumes a specific panel size
}

const QUORUM_PRESETS: Preset[] = [
  {
    label: "unanimous (3)",
    expr: "a + b + c >= 3",
    hint: "all three encoders must agree — most conservative; minimises false positives",
    needsN: 3,
  },
  {
    label: "majority (3)",
    expr: "a + b + c >= 2",
    hint: "default rule — at least 2 of 3 encoders must agree",
    needsN: 3,
  },
  {
    label: "majority (5)",
    expr: "a + b + c + d + e >= 3",
    hint: "majority for a 5-encoder panel",
    needsN: 5,
  },
  {
    label: "any (3)",
    expr: "a + b + c >= 1",
    hint: "single-source — accepts whenever any encoder votes (warns: skips redundancy)",
    needsN: 3,
  },
  {
    label: "weighted gliner",
    expr: "2*a + b + c >= 3",
    hint: "encoder 'a' counts double — useful when one encoder is far more accurate",
    needsN: 3,
  },
  {
    label: "veto",
    expr: "a + b - c >= 1",
    hint: "encoder 'c' subtracts — treats it as a noisy/false-positive-prone veto",
    needsN: 3,
  },
  {
    label: "logical AND/OR",
    expr: "a & (b | c)",
    hint: "encoder 'a' is mandatory; b OR c provides corroboration",
    needsN: 3,
  },
  {
    label: "coverage groups",
    expr: "min(a + b, c + d) >= 1",
    hint: "at least one from {a,b} AND at least one from {c,d} — useful with mixed-tier panels",
    needsN: 4,
  },
];

interface BenchConfig {
  id: string;
  name: string;
  description: string;
  ensemble: string[];
  spo_models: string[];
  ner_quorum: string;
  all_videos: boolean;
  full: boolean;
  ensemble_only: boolean;
  spo_only: boolean;
  no_consensus: boolean;
  regen: boolean;
  sample_per_domain: number | null;
  extra_args: string[];
  env_overrides: Record<string, string>;
  created_at: number;
  updated_at: number;
}

/**
 * Curated env vars surfaced in the form. Anything else can be added via the
 * "+ add var" button at the bottom of the env-overrides section.
 *
 *  - ``sensitive`` flips the input to ``type=password`` so secrets aren't
 *    shoulder-surfaced. Values still go to localStorage like every other
 *    field — this is dev-mode UX, not a vault.
 *  - ``placeholder`` doubles as a "what should this look like" hint.
 *  - ``help`` populates the field's tooltip.
 */
interface EnvVarSpec {
  key: string;
  label: string;
  placeholder: string;
  help: string;
  sensitive?: boolean;
}

const COMMON_ENV_VARS: EnvVarSpec[] = [
  {
    key: "LLM_API_KEY",
    label: "LLM_API_KEY",
    placeholder: "sk-… or empty for inherited",
    help: "API key for the LLM proxy / SPO models. Sent to the harness as LLM_API_KEY (also accepted as OPENAI_API_KEY). Leave blank to inherit from the viewer-api process env.",
    sensitive: true,
  },
  {
    key: "LLM_BASE_URL",
    label: "LLM_BASE_URL",
    placeholder: "http://localhost:4000  (LiteLLM proxy)",
    help: "Base URL for the LLM provider. Set to your LiteLLM proxy when running locally; leave blank for the cloud OpenAI default.",
  },
  {
    key: "LLM_MODEL_NAME",
    label: "LLM_MODEL_NAME",
    placeholder: "gpt-4o-mini",
    help: "Default LLM model name when an asset doesn't override it. Most bench runs ignore this in favour of --spo-models.",
  },
  {
    key: "EMBEDDING_PROVIDER",
    label: "EMBEDDING_PROVIDER",
    placeholder: "openai · huggingface · ollama-mac",
    help: "Provider for chunk + cluster embeddings. 'openai' calls the LiteLLM proxy; 'huggingface' loads a sentence-transformers model locally; 'ollama-mac' uses ollama.",
  },
  {
    key: "EMBEDDING_MODEL",
    label: "EMBEDDING_MODEL",
    placeholder: "text-embedding-3-small",
    help: "Model name for embeddings. Pair with EMBEDDING_PROVIDER — e.g. 'sentence-transformers/all-mpnet-base-v2' for huggingface, 'nomic-embed-text' for ollama-mac.",
  },
  {
    key: "CONGRESS_API_KEY",
    label: "CONGRESS_API_KEY",
    placeholder: "leave blank to skip congress seeding",
    help: "api.congress.gov key. Required for the congress domain branch of the seed and for any bench run that touches congress chunks.",
    sensitive: true,
  },
  {
    key: "DAGSTER_S3_ENDPOINT_URL",
    label: "DAGSTER_S3_ENDPOINT_URL",
    placeholder: "http://localhost:9000",
    help: "MinIO / S3 endpoint the harness reads chunks + writes bench artefacts to. Defaults to localhost:9000 (Tilt-managed MinIO).",
  },
];

interface ModelEntry {
  name: string;
  model: string;
  tags: string[];
}

interface ModelsRegistry {
  ENCODER_MODELS: ModelEntry[];
  EXTRACTION_MODELS: ModelEntry[];
  CLOUD_MODELS: ModelEntry[];
  LLM_MODELS: ModelEntry[];
}

interface RunHandle {
  run_id: string;
  pid: number;
  started_at: number;
  status: "running" | "ok" | "error";
  return_code: number | null;
  config: Partial<BenchConfig>;
  log_path: string;
}

const EMPTY_CONFIG: BenchConfig = {
  id: "",
  name: "",
  description: "",
  ensemble: [],
  spo_models: [],
  ner_quorum: "",
  all_videos: false,
  full: false,
  ensemble_only: false,
  spo_only: false,
  no_consensus: false,
  regen: false,
  sample_per_domain: null,
  extra_args: [],
  env_overrides: {},
  created_at: 0,
  updated_at: 0,
};

const API = "/viewer/api/bench/runner";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`HTTP ${r.status}: ${txt}`);
  }
  return (await r.json()) as T;
}

type LoadStage = "pending" | "ok" | "error";

export function BenchmarkRunner() {
  const [configs, setConfigs] = useState<BenchConfig[]>([]);
  const [models, setModels] = useState<ModelsRegistry | null>(null);
  // ``draft`` is the form's working copy. localStorage-backed so partially
  // filled configs survive a refresh (saving a config is a separate step
  // that writes the named copy to ``.test-output/bench-configs/``).
  const [draft, setDraft] = useLocalStorageState<BenchConfig>("bench-runner.draft", EMPTY_CONFIG);
  const [runs, setRuns] = useState<RunHandle[]>([]);
  const [activeRunId, setActiveRunId] = useLocalStorageState<string | null>(
    "bench-runner.active-run",
    null,
  );
  const [logText, setLogText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadStart] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  const [stages, setStages] = useState<{
    configs: LoadStage;
    models: LoadStage;
    runs: LoadStage;
  }>({ configs: "pending", models: "pending", runs: "pending" });

  // Initial load: fire each fetch independently so the UI shows progress
  // per stage instead of blocking on Promise.all (models registry is the
  // slow one — it imports tests/benchmark_config.py + boto3).
  useEffect(() => {
    api<BenchConfig[]>("/configs")
      .then((cfgs) => {
        setConfigs(cfgs);
        setStages((s) => ({ ...s, configs: "ok" }));
      })
      .catch((e) => {
        setError(String(e));
        setStages((s) => ({ ...s, configs: "error" }));
      });

    api<ModelsRegistry>("/models")
      .then((mdls) => {
        setModels(mdls);
        setStages((s) => ({ ...s, models: "ok" }));
      })
      .catch((e) => {
        setError(String(e));
        setStages((s) => ({ ...s, models: "error" }));
      });

    api<RunHandle[]>("/runs")
      .then((rs) => {
        setRuns(rs);
        const live = rs.find((r) => r.status === "running");
        if (live) setActiveRunId(live.run_id);
        setStages((s) => ({ ...s, runs: "ok" }));
      })
      .catch((e) => {
        setError(String(e));
        setStages((s) => ({ ...s, runs: "error" }));
      });
  }, []);

  // Tick a clock while the loading screen is up so the elapsed counter
  // updates. Stops once models has resolved (the gating fetch).
  useEffect(() => {
    if (models) return;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [models]);

  // Poll runs + log for the active run while it's running.
  const logRef = useRef<HTMLPreElement | null>(null);
  useEffect(() => {
    if (!activeRunId) return;
    let cancelled = false;

    async function tick() {
      try {
        const [rs, logResp] = await Promise.all([
          api<RunHandle[]>("/runs"),
          api<{ log: string }>(`/runs/${activeRunId}/log`),
        ]);
        if (cancelled) return;
        setRuns(rs);
        setLogText(logResp.log);
        // Auto-scroll the log pane to the bottom on every update.
        if (logRef.current) {
          logRef.current.scrollTop = logRef.current.scrollHeight;
        }
        const me = rs.find((r) => r.run_id === activeRunId);
        if (me && me.status !== "running") return; // stop polling once done
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
      if (!cancelled) setTimeout(tick, 2000);
    }
    void tick();
    return () => {
      cancelled = true;
    };
  }, [activeRunId]);

  const onSelectConfig = useCallback((c: BenchConfig) => {
    // Older saved configs predate ``env_overrides`` — coerce to {} so the
    // editor doesn't blow up on undefined.
    setDraft({ ...c, env_overrides: c.env_overrides ?? {} });
  }, []);

  const onNewConfig = useCallback(() => {
    setDraft({ ...EMPTY_CONFIG });
  }, []);

  // Likewise normalise the localStorage-hydrated draft once on mount so a
  // pre-env_overrides snapshot doesn't leak `undefined` into the form.
  useEffect(() => {
    if (draft.env_overrides == null) {
      setDraft({ ...draft, env_overrides: {} });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onSave = useCallback(async () => {
    if (!draft.name.trim()) {
      setError("config name is required");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = await api<BenchConfig>("/configs", {
        method: "POST",
        body: JSON.stringify(draft),
      });
      setDraft(saved);
      setConfigs((prev) => {
        const without = prev.filter((c) => c.id !== saved.id);
        return [saved, ...without];
      });
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }, [draft]);

  const onDelete = useCallback(async (id: string) => {
    if (!window.confirm("delete this config?")) return;
    try {
      await api(`/configs/${id}`, { method: "DELETE" });
      setConfigs((prev) => prev.filter((c) => c.id !== id));
    } catch (e) {
      setError(String(e));
    }
  }, []);

  const onRun = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const handle = await api<RunHandle>("/run", {
        method: "POST",
        body: JSON.stringify(draft),
      });
      setActiveRunId(handle.run_id);
      setRuns((prev) => [handle, ...prev]);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }, [draft]);

  const onStop = useCallback(async (runId: string) => {
    try {
      await api(`/runs/${runId}/stop`, { method: "POST" });
    } catch (e) {
      setError(String(e));
    }
  }, []);

  const activeRun = useMemo(
    () => runs.find((r) => r.run_id === activeRunId) ?? null,
    [runs, activeRunId],
  );

  if (!models) {
    const elapsed = ((now - loadStart) / 1000).toFixed(1);
    return (
      <div className="h-full flex items-center justify-center font-mono text-[11px] text-zinc-400">
        <div className="w-[360px] space-y-3">
          <div className="flex items-center justify-between border-b border-white/5 pb-2">
            <span className="text-zinc-300">loading bench runner</span>
            <span className="text-zinc-600">{elapsed}s</span>
          </div>
          <LoadRow
            label="saved configs"
            stage={stages.configs}
            hint="/viewer/api/bench/runner/configs"
          />
          <LoadRow
            label="model registry"
            stage={stages.models}
            hint="imports benchmark_config.py + boto3 — first hit takes ~3-10s"
            slow
          />
          <LoadRow label="active runs" stage={stages.runs} hint="/viewer/api/bench/runner/runs" />
          {error && (
            <div className="rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-red-300 whitespace-pre-wrap text-[10px]">
              {error}
            </div>
          )}
        </div>
      </div>
    );
  }

  const encoderOptions = [...models.ENCODER_MODELS, ...models.EXTRACTION_MODELS];
  const spoOptions = [...models.LLM_MODELS, ...models.CLOUD_MODELS];

  return (
    <div className="flex h-full">
      {/* ─── Saved configs ───────────────────────────────────────── */}
      <aside className="w-56 flex-shrink-0 border-r border-white/10 overflow-y-auto">
        <div className="px-3 py-2 border-b border-white/5 flex items-center justify-between font-mono text-[10px] text-zinc-400">
          <span>saved configs</span>
          <button
            type="button"
            onClick={onNewConfig}
            className="p-1 hover:bg-white/5 rounded text-zinc-500 hover:text-zinc-200"
            title="new config"
          >
            <Plus className="h-3 w-3" />
          </button>
        </div>
        <div className="py-1">
          {configs.length === 0 && (
            <div className="px-3 py-2 font-mono text-[10px] text-zinc-600">
              no saved configs yet — fill the form and save.
            </div>
          )}
          {configs.map((c) => (
            <button
              type="button"
              key={c.id}
              onClick={() => onSelectConfig(c)}
              className={`w-full text-left px-3 py-1.5 font-mono text-[11px] flex items-center gap-2 group hover:bg-white/[0.03] ${
                draft.id === c.id ? "bg-cyan-500/10 text-cyan-200" : "text-zinc-300"
              }`}
            >
              <span className="flex-1 truncate">{c.name || "untitled"}</span>
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation();
                  void onDelete(c.id);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    void onDelete(c.id);
                  }
                }}
                className="opacity-0 group-hover:opacity-100 text-zinc-500 hover:text-red-400 cursor-pointer"
                title="delete"
              >
                <Trash2 className="h-3 w-3" />
              </span>
            </button>
          ))}
        </div>
      </aside>

      {/* ─── Form ────────────────────────────────────────────────── */}
      <section className="flex-1 min-w-0 overflow-y-auto p-4 space-y-4 font-mono text-[11px]">
        {error && (
          <div className="rounded border border-red-500/30 bg-red-500/10 px-3 py-2 text-red-300 whitespace-pre-wrap">
            {error}
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Field
            label="name"
            help="Display name for the saved config. Required when saving (the run itself doesn't need a name)."
          >
            <input
              type="text"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="e.g. majority-of-3 · all videos"
              className={inputCls}
            />
          </Field>
          <Field
            label="description"
            help="Free-form note for what this config is for. Useful when you want to remember why you saved it."
          >
            <input
              type="text"
              value={draft.description}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              placeholder="optional"
              className={inputCls}
            />
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field
            label="ensemble (NER encoder panel)"
            help={
              "Which encoder models vote in NER consensus.\n\n" +
              "Order matters — the first encoder is letter 'a' in the ner_quorum expression, " +
              "second is 'b', etc.\n\n" +
              "CLI: --ensemble <comma-separated names>"
            }
          >
            <MultiSelect
              options={encoderOptions}
              selected={draft.ensemble}
              onChange={(v) => setDraft({ ...draft, ensemble: v })}
            />
          </Field>
          <Field
            label="spo_models (LLMs that consume consensus)"
            help={
              "LLMs that run SPO (subject-predicate-object) extraction over the consensus mentions in Phase 4.\n\n" +
              "Each model produces its own SPO output — the bench compares them side by side.\n\n" +
              "CLI: --spo-models <comma-separated names>"
            }
          >
            <MultiSelect
              options={spoOptions}
              selected={draft.spo_models}
              onChange={(v) => setDraft({ ...draft, spo_models: v })}
            />
          </Field>
        </div>

        <Field
          label="ner_quorum (consensus expression)"
          help={
            "Boolean/arithmetic expression over the encoder vote vector.\n\n" +
            "Variables: letters a,b,c,… map to ensemble[0,1,2,…]. Slugs like " +
            "'gliner_large' work too.\n\n" +
            "Operators: + - * / & (AND) | (OR) ! (NOT) min() max() and >= > == != < <=\n\n" +
            "Bare integer K is shorthand for 'sum(all encoders) >= K'.\n\n" +
            "Misconfigurations (unreachable, trivial, accepts-zero-votes) abort the run with a diagnostic banner. " +
            "Single-source / mandatory-encoder are warnings, not errors. " +
            "See libs/catalyst-exgraph/CONSENSUS_PREDICATES.md for the full grammar."
          }
        >
          <input
            type="text"
            value={draft.ner_quorum}
            onChange={(e) => setDraft({ ...draft, ner_quorum: e.target.value })}
            placeholder="e.g. a + b + c >= 2  or  2*a + b + c >= 3  or  '2'"
            className={inputCls}
          />
          {/* Preset strip — click to populate the input. Hover each chip
           *  for a one-line description; the preset list lives at the
           *  module top so it's easy to extend. */}
          <div className="flex flex-wrap gap-1.5 mt-2">
            {QUORUM_PRESETS.map((p) => {
              const ensembleN = draft.ensemble.length;
              const mismatch = p.needsN != null && ensembleN > 0 && ensembleN !== p.needsN;
              return (
                <Tooltip key={p.label}>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={() => setDraft({ ...draft, ner_quorum: p.expr })}
                      className={`px-2 py-0.5 rounded text-[10px] font-mono border transition-colors ${
                        mismatch
                          ? "bg-amber-500/5 border-amber-500/20 text-amber-300/70 hover:text-amber-200"
                          : "bg-zinc-800/40 border-white/10 text-zinc-300 hover:bg-cyan-500/10 hover:border-cyan-500/40 hover:text-cyan-200"
                      }`}
                    >
                      {p.label}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom" sideOffset={4} className={TOOLTIP_CLS}>
                    {`${p.expr}\n\n${p.hint}${
                      p.needsN
                        ? `\n\nDesigned for an ${p.needsN}-encoder panel.${
                            mismatch
                              ? ` Currently ${ensembleN} selected — adjust ensemble or expression.`
                              : ""
                          }`
                        : ""
                    }`}
                  </TooltipContent>
                </Tooltip>
              );
            })}
          </div>
        </Field>

        <div className="grid grid-cols-3 gap-3">
          <Toggle
            label="--full"
            value={draft.full}
            onChange={(v) => setDraft({ ...draft, full: v })}
            help={
              "Run the full methodology: Phase 1 NER ensemble → Phase 2 consensus + clustering → Phase 4 SPO. " +
              "Off by default — without --full the harness drops into interactive mode."
            }
          />
          <Toggle
            label="--all-videos"
            value={draft.all_videos}
            onChange={(v) => setDraft({ ...draft, all_videos: v })}
            help={
              "Sweep every video listed in audio_manifest.yaml instead of only demo_video.\n\n" +
              "If any media_chunks are missing in S3, the harness auto-runs the seed (and " +
              "tells you to run `task bench:fixtures:regen` if media_segment_merge is also missing)."
            }
          />
          <Toggle
            label="--regen"
            value={draft.regen}
            onChange={(v) => setDraft({ ...draft, regen: v })}
            help={
              "Bypass the cluster cache (Phase A warm hits) and force a fresh ensemble run for each doc. " +
              "Use after changing encoder code/prompts; cache is keyed on doc text + model + params."
            }
          />
          <Toggle
            label="--ensemble-only"
            value={draft.ensemble_only}
            onChange={(v) =>
              setDraft({ ...draft, ensemble_only: v, spo_only: false, no_consensus: false })
            }
            help={
              "Skip Phase 4 SPO entirely. Run Phase 1 NER consensus only — produces per-encoder + " +
              "ensemble fixtures. Useful when you only care about NER F1, not SPO."
            }
          />
          <Toggle
            label="--spo-only"
            value={draft.spo_only}
            onChange={(v) =>
              setDraft({ ...draft, spo_only: v, ensemble_only: false, no_consensus: false })
            }
            help={
              "Skip Phase 1+2. Load cached consensus from a previous run's ClusterCache and run " +
              "Phase 4 SPO only. Useful when iterating on SPO prompts without re-running NER. " +
              "Requires a saved run id."
            }
          />
          <Toggle
            label="--no-consensus"
            value={draft.no_consensus}
            onChange={(v) =>
              setDraft({ ...draft, no_consensus: v, ensemble_only: false, spo_only: false })
            }
            help={
              "v3 fairness path: run each ensemble model as a standalone NER+SPO pipeline (no consensus vote). " +
              "Each model produces its own NER + clusters + SPO independently. Lets you compare " +
              "isolated-model performance vs the ensemble."
            }
          />
        </div>

        <EnvOverridesEditor
          value={draft.env_overrides ?? {}}
          onChange={(v) => setDraft({ ...draft, env_overrides: v })}
        />

        <Field
          label="sample_per_domain (optional cap)"
          help={
            "Cap chunks per domain (media / congress / leaks) before extraction. " +
            "Sets BENCH_SAMPLE_PER_DOMAIN env var on the harness. " +
            "Useful for fast smoke runs — leave blank to process every chunk in S3."
          }
        >
          <input
            type="number"
            min={1}
            value={draft.sample_per_domain ?? ""}
            onChange={(e) =>
              setDraft({
                ...draft,
                sample_per_domain: e.target.value ? Number(e.target.value) : null,
              })
            }
            placeholder="leave blank for no cap"
            className={inputCls}
          />
        </Field>

        <div className="flex items-center gap-2 pt-2">
          <button
            type="button"
            onClick={onSave}
            disabled={busy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-zinc-700/40 hover:bg-zinc-700/60 disabled:opacity-50 text-zinc-200"
          >
            <Save className="h-3.5 w-3.5" />
            Save config
          </button>
          <button
            type="button"
            onClick={onRun}
            // Only disable while an in-flight run is actually still going.
            // ``activeRun`` can also point at a completed-with-error or
            // completed-OK run (the runs list is sticky + localStorage'd
            // for the active selection), and we should still let the user
            // launch another run in those states.
            disabled={busy || activeRun?.status === "running"}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-emerald-600/30 hover:bg-emerald-600/50 disabled:opacity-50 text-emerald-100"
          >
            <Play className="h-3.5 w-3.5" />
            Run bench
          </button>
          {activeRun?.status === "running" && (
            <button
              type="button"
              onClick={() => onStop(activeRun.run_id)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-red-600/30 hover:bg-red-600/50 text-red-100"
            >
              <Square className="h-3.5 w-3.5" />
              Stop run
            </button>
          )}
          <span className="text-zinc-600 text-[10px] ml-auto">
            CLI preview: <span className="text-zinc-400">{previewCli(draft)}</span>
          </span>
        </div>
      </section>

      {/* ─── Active runs + log tail ─────────────────────────────── */}
      <aside className="w-[420px] flex-shrink-0 border-l border-white/10 flex flex-col overflow-hidden">
        <div className="px-3 py-2 border-b border-white/5 font-mono text-[10px] text-zinc-400 flex items-center gap-2">
          <FileText className="h-3 w-3" />
          runs
          <span className="text-zinc-600">·</span>
          <span className="text-zinc-500">{runs.length}</span>
        </div>
        <div className="overflow-y-auto max-h-[40%] border-b border-white/5">
          {runs.length === 0 && (
            <div className="px-3 py-2 font-mono text-[10px] text-zinc-600">no runs yet.</div>
          )}
          {runs.map((r) => (
            <button
              type="button"
              key={r.run_id}
              onClick={() => setActiveRunId(r.run_id)}
              className={`w-full text-left px-3 py-1.5 font-mono text-[10px] flex items-center gap-2 hover:bg-white/[0.03] ${
                activeRunId === r.run_id ? "bg-cyan-500/10" : ""
              }`}
            >
              <span
                className={`px-1.5 py-0.5 rounded text-[9px] ${
                  r.status === "running"
                    ? "bg-amber-500/20 text-amber-300"
                    : r.status === "ok"
                      ? "bg-emerald-500/20 text-emerald-300"
                      : "bg-red-500/20 text-red-300"
                }`}
              >
                {r.status}
              </span>
              <span className="text-zinc-500 w-20 truncate">{r.run_id.slice(0, 8)}</span>
              <span className="flex-1 truncate text-zinc-400">{r.config.name ?? "—"}</span>
              <span className="text-zinc-600">
                {new Date(r.started_at * 1000).toLocaleTimeString()}
              </span>
            </button>
          ))}
        </div>
        <div className="flex-1 min-h-0 flex flex-col">
          <div className="px-3 py-1.5 border-b border-white/5 font-mono text-[10px] text-zinc-500 flex items-center justify-between">
            <span>{activeRun ? `log · ${activeRun.run_id.slice(0, 8)}` : "select a run"}</span>
            {activeRun && (
              <span className="text-zinc-600">
                pid {activeRun.pid} · {activeRun.status}
                {activeRun.return_code != null && ` (rc=${activeRun.return_code})`}
              </span>
            )}
          </div>
          <pre
            ref={logRef}
            className="flex-1 overflow-y-auto px-3 py-2 font-mono text-[10px] text-zinc-300 whitespace-pre-wrap"
          >
            {logText || (activeRun ? "(no output yet)" : "")}
          </pre>
        </div>
      </aside>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

const inputCls =
  "w-full px-2 py-1 rounded bg-black/30 border border-white/10 text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:border-cyan-500/50";

function LoadRow({
  label,
  stage,
  hint,
  slow,
}: {
  label: string;
  stage: LoadStage;
  hint?: string;
  slow?: boolean;
}) {
  const icon =
    stage === "ok" ? (
      <Check className="h-3.5 w-3.5 text-emerald-400" />
    ) : stage === "error" ? (
      <span className="text-red-400 text-[11px]">✕</span>
    ) : (
      <Loader2
        className={`h-3.5 w-3.5 animate-spin ${slow ? "text-amber-400" : "text-zinc-400"}`}
      />
    );
  return (
    <div className="flex items-start gap-2.5">
      <div className="w-4 h-4 flex-shrink-0 flex items-center justify-center mt-0.5">{icon}</div>
      <div className="flex-1 min-w-0">
        <div
          className={`flex items-center gap-2 ${
            stage === "ok" ? "text-zinc-500" : stage === "error" ? "text-red-300" : "text-zinc-200"
          }`}
        >
          <span>{label}</span>
          {slow && stage === "pending" && (
            <span className="px-1.5 py-0 rounded text-[9px] bg-amber-500/15 text-amber-300 border border-amber-500/30">
              slow
            </span>
          )}
        </div>
        {hint && <div className="text-[9.5px] text-zinc-600 truncate">{hint}</div>}
      </div>
    </div>
  );
}

/**
 * Single dotted-underline label with a hover tooltip — the page's only
 * tooltip primitive (mirrors the MetricLabel pattern in
 * components/benchmark/shared.tsx). Use everywhere a control needs a
 * "what does this do" hint instead of inlining Tooltip JSX repeatedly.
 */
function HelpLabel({ label, help }: { label: string; help?: string }) {
  if (!help)
    return <span className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</span>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className="text-[10px] uppercase tracking-wide text-zinc-500 cursor-help border-b border-dotted border-zinc-700 hover:border-zinc-500 hover:text-zinc-300 transition-colors"
          aria-label={`${label}: ${help}`}
        >
          {label}
        </span>
      </TooltipTrigger>
      <TooltipContent
        side="top"
        sideOffset={6}
        className={`${TOOLTIP_CLS} normal-case tracking-normal`}
      >
        {help}
      </TooltipContent>
    </Tooltip>
  );
}

function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <HelpLabel label={label} help={help} />
      {children}
    </label>
  );
}

function Toggle({
  label,
  value,
  onChange,
  help,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
  help?: string;
}) {
  const button = (
    <button
      type="button"
      onClick={() => onChange(!value)}
      className={`flex items-center gap-2 px-2 py-1.5 rounded border w-full ${
        value
          ? "bg-cyan-500/15 border-cyan-500/40 text-cyan-200"
          : "bg-zinc-800/40 border-white/10 text-zinc-400 hover:text-zinc-200"
      }`}
    >
      <span className={`w-2 h-2 rounded-full ${value ? "bg-cyan-400" : "bg-zinc-600"}`} />
      <span className="font-mono text-[10px]">{label}</span>
    </button>
  );
  if (!help) return button;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent side="top" sideOffset={6} className={TOOLTIP_CLS}>
        {help}
      </TooltipContent>
    </Tooltip>
  );
}

function MultiSelect({
  options,
  selected,
  onChange,
}: {
  options: ModelEntry[];
  selected: string[];
  onChange: (v: string[]) => void;
}) {
  const toggle = (name: string) => {
    onChange(selected.includes(name) ? selected.filter((s) => s !== name) : [...selected, name]);
  };
  return (
    <div className="rounded border border-white/10 bg-black/30 p-1.5 max-h-32 overflow-y-auto space-y-0.5">
      {options.map((m) => {
        const on = selected.includes(m.name);
        return (
          <button
            type="button"
            key={m.name}
            onClick={() => toggle(m.name)}
            className={`w-full text-left px-2 py-0.5 rounded flex items-center gap-2 text-[10px] ${
              on ? "bg-cyan-500/15 text-cyan-200" : "text-zinc-400 hover:bg-white/[0.03]"
            }`}
          >
            <span
              className={`w-2 h-2 rounded-sm flex-shrink-0 ${on ? "bg-cyan-400" : "bg-zinc-700"}`}
            />
            <span className="flex-1 truncate">{m.name}</span>
            <span className="text-zinc-600 text-[9px] truncate max-w-[90px]">
              {m.tags.slice(0, 2).join(",")}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/**
 * Env-overrides editor — a curated row per known key (LLM_API_KEY, EMBEDDING_*,
 * CONGRESS_API_KEY, …) plus a "+ add var" affordance for arbitrary keys. Empty
 * values are preserved in state but the backend drops them at save_config time
 * so they don't shadow the inherited viewer-api env.
 */
function EnvOverridesEditor({
  value,
  onChange,
}: {
  value: Record<string, string>;
  onChange: (v: Record<string, string>) => void;
}) {
  const setVar = (key: string, v: string) => onChange({ ...value, [key]: v });
  const removeVar = (key: string) => {
    const { [key]: _drop, ...rest } = value;
    void _drop;
    onChange(rest);
  };

  // Custom keys = anything in ``value`` that isn't in the curated list.
  const known = new Set(COMMON_ENV_VARS.map((s) => s.key));
  const customKeys = Object.keys(value)
    .filter((k) => !known.has(k))
    .sort();

  return (
    <div className="rounded border border-white/10 bg-black/20 p-3 space-y-2">
      <div className="flex items-center justify-between">
        <HelpLabel
          label="env overrides"
          help={
            "Per-run env vars merged into the harness subprocess. Useful for swapping the embedding model, " +
            "the LLM proxy URL, or a test API key without re-launching viewer-api.\n\n" +
            "Empty values are dropped — the harness then falls back to whatever the viewer-api process has."
          }
        />
        <button
          type="button"
          onClick={() => {
            const k = window.prompt("env var name (e.g. MY_FLAG)");
            if (!k || !k.trim()) return;
            setVar(k.trim(), "");
          }}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-[10px] bg-zinc-800/40 border border-white/10 text-zinc-400 hover:text-zinc-200 hover:bg-white/5"
        >
          <Plus className="h-3 w-3" />
          add var
        </button>
      </div>

      <div className="space-y-1.5">
        {COMMON_ENV_VARS.map((spec) => (
          <EnvRow
            key={spec.key}
            spec={spec}
            value={value[spec.key] ?? ""}
            onChange={(v) => setVar(spec.key, v)}
          />
        ))}
        {customKeys.map((k) => (
          <EnvRow
            key={k}
            spec={{
              key: k,
              label: k,
              placeholder: "",
              help: "Custom env var (added via + add var).",
              sensitive: false,
            }}
            value={value[k] ?? ""}
            onChange={(v) => setVar(k, v)}
            onRemove={() => removeVar(k)}
          />
        ))}
      </div>
    </div>
  );
}

function EnvRow({
  spec,
  value,
  onChange,
  onRemove,
}: {
  spec: EnvVarSpec;
  value: string;
  onChange: (v: string) => void;
  onRemove?: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <div className="w-44 flex-shrink-0">
        <HelpLabel label={spec.label} help={spec.help} />
      </div>
      <input
        type={spec.sensitive ? "password" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={spec.placeholder}
        className={`flex-1 ${inputCls}`}
        autoComplete="off"
        spellCheck={false}
      />
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="p-1 text-zinc-500 hover:text-red-400"
          title="remove"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      )}
    </div>
  );
}

/** Mirror of the server-side _slugify_label — keep the CLI preview honest. */
function slugifyLabel(name: string): string {
  const s = name
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return s || "unnamed";
}

function previewCli(c: BenchConfig): string {
  const parts: string[] = ["python tests/benchmark_harness.py"];
  if (c.name) parts.push(`--label ${slugifyLabel(c.name)}`);
  if (c.full) parts.push("--full");
  if (c.all_videos) parts.push("--all-videos");
  if (c.ensemble_only) parts.push("--ensemble-only");
  if (c.spo_only) parts.push("--spo-only");
  if (c.no_consensus) parts.push("--no-consensus");
  if (c.regen) parts.push("--regen");
  if (c.ensemble.length) parts.push(`--ensemble ${c.ensemble.join(",")}`);
  if (c.spo_models.length) parts.push(`--spo-models ${c.spo_models.join(",")}`);
  if (c.ner_quorum) parts.push(`--ner-quorum '${c.ner_quorum}'`);
  return parts.join(" ");
}

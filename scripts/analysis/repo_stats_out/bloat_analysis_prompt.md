# Codebase Remediation Analysis — `catalyst-data`

You are an expert reviewer auditing the **catalyst-data** monorepo for
bloat, DRY violations, **and messy interfaces**. This task is a follow-up
to a heuristic statistical pass (commits, churn, comment density, hot
files, bloat scorecard, cyclomatic complexity, fan-in/fan-out instability,
and coverage). Your job is the *structural* layer: cross-reference these
signals against the actual code graph and surface concrete, reviewable
remediation opportunities.

## HARD CONSTRAINT — NO FUNCTIONALITY LOSS

Every suggestion MUST preserve the **observable behavior** of the codebase.

ALLOWED:
- Merge duplicate / near-duplicate functions or classes
- Extract shared logic into a helper or base class
- Remove dead code (unreferenced public symbols, unreachable branches)
- Trim low-value AI-generated comments (paraphrase-of-code, not WHY)
- Collapse redundant abstractions (one-line wrappers, single-use indirections)
- Replace copy-pasted blocks with calls to an existing equivalent
- **Narrow over-wide interfaces** (split god-classes, reduce parameter lists
  via dataclasses, extract sub-modules) — but keep the public surface
  reachable through a deprecation shim if anything outside this repo imports it

NOT ALLOWED:
- Removing features, endpoints, CLI flags, env vars, public API surface
- Loosening validation/error handling at external boundaries
- Behavior changes disguised as refactors

## REQUIRED TOOLING — `codebase-memory` MCP (knowledge graph)

This repo is indexed in the `codebase-memory` MCP server. Before drafting
any remediation, you MUST use these tools to verify your hypotheses are
real (not just statistical artifacts):

- `mcp__codebase-memory__list_projects` — confirm the repo is indexed
- `mcp__codebase-memory__get_architecture` — orient on package layout
- `mcp__codebase-memory__get_graph_schema` — see node/edge types available
- `mcp__codebase-memory__search_graph` — find a function/class by name
  across packages (look for near-duplicates by name: multiple `parse_chunk`,
  `load_config`, `norm`, `classify`, `to_records`, etc.)
- `mcp__codebase-memory__query_graph` — Cypher-like queries. Examples:

    ```
    // ── Duplication ──────────────────────────────────────────────
    // Function names defined more than once across the repo
    MATCH (f:Function) WITH f.name AS name, count(*) AS c
    WHERE c > 1 RETURN name, c ORDER BY c DESC

    // ── Dead code ────────────────────────────────────────────────
    // Functions never called by any other function
    MATCH (f:Function) WHERE NOT (()-[:CALLS]->(f))
    RETURN f.name, f.file LIMIT 50

    // ── Messy / wide interfaces ──────────────────────────────────
    // Functions with very wide parameter lists
    MATCH (f:Function) WHERE size(f.params) > 8
    RETURN f.file, f.name, size(f.params) AS arity ORDER BY arity DESC LIMIT 30

    // Classes with too many methods (god-class smell)
    MATCH (c:Class)-[:HAS_METHOD]->(m:Function)
    WITH c, count(m) AS method_count
    WHERE method_count > 15
    RETURN c.file, c.name, method_count ORDER BY method_count DESC LIMIT 20

    // Modules with high afferent coupling (everyone depends on them — risky)
    MATCH (m:Module)<-[:IMPORTS]-(other:Module)
    WITH m, count(DISTINCT other) AS afferent
    WHERE afferent > 10
    RETURN m.path, afferent ORDER BY afferent DESC LIMIT 20

    // Modules with high efferent coupling (depend on too many things)
    MATCH (m:Module)-[:IMPORTS]->(other:Module)
    WITH m, count(DISTINCT other) AS efferent
    WHERE efferent > 15
    RETURN m.path, efferent ORDER BY efferent DESC LIMIT 20
    ```
- `mcp__codebase-memory__trace_call_path` — confirm a candidate dead
  function truly has no inbound edges
- `mcp__codebase-memory__search_code` — semantic snippet lookup (graph)
- `mcp__codebase-memory__get_code_snippet` — pull the actual source for a
  symbol before recommending a change

`mcp__claude-context__search_code` is also available for semantic search
across the same repo when a graph query comes up empty.

## STATISTICAL SIGNALS (from `scripts/repo_stats.ipynb`)

Use these as a **prioritization layer** — investigate top entries first,
since they combine size, comment density, churn, recency, complexity,
instability, and coverage gaps.

### Repo summary
```json
{
  "commits": 463,
  "file_changes": 2502,
  "first_commit": "2026-03-10",
  "last_commit": "2026-05-02",
  "added": 187560,
  "removed": 87548,
  "net": 100012,
  "current_total_lines": 100035,
  "code_comment_ratio_overall": 0.1303594629709831,
  "commit_churn_p50": 68.0,
  "commit_churn_p90": 805.4000000000003,
  "commit_churn_p99": 6452.499999999998,
  "commit_churn_max": 49714,
  "py_files_analyzed": 303,
  "overall_line_coverage": null,
  "overall_branch_coverage": null
}
```

### Lines by language
```json
{
  "other": 8755,
  "markdown": 9954,
  "yaml": 7398,
  "json": 11999,
  "config": 421,
  "docker": 489,
  "python": 46238,
  "ts": 13419,
  "js": 29,
  "web": 602,
  "shell": 731
}
```

### Lines by bucket (code / tests / fixtures)
```json
{
  "code": 78309,
  "tests": 17753,
  "fixtures": 429
}
```

### Category net-growth share
```json
[
  {
    "category": "src",
    "added": 76190,
    "removed": 29037,
    "net": 47153,
    "share_of_net": 0.4714734231892173
  },
  {
    "category": "config",
    "added": 21707,
    "removed": 1973,
    "net": 19734,
    "share_of_net": 0.19731632204135505
  },
  {
    "category": "tests",
    "added": 73687,
    "removed": 55478,
    "net": 18209,
    "share_of_net": 0.18206815182178138
  },
  {
    "category": "docs",
    "added": 10866,
    "removed": 1058,
    "net": 9808,
    "share_of_net": 0.09806823181218254
  },
  {
    "category": "lockfiles",
    "added": 5110,
    "removed": 2,
    "net": 5108,
    "share_of_net": 0.051073871135463744
  }
]
```

### Top files by net growth (added − removed)
```json
[
  {
    "path": "packages/media-ingest/viewer-ui/package-lock.json",
    "added": 9240,
    "removed": 435,
    "commits": 7,
    "last_touched": "2026-04-29 00:00:00",
    "net_growth": 8805
  },
  {
    "path": "k8s/monitoring/grafana-dashboard.yaml",
    "added": 4770,
    "removed": 779,
    "commits": 9,
    "last_touched": "2026-04-13 00:00:00",
    "net_growth": 3991
  },
  {
    "path": "packages/knowledge-graph-api/package-lock.json",
    "added": 2935,
    "removed": 0,
    "commits": 1,
    "last_touched": "2026-04-11 00:00:00",
    "net_growth": 2935
  },
  {
    "path": "libs/catalyst-langgraph-aio/uv.lock",
    "added": 2778,
    "removed": 1,
    "commits": 3,
    "last_touched": "2026-04-11 00:00:00",
    "net_growth": 2777
  },
  {
    "path": "uv.lock",
    "added": 1192,
    "removed": 1,
    "commits": 2,
    "last_touched": "2026-05-02 00:00:00",
    "net_growth": 1191
  },
  {
    "path": "tests/benchmark_harness.py",
    "added": 1466,
    "removed": 340,
    "commits": 15,
    "last_touched": "2026-05-02 00:00:00",
    "net_growth": 1126
  },
  {
    "path": "docs/feature-request/entity-stack-viewer.md",
    "added": 1115,
    "removed": 15,
    "commits": 2,
    "last_touched": "2026-04-09 00:00:00",
    "net_growth": 1100
  },
  {
    "path": "ONTOLOGY.md",
    "added": 1091,
    "removed": 9,
    "commits": 2,
    "last_touched": "2026-04-14 00:00:00",
    "net_growth": 1082
  },
  {
    "path": "packages/media-ingest/viewer-ui/src/components/benchmark/GroundTruthPanel.tsx",
    "added": 1204,
    "removed": 231,
    "commits": 6,
    "last_touched": "2026-05-02 00:00:00",
    "net_growth": 973
  },
  {
    "path": "libs/dagster-io/src/dagster_io/chunking.py",
    "added": 829,
    "removed": 16,
    "commits": 8,
    "last_touched": "2026-05-01 00:00:00",
    "net_growth": 813
  },
  {
    "path": "libs/catalyst-llm-contract-mcp/uv.lock",
    "added": 813,
    "removed": 0,
    "commits": 1,
    "last_touched": "2026-03-16 00:00:00",
    "net_growth": 813
  },
  {
    "path": "docs/feature-request/financial-commentary-analysis.md",
    "added": 812,
    "removed": 0,
    "commits": 1,
    "last_touched": "2026-04-09 00:00:00",
    "net_growth": 812
  },
  {
    "path": "packages/media-ingest/viewer-ui/src/components/Transcript.tsx",
    "added": 904,
    "removed": 161,
    "commits": 9,
    "last_touched": "2026-04-17 00:00:00",
    "net_growth": 743
  },
  {
    "path": "scripts/sample_gt_candidates.py",
    "added": 1012,
    "removed": 318,
    "commits": 2,
    "last_touched": "2026-05-01 00:00:00",
    "net_growth": 694
  },
  {
    "path": "BENCHMARK.md",
    "added": 755,
    "removed": 87,
    "commits": 4,
    "last_touched": "2026-05-01 00:00:00",
    "net_growth": 668
  },
  {
    "path": "packages/media-ingest/src/media_ingest/assets/transcription.py",
    "added": 1215,
    "removed": 553,
    "commits": 25,
    "last_touched": "2026-05-01 00:00:00",
    "net_growth": 662
  },
  {
    "path": "libs/catalyst-langgraph-aio/tests/test_real_validator_integration.py",
    "added": 679,
    "removed": 51,
    "commits": 3,
    "last_touched": "2026-04-14 00:00:00",
    "net_growth": 628
  },
  {
    "path": "packages/congress-data/src/congress_data/bill_chunker.py",
    "added": 651,
    "removed": 27,
    "commits": 3,
    "last_touched": "2026-05-01 00:00:00",
    "net_growth": 624
  },
  {
    "path": "libs/dagster-io/src/dagster_io/concordance.py",
    "added": 713,
    "removed": 92,
    "commits": 11,
    "last_touched": "2026-04-14 00:00:00",
    "net_growth": 621
  },
  {
    "path": "libs/dagster-io/src/dagster_io/metrics.py",
    "added": 945,
    "removed": 370,
    "commits": 18,
    "last_touched": "2026-05-01 00:00:00",
    "net_growth": 575
  }
]
```

### Top comment-heavy files
```json
[
  {
    "path": ".beads/config.yaml",
    "code": 0,
    "comment": 49,
    "blank": 13,
    "total": 62,
    "comment_ratio": 1.0
  },
  {
    "path": ".gitleaks.toml",
    "code": 17,
    "comment": 49,
    "blank": 10,
    "total": 76,
    "comment_ratio": 0.7424242424242424
  },
  {
    "path": "tests/benchmark_harness.py",
    "code": 391,
    "comment": 591,
    "blank": 144,
    "total": 1126,
    "comment_ratio": 0.6018329938900203
  },
  {
    "path": "lefthook.yml",
    "code": 32,
    "comment": 48,
    "blank": 7,
    "total": 87,
    "comment_ratio": 0.6
  },
  {
    "path": "libs/dagster-io/src/dagster_io/versioning.py",
    "code": 14,
    "comment": 21,
    "blank": 9,
    "total": 44,
    "comment_ratio": 0.6
  },
  {
    "path": "libs/dagster-io/src/dagster_io/_runtime_context.py",
    "code": 20,
    "comment": 28,
    "blank": 13,
    "total": 61,
    "comment_ratio": 0.5833333333333334
  },
  {
    "path": "packages/congress-data/tests/test_bill_chunker.py",
    "code": 22,
    "comment": 30,
    "blank": 13,
    "total": 65,
    "comment_ratio": 0.5769230769230769
  },
  {
    "path": "libs/catalyst-exgraph/src/catalyst_exgraph/state.py",
    "code": 32,
    "comment": 41,
    "blank": 31,
    "total": 104,
    "comment_ratio": 0.5616438356164384
  },
  {
    "path": "scripts/dump_concordance.py",
    "code": 128,
    "comment": 163,
    "blank": 47,
    "total": 338,
    "comment_ratio": 0.5601374570446735
  },
  {
    "path": "libs/dagster-io/src/dagster_io/semantic_seed.py",
    "code": 88,
    "comment": 82,
    "blank": 39,
    "total": 209,
    "comment_ratio": 0.4823529411764706
  },
  {
    "path": "libs/dagster-io/src/dagster_io/io_backend.py",
    "code": 20,
    "comment": 18,
    "blank": 11,
    "total": 49,
    "comment_ratio": 0.47368421052631576
  },
  {
    "path": "libs/dagster-io/src/dagster_io/run_status_sensor.py",
    "code": 106,
    "comment": 87,
    "blank": 32,
    "total": 225,
    "comment_ratio": 0.45077720207253885
  },
  {
    "path": "libs/catalyst-exgraph/src/catalyst_exgraph/dispatch.py",
    "code": 36,
    "comment": 26,
    "blank": 25,
    "total": 87,
    "comment_ratio": 0.41935483870967744
  },
  {
    "path": "libs/catalyst-exgraph/src/catalyst_exgraph/nodes/spans.py",
    "code": 62,
    "comment": 38,
    "blank": 28,
    "total": 128,
    "comment_ratio": 0.38
  },
  {
    "path": "libs/catalyst-exgraph/src/catalyst_exgraph/config.py",
    "code": 82,
    "comment": 45,
    "blank": 45,
    "total": 172,
    "comment_ratio": 0.3543307086614173
  },
  {
    "path": "packages/knowledge-graph/src/knowledge_graph/resources.py",
    "code": 339,
    "comment": 180,
    "blank": 37,
    "total": 556,
    "comment_ratio": 0.3468208092485549
  },
  {
    "path": "libs/dagster-io/src/dagster_io/prompts.py",
    "code": 53,
    "comment": 28,
    "blank": 21,
    "total": 102,
    "comment_ratio": 0.345679012345679
  },
  {
    "path": "libs/catalyst-langgraph-aio/src/catalyst_langgraph/prompts.py",
    "code": 59,
    "comment": 31,
    "blank": 28,
    "total": 118,
    "comment_ratio": 0.34444444444444444
  },
  {
    "path": "libs/catalyst-llm-contract-mcp/src/catalyst_contracts/models/extraction_output.py",
    "code": 37,
    "comment": 19,
    "blank": 18,
    "total": 74,
    "comment_ratio": 0.3392857142857143
  },
  {
    "path": "libs/dagster-io/tests/test_concordance_regression.py",
    "code": 62,
    "comment": 31,
    "blank": 24,
    "total": 117,
    "comment_ratio": 0.3333333333333333
  }
]
```

### Top bloat scorecard (combined heuristic)
```json
[
  {
    "path": "k8s/monitoring/grafana-dashboard.yaml",
    "total": 3991,
    "comment_ratio": 0.0,
    "net_growth": 3991.0,
    "commits": 9.0,
    "recent_added": 3741.0,
    "bloat_score": 0.75
  },
  {
    "path": "tests/benchmark_harness.py",
    "total": 1126,
    "comment_ratio": 0.6018329938900203,
    "net_growth": 1126.0,
    "commits": 15.0,
    "recent_added": 1466.0,
    "bloat_score": 0.3826479064662954
  },
  {
    "path": ".beads/config.yaml",
    "total": 62,
    "comment_ratio": 1.0,
    "net_growth": 0.0,
    "commits": 0.0,
    "recent_added": 0.0,
    "bloat_score": 0.25201969199697044
  },
  {
    "path": "packages/media-ingest/viewer-ui/src/components/benchmark/GroundTruthPanel.tsx",
    "total": 973,
    "comment_ratio": 0.06637168141592921,
    "net_growth": 973.0,
    "commits": 6.0,
    "recent_added": 1204.0,
    "bloat_score": 0.21361809900070247
  },
  {
    "path": "scripts/sample_gt_candidates.py",
    "total": 694,
    "comment_ratio": 0.23049001814882034,
    "net_growth": 694.0,
    "commits": 2.0,
    "recent_added": 1012.0,
    "bloat_score": 0.20580167103934188
  },
  {
    "path": "libs/dagster-io/src/dagster_io/chunking.py",
    "total": 813,
    "comment_ratio": 0.2460431654676259,
    "net_growth": 813.0,
    "commits": 8.0,
    "recent_added": 606.0,
    "bloat_score": 0.20444038766089112
  },
  {
    "path": "packages/media-ingest/src/media_ingest/assets/transcription.py",
    "total": 742,
    "comment_ratio": 0.19169329073482427,
    "net_growth": 662.0,
    "commits": 25.0,
    "recent_added": 1151.0,
    "bloat_score": 0.20415778314076122
  },
  {
    "path": ".gitleaks.toml",
    "total": 76,
    "comment_ratio": 0.7424242424242424,
    "net_growth": 0.0,
    "commits": 0.0,
    "recent_added": 76.0,
    "bloat_score": 0.19257245258840705
  },
  {
    "path": "packages/knowledge-graph/src/knowledge_graph/resources.py",
    "total": 556,
    "comment_ratio": 0.3468208092485549,
    "net_growth": 556.0,
    "commits": 9.0,
    "recent_added": 401.0,
    "bloat_score": 0.18313604424487812
  },
  {
    "path": "scripts/dump_concordance.py",
    "total": 338,
    "comment_ratio": 0.5601374570446735,
    "net_growth": 0.0,
    "commits": 0.0,
    "recent_added": 442.0,
    "bloat_score": 0.18310394517440418
  },
  {
    "path": "packages/media-ingest/viewer-ui/src/components/Transcript.tsx",
    "total": 743,
    "comment_ratio": 0.0844062947067239,
    "net_growth": 743.0,
    "commits": 9.0,
    "recent_added": 904.0,
    "bloat_score": 0.17028282368840345
  },
  {
    "path": "libs/dagster-io/src/dagster_io/concordance.py",
    "total": 621,
    "comment_ratio": 0.24952380952380954,
    "net_growth": 621.0,
    "commits": 11.0,
    "recent_added": 368.0,
    "bloat_score": 0.16603605300589125
  },
  {
    "path": "packages/congress-data/src/congress_data/bill_chunker.py",
    "total": 624,
    "comment_ratio": 0.18532818532818532,
    "net_growth": 624.0,
    "commits": 3.0,
    "recent_added": 651.0,
    "bloat_score": 0.1655316449534179
  },
  {
    "path": "packages/media-ingest/viewer-ui/src/pages/BenchmarkReport.tsx",
    "total": 567,
    "comment_ratio": 0.028142589118198873,
    "net_growth": 567.0,
    "commits": 18.0,
    "recent_added": 1428.0,
    "bloat_score": 0.15989272435897017
  },
  {
    "path": "libs/dagster-io/src/dagster_io/bench_store.py",
    "total": 519,
    "comment_ratio": 0.2458628841607565,
    "net_growth": 519.0,
    "commits": 1.0,
    "recent_added": 519.0,
    "bloat_score": 0.15908850994145535
  },
  {
    "path": "lefthook.yml",
    "total": 87,
    "comment_ratio": 0.6,
    "net_growth": 0.0,
    "commits": 0.0,
    "recent_added": 87.0,
    "bloat_score": 0.1582487391603013
  },
  {
    "path": "libs/dagster-io/src/dagster_io/versioning.py",
    "total": 44,
    "comment_ratio": 0.6,
    "net_growth": 0.0,
    "commits": 0.0,
    "recent_added": 44.0,
    "bloat_score": 0.1532359274646596
  },
  {
    "path": "libs/dagster-io/src/dagster_io/_runtime_context.py",
    "total": 61,
    "comment_ratio": 0.5833333333333334,
    "net_growth": 0.0,
    "commits": 0.0,
    "recent_added": 61.0,
    "bloat_score": 0.1510510700730141
  },
  {
    "path": "libs/catalyst-exgraph/src/catalyst_exgraph/state.py",
    "total": 104,
    "comment_ratio": 0.5616438356164384,
    "net_growth": 0.0,
    "commits": 0.0,
    "recent_added": 104.0,
    "bloat_score": 0.15064150733943205
  },
  {
    "path": "packages/congress-data/tests/test_bill_chunker.py",
    "total": 65,
    "comment_ratio": 0.5769230769230769,
    "net_growth": 0.0,
    "commits": 0.0,
    "recent_added": 65.0,
    "bloat_score": 0.14991481403516083
  }
]
```

### Top messy interfaces (cyclomatic + fan-in/out + coverage)
```json
[
  {
    "path": "tests/benchmark_harness.py",
    "cc_max": 116,
    "cc_mean": 14.625,
    "public_funcs": 1,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 5,
    "fan_in": 0,
    "fan_out": 7,
    "instability": 1.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.5577302631578948
  },
  {
    "path": "tests/shared/report.py",
    "cc_max": 38,
    "cc_mean": 38.0,
    "public_funcs": 1,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 5,
    "fan_in": 1,
    "fan_out": 4,
    "instability": 0.8,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.44189655172413794
  },
  {
    "path": "scripts/sample_gt_candidates.py",
    "cc_max": 15,
    "cc_mean": 4.913043478260869,
    "public_funcs": 3,
    "public_classes": 3,
    "max_class_methods": 1,
    "max_func_params": 7,
    "fan_in": 0,
    "fan_out": 1,
    "instability": 1.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.3289939061569694
  },
  {
    "path": "scripts/regen_audio_fixtures.py",
    "cc_max": 26,
    "cc_mean": 7.2,
    "public_funcs": 1,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 4,
    "fan_in": 0,
    "fan_out": 1,
    "instability": 1.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.3253646262992905
  },
  {
    "path": "scripts/bench_extract_per_video.py",
    "cc_max": 16,
    "cc_mean": 9.0,
    "public_funcs": 1,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 4,
    "fan_in": 0,
    "fan_out": 1,
    "instability": 1.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.31091816531925426
  },
  {
    "path": "packages/media-ingest/tests/integration/test_pipeline_integration.py",
    "cc_max": 6,
    "cc_mean": 2.3846153846153846,
    "public_funcs": 13,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 1,
    "fan_in": 0,
    "fan_out": 1,
    "instability": 1.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.2905258081301639
  },
  {
    "path": "tests/test_extraction_e2e.py",
    "cc_max": 12,
    "cc_mean": 4.222222222222222,
    "public_funcs": 7,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 1,
    "fan_in": 0,
    "fan_out": 2,
    "instability": 1.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.2834378265412748
  },
  {
    "path": "tests/test_extraction_benchmark.py",
    "cc_max": 13,
    "cc_mean": 4.0,
    "public_funcs": 0,
    "public_classes": 3,
    "max_class_methods": 5,
    "max_func_params": 3,
    "fan_in": 0,
    "fan_out": 4,
    "instability": 1.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.27107944233624814
  },
  {
    "path": "tests/shared/ground_truth.py",
    "cc_max": 29,
    "cc_mean": 10.666666666666666,
    "public_funcs": 1,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 7,
    "fan_in": 4,
    "fan_out": 3,
    "instability": 0.42857142857142855,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.2585013670539986
  },
  {
    "path": "packages/knowledge-graph/src/knowledge_graph/assets/canonical_entities.py",
    "cc_max": 45,
    "cc_mean": 27.5,
    "public_funcs": 1,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 5,
    "fan_in": 0,
    "fan_out": 0,
    "instability": 0.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.255535390199637
  },
  {
    "path": "tests/shared/test_ground_truth_candidates.py",
    "cc_max": 6,
    "cc_mean": 3.5,
    "public_funcs": 2,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 0,
    "fan_in": 0,
    "fan_out": 1,
    "instability": 1.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.23583773304735195
  },
  {
    "path": "libs/catalyst-llm-contract-mcp/src/catalyst_contracts/validators/mention_validator.py",
    "cc_max": 32,
    "cc_mean": 32.0,
    "public_funcs": 1,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 3,
    "fan_in": 0,
    "fan_out": 0,
    "instability": 0.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.22709948853324533
  },
  {
    "path": "libs/dagster-io/src/dagster_io/chunking.py",
    "cc_max": 35,
    "cc_mean": 7.666666666666667,
    "public_funcs": 2,
    "public_classes": 4,
    "max_class_methods": 8,
    "max_func_params": 11,
    "fan_in": 0,
    "fan_out": 0,
    "instability": 0.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.21478510146840457
  },
  {
    "path": "libs/catalyst-llm-contract-mcp/src/catalyst_contracts/validators/proposition_validator.py",
    "cc_max": 25,
    "cc_mean": 25.0,
    "public_funcs": 1,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 3,
    "fan_in": 0,
    "fan_out": 0,
    "instability": 0.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.18438170268932522
  },
  {
    "path": "libs/dagster-io/src/dagster_io/concordance.py",
    "cc_max": 42,
    "cc_mean": 9.058823529411764,
    "public_funcs": 2,
    "public_classes": 2,
    "max_class_methods": 4,
    "max_func_params": 4,
    "fan_in": 0,
    "fan_out": 0,
    "instability": 0.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.17173030076574436
  },
  {
    "path": "scripts/dump_concordance.py",
    "cc_max": 26,
    "cc_mean": 9.333333333333334,
    "public_funcs": 5,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 6,
    "fan_in": 0,
    "fan_out": 0,
    "instability": 0.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.17014931529450586
  },
  {
    "path": "packages/knowledge-graph/src/knowledge_graph/assets/entity_alignments.py",
    "cc_max": 18,
    "cc_mean": 18.0,
    "public_funcs": 1,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 5,
    "fan_in": 0,
    "fan_out": 0,
    "instability": 0.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.1598457350272232
  },
  {
    "path": "scripts/chunk_stats.py",
    "cc_max": 35,
    "cc_mean": 12.666666666666666,
    "public_funcs": 3,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 2,
    "fan_in": 0,
    "fan_out": 0,
    "instability": 0.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.15724921630094044
  },
  {
    "path": "packages/media-ingest/src/media_ingest/assets/entity_candidates.py",
    "cc_max": 19,
    "cc_mean": 19.0,
    "public_funcs": 1,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 4,
    "fan_in": 0,
    "fan_out": 0,
    "instability": 0.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.15685736677115988
  },
  {
    "path": "libs/dagster-io/src/dagster_io/event_tail.py",
    "cc_max": 5,
    "cc_mean": 3.142857142857143,
    "public_funcs": 7,
    "public_classes": 0,
    "max_class_methods": 0,
    "max_func_params": 11,
    "fan_in": 0,
    "fan_out": 0,
    "instability": 0.0,
    "line_rate": 0.0,
    "branch_rate": 0.0,
    "messiness_score": 0.15500005892474134
  }
]
```

### Most-complex files (cc_max)
```json
[
  {
    "path": "tests/benchmark_harness.py",
    "cc_max": 116,
    "cc_mean": 14.625,
    "func_count": 16,
    "max_func_params": 5
  },
  {
    "path": "packages/knowledge-graph/src/knowledge_graph/assets/canonical_entities.py",
    "cc_max": 45,
    "cc_mean": 27.5,
    "func_count": 2,
    "max_func_params": 5
  },
  {
    "path": "libs/dagster-io/src/dagster_io/concordance.py",
    "cc_max": 42,
    "cc_mean": 9.058823529411764,
    "func_count": 17,
    "max_func_params": 4
  },
  {
    "path": "libs/dagster-io/src/dagster_io/extraction.py",
    "cc_max": 38,
    "cc_mean": 5.214285714285714,
    "func_count": 14,
    "max_func_params": 5
  },
  {
    "path": "tests/shared/report.py",
    "cc_max": 38,
    "cc_mean": 38.0,
    "func_count": 1,
    "max_func_params": 5
  },
  {
    "path": "libs/dagster-io/src/dagster_io/chunking.py",
    "cc_max": 35,
    "cc_mean": 7.666666666666667,
    "func_count": 21,
    "max_func_params": 11
  },
  {
    "path": "scripts/chunk_stats.py",
    "cc_max": 35,
    "cc_mean": 12.666666666666666,
    "func_count": 3,
    "max_func_params": 2
  },
  {
    "path": "libs/catalyst-llm-contract-mcp/src/catalyst_contracts/validators/mention_validator.py",
    "cc_max": 32,
    "cc_mean": 32.0,
    "func_count": 1,
    "max_func_params": 3
  },
  {
    "path": "tests/shared/ground_truth.py",
    "cc_max": 29,
    "cc_mean": 10.666666666666666,
    "func_count": 6,
    "max_func_params": 7
  },
  {
    "path": "scripts/regen_audio_fixtures.py",
    "cc_max": 26,
    "cc_mean": 7.2,
    "func_count": 5,
    "max_func_params": 4
  },
  {
    "path": "scripts/dump_concordance.py",
    "cc_max": 26,
    "cc_mean": 9.333333333333334,
    "func_count": 6,
    "max_func_params": 6
  },
  {
    "path": "libs/catalyst-llm-contract-mcp/src/catalyst_contracts/validators/proposition_validator.py",
    "cc_max": 25,
    "cc_mean": 25.0,
    "func_count": 1,
    "max_func_params": 3
  },
  {
    "path": "packages/open-leaks/src/open_leaks/assets/extraction.py",
    "cc_max": 24,
    "cc_mean": 6.944444444444445,
    "func_count": 18,
    "max_func_params": 4
  },
  {
    "path": "packages/congress-data/src/congress_data/bill_chunker.py",
    "cc_max": 24,
    "cc_mean": 10.9,
    "func_count": 10,
    "max_func_params": 6
  },
  {
    "path": "libs/dagster-io/src/dagster_io/asset_factory.py",
    "cc_max": 24,
    "cc_mean": 15.333333333333334,
    "func_count": 3,
    "max_func_params": 3
  },
  {
    "path": "libs/catalyst-llm-contract-mcp/src/catalyst_contracts/validators/spatial_validator.py",
    "cc_max": 23,
    "cc_mean": 12.5,
    "func_count": 2,
    "max_func_params": 2
  },
  {
    "path": "tests/shared/extraction_scoring.py",
    "cc_max": 23,
    "cc_mean": 8.266666666666667,
    "func_count": 15,
    "max_func_params": 4
  },
  {
    "path": "scripts/compress_fixtures.py",
    "cc_max": 21,
    "cc_mean": 8.666666666666666,
    "func_count": 3,
    "max_func_params": 1
  },
  {
    "path": "libs/catalyst-exgraph/src/catalyst_exgraph/resource.py",
    "cc_max": 21,
    "cc_mean": 4.0,
    "func_count": 12,
    "max_func_params": 6
  },
  {
    "path": "packages/congress-data/src/congress_data/assets/bill_tail.py",
    "cc_max": 20,
    "cc_mean": 6.2,
    "func_count": 10,
    "max_func_params": 3
  }
]
```

### Most-unstable modules (Martin's I = fan_out / (fan_in + fan_out))
```json
[
  {
    "path": "tests/benchmark_harness.py",
    "fan_in": 0,
    "fan_out": 7,
    "instability": 1.0,
    "public_funcs": 1,
    "public_classes": 0
  },
  {
    "path": "tests/test_extraction_benchmark.py",
    "fan_in": 0,
    "fan_out": 4,
    "instability": 1.0,
    "public_funcs": 0,
    "public_classes": 3
  },
  {
    "path": "tests/shared/report.py",
    "fan_in": 1,
    "fan_out": 4,
    "instability": 0.8,
    "public_funcs": 1,
    "public_classes": 0
  },
  {
    "path": "tests/shared/ground_truth.py",
    "fan_in": 4,
    "fan_out": 3,
    "instability": 0.42857142857142855,
    "public_funcs": 1,
    "public_classes": 0
  },
  {
    "path": "tests/shared/extraction_scoring.py",
    "fan_in": 3,
    "fan_out": 0,
    "instability": 0.0,
    "public_funcs": 8,
    "public_classes": 0
  },
  {
    "path": "tests/shared/medallion.py",
    "fan_in": 6,
    "fan_out": 0,
    "instability": 0.0,
    "public_funcs": 1,
    "public_classes": 0
  },
  {
    "path": "tests/shared/store.py",
    "fan_in": 8,
    "fan_out": 0,
    "instability": 0.0,
    "public_funcs": 0,
    "public_classes": 0
  }
]
```

### Lowest-coverage files
```json
[]
```

## DELIVERABLE

Produce a single markdown report titled **Bloat & Messiness Remediation
Plan** with:

1. **Executive summary** — one paragraph, ≤ 5 bullets of headline findings.

2. **Duplication map** — for each cluster of duplicated logic, list:
   - canonical path you'd keep
   - duplicate(s) you'd remove or fold in
   - concrete shared abstraction (function name + signature)
   - estimated LOC saved
   - the codebase-memory query you used to confirm the duplication

3. **Dead-code candidates** — symbols with no inbound `CALLS` / `REFERENCES`
   edges. Format: `path:line — symbol — confidence (why)`. Always include
   the `query_graph` Cypher or `trace_call_path` trace that justifies it.

4. **Messy interface diagnoses** — for each file in the top messiness list:
   - what makes it messy (high cc, wide params, god-class, high instability)
   - the codebase-memory query that confirms it
   - concrete narrowing suggestion (split / dataclass / sub-module / shim)
   - ALWAYS preserve external API: name the deprecation shim if needed

5. **Comment-bloat trim list** — files where comment density > 30% AND
   the comments paraphrase code rather than encode non-obvious WHY. Quote
   2–3 example blocks per file.

6. **Abstraction collapses** — wrapper functions, single-use config
   indirections, premature-generic helpers that can fold inline without
   losing testability.

7. **Coverage-driven priorities** — files in the lowest-coverage list that
   you'd want covered *before* refactoring. Note any low-coverage file you
   intend to refactor and propose the regression-pinning tests first.

8. **Out of scope / risky** — things that *look* like bloat or messiness
   from the stats but are load-bearing (test fixtures, generated code,
   intentional verbosity in security-critical paths, integration boundaries
   that must stay unstable). Be explicit about what you investigated and
   decided NOT to touch, and why.

9. **Suggested PR slicing** — group remediations into 3–6 reviewable PRs,
   ordered by risk (lowest first). For each, list touched files and a
   one-line summary.

For EVERY concrete suggestion, include `file:line` references AND the
codebase-memory tool call (or query) that justifies it. Suggestions
without graph-level evidence are not acceptable.

**Begin** by calling `mcp__codebase-memory__list_projects` and
`mcp__codebase-memory__get_architecture`, then proceed.

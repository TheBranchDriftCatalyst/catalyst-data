# NER Consensus Predicates

The `--ner-quorum` CLI flag (and its Dagster-config counterpart
`ExtractionResource.ner_quorum_expr`) lets you replace the default
`ceil(N/2)` majority rule with an arbitrary boolean / arithmetic
expression over the encoder panel — without touching code.

## Quick reference

```bash
# Majority of 3 (this is the default; explicit form for clarity)
--ner-quorum='a + b + c >= 2'

# Super-majority
--ner-quorum='a + b + c + d + e >= 4'

# Unanimous
--ner-quorum='a + b + c + d + e >= 5'

# Any encoder accepts (single-source)
--ner-quorum='a + b + c >= 1'

# Weighted: encoder a counts double
--ner-quorum='2*a + b + c >= 3'

# Logical: a AND (b OR c)
--ner-quorum='a & (b | c)'

# Veto: c subtracts from the running total
--ner-quorum='a + b - c >= 1'

# Coverage groups: at least one from {a,b} AND at least one from {c,d}
--ner-quorum='min(a + b, c + d) >= 1'

# Bare integer = "sum >= K" shorthand
--ner-quorum='2'
```

## Variables

Each encoder in `--ensemble` (in declaration order) gets two names:

| Form        | Example                                                          |
|-------------|------------------------------------------------------------------|
| Letter      | `a`, `b`, `c`, … `z` (capped at 26)                              |
| Slug        | `gliner_large`, `nuextract_2_0_8b`, `universalner_7b`            |

Letter form is `encoder[i] = chr(ord('a') + i)`. Slug form is the encoder
name lowercased with non-alphanumeric runs collapsed to `_`. Both forms
are usable in the same expression — pick whichever reads better at the
call site:

```bash
--ner-quorum='gliner_large + b + c >= 2'   # mixed, perfectly valid
```

Each variable evaluates to `1.0` when the encoder voted for the cluster
under consideration, `0.0` otherwise.

## Operators

| Operator   | Meaning                       | Example                |
|------------|-------------------------------|------------------------|
| `+ - * /`  | arithmetic on numeric values  | `2*a + b - c`          |
| `&  \|`    | logical AND / OR (returns 0/1)| `a & (b \| c)`         |
| `!`        | logical NOT                   | `!a` (1 if a not voted)|
| `>= > == != < <=` | comparison → final accept/reject | terminal node |
| `min() max()` | helpers for grouped logic     | `min(a+b, c+d) >= 1`   |

The expression must terminate in a comparison so the result is boolean.
Bare arithmetic without a `>=`/`>` etc gets flagged as `trivial_accept`
or `unreachable` because the truth-table will be all-True or all-False
under integer comparison-to-zero coercion.

## Pathology detection

At compile time, the harness enumerates the entire 2^N truth table
(N = panel size, capped at 26 → at most 67M rows in the absolute worst
case; in practice ≤8 encoders → ≤256 rows). Each row is checked against
the predicate, and the result table is searched for misconfigurations:

### Hard errors (abort the run)

* **`unreachable`** — no vote combination is accepted.
  Example: `a + b + c >= 4` with N=3.
  Every cluster would be rejected; no useful work would happen.

* **`trivially true`** — every vote combination is accepted.
  Example: forgetting the comparison entirely (`a + b + c`) — under
  Python truthiness this is always non-zero so always-True.

* **`accepts_zero_votes`** — the predicate accepts when every variable
  is 0. Example: `a + b + c >= 0` or `1 >= 0`. Every cluster would pass
  consensus; the stage becomes a passthrough.

### Soft warnings (proceed, but surface in logs + audit)

* **`single-source acceptance`** — accepts on a single vote. Example:
  `a + b + c >= 1`. Skips the multi-encoder redundancy that consensus
  exists to provide. Useful for some debugging modes; surfaced so it's
  never an accident.

* **`ignored encoder`** — an encoder is in `--ensemble` but the
  expression is independent of its variable. Example:
  `--ensemble a,b,c,d --ner-quorum='a + b + c >= 2'` ignores `d`. Suggests
  dropping it from the panel (cuts cost) or including it.

* **`mandatory encoder`** — predicate is False whenever a particular
  variable is 0 (regardless of the others). That encoder is now a single
  point of failure; if it crashes or returns nothing, every cluster gets
  rejected. Example: `a & (b | c)` makes `a` mandatory.

Diagnostics are printed to stdout before the run starts and embedded in
the audit log under `consensus_started.details.predicate_summary` so the
State Inspector can render them next to the consensus node.

## Where it plugs in

* CLI: `tests/benchmark_harness.py --ner-quorum=<expr>`
* Dagster resource: `ExtractionResource.ner_quorum_expr` (string field
  on the configurable resource — visible in the Dagster UI under
  Resources → Extraction → ner_quorum_expr)
* Programmatic: import `compile_consensus_expr` and pass the result via
  `build_ensemble_pipeline(predicate=...)`

## Worked example

```bash
$ python tests/benchmark_harness.py \
    --ensemble=gliner-large,nuextract-2.0-8b,universalner-7b,gliner-medium,gliner-pii \
    --ner-quorum='2*a + b + c + d + e >= 4'

  ner-quorum: predicate '2*a + b + c + d + e >= 4' over 5 encoders:
              13/32 vote combos accept (min votes to accept = 2)
  …  (no warnings — predicate is reachable, not trivial, and uses every encoder)

phase_a [1/5] 117-hr-5376  cold  (3,200,000 chars, 5 encoders)…
…
```

The formula reads as: *encoder a (gliner-large) carries weight 2; the
other four carry weight 1; need a total weight of at least 4 to accept*.
That accepts a cluster when:
- gliner-large + any 2 others vote (2 + 1 + 1 = 4), OR
- gliner-large + any 3 others (2 + 1 + 1 + 1 ≥ 4), OR
- 4-or-more of the non-gliner-large encoders vote (1 + 1 + 1 + 1 = 4)

## See also

* `libs/catalyst-exgraph/src/catalyst_exgraph/consensus_predicate.py` —
  parser, evaluator, diagnostics
* `libs/catalyst-exgraph/src/catalyst_exgraph/nodes/consensus.py` —
  ConsensusNode integration
* `libs/catalyst-exgraph/tests/test_consensus_predicate.py` — full
  behavioural test surface (each pattern documented above is asserted)

"""Compile a small math/boolean expression into a consensus predicate.

The user-facing spec (``--ner-quorum=<expr>``) lets operators express
arbitrary vote rules over the encoder panel without touching code:

    a + b + c >= 2          # majority of 3 (current default)
    a + b + c + d + e >= 3  # majority of 5
    2*a + b + c >= 3        # weighted: encoder 'a' counts double
    a & (b | c)             # logical: a AND (b OR c)
    min(a+b, c+d) >= 1      # at least one from each pair

Variable forms (use whichever you prefer; mixable in one expression):

* **Letter form** — single lowercase letter. ``a`` → encoder[0],
  ``b`` → encoder[1], … capped at 26 encoders.
* **Name form** — the encoder's name slugged: lowercase, hyphens and
  dots become underscores. ``gliner-large`` → ``gliner_large``;
  ``nuextract-2.0-8b`` → ``nuextract_2_0_8b``.

So with ``--ensemble=gliner-large,nuextract-2.0-8b,universalner-7b`` all
three of these accept the same set of vote combos::

    a + b + c >= 2
    gliner_large + nuextract_2_0_8b + universalner_7b >= 2
    gliner_large + b + c >= 2     # mixed

The grammar is a strict subset of Python expressions, parsed with
``ast.parse(mode="eval")`` and walked with an allowlist of node types.
No function calls except ``min``/``max``; no attribute access, names
outside the encoder letters, comprehensions, lambdas, etc.

Pathology detection
-------------------
Because we only support boolean variables (each encoder either voted or
didn't), the truth-table of any expression has 2^N rows.  We enumerate
all of them at compile time and flag misconfigurations:

  * **unreachable_accept** — no vote combo accepts.  Hard fail.
  * **trivial_accept** — every vote combo accepts.  Hard fail.
  * **accepts_zero_votes** — accepts when no encoder voted.  Hard fail
    (every cluster would pass consensus, defeats the purpose).
  * **single_source** — accepts on a single vote.  Warn: pseudo-consensus.
  * **ignored_encoder** — output is independent of variable ``x``.  Warn
    with suggestion to drop the encoder from ``--ensemble``.
  * **mandatory_encoder** — predicate is False whenever ``x`` is 0.
    Warn: that encoder is now a single point of failure.
"""

from __future__ import annotations

import ast
import re
import string
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import product
from typing import Any


def _slugify_encoder(name: str) -> str:
    """Encoder-name → variable identifier.

    Lowercases, replaces any run of non-alphanumeric chars with ``_``,
    strips leading/trailing underscores. Examples::

        gliner-large       → gliner_large
        nuextract-2.0-8b   → nuextract_2_0_8b
        UniversalNER-7B    → universalner_7b
    """
    s = re.sub(r"[^0-9a-zA-Z]+", "_", name).strip("_").lower()
    if s and s[0].isdigit():
        # Python identifiers can't start with a digit; prepend ``_`` so
        # an encoder named e.g. "7b-tiny" still works as ``_7b_tiny``.
        s = "_" + s
    return s


# Permit only these AST node classes.  Anything else (Call to a non-min/max,
# attribute access, comprehensions, ...) raises at compile time.
_ALLOWED_NODES: tuple[type[ast.AST], ...] = (
    ast.Expression,
    ast.BoolOp,
    ast.UnaryOp,
    ast.BinOp,
    ast.Compare,
    ast.Call,
    ast.Name,
    ast.Constant,
    # Operators (these are leaf "tag" nodes, no traversal needed but the
    # generic_visit expects them in the allowed set).
    ast.And,
    ast.Or,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.BitAnd,
    ast.BitOr,
    ast.Load,
)

_ALLOWED_FUNCS = {"min", "max"}


class ConsensusExprError(ValueError):
    """Raised when an expression is syntactically invalid or unsafe."""


@dataclass(frozen=True)
class CompiledPredicate:
    """Compiled consensus predicate ready for evaluation.

    Attributes
    ----------
    expr_text:
        The raw expression as the user typed it (preserved for audit logs
        + diagnostic banners).
    encoder_names:
        Encoder names in the order they were bound. ``encoder_names[0]``
        is letter ``a`` and slug ``slug(encoder_names[0])``, etc.
    used_indices:
        Set of encoder *indices* that actually appear in the expression
        (regardless of which form they were referenced by). Drives the
        ``ignored_encoder`` warning.
    """

    expr_text: str
    encoder_names: tuple[str, ...]
    used_indices: frozenset[int]
    _tree: ast.Expression

    def evaluate(self, votes: Mapping[str, bool]) -> bool:
        """Evaluate the predicate against a per-encoder vote mapping.

        ``votes`` maps encoder *name* (e.g. "gliner-large") to a bool.
        Encoders not present in ``votes`` are treated as 0.
        """
        env: dict[str, float] = {}
        for i, name in enumerate(self.encoder_names):
            voted = 1.0 if votes.get(name) else 0.0
            env[string.ascii_lowercase[i]] = voted
            env[_slugify_encoder(name)] = voted
        result = _eval_node(self._tree.body, env)
        return bool(result)

    def truth_table(self) -> list[tuple[tuple[bool, ...], bool]]:
        """Return ``[(votes_tuple, accepted), …]`` for all 2^N inputs.

        Useful for diagnostics + tests.  Vote tuples are in encoder-index
        order (matches ``encoder_names``).
        """
        n = len(self.encoder_names)
        out: list[tuple[tuple[bool, ...], bool]] = []
        for combo in product([False, True], repeat=n):
            votes = {name: combo[i] for i, name in enumerate(self.encoder_names)}
            out.append((combo, self.evaluate(votes)))
        return out


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def compile_consensus_expr(expr: str, encoder_names: list[str]) -> CompiledPredicate:
    """Parse, validate, and compile ``expr`` against the encoder panel.

    Raises ``ConsensusExprError`` on any structural problem (bad grammar,
    disallowed constructs, undefined letters, ...).  Pathology detection
    (unreachable / trivial / etc) is *not* fatal here — it's surfaced via
    :func:`diagnose_predicate` so callers can render a banner.
    """
    if not expr or not expr.strip():
        raise ConsensusExprError("empty consensus expression")

    if not encoder_names:
        raise ConsensusExprError("encoder panel is empty — nothing to bind letters to")

    if len(encoder_names) > 26:
        raise ConsensusExprError(f"encoder panel has {len(encoder_names)} models; letter form supports at most 26")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ConsensusExprError(f"invalid expression syntax: {exc.msg}") from exc

    # Build the variable → encoder-index lookup. Both letter form
    # ("a", "b", ...) and name form (slug of encoder name) are valid.
    name_to_index: dict[str, int] = {}
    slug_collisions: dict[str, list[str]] = {}
    for i, ename in enumerate(encoder_names):
        letter = string.ascii_lowercase[i]
        slug = _slugify_encoder(ename)
        name_to_index[letter] = i
        if slug in name_to_index:
            slug_collisions.setdefault(slug, []).append(ename)
        else:
            name_to_index[slug] = i
    if slug_collisions:
        raise ConsensusExprError(
            "ambiguous encoder slugs (multiple names slug to the same "
            f"identifier): {slug_collisions} — rename the encoders or use "
            "letter form."
        )

    used_indices: set[int] = set()
    _validate_safe(tree, name_to_index, used_indices)
    return CompiledPredicate(
        expr_text=expr.strip(),
        encoder_names=tuple(encoder_names),
        used_indices=frozenset(used_indices),
        _tree=tree,
    )


def _validate_safe(
    node: ast.AST,
    name_to_index: dict[str, int],
    used: set[int],
) -> None:
    """Walk the AST, rejecting any node outside the allowlist.

    Function-name positions (``min``/``max`` in ``min(a+b, c+d)``) are
    skipped — they're allowed identifiers in that slot but would
    otherwise look like undefined variables.
    """
    valid_names = sorted(name_to_index.keys())
    fn_name_ids: set[int] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            fn_name_ids.add(id(child.func))

    for child in ast.walk(node):
        if not isinstance(child, _ALLOWED_NODES):
            raise ConsensusExprError(
                f"unsupported expression construct: {type(child).__name__} "
                f"— allowed: arithmetic, comparisons, &/|/!, min(), max()"
            )
        if isinstance(child, ast.Call) and (
            not isinstance(child.func, ast.Name) or child.func.id not in _ALLOWED_FUNCS
        ):
            fn = ast.unparse(child.func) if hasattr(ast, "unparse") else "<call>"
            raise ConsensusExprError(f"function calls disallowed except min()/max(); got {fn}()")
        if isinstance(child, ast.Name) and id(child) not in fn_name_ids:
            if child.id not in name_to_index:
                raise ConsensusExprError(f"unknown variable '{child.id}' — valid identifiers: {valid_names}")
            used.add(name_to_index[child.id])


def _eval_node(node: ast.AST, env: Mapping[str, float]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env.get(node.id, 0.0)
    if isinstance(node, ast.UnaryOp):
        v = _eval_node(node.operand, env)
        if isinstance(node.op, ast.Not):
            return 1.0 if not v else 0.0
        if isinstance(node.op, ast.USub):
            return -v
        if isinstance(node.op, ast.UAdd):
            return +v
    if isinstance(node, ast.BinOp):
        a = _eval_node(node.left, env)
        b = _eval_node(node.right, env)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b if b != 0 else 0.0
        if isinstance(node.op, ast.Mod):
            return a % b if b != 0 else 0.0
        if isinstance(node.op, ast.BitAnd):
            return 1.0 if (a and b) else 0.0
        if isinstance(node.op, ast.BitOr):
            return 1.0 if (a or b) else 0.0
    if isinstance(node, ast.BoolOp):
        vals = [_eval_node(v, env) for v in node.values]
        if isinstance(node.op, ast.And):
            return 1.0 if all(vals) else 0.0
        if isinstance(node.op, ast.Or):
            return 1.0 if any(vals) else 0.0
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, env)
        for op, comp_node in zip(node.ops, node.comparators, strict=False):
            right = _eval_node(comp_node, env)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        fn_name = node.func.id  # already validated to be min/max
        args = [_eval_node(a, env) for a in node.args]
        if fn_name == "min":
            return min(args) if args else 0.0
        if fn_name == "max":
            return max(args) if args else 0.0
    raise ConsensusExprError(f"runtime: unhandled node {type(node).__name__}")


def _compare(op: ast.AST, a: float, b: float) -> bool:
    if isinstance(op, ast.Eq):
        return a == b
    if isinstance(op, ast.NotEq):
        return a != b
    if isinstance(op, ast.Lt):
        return a < b
    if isinstance(op, ast.LtE):
        return a <= b
    if isinstance(op, ast.Gt):
        return a > b
    if isinstance(op, ast.GtE):
        return a >= b
    return False


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PredicateDiagnostics:
    """Result of analysing a predicate's truth table for pathologies.

    ``hard_errors`` is a list of misconfigurations severe enough to refuse
    to run (e.g. predicate accepts every cluster, or rejects everything).
    ``warnings`` is a list of less-severe issues (single-source, ignored
    encoder, etc) that we surface but proceed past.
    """

    hard_errors: tuple[str, ...]
    warnings: tuple[str, ...]
    summary: str  # one-line, suitable for the bench banner


def diagnose_predicate(p: CompiledPredicate) -> PredicateDiagnostics:
    """Analyse a compiled predicate's truth table for misconfigurations.

    Cheap when the panel size N ≤ 8 (truth table has ≤ 256 rows); we don't
    expect operators to run with more than 8 encoders in practice.
    """
    n = len(p.encoder_names)
    table = p.truth_table()  # list[(combo: tuple[bool], accepted: bool)]
    accept_count = sum(1 for _, ok in table if ok)
    total = len(table)

    hard: list[str] = []
    warns: list[str] = []

    # ── Hard errors ────────────────────────────────────────────────────
    if accept_count == 0:
        hard.append(
            f"predicate '{p.expr_text}' is unreachable — no encoder vote "
            f"combination is accepted by it. Every cluster would be "
            f"rejected. Did you mean to flip the comparison?"
        )
    if accept_count == total:
        hard.append(
            f"predicate '{p.expr_text}' is trivially true — every cluster "
            f"would be accepted regardless of votes. Did you forget the "
            f"comparison threshold?"
        )

    # accepts_zero_votes: when every encoder votes 0
    zero_combo = tuple([False] * n)
    zero_accepted = next((ok for combo, ok in table if combo == zero_combo), False)
    if zero_accepted:
        hard.append(
            f"predicate '{p.expr_text}' accepts even when no encoder votes — "
            f"every cluster would pass consensus. Tighten the threshold."
        )

    # ── Soft warnings ──────────────────────────────────────────────────
    # single_source: cheapest accept requires only 1 encoder.
    min_votes_to_accept = min(
        (sum(combo) for combo, ok in table if ok),
        default=None,
    )
    if min_votes_to_accept == 1:
        warns.append(
            f"single-source acceptance: predicate accepts on a single vote "
            f"({_describe_min_combo(p, table)}). This skips the multi-encoder "
            f"redundancy the consensus stage exists to provide."
        )

    # ignored_encoder: column in truth table is constant
    for i, name in enumerate(p.encoder_names):
        if not _column_affects_output(table, i):
            letter = string.ascii_lowercase[i]
            slug = _slugify_encoder(name)
            warns.append(
                f"encoder '{name}' (letter '{letter}', slug '{slug}') is "
                f"referenced in the panel but its vote does not affect the "
                f"predicate output — drop it from --ensemble or include it "
                f"in the expression."
            )

    # mandatory_encoder: predicate False whenever variable is 0.
    for i, name in enumerate(p.encoder_names):
        if _is_mandatory(table, i):
            letter = string.ascii_lowercase[i]
            slug = _slugify_encoder(name)
            warns.append(
                f"encoder '{name}' (letter '{letter}', slug '{slug}') is "
                f"*mandatory* under this predicate — clusters are rejected "
                f"whenever it doesn't vote, regardless of the others. That "
                f"encoder is now a single point of failure."
            )

    # ── Summary ────────────────────────────────────────────────────────
    summary = (
        f"predicate '{p.expr_text}' over {n} encoders: "
        f"{accept_count}/{total} vote combos accept "
        f"(min votes to accept = {min_votes_to_accept})"
    )

    return PredicateDiagnostics(
        hard_errors=tuple(hard),
        warnings=tuple(warns),
        summary=summary,
    )


def _column_affects_output(
    table: list[tuple[tuple[bool, ...], bool]],
    col: int,
) -> bool:
    """True iff flipping bit ``col`` changes the predicate output for at
    least one combination of the other bits."""
    by_other: dict[tuple[bool, ...], list[bool]] = {}
    for combo, ok in table:
        other = combo[:col] + combo[col + 1 :]
        by_other.setdefault(other, []).append(ok)
    return any(len(set(outs)) > 1 for outs in by_other.values())


def _is_mandatory(table: list[tuple[tuple[bool, ...], bool]], col: int) -> bool:
    """True iff every accepted combo has ``combo[col] == True``."""
    accept_combos = [combo for combo, ok in table if ok]
    if not accept_combos:
        return False
    return all(c[col] for c in accept_combos)


def _describe_min_combo(
    p: CompiledPredicate,
    table: list[tuple[tuple[bool, ...], bool]],
) -> str:
    """Human-readable hint pointing to the cheapest accepting combo."""
    candidates = sorted((combo for combo, ok in table if ok), key=lambda c: sum(c))
    if not candidates:
        return ""
    cheapest = candidates[0]
    voters = [p.encoder_names[i] for i, voted in enumerate(cheapest) if voted]
    return f"e.g. only '{voters[0]}'" if len(voters) == 1 else f"e.g. {voters}"

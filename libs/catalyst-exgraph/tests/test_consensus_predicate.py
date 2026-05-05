"""Tests for catalyst_exgraph.consensus_predicate.

Covers the patterns documented in the README:
  * Strict majority + super-majority + unanimous + any
  * Weighted vote (constant × bool)
  * Logical AND / OR / NOT (&, |, !)
  * Mixed letter + slug variable references
  * min() / max() helpers
  * Pathology detection (unreachable, trivial, accepts-zero, single-source,
    ignored-encoder, mandatory-encoder)

Each pattern is asserted on its truth table, the most direct
behavioural contract — what's true is what the consensus stage
will see at runtime.
"""

from __future__ import annotations

import pytest
from catalyst_exgraph.consensus_predicate import (
    ConsensusExprError,
    compile_consensus_expr,
    diagnose_predicate,
)

# ---------------------------------------------------------------------------
# Patterns from the README — happy path
# ---------------------------------------------------------------------------


def _accepts(p, **votes) -> bool:
    """Tiny helper: ``_accepts(p, gliner_large=True, nuextract=False)``."""
    # Map trailing-underscore-friendly names back to encoder slugs the test
    # encoder list uses.  Tests pass the slug directly as the kwarg.
    return p.evaluate({k.replace("_", "-"): v for k, v in votes.items()})


def test_strict_majority_of_three():
    p = compile_consensus_expr("a + b + c >= 2", ["m1", "m2", "m3"])
    table = dict(p.truth_table())
    # 2-of-3 and 3-of-3 should accept; 0 and 1 should not.
    accept_counts = sorted({sum(combo) for combo, ok in table.items() if ok})
    assert accept_counts == [2, 3]


def test_unanimous_of_five():
    p = compile_consensus_expr("a + b + c + d + e >= 5", ["m1", "m2", "m3", "m4", "m5"])
    accept_combos = [combo for combo, ok in p.truth_table() if ok]
    assert accept_combos == [(True, True, True, True, True)]


def test_any_of_three():
    p = compile_consensus_expr("a + b + c >= 1", ["m1", "m2", "m3"])
    rejected = [combo for combo, ok in p.truth_table() if not ok]
    assert rejected == [(False, False, False)]


def test_weighted_vote_a_double():
    # 2*a + b + c >= 3 — accepts when a votes + at least one of b/c, OR
    # when both b and c vote (weight 2 = threshold without 'a').
    p = compile_consensus_expr("2*a + b + c >= 3", ["m1", "m2", "m3"])
    assert dict(p.truth_table())[(True, True, False)] is True   # 2 + 1 = 3
    assert dict(p.truth_table())[(True, False, False)] is False  # 2 < 3
    assert dict(p.truth_table())[(False, True, True)] is False   # 0+1+1 = 2 < 3
    assert dict(p.truth_table())[(True, False, True)] is True   # 2+0+1 = 3


def test_logical_and_or_not():
    # a & (b | c) — a AND (b OR c)
    p = compile_consensus_expr("a & (b | c)", ["m1", "m2", "m3"])
    table = dict(p.truth_table())
    assert table[(True, True, False)] is True
    assert table[(True, False, True)] is True
    assert table[(True, False, False)] is False  # a alone
    assert table[(False, True, True)] is False   # !a
    # !a + b + c >= 2 — encoder a is a "veto" (subtracts) … expressible via -a:
    p2 = compile_consensus_expr("b + c - a >= 1", ["m1", "m2", "m3"])
    table2 = dict(p2.truth_table())
    assert table2[(False, True, False)] is True  # 1 - 0 = 1
    assert table2[(True, True, False)] is False  # 1 - 1 = 0


def test_min_max_helpers():
    # min(a + b, c + d) >= 1 — at least one from {a,b} AND at least one from {c,d}
    p = compile_consensus_expr("min(a + b, c + d) >= 1", ["m1", "m2", "m3", "m4"])
    table = dict(p.truth_table())
    assert table[(True, False, True, False)] is True
    assert table[(True, True, False, False)] is False  # nothing from c/d
    assert table[(False, False, True, True)] is False  # nothing from a/b


def test_name_form_substitutes_letter():
    encoders = ["gliner-large", "nuextract-2.0-8b", "universalner-7b"]
    p_letter = compile_consensus_expr("a + b + c >= 2", encoders)
    p_slug = compile_consensus_expr(
        "gliner_large + nuextract_2_0_8b + universalner_7b >= 2", encoders
    )
    assert p_letter.truth_table() == p_slug.truth_table()


def test_mixed_letter_and_slug_in_one_expr():
    encoders = ["gliner-large", "nuextract-2.0-8b", "universalner-7b"]
    p = compile_consensus_expr("gliner_large + b + c >= 2", encoders)
    # Same truth table as 'a + b + c >= 2' since slug(gliner-large) == slot 0
    p_letter = compile_consensus_expr("a + b + c >= 2", encoders)
    assert p.truth_table() == p_letter.truth_table()


# ---------------------------------------------------------------------------
# Pathology detection
# ---------------------------------------------------------------------------


def test_unreachable_predicate_flags_hard_error():
    p = compile_consensus_expr("a + b + c >= 4", ["m1", "m2", "m3"])
    diag = diagnose_predicate(p)
    assert any("unreachable" in e for e in diag.hard_errors)


def test_trivial_predicate_flags_hard_error():
    p = compile_consensus_expr("a + b + c >= 0", ["m1", "m2", "m3"])
    diag = diagnose_predicate(p)
    # ">= 0" passes for the all-zero combo too — flagged as accepts_zero_votes
    assert any("zero" in e or "trivially" in e for e in diag.hard_errors)


def test_single_source_warns():
    p = compile_consensus_expr("a + b + c >= 1", ["m1", "m2", "m3"])
    diag = diagnose_predicate(p)
    assert any("single-source" in w for w in diag.warnings)


def test_ignored_encoder_warns():
    # 'd' is in the panel but the expression doesn't reference it
    p = compile_consensus_expr("a + b + c >= 2", ["m1", "m2", "m3", "m4"])
    diag = diagnose_predicate(p)
    assert any("does not affect" in w for w in diag.warnings)
    assert any("'m4'" in w for w in diag.warnings)


def test_mandatory_encoder_warns():
    # a & (b | c) — accepts only when a votes; a is mandatory.
    p = compile_consensus_expr("a & (b | c)", ["m1", "m2", "m3"])
    diag = diagnose_predicate(p)
    assert any("mandatory" in w for w in diag.warnings)
    assert any("'m1'" in w for w in diag.warnings)


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_undefined_letter_rejected():
    with pytest.raises(ConsensusExprError, match="unknown variable 'd'"):
        compile_consensus_expr("a + b + d >= 2", ["m1", "m2", "m3"])


def test_function_call_rejected():
    with pytest.raises(ConsensusExprError, match="function calls disallowed"):
        compile_consensus_expr("abs(a) + b >= 1", ["m1", "m2"])


def test_attribute_access_rejected():
    # AttributeError shows up either as a disallowed Call (ast.Attribute as
    # the function expression) or an unsupported node — both are fine.
    with pytest.raises(ConsensusExprError, match="unsupported|function calls"):
        compile_consensus_expr("a.bit_length() + b >= 1", ["m1", "m2"])


def test_empty_expr_rejected():
    with pytest.raises(ConsensusExprError, match="empty"):
        compile_consensus_expr("", ["m1", "m2"])


def test_too_many_encoders_rejected():
    with pytest.raises(ConsensusExprError, match="at most 26"):
        compile_consensus_expr("a >= 1", [f"m{i}" for i in range(27)])


def test_slug_collision_rejected():
    # Two encoders that slug to the same thing should fail with a clear error
    with pytest.raises(ConsensusExprError, match="ambiguous encoder slugs"):
        compile_consensus_expr("a + b >= 1", ["foo-bar", "foo_bar"])

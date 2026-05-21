"""Bill-claim domain model — LegalRuleML-style normative statement.

A `BillClaim` is the output of the LLM claim-synthesis pass. It composes
the AMR primitives (atomic frame extractions) and the bill text into one
structured legal claim with explicit deontic structure, conditions,
exceptions, and provenance back to the source sentence.

This is a NEW wire type — deliberately separate from
``catalyst_contracts_core.Assertion`` (the flat SPO + AMR-aware shape).
We made that call because:

- Legal claims need typed Conditions (deadline / scope / trigger / …),
  Exceptions, and Penalty as first-class fields. Squeezing them into
  `qualifiers: dict[str, str]` loses the type discipline.
- Deontic logic vocabulary (`requires` / `prohibits` / `permits` as
  obligations / prohibitions / permissions) is a closed enum, not the
  free-form predicate strings the assertion path uses.
- The reader of a Claims tab cares about different fields than the
  reader of an AMR primitives tab — separate Pydantic models keeps each
  surface honest about what it carries.

Shape draws from:
- LegalRuleML (W3C / OASIS) — normative-statement structure
- LKIF (Legal Knowledge Interchange Format) — Norm / Bearer / Action
- PropBank role mapping — actor + action + object

References folded into the design:
- Servantez et al. 2023 (arxiv 2311.04911) — GPT-4 → legislative structure
- Legal2LogicICL (arxiv 2604.11699) — few-shot ICL for legal formulas
- Claimify (arxiv 2502.10855) — decontextualisation discipline
- LegalBench Explainable Rule Application (arxiv 2506.16335) — self-flag
- GroundedKG-RAG (arxiv 2604.04359) — every claim grounded in source span
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from catalyst_contracts_core import Provenance


class ClaimOperator(StrEnum):
    """Closed predicate vocabulary — never extended at runtime.

    Split into deontic (requires/prohibits/permits — the standard
    deontic-logic primitives: obligation / prohibition / permission) and
    structural (defines/establishes/applies_to/amends/repeals/
    authorizes/appropriates/designates/exempts — declarative-but-
    legally-operative predicates that don't carry deontic force).
    """

    # ── Deontic operators ────────────────────────────────────────
    REQUIRES = "requires"
    PROHIBITS = "prohibits"
    PERMITS = "permits"

    # ── Structural / declarative operators ───────────────────────
    DEFINES = "defines"
    ESTABLISHES = "establishes"
    APPLIES_TO = "applies_to"
    AMENDS = "amends"
    REPEALS = "repeals"
    AUTHORIZES = "authorizes"
    APPROPRIATES = "appropriates"
    DESIGNATES = "designates"
    EXEMPTS = "exempts"


DEONTIC_OPERATORS: frozenset[ClaimOperator] = frozenset(
    {ClaimOperator.REQUIRES, ClaimOperator.PROHIBITS, ClaimOperator.PERMITS}
)


def operator_class(op: ClaimOperator) -> Literal["deontic", "structural"]:
    """``"deontic"`` for the three deontic-logic primitives;
    ``"structural"`` for everything else. Used by the UI to render
    deontic chips differently from structural ones."""
    return "deontic" if op in DEONTIC_OPERATORS else "structural"


ConditionType = Literal[
    "deadline",  # time bound (e.g. "within 48 hours")
    "scope",  # limiting clause (e.g. "platforms with > 1M users")
    "trigger",  # precondition (e.g. "upon written request")
    "jurisdiction",  # territorial / forum scope (e.g. "federal courts")
    "frequency",  # recurrence (e.g. "annually")
    "form",  # manner / form (e.g. "in writing")
]


class ClaimCondition(BaseModel):
    """Typed condition on a claim. Multiple per claim allowed."""

    model_config = {"extra": "forbid"}

    type: ConditionType
    text: str


class ClaimTemporalWindow(BaseModel):
    """Temporal validity window for a claim.

    Most bill claims are atemporal once enacted (definitions, permanent
    rules) — ``is_atemporal=True`` skips the time-window filter on
    downstream queries. Use ``valid_from`` for delayed effective dates
    (e.g. "this Act takes effect 180 days after enactment").
    """

    model_config = {"extra": "forbid"}

    valid_from: str | None = None  # ISO date
    valid_until: str | None = None  # ISO date — None = open-ended
    is_atemporal: bool = False


class BillClaim(BaseModel):
    """One synthesised legal claim from a congressional bill.

    Serialised to ``gold/congress_data/bill/bill_claims/{partition}/data.jsonl``
    via the standard JSONL serializer. Wire-shape contract: ``extra="forbid"``,
    frozen=False (the LLM may need to populate fields incrementally during
    structured-output generation).
    """

    model_config = {"extra": "forbid"}

    # ── Identification ───────────────────────────────────────────
    claim_id: str = Field(
        description=(
            "Stable hash of (actor, operator, action, source_chunk_id). "
            "Computed by the asset post-LLM-response; the model itself can "
            "emit an empty string and the asset fills it."
        ),
        default="",
    )

    # ── LegalRuleML-style normative core ────────────────────────
    actor: str = Field(
        description=(
            "WHO bears the deontic operator. Short noun phrase, "
            "decontextualised. E.g. 'covered platforms', "
            "'the Attorney General', 'any person who knowingly publishes', "
            "'the bill' (when no specific actor)."
        )
    )
    operator: ClaimOperator = Field(
        description=(
            "Exactly one of the closed predicate vocab. EXACTLY one of "
            "{requires, prohibits, permits, defines, establishes, "
            "applies_to, amends, repeals, authorizes, appropriates, "
            "designates, exempts}. No invention."
        )
    )
    action: str = Field(
        description=(
            "The verb phrase (or noun phrase for structural ops) the "
            "operator applies to. E.g. 'remove non-consensual intimate "
            "visual depictions', 'the offence of cyber-flashing', "
            "'Section 230 of the Communications Decency Act'."
        )
    )
    object: str | None = Field(
        default=None,
        description=(
            "When the action's direct object is meaningfully distinct "
            "from action, populate it. Often null for full verb-phrase "
            "actions."
        ),
    )

    # ── Conditions, exceptions, penalty ─────────────────────────
    conditions: list[ClaimCondition] = Field(default_factory=list)
    exceptions: list[str] = Field(
        default_factory=list,
        description=(
            "Carve-outs that defeasibly limit the claim. Short text "
            "descriptions, e.g. ['news-reporting platforms', 'users "
            "under 13 with parental consent']."
        ),
    )
    penalty: str | None = Field(
        default=None,
        description="Enforcement clause, e.g. 'civil fine up to $500,000 per violation'.",
    )

    # ── Temporal ─────────────────────────────────────────────────
    temporal_window: ClaimTemporalWindow | None = None

    # ── Source grounding ────────────────────────────────────────
    sentence_text: str = Field(
        description=(
            "A single verbatim sentence from the bill text — the source "
            "the LLM composed this claim from. Never paraphrased."
        )
    )
    source_chunk_id: str | None = Field(
        default=None,
        description=(
            "chunk_id from bill_chunks/{partition} that contained the "
            "sentence_text. Filled by the asset post-LLM-response by "
            "substring-matching sentence_text against the chunks list."
        ),
    )

    # ── Quality / review ────────────────────────────────────────
    confidence: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description=(
            "LLM self-rating: >0.9 = direct paraphrase of bill language; "
            "0.7–0.9 = composed across primitives; <0.7 = set "
            "review_needed=true."
        ),
    )
    review_needed: bool = Field(
        default=False,
        description=(
            "LLM self-flag when (a) it had to choose between two "
            "predicates and wasn't sure, (b) the source sentence is "
            "ambiguous in scope, or (c) it composed across more than two "
            "sentences."
        ),
    )
    review_reason: str | None = Field(
        default=None,
        description=("When review_needed=true, a 1-line note explaining what's uncertain."),
    )

    # ── Provenance (mirrors contracts_core.Provenance) ─────────
    provenance: Provenance | None = Field(
        default=None,
        description=(
            "Stamped by the asset post-LLM-response: extraction_method=LLM, "
            "extraction_model='bill_claims_v1', source_document_id + "
            "chunk_id from the bill. The LLM doesn't fill this."
        ),
    )


class BillClaimsResult(BaseModel):
    """LLM structured-output wrapper. The asset asks the LLM to emit
    ``claims: list[BillClaim]`` and unwraps the list.

    No hard ``max_length`` — the right number of claims scales with
    the bill (a procedural resolution has 1–3; an omnibus appropriations
    bill can have hundreds). The prompt + bill_size context tells the
    LLM what's appropriate; the ceiling here is just a sanity guard
    against runaway output. The LLM's own context window is the real
    upper bound."""

    model_config = {"extra": "forbid"}

    claims: list[BillClaim] = Field(
        description=(
            "Adaptive count — one claim per substantive provision in "
            "the bill. Procedural resolutions emit 1–3; substantive "
            "bills emit 10–30; omnibus bills can emit 100+."
        ),
        min_length=1,
        # 500 is a sanity ceiling, not a recommended target. The prompt
        # asks the LLM to scale claim count to bill substance. Hitting
        # this cap on a non-omnibus bill means the LLM is being too
        # granular and the result needs review.
        max_length=500,
    )

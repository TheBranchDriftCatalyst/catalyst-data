"""Consensus taxonomy — per-encoder type → canonical MentionType map.

Phase B of the v4 NER-ensemble extraction epic (CD-94ow / CD-y4u0).

Each encoder emits its own raw label strings.  This module provides a
TYPE_CANONICAL lookup table that maps ``(encoder_name, raw_label)`` →
canonical MentionType string (one of the MentionType enum values from
``catalyst_contracts_core.enums``).

Unknown raw labels pass through unchanged and trigger a one-time WARNING
so the problem is visible in smoke tests without spamming production logs.

PII_TYPES
---------
Canonical type strings that require only K=1 quorum in ConsensusNode
because gliner-pii is the *only* encoder in the default ensemble that
reliably surfaces these categories.  Relaxing the quorum from ceil(N/2)
to 1 preserves the recall advantage of specialised PII detection without
requiring all general NER encoders to agree.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── Canonical PII types (quorum=1 override in ConsensusNode) ─────────────────

PII_TYPES: set[str] = {
    "PHONE_NUMBER",
    "EMAIL",
    "SSN",
    "CREDIT_CARD",
    "ADDRESS",
    "DOB",
}

# ── Per-encoder raw-label → canonical-type maps ───────────────────────────────
#
# The canonical labels are MentionType enum values from
# ``catalyst_contracts_core.enums.MentionType``.
#
# Structure: TYPE_CANONICAL[encoder_name][raw_label] = canonical_str
#
# Notes on each encoder:
#
# gliner-medium / gliner-large
#   GLiNERClient reverses MENTION_TYPE_TO_GLINER_LABEL, so the labels it
#   emits as `mention_type` are already the MentionType enum VALUE strings
#   (e.g. "PERSON", "ORG", "LOC").  Passthrough is correct; aliases for the
#   common abbreviated forms are included defensively.
#
# gliner-pii (urchade/gliner_multi_pii-v1)
#   This model returns raw GLiNER label strings rather than enum values.
#   Typical labels observed in bench runs: "name", "phone number",
#   "email address", "social security number", "credit card number",
#   "address", "date of birth".  Mapped to PII_TYPES canonical values or
#   PERSON for name.
#
# nuextract-2.0-8b
#   NuExtract's structured JSON output uses title-case category names
#   ("Person", "Organization", "Location").  The nuextract client in
#   catalyst_langgraph maps these to MentionType enum values, so the
#   labels arriving here should already be canonical.  Passthrough + aliases
#   handle edge cases where the raw category bleeds through.
#
# universalner-7b
#   UniversalNER's client maps its answers to MentionType enum values via
#   MENTION_TYPE_TO_QUERY reverse-lookup.  Passthrough covers the typical
#   case; common spacy-style abbreviations are aliased defensively.

TYPE_CANONICAL: dict[str, dict[str, str]] = {
    # ── gliner-medium ──────────────────────────────────────────────────────
    "gliner-medium": {
        # Already canonical (MentionType enum values)
        "PERSON": "PERSON",
        "ORG": "ORG",
        "GPE": "GPE",
        "LOC": "LOC",
        "DATE": "DATE",
        "LAW": "LAW",
        "EVENT": "EVENT",
        "MONEY": "MONEY",
        "NORP": "NORP",
        "FACILITY": "FACILITY",
        "DOCUMENT": "DOCUMENT",
        "BOOK": "BOOK",
        "ROLE": "ROLE",
        "STRATEGIC_ASSET": "STRATEGIC_ASSET",
        "FINANCIAL_INSTRUMENT": "FINANCIAL_INSTRUMENT",
        "OTHER": "OTHER",
        # Defensive aliases for raw GLiNER label strings that could bleed through
        "person": "PERSON",
        "organization": "ORG",
        "country or city": "GPE",
        "location": "LOC",
        "date": "DATE",
        "law or legislation": "LAW",
        "event": "EVENT",
        "money or financial amount": "MONEY",
        "political or national group": "NORP",
        "facility or building": "FACILITY",
        "document or report": "DOCUMENT",
        "book": "BOOK",
        "role or job title": "ROLE",
        "strategic asset": "STRATEGIC_ASSET",
        "financial instrument": "FINANCIAL_INSTRUMENT",
        # Common shorthand
        "ORGANIZATION": "ORG",
        "LOCATION": "LOC",
    },
    # ── gliner-large (same label set as gliner-medium) ─────────────────────
    "gliner-large": {
        "PERSON": "PERSON",
        "ORG": "ORG",
        "GPE": "GPE",
        "LOC": "LOC",
        "DATE": "DATE",
        "LAW": "LAW",
        "EVENT": "EVENT",
        "MONEY": "MONEY",
        "NORP": "NORP",
        "FACILITY": "FACILITY",
        "DOCUMENT": "DOCUMENT",
        "BOOK": "BOOK",
        "ROLE": "ROLE",
        "STRATEGIC_ASSET": "STRATEGIC_ASSET",
        "FINANCIAL_INSTRUMENT": "FINANCIAL_INSTRUMENT",
        "OTHER": "OTHER",
        "person": "PERSON",
        "organization": "ORG",
        "country or city": "GPE",
        "location": "LOC",
        "date": "DATE",
        "law or legislation": "LAW",
        "event": "EVENT",
        "money or financial amount": "MONEY",
        "political or national group": "NORP",
        "facility or building": "FACILITY",
        "document or report": "DOCUMENT",
        "book": "BOOK",
        "role or job title": "ROLE",
        "strategic asset": "STRATEGIC_ASSET",
        "financial instrument": "FINANCIAL_INSTRUMENT",
        "ORGANIZATION": "ORG",
        "LOCATION": "LOC",
    },
    # ── gliner-pii (urchade/gliner_multi_pii-v1) ───────────────────────────
    # Raw labels emitted by this model use lowercase natural-language strings.
    # Mapped to PII_TYPES canonical values so ConsensusNode applies K=1 quorum.
    "gliner-pii": {
        # Name variants → PERSON
        "name": "PERSON",
        "person name": "PERSON",
        "full name": "PERSON",
        "first name": "PERSON",
        "last name": "PERSON",
        # Phone
        "phone number": "PHONE_NUMBER",
        "phone": "PHONE_NUMBER",
        "telephone": "PHONE_NUMBER",
        "mobile number": "PHONE_NUMBER",
        # Email
        "email": "EMAIL",
        "email address": "EMAIL",
        # SSN / government IDs
        "social security number": "SSN",
        "ssn": "SSN",
        "national id": "SSN",
        "passport number": "SSN",
        "driver's license": "SSN",
        "driver license": "SSN",
        # Credit card
        "credit card number": "CREDIT_CARD",
        "credit card": "CREDIT_CARD",
        "debit card": "CREDIT_CARD",
        # Physical address
        "address": "ADDRESS",
        "street address": "ADDRESS",
        "home address": "ADDRESS",
        "mailing address": "ADDRESS",
        # Date of birth
        "date of birth": "DOB",
        "dob": "DOB",
        "birthday": "DOB",
        "birth date": "DOB",
        # Passthrough for canonical values in case client already mapped them
        "PERSON": "PERSON",
        "PHONE_NUMBER": "PHONE_NUMBER",
        "EMAIL": "EMAIL",
        "SSN": "SSN",
        "CREDIT_CARD": "CREDIT_CARD",
        "ADDRESS": "ADDRESS",
        "DOB": "DOB",
        # gliner-pii is now prompted with PII_GLINER_LABELS only (see
        # catalyst_langgraph.clients.gliner.PII_GLINER_LABELS) so the
        # general-NER passthroughs the previous expand carried (DATE/LAW/
        # MONEY/etc.) are dead code. Keeping a defensive minimal "OTHER"
        # so any stray label the model emits flows to OTHER instead of
        # warning. Reverts CD-lxcf-adjacent expand from 73ea4df.
        "OTHER": "OTHER",
    },
    # ── nuextract-2.0-8b ───────────────────────────────────────────────────
    # NuExtract returns structured JSON with title-case category labels.
    # The catalyst_langgraph NuExtract client maps these via CATEGORY_TO_MENTION_TYPE
    # (Person→PERSON, Organization→ORG, Location→LOC).
    # Passthrough handles already-canonical labels; title-case aliases handle
    # cases where the raw category leaks through.
    "nuextract-2.0-8b": {
        # Already canonical
        "PERSON": "PERSON",
        "ORG": "ORG",
        "GPE": "GPE",
        "LOC": "LOC",
        "DATE": "DATE",
        "LAW": "LAW",
        "EVENT": "EVENT",
        "MONEY": "MONEY",
        "NORP": "NORP",
        "FACILITY": "FACILITY",
        "DOCUMENT": "DOCUMENT",
        "BOOK": "BOOK",
        "ROLE": "ROLE",
        "STRATEGIC_ASSET": "STRATEGIC_ASSET",
        "FINANCIAL_INSTRUMENT": "FINANCIAL_INSTRUMENT",
        "OTHER": "OTHER",
        # Title-case raw NuExtract categories
        "Person": "PERSON",
        "Organization": "ORG",
        "Location": "LOC",
        "Date": "DATE",
        "Event": "EVENT",
        "Money": "MONEY",
        "Facility": "FACILITY",
        "Document": "DOCUMENT",
        # Common abbreviations
        "ORGANIZATION": "ORG",
        "LOCATION": "LOC",
    },
    # ── universalner-7b ────────────────────────────────────────────────────
    # UniversalNER (via zero-shot prompting) maps answers back to MentionType
    # enum values through MENTION_TYPE_TO_QUERY reverse lookup.
    # Labels arriving here should be canonical; aliases cover common variants.
    "universalner-7b": {
        "PERSON": "PERSON",
        "ORG": "ORG",
        "GPE": "GPE",
        "LOC": "LOC",
        "DATE": "DATE",
        "LAW": "LAW",
        "EVENT": "EVENT",
        "MONEY": "MONEY",
        "NORP": "NORP",
        "FACILITY": "FACILITY",
        "DOCUMENT": "DOCUMENT",
        "BOOK": "BOOK",
        "ROLE": "ROLE",
        "STRATEGIC_ASSET": "STRATEGIC_ASSET",
        "FINANCIAL_INSTRUMENT": "FINANCIAL_INSTRUMENT",
        "OTHER": "OTHER",
        # Common shorthand / aliases
        "ORGANIZATION": "ORG",
        "LOCATION": "LOC",
        "person": "PERSON",
        "organization": "ORG",
        "location": "LOC",
    },
}

# ── Module-level seen-set for warn-once on unknown types ─────────────────────
_warned_unknowns: set[tuple[str, str]] = set()


def canonicalize_type(encoder_name: str, raw_type: str) -> str:
    """Map a raw encoder label to its canonical MentionType string.

    Returns the canonical string if found.  Falls back to ``raw_type``
    unchanged and emits a one-time WARNING per ``(encoder_name, raw_type)``
    pair so unknown labels are visible in smoke-test logs without flooding
    production.

    Args:
        encoder_name: The encoder whose label set to look up (e.g.
            ``"gliner-pii"``).
        raw_type: The raw mention_type / entity_type string from the
            encoder's output.

    Returns:
        Canonical type string.
    """
    encoder_map = TYPE_CANONICAL.get(encoder_name)
    if encoder_map is None:
        # Completely unknown encoder — warn once then passthrough
        key = (encoder_name, "<encoder>")
        if key not in _warned_unknowns:
            _warned_unknowns.add(key)
            logger.warning(
                "consensus_taxonomy: unknown encoder %r — type labels will "
                "pass through unchanged.  Add a mapping to TYPE_CANONICAL.",
                encoder_name,
            )
        return raw_type

    canonical = encoder_map.get(raw_type)
    if canonical is not None:
        return canonical

    # Known encoder but unknown label — warn once then passthrough
    key = (encoder_name, raw_type)
    if key not in _warned_unknowns:
        _warned_unknowns.add(key)
        logger.warning(
            "consensus_taxonomy: encoder %r emitted unknown type %r — "
            "passing through unchanged.  Add a mapping to TYPE_CANONICAL.",
            encoder_name,
            raw_type,
        )
    return raw_type
